"""Regression checks for helper failures observed in the local voice log."""
import base64
from contextlib import redirect_stdout, redirect_stderr
import io
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Importing the CLI helper must not replace the test runner's signal handlers.
_signals = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1)}
try:
    import voice_helper as helper
finally:
    for _sig, _handler in _signals.items():
        signal.signal(_sig, _handler)


def wav_result(frames=240):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b'\0\0' * frames)
    return {'wav': base64.b64encode(buffer.getvalue()).decode(),
            'engine': 'test', 'providers': []}


class HelperBugTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.logs = patch.multiple(helper, LOG_DIR=self.root / 'logs',
                                   LOG_FILE=self.root / 'logs/voice.log')
        self.logs.start()
        self.addCleanup(self.logs.stop)

    def test_thread_overrides_cannot_exceed_worker_limits(self):
        cases = [
            ('TUI_VOICE_ASR_THREADS', 16, 8),
            ('TUI_VOICE_TTS_THREADS', 99, 24),
            ('TUI_VOICE_ASR_THREADS', 0, 1),
            ('TUI_VOICE_TTS_THREADS', 'not-a-number', 8),
        ]
        for name, value, expected in cases:
            with self.subTest(name=name, value=value):
                os.environ[name] = str(value)
                maximum = 8 if name.endswith('ASR_THREADS') else 24
                default = 8
                self.assertEqual(
                    helper._bounded_env_int(name, default, maximum), expected)

    def test_start_worker_passes_clamped_threads(self):
        completed = type('Completed', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        for kind, attribute, requested, expected in (
                ('asr', 'ASR_THREADS', 16, '8'),
                ('tts', 'TTS_THREADS', 99, '24')):
            with self.subTest(kind=kind), \
                 patch.object(helper, attribute, requested), \
                 patch.object(helper, 'worker_request', side_effect=[None, None]), \
                 patch.object(helper.subprocess, 'run', return_value=completed) as run:
                helper.start_worker(kind)
            command = run.call_args.args[0]
            self.assertEqual(command[command.index('--threads') + 1], expected)

    def test_stopping_an_absent_worker_is_silent(self):
        os.environ['TUI_VOICE_TTS_SOCKET'] = str(self.root / 'absent.sock')
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = helper.stop_worker('tts')
        self.assertTrue(result['ok'])
        self.assertEqual(result['state'], 'not running')
        self.assertEqual(stderr.getvalue(), '')

    def test_warm_stop_exits_nonzero_when_worker_does_not_stop(self):
        for ok in (True, False):
            with self.subTest(ok=ok), \
                 patch.object(sys, 'argv', ['voice_helper.py', 'warm', '--stop', '--kind', 'tts']), \
                 patch.object(helper, 'stop_worker', return_value={'ok': ok}), \
                 redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as failure:
                    helper.main()
                self.assertEqual(failure.exception.code, 0 if ok else 1)

    def test_warm_restart_refuses_to_report_success_when_stop_fails(self):
        with patch.object(sys, 'argv', ['voice_helper.py', 'warm', '--restart', '--kind', 'tts']), \
             patch.object(helper, 'stop_worker', return_value={'ok': False}), \
             patch.object(helper, 'start_worker') as start, \
             redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as failure:
                helper.main()
        self.assertEqual(failure.exception.code, 1)
        start.assert_not_called()

    def test_playback_failure_leaves_no_audio_file(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(helper, 'windows_temp', return_value=self.root), \
             patch.object(helper, 'warm_speak', return_value=wav_result()), \
             patch.object(helper, 'play', side_effect=RuntimeError('no audio device')), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as failure:
                helper.say('Hello there.', should_play=True)
        self.assertEqual(failure.exception.code, 1)
        self.assertEqual(list(self.root.glob('*.wav')), [])
        self.assertIn('no audio device', stdout.getvalue())

    def test_successful_say_also_removes_temp_audio(self):
        with patch.object(helper, 'windows_temp', return_value=self.root), \
             patch.object(helper, 'warm_speak', return_value=wav_result()), \
             patch.object(helper, 'play'), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = helper.say('Hello there.', should_play=True)
        self.assertTrue(result['ok'])
        self.assertEqual(result['chunks'], 1)
        self.assertEqual(list(self.root.glob('*.wav')), [])

    def test_sigterm_removes_partial_capture(self):
        path = self.root / 'capture.wav'
        path.write_bytes(b'RIFF' + b'\0' * 40)
        with patch.object(helper, '_current', None), \
             patch.object(helper, '_capture_path', str(path)):
            with self.assertRaises(SystemExit) as failure:
                helper.stop_child()
        self.assertEqual(failure.exception.code, 1)
        self.assertFalse(path.exists())

    def test_long_reply_is_not_silently_truncated(self):
        text = ('A complete sentence for reading aloud. ' * 500).strip()
        parts = helper.chunks(text)
        self.assertGreater(len(parts), 40)
        self.assertEqual(' '.join(parts), text)

    def test_pathological_reply_fails_instead_of_truncating(self):
        with self.assertRaisesRegex(ValueError, 'too long to read aloud'):
            helper.chunks('x' * (helper.MAX_SPEECH_CHUNKS * 421))

    def test_say_rejects_markup_only_reply(self):
        stdout = io.StringIO()
        with patch.object(helper, 'windows_temp', return_value=self.root), \
             patch.object(helper, 'warm_speak') as warm, \
             redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as failure:
                helper.say('```python\nprint("hidden")\n```', should_play=False)
        self.assertEqual(failure.exception.code, 1)
        self.assertIn('after removing markup', stdout.getvalue())
        warm.assert_not_called()

    def test_say_rejects_exhausted_resume_offset(self):
        stdout = io.StringIO()
        with patch.object(helper, 'windows_temp', return_value=self.root), \
             patch.object(helper, 'warm_speak') as warm, \
             redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as failure:
                helper.say('One sentence.', should_play=False, start_chunk=1)
        self.assertEqual(failure.exception.code, 1)
        self.assertIn('No speech remains', stdout.getvalue())
        warm.assert_not_called()


if __name__ == '__main__':
    unittest.main()
