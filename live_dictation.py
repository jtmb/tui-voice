"""Bounded, single-flight local ASR with replaceable live hypotheses."""
from collections import deque
import math
import struct
import threading
import time

RATE = 16000
FRAME_BYTES = RATE * 2


def audible(pcm, threshold):
    # Match silencedetect's sample-amplitude gate rather than whole-turn RMS.
    return any(abs(x) >= threshold for x in struct.unpack(f'<{len(pcm) // 2}h', pcm))


class LiveTranscriber:
    """Capture never waits for ASR; one consumer decodes only the newest preview.

    Sealed phrases are decoded once in order. The open phrase is revised roughly
    once per second, never queued as a backlog of obsolete partial requests.
    """

    def __init__(self, transcribe, emit, threshold=-35, interval=1.0):
        self.transcribe = transcribe
        self.emit = emit
        self.interval = interval
        self.threshold = 32768 * math.pow(10, threshold / 20)
        self.condition = threading.Condition()
        self.phrases = deque()
        self.audio = bytearray()
        self.quiet = 0.0
        self.voiced = False
        self.done = False
        self.cancelled = False
        self.error = None
        self.text = ""
        self.committed = []
        self.revision = 0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def feed(self, pcm):
        with self.condition:
            self.audio.extend(pcm)
            self.revision += 1
            duration = len(pcm) / FRAME_BYTES
            sound = audible(pcm, self.threshold)
            if sound:
                self.voiced = True
                self.quiet = 0
            else:
                self.quiet += duration
            if not self.voiced and len(self.audio) > int(0.3 * FRAME_BYTES):
                del self.audio[:-int(0.3 * FRAME_BYTES)]
            # Prefer natural phrase boundaries; cap every request at eight
            # seconds without stopping the capture itself.
            if (self.voiced and self.quiet >= 0.35) or len(self.audio) >= 8 * FRAME_BYTES:
                if self.voiced:
                    if sum(map(len, self.phrases)) >= 120 * FRAME_BYTES:
                        raise RuntimeError('Local transcription fell behind; stop and retry dictation')
                    self.phrases.append(bytes(self.audio))
                self.audio.clear()
                self.voiced = False
                self.revision += 1
            self.condition.notify_all()
            return sound

    def finish(self):
        with self.condition:
            if self.audio and self.voiced:
                self.phrases.append(bytes(self.audio))
            self.audio.clear()
            self.done = True
            self.condition.notify_all()
        self.thread.join(timeout=150)
        if self.thread.is_alive():
            self.cancel()
            raise TimeoutError('Local live transcription did not finish')
        if self.error:
            raise self.error
        return self.text

    def cancel(self):
        with self.condition:
            self.cancelled = True
            self.audio.clear()
            self.phrases.clear()
            self.condition.notify_all()

    def _run(self):
        last_preview = 0.0
        last_revision = -1
        try:
            while True:
                with self.condition:
                    while True:
                        if self.cancelled:
                            return
                        sealed = bool(self.phrases)
                        if sealed:
                            pcm = self.phrases.popleft()
                            break
                        if self.done:
                            return
                        now = time.monotonic()
                        if (self.voiced and len(self.audio) >= FRAME_BYTES
                                and self.revision != last_revision
                                and now - last_preview >= self.interval):
                            pcm = bytes(self.audio)
                            last_revision = self.revision
                            last_preview = now
                            break
                        self.condition.wait(timeout=self.interval)
                value = str(self.transcribe(pcm) or '').strip()
                with self.condition:
                    if self.cancelled:
                        return
                    if sealed:
                        if value:
                            self.committed.append(value)
                        self.text = ' '.join(self.committed)
                    else:
                        self.text = ' '.join([*self.committed, value]).strip()
                    self.emit({'event': 'partial', 'text': self.text})
        except Exception as error:
            self.error = error
