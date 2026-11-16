"""Streaming behavior, bounded backpressure and capture protocol regressions."""
import io
from pathlib import Path
import struct
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_dictation import FRAME_BYTES, LiveTranscriber, audible
from test_helper_bugs import helper


def feed(live, seconds, amplitude=1000):
    for _ in range(round(seconds * 10)):
        live.feed(struct.pack('<1600h', *([amplitude] * 1600)))


class LiveTranscriptionTests(unittest.TestCase):
    def test_preview_arrives_before_stop_then_final_replaces_it(self):
        first = threading.Event()
        events = []

        def emit(event):
            events.append(event['text'])
            first.set()

        live = LiveTranscriber(lambda pcm: 'Turn on' if len(pcm) == FRAME_BYTES else 'Turn on lights.',
                               emit, interval=0.01)
        live.start()
        self.addCleanup(live.cancel)
        feed(live, 1)
        self.assertTrue(first.wait(2), 'no transcript while capture is still open')
        self.assertFalse(live.done)
        feed(live, 0.4, 0)
        self.assertEqual(live.finish(), 'Turn on lights.')
        self.assertEqual(events[0], 'Turn on')
        self.assertEqual(events[-1], 'Turn on lights.')

    def test_slow_asr_coalesces_previews_instead_of_building_a_backlog(self):
        entered, release = threading.Event(), threading.Event()
        lengths = []

        def decode(pcm):
            lengths.append(len(pcm))
            if len(lengths) == 1:
                entered.set()
                self.assertTrue(release.wait(2))
            return 'latest'

        live = LiveTranscriber(decode, lambda event: None, interval=0.01)
        live.start()
        self.addCleanup(live.cancel)
        self.addCleanup(release.set)
        feed(live, 1)
        self.assertTrue(entered.wait(2))
        feed(live, 3)
        result = []
        finisher = threading.Thread(target=lambda: result.append(live.finish()))
        finisher.start()
        with live.condition:
            self.assertTrue(live.condition.wait_for(lambda: live.done, timeout=2))
        release.set()
        finisher.join(2)
        self.assertEqual(result, ['latest'])
        self.assertEqual(lengths, [FRAME_BYTES, 4 * FRAME_BYTES])

    def test_long_turn_uses_bounded_requests_without_losing_audio(self):
        lengths = []
        live = LiveTranscriber(lambda pcm: lengths.append(len(pcm)) or f'phrase{len(lengths)}',
                               lambda event: None)
        feed(live, 40)
        live.start()
        self.addCleanup(live.cancel)
        self.assertEqual(live.finish(), 'phrase1 phrase2 phrase3 phrase4 phrase5')
        self.assertEqual(sum(lengths), 40 * FRAME_BYTES)
        self.assertLessEqual(max(lengths), 8 * FRAME_BYTES)

    def test_initial_silence_is_not_sent_to_whisper(self):
        calls = []
        live = LiveTranscriber(lambda pcm: calls.append(pcm) or 'hallucinated', lambda event: None)
        feed(live, 20, 0)
        live.start()
        self.assertEqual(live.finish(), '')
        self.assertEqual(calls, [])
        # A low-RMS syllable can still clear the same peak gate as ffmpeg.
        self.assertTrue(audible(b'\0\0' * 1599 + struct.pack('<h', 1000), 583))

    def test_cancel_discards_late_asr_and_failure_is_not_silent(self):
        entered, release = threading.Event(), threading.Event()
        events = []

        def decode(pcm):
            entered.set()
            release.wait(2)
            return 'must not arrive'

        live = LiveTranscriber(decode, events.append, interval=0.01)
        live.start()
        feed(live, 1)
        self.assertTrue(entered.wait(2))
        live.cancel()
        release.set()
        live.thread.join(2)
        self.assertEqual(events, [])

        def fail(pcm):
            raise RuntimeError('local worker failed')

        live = LiveTranscriber(fail, events.append)
        feed(live, 1)
        live.start()
        with self.assertRaisesRegex(RuntimeError, 'local worker failed'):
            live.finish()

    def test_capture_streams_pcm_and_graceful_stop_without_audio_files(self):
        class Capture:
            def __init__(self):
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(struct.pack('<19200h', *([1000] * 19200)))
                self.stderr = io.BytesIO()
                self.returncode = None

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.returncode = 0

            def kill(self):
                self.returncode = -9

        process = Capture()
        events = []
        received_stop = []

        def emit(event):
            events.append(event)
            if event['event'] == 'listening':
                helper._stop_requested = True
            if event['event'] == 'transcribing':
                received_stop.append(process.stdin.getvalue())

        with patch.object(helper.subprocess, 'Popen', return_value=process) as spawn, \
             patch.object(helper, 'ffmpeg', return_value='local-ffmpeg'), \
             patch.object(helper, 'choose_mic') as discovery, \
             patch.object(helper, 'windows_env', return_value={}), \
             patch.object(helper, 'warm_transcribe', return_value={'text': 'Live words'}), \
             patch.object(helper, 'log_event'):
            result = helper.dictate(mic='Known microphone', emit=emit)
        self.assertTrue(result['ok'])
        self.assertEqual(result['text'], 'Live words')
        self.assertFalse(result['raw_audio_retained'])
        self.assertEqual(events[0]['event'], 'starting')
        self.assertTrue(any(event['event'] == 'partial' for event in events))
        self.assertEqual(received_stop, [b'q'])
        discovery.assert_not_called()
        self.assertEqual(spawn.call_args.args[0][-3:], ['-f', 's16le', 'pipe:1'])
        self.assertIsNone(helper._current)

    def test_stale_windows_bridge_is_repaired_only_in_child_environment(self):
        import os
        import stat
        from types import SimpleNamespace

        def exists(path):
            return str(path) != '/proc/999999999'

        with patch.dict(os.environ, {'WSL_INTEROP': '/run/WSL/999999999_interop'}), \
             patch.object(helper, '_windows_environment', None), \
             patch.object(Path, 'stat', return_value=SimpleNamespace(st_mode=stat.S_IFSOCK)), \
             patch.object(Path, 'exists', exists), \
             patch.object(helper.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as probe, \
             patch.object(helper, 'log_event'):
            env = helper.windows_env()
            self.assertEqual(env['WSL_INTEROP'], '/run/WSL/1_interop')
            self.assertEqual(os.environ['WSL_INTEROP'], '/run/WSL/999999999_interop')
            self.assertIs(helper.windows_env(), env)
            self.assertEqual(probe.call_count, 1)

    def test_manual_toggle_survives_long_pause_and_old_turn_limit(self):
        speech = struct.pack('<16000h', *([1000] * 16000))
        audio = speech + b'\0\0' * (65 * 16000) + speech

        class Capture:
            text_mode = False

            def __init__(self):
                self.stdin = io.BytesIO()
                self.stderr = io.BytesIO()
                self.returncode = None
                self.position = 0
                self.stdout = self

            def read(self, size):
                # No q may be sent for a pause, or at the former 60-second cap.
                if self.stdin.getvalue():
                    raise AssertionError('Capture switched off before the user did')
                if self.position == len(audio):
                    helper.request_stop()
                    return b''
                block = audio[self.position:self.position + size]
                self.position += len(block)
                return block

            def close(self):
                pass

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.returncode = 0

            def kill(self):
                self.returncode = -9

        process = Capture()
        stop_commands = []

        def emit(event):
            if event['event'] == 'transcribing':
                stop_commands.append(process.stdin.getvalue())

        with patch.object(helper.subprocess, 'Popen', return_value=process) as spawn, \
             patch.object(helper, 'ffmpeg', return_value='local-ffmpeg'), \
             patch.object(helper, 'windows_env', return_value={}), \
             patch.object(helper, 'warm_transcribe', return_value={'text': 'Words'}), \
             patch.object(helper, 'log_event'):
            result = helper.dictate(mic='Known microphone', emit=emit)
        self.assertTrue(result['ok'])
        self.assertEqual(process.position, len(audio))
        self.assertEqual(stop_commands, [b'q'])
        self.assertNotIn('-t', spawn.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
