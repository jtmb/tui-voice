"""Continuous, local turn detection with bounded, asynchronous transcription.

This is a separate mode from draft-only dictation. Capture emits interruption
and endpoint events immediately; one ASR consumer finishes each utterance in
order. The caller owns automatic sending and the explicit listening toggle.
"""
from collections import deque
import math
import re
import threading

from live_dictation import FRAME_BYTES, audible

FRAME_SIZE = FRAME_BYTES // 50  # 20 ms, mono signed-16-bit PCM at 16 kHz.


def _join_overlap(previous, text):
    """Remove repeated boundary words only for an explicitly overlapped chunk."""
    left, right = previous.split(), text.split()
    normalize = lambda word: re.sub(r"[^\w']", '', word).casefold()
    for count in range(min(12, len(left), len(right)), 0, -1):
        suffix = [normalize(word) for word in left[-count:]]
        if all(suffix) and suffix == [normalize(word) for word in right[:count]]:
            return ' '.join(left + right[count:])
    return ' '.join(left + right)


class LiveConversation:
    """Detect turns without ever waiting for inference in ``feed``.

    Input is mono 16 kHz s16le PCM. Events use one integer ``turn_id`` per
    utterance: speech_start, speech_end, then a final turn (possibly empty). A later
    speech_start may precede an earlier turn's ASR result. A queue end marker
    ensures the final text includes every segment, including an in-flight one.

    ``cancel_event`` is also passed to the transport by the caller so stopping
    the listener can interrupt a blocked local socket read.
    """

    def __init__(self, transcribe, emit, threshold=-35, silence=0.8,
                 start_seconds=0.1, max_pending_seconds=32,
                 cancel_event=None):
        if not math.isfinite(silence) or not 0.2 <= silence <= 5:
            raise ValueError('Live voice silence must be between 0.2 and 5 seconds')
        if not math.isfinite(threshold) or not -80 <= threshold <= 0:
            raise ValueError('Live voice threshold must be between -80 and 0 dB')
        self.transcribe = transcribe
        self.emit = emit
        self.threshold = 32768 * math.pow(10, threshold / 20)
        self.silence_bytes = round(silence * FRAME_BYTES)
        self.start_bytes = max(FRAME_SIZE, round(start_seconds * FRAME_BYTES))
        self.max_pending_bytes = round(max_pending_seconds * FRAME_BYTES)
        self.condition = threading.Condition()
        self.cancel_event = cancel_event or threading.Event()
        self.jobs = deque()
        self.pending_bytes = 0  # Includes the in-flight request, not just the queue.
        self.partial_frame = bytearray()
        self.preroll = bytearray()
        self.audio = bytearray()
        self.candidate_bytes = 0
        self.quiet_bytes = 0
        self.segment_has_voice = False
        self.segment_overlap = False
        self.turn_id = 0
        self.active = False
        self.done = False
        self.error = None
        self.turns = 0
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name='tui-voice-listen-asr')

    def start(self):
        self.thread.start()

    def feed(self, pcm):
        if len(pcm) % 2:
            raise ValueError('Microphone returned a truncated PCM sample')
        with self.condition:
            if self.error:
                raise self.error
            if self.done or self.cancel_event.is_set():
                return
            self.partial_frame.extend(pcm)
            while len(self.partial_frame) >= FRAME_SIZE:
                block = bytes(self.partial_frame[:FRAME_SIZE])
                del self.partial_frame[:FRAME_SIZE]
                self._feed_frame(block)

    def _feed_frame(self, block):
        sound = audible(block, self.threshold)
        if not self.active:
            self.preroll.extend(block)
            del self.preroll[:-round(0.3 * FRAME_BYTES)]
            self.candidate_bytes = self.candidate_bytes + len(block) if sound else 0
            if self.candidate_bytes < self.start_bytes:
                return
            self.turn_id += 1
            self.active = True
            self.audio = self.preroll
            self.preroll = bytearray()
            self.quiet_bytes = 0
            self.segment_has_voice = True
            self.emit({'event': 'speech_start', 'turn_id': self.turn_id})
            return

        self.audio.extend(block)
        if sound:
            self.quiet_bytes = 0
            self.segment_has_voice = True
        else:
            self.quiet_bytes += len(block)
        if self.quiet_bytes >= self.silence_bytes:
            self._end_turn('silence')
        elif len(self.audio) >= 8 * FRAME_BYTES:
            # Keep a short overlap when there was no natural boundary. The
            # next request can recover a word straddling the eight-second cut.
            self._seal_segment(overlap=True)
        elif self.segment_has_voice and self.quiet_bytes >= round(0.3 * FRAME_BYTES):
            # A phrase can be decoded before the longer end-of-turn silence.
            self._seal_segment(overlap=False)

    def _seal_segment(self, overlap=False):
        if self.segment_has_voice:
            pcm = bytes(self.audio)
            if self.pending_bytes + len(pcm) > self.max_pending_bytes:
                raise RuntimeError(
                    'Local transcription fell behind live speech; stop Live Voice '
                    'and check the local ASR worker before retrying')
            self.jobs.append(('audio', self.turn_id, pcm, self.segment_overlap))
            self.pending_bytes += len(pcm)
        self.audio = self.audio[-round(0.3 * FRAME_BYTES):] if overlap else bytearray()
        self.segment_overlap = overlap
        self.segment_has_voice = False
        self.condition.notify_all()

    def _end_turn(self, reason):
        self._seal_segment()
        # This marker follows every audio job, even one already in flight.
        self.jobs.append(('end', self.turn_id, b'', False))
        self.emit({'event': 'speech_end', 'turn_id': self.turn_id, 'reason': reason})
        self.audio.clear()
        self.preroll.clear()
        self.candidate_bytes = 0
        self.quiet_bytes = 0
        self.active = False
        self.condition.notify_all()

    def finish(self, timeout=150):
        with self.condition:
            if not self.done and not self.cancel_event.is_set():
                if self.partial_frame:
                    self._feed_frame(bytes(self.partial_frame))
                    self.partial_frame.clear()
                if self.active:
                    self._end_turn('stop')
                self.done = True
                self.condition.notify_all()
        self.thread.join(timeout=timeout)
        if self.thread.is_alive():
            self.cancel()
            raise TimeoutError('Local live conversation transcription did not finish')
        if self.error:
            raise self.error
        # Final text is emitted once per turn, never duplicated in the helper's
        # final status line and never accumulated across an unlimited session.
        return ''

    def cancel(self):
        with self.condition:
            self.cancel_event.set()
            self.partial_frame.clear()
            self.audio.clear()
            self.preroll.clear()
            self.jobs.clear()
            self.condition.notify_all()
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=0.5)

    def _run(self):
        texts = {}
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(
                        lambda: self.cancel_event.is_set() or self.jobs or self.done)
                    if self.cancel_event.is_set():
                        return
                    if not self.jobs:
                        return
                    kind, turn_id, pcm, overlap = self.jobs.popleft()
                value = str(self.transcribe(pcm) or '').strip() if kind == 'audio' else ''
                with self.condition:
                    if self.cancel_event.is_set():
                        return
                    if kind == 'audio':
                        self.pending_bytes -= len(pcm)
                        if value:
                            old = texts.get(turn_id, '')
                            texts[turn_id] = (_join_overlap(old, value) if overlap
                                              else ' '.join(filter(None, (old, value))))
                    else:
                        text = texts.pop(turn_id, '')
                        self.turns += 1
                        self.emit({'event': 'turn', 'turn_id': turn_id, 'text': text})
        except Exception as error:
            with self.condition:
                if not self.cancel_event.is_set():
                    self.error = error
                self.condition.notify_all()
