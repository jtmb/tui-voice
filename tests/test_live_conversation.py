"""Live turn boundaries, interruption latency, backpressure and helper wiring."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import struct
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_conversation import FRAME_SIZE, LiveConversation
from live_dictation import FRAME_BYTES
from test_helper_bugs import helper


def pcm(seconds, amplitude=1000):
    return struct.pack('<h', amplitude) * round(16000 * seconds)


class ConversationTests(unittest.TestCase):
    def listener(self, decode, events, **kwargs):
        live = LiveConversation(decode, events.append, **kwargs)
        self.addCleanup(live.cancel)
        return live

    def test_debounced_interrupt_and_endpoint_do_not_wait_for_busy_asr(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        events = []

        def decode(data):
            entered.set()
            self.assertTrue(release.wait(2))
            return 'first'

        live = self.listener(decode, events)
        live.start()
        live.feed(pcm(0.06))
        live.feed(pcm(0.1, 0))
        self.assertEqual(events, [], 'a brief click must not interrupt playback')
        live.feed(pcm(0.08))
        self.assertEqual(events, [])
        live.feed(pcm(0.02))
        self.assertEqual(events, [{'event': 'speech_start', 'turn_id': 1}])
        live.feed(pcm(0.3, 0))  # Early phrase ASR starts before the turn ends.
        self.assertTrue(entered.wait(2))
        live.feed(pcm(0.5, 0))
        live.feed(pcm(0.1))
        self.assertEqual([(item['event'], item['turn_id']) for item in events],
                         [('speech_start', 1), ('speech_end', 1), ('speech_start', 2)])
        release.set()
        live.finish()
        self.assertEqual([item['turn_id'] for item in events if item['event'] == 'turn'], [1, 2])

    def test_final_turn_waits_for_inflight_chunk_and_keeps_last_word(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        events, lengths = [], []

        def decode(data):
            lengths.append(len(data))
            if len(lengths) == 1:
                entered.set()
                self.assertTrue(release.wait(2))
                return 'The eight second boundary'
            return 'boundary has the final word.'

        live = self.listener(decode, events)
        live.start()
        live.feed(pcm(8))
        self.assertTrue(entered.wait(2))
        live.feed(pcm(0.5))
        live.feed(pcm(0.8, 0))
        self.assertEqual(events[-1]['event'], 'speech_end')
        self.assertFalse(any(item['event'] == 'turn' for item in events))
        release.set()
        live.finish()
        self.assertEqual([item for item in events if item['event'] == 'turn'],
                         [{'event': 'turn', 'turn_id': 1,
                           'text': 'The eight second boundary has the final word.'}])
        self.assertEqual(lengths, [8 * FRAME_BYTES, round(1.1 * FRAME_BYTES)])

    def test_long_speech_is_one_turn_with_bounded_overlapping_requests(self):
        events, lengths = [], []
        live = self.listener(lambda data: lengths.append(len(data)) or 'words', events,
                             max_pending_seconds=64)
        live.feed(pcm(40))
        self.assertEqual(events, [{'event': 'speech_start', 'turn_id': 1}])
        live.start()
        live.finish()
        self.assertLessEqual(max(lengths), 8 * FRAME_BYTES)
        self.assertEqual(sum(lengths) - round(0.3 * FRAME_BYTES) * (len(lengths) - 1),
                         40 * FRAME_BYTES)
        self.assertEqual([item['turn_id'] for item in events if item['event'] == 'turn'], [1])
        self.assertEqual(events[-2], {'event': 'speech_end', 'turn_id': 1, 'reason': 'stop'})

    def test_silence_keeps_listener_on_and_empty_recognition_still_completes(self):
        events = []
        live = self.listener(lambda data: '', events)
        live.start()
        live.feed(pcm(60, 0))
        self.assertEqual(events, [])
        live.feed(pcm(0.3))
        live.feed(pcm(0.8, 0))
        live.feed(pcm(60, 0))
        self.assertFalse(live.done)
        live.feed(pcm(0.3))
        live.finish()
        self.assertEqual([item for item in events if item['event'] == 'turn'],
                         [{'event': 'turn', 'turn_id': 1, 'text': ''},
                          {'event': 'turn', 'turn_id': 2, 'text': ''}])

    def test_short_pause_splits_asr_phrases_without_finishing_user_turn(self):
        events, calls = [], []
        live = self.listener(lambda data: calls.append(data) or f'phrase{len(calls)}', events)
        live.feed(pcm(0.5))
        live.feed(pcm(0.4, 0))
        live.feed(pcm(0.5))
        self.assertFalse(any(item['event'] == 'speech_end' for item in events))
        live.start()
        live.finish()
        self.assertEqual(events[-1], {'event': 'turn', 'turn_id': 1, 'text': 'phrase1 phrase2'})

    def test_backlog_limit_counts_the_request_already_in_flight(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def decode(data):
            entered.set()
            release.wait(2)
            return 'late'

        live = self.listener(decode, [], max_pending_seconds=9)
        live.start()
        live.feed(pcm(8))
        self.assertTrue(entered.wait(2))
        with self.assertRaisesRegex(RuntimeError, 'fell behind'):
            live.feed(pcm(8))
        live.cancel()
        release.set()
        live.thread.join(2)

    def test_cancel_discards_late_result_and_inference_error_is_reported(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        events = []

        def decode(data):
            entered.set()
            release.wait(2)
            return 'must not arrive'

        live = self.listener(decode, events)
        live.start()
        live.feed(pcm(0.5))
        live.feed(pcm(0.8, 0))
        self.assertTrue(entered.wait(2))
        live.cancel()
        release.set()
        live.thread.join(2)
        self.assertFalse(any(item['event'] == 'turn' for item in events))

        def fail(data):
            raise RuntimeError('ASR disconnected')

        live = self.listener(fail, [])
        live.feed(pcm(0.3))
        live.start()
        with self.assertRaisesRegex(RuntimeError, 'ASR disconnected'):
            live.finish()


class ListenHelperTests(unittest.TestCase):
    def tearDown(self):
        helper._stop_requested = False

    def test_listen_uses_platform_capture_and_flushes_on_manual_stop_without_files(self):
        audio = pcm(0.4) + pcm(0.8, 0) + pcm(0.5)

        class Capture:
            text_mode = False

            def __init__(self):
                self.stdin = io.BytesIO()
                self.stderr = io.BytesIO()
                self.stdout = self
                self.position = 0
                self.returncode = None
                self.closed = False

            def read(self, size):
                if self.position == len(audio):
                    helper.request_stop()
                    return b''
                block = audio[self.position:self.position + size]
                self.position += len(block)
                return block

            def close(self):
                self.closed = True

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.returncode = 0

            def kill(self):
                self.returncode = -9

        for platform, kind in (('linux', 'pulse'), ('linux', 'alsa'), ('wsl2', 'dshow')):
            with self.subTest(platform=platform, capture=kind):
                events = []
                capture = Capture()
                source = helper.audio_backend.Source('hw:1,0', 'Known mic', kind, False)
                with patch.object(helper.subprocess, 'Popen', return_value=capture) as spawn, \
                     patch.object(helper.audio_backend, 'PLATFORM', platform), \
                     patch.object(helper.audio_backend, 'resolve_device', return_value=source), \
                     patch.dict(os.environ, {'TUI_VOICE_CAPTURE': kind}), \
                     patch.object(helper, 'ffmpeg', return_value='local-ffmpeg'), \
                     patch.object(helper, 'windows_env', return_value={}), \
                     patch.object(helper, 'asr_running', return_value=True), \
                     patch.object(helper, 'warm_transcribe', return_value={'text': 'Words'}) as decode, \
                     patch.object(helper, 'cold_voice') as cold, \
                     patch.object(helper, 'log_event'):
                    result = helper.listen(mic='Known mic', gain=4, emit=events.append)
                self.assertEqual(result, {'ok': True, 'device': 'Known mic',
                                          'raw_audio_retained': False, 'turns': 2})
                self.assertEqual(events[0]['event'], 'starting')
                self.assertEqual([item['turn_id'] for item in events if item['event'] == 'turn'], [1, 2])
                command = spawn.call_args.args[0]
                self.assertIn(kind, command)
                self.assertIn('volume=4dB', command)
                self.assertNotIn('-t', command)
                self.assertEqual(command[-3:], ['-f', 's16le', 'pipe:1'])
                self.assertTrue(all(call.kwargs['required'] for call in decode.call_args_list))
                self.assertTrue(capture.closed)
                self.assertTrue(capture.stdin.closed)
                self.assertTrue(capture.stderr.closed)
                self.assertIsNone(helper._current)
                cold.assert_not_called()

    def test_invalid_silence_is_rejected_before_opening_microphone(self):
        with patch.object(helper.subprocess, 'Popen') as spawn:
            for silence in (float('nan'), float('inf'), 0, 10):
                with self.subTest(silence=silence), self.assertRaisesRegex(ValueError, 'silence'):
                    helper.listen(mic='Known mic', silence=silence)
        spawn.assert_not_called()

    def test_live_off_before_first_pcm_is_success_but_device_eof_is_failure(self):
        class Capture:
            text_mode = False

            def __init__(self):
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO()
                self.stderr = io.BytesIO()
                self.returncode = None
                self.stop_sent = b''

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.stop_sent = self.stdin.getvalue()
                self.returncode = 255 if self.stop_sent else 0

            def kill(self):
                self.returncode = -9

        for manual_stop in (True, False):
            with self.subTest(manual_stop=manual_stop):
                capture = Capture()
                events = []

                def spawn(*args, **kwargs):
                    if manual_stop:
                        helper.request_stop()  # The process is not in _current yet.
                    return capture

                with patch.object(helper.subprocess, 'Popen', side_effect=spawn), \
                     patch.object(helper.audio_backend, 'capture_args', return_value=[]), \
                     patch.object(helper, 'audio_env', return_value=None), \
                     patch.object(helper, 'ffmpeg', return_value='local-ffmpeg'), \
                     patch.object(helper, 'asr_running', return_value=True), \
                     patch.object(helper, 'warm_transcribe') as decode, \
                     patch.object(helper, 'log_event'):
                    if manual_stop:
                        result = helper.listen(mic='Known mic', emit=events.append)
                        self.assertTrue(result['ok'])
                        self.assertEqual(result['turns'], 0)
                        self.assertEqual(capture.stop_sent, b'q')
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'Microphone capture failed'):
                            helper.listen(mic='Known mic', emit=events.append)
                self.assertFalse(any(item['event'] == 'turn' for item in events))
                self.assertTrue(capture.stdin.closed)
                self.assertTrue(capture.stdout.closed)
                self.assertTrue(capture.stderr.closed)
                self.assertIsNone(helper._current)
                decode.assert_not_called()

    def test_blocked_socket_read_can_be_cancelled_without_waiting_for_asr(self):
        cancelled = threading.Event()

        class WaitingSocket:
            def recv(self, size):
                cancelled.set()
                raise TimeoutError('receive poll elapsed')

        with self.assertRaisesRegex(InterruptedError, 'cancelled'):
            helper._recv_exact(WaitingSocket(), 4, cancelled, float('inf'))

    def test_trickling_or_late_final_bytes_cannot_extend_total_asr_deadline(self):
        for chunk, elapsed in ((1, 0.4), (4, 1.1)):
            with self.subTest(bytes_per_recv=chunk):
                now = [0.0]

                class SlowSocket:
                    def recv(self, size):
                        now[0] += elapsed
                        return b'x' * min(size, chunk)

                with patch.object(helper.time, 'monotonic', side_effect=lambda: now[0]), \
                     self.assertRaisesRegex(TimeoutError, 'exceeded its timeout'):
                    helper._recv_exact(SlowSocket(), 4, threading.Event(), deadline=1.0)

    def test_cli_listen_forwards_device_and_endpoint_settings(self):
        with patch.object(sys, 'argv', ['voice_helper.py', 'listen', '--mic', 'Known mic',
                                       '--threshold', '-40', '--gain', '3', '--silence', '1.2']), \
             patch.object(helper, 'listen', return_value={'ok': True, 'turns': 0}) as listen, \
             redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as status:
                helper.main()
        self.assertEqual(status.exception.code, 0)
        self.assertEqual(json.loads(output.getvalue()), {'ok': True, 'turns': 0})
        listen.assert_called_once_with('Known mic', -40, 3, 1.2)


if __name__ == '__main__':
    unittest.main()
