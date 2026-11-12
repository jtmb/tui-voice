"""Small hardware-free regression checks; these do not qualify AMD inference."""
import base64
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tts_worker as worker

# Importing the CLI helper must not replace the test runner's signal handlers.
_signals = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1)}
try:
    import voice_helper as helper
finally:
    for _sig, _handler in _signals.items():
        signal.signal(_sig, _handler)


def fake_ort(available, active):
    options = SimpleNamespace(add_provider_for_devices=Mock())
    session = SimpleNamespace(get_providers=Mock(return_value=active), disable_fallback=Mock())
    return SimpleNamespace(
        SessionOptions=Mock(return_value=options),
        InferenceSession=Mock(return_value=session),
        get_available_providers=Mock(return_value=available),
        register_execution_provider_library=Mock(),
    )


class TtsGpuTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.logs = patch.multiple(helper, LOG_DIR=self.root / 'logs', LOG_FILE=self.root / 'logs/voice.log')
        self.logs.start()
        self.addCleanup(self.logs.stop)
        self.voice_dir = patch.object(worker, 'VOICE_DIR', self.root)
        self.voice_dir.start()
        self.addCleanup(self.voice_dir.stop)

    def test_cpu_is_default_and_keeps_original_model(self):
        ort = fake_ort([worker.CPU_PROVIDER], [worker.CPU_PROVIDER])
        session = worker.create_session(ort, worker.selected_model(), 8)
        self.assertEqual(worker.requested_provider(), worker.CPU_PROVIDER)
        self.assertEqual(worker.selected_model().name, 'kokoro-v1.0.int8.onnx')
        self.assertEqual(ort.InferenceSession.call_args.kwargs['providers'], [worker.CPU_PROVIDER])
        session.disable_fallback.assert_not_called()

    def test_bad_provider_configuration_is_not_treated_as_cpu(self):
        os.environ['TUI_VOICE_TTS_PROVIDER'] = 'migraphxx'
        with self.assertRaisesRegex(ValueError, 'cpu or migraphx'):
            worker.requested_provider()

    def test_competing_worker_exits_before_loading_a_second_model(self):
        path = self.root / 'runtime/tts.sock'
        with worker.worker_lock(path):
            process = subprocess.run(
                [sys.executable, str(Path(worker.__file__)), '--run', '--socket', str(path)],
                capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, worker.WORKER_BUSY_EXIT, process.stderr)
        self.assertIn('already starting or running', process.stderr)
        # The same lock can be reacquired after the owner exits, without unlinking it.
        with worker.worker_lock(path):
            self.assertTrue(path.with_suffix('.lock').is_file())

    def test_missing_amd_package_cannot_create_a_cpu_session(self):
        os.environ['TUI_VOICE_TTS_PROVIDER'] = 'migraphx'
        ort = fake_ort([worker.CPU_PROVIDER], [worker.CPU_PROVIDER])
        with patch.object(worker.importlib, 'import_module', side_effect=ImportError('not installed')):
            with self.assertRaisesRegex(RuntimeError, 'MIGraphX is unavailable'):
                worker.create_session(ort, self.root / 'model.onnx', 8)
        ort.InferenceSession.assert_not_called()

    def test_advertised_gpu_is_rejected_if_session_drops_it(self):
        os.environ['TUI_VOICE_TTS_PROVIDER'] = 'migraphx'
        ort = fake_ort([worker.GPU_PROVIDER, worker.CPU_PROVIDER], [worker.CPU_PROVIDER])
        with self.assertRaisesRegex(RuntimeError, 'was requested but the session loaded'):
            worker.create_session(ort, self.root / 'model.onnx', 8)

    def test_plugin_uses_device_attachment_and_disables_session_fallback(self):
        os.environ['TUI_VOICE_TTS_PROVIDER'] = 'migraphx'
        ort = fake_ort([], [worker.GPU_PROVIDER, worker.CPU_PROVIDER])
        ort.get_available_providers.side_effect = [[worker.CPU_PROVIDER], [worker.GPU_PROVIDER]]
        device = SimpleNamespace(ep_name=worker.GPU_PROVIDER)
        ort.get_ep_devices = Mock(return_value=[device])
        plugin = SimpleNamespace(get_ep_names=lambda: [worker.GPU_PROVIDER],
                                 get_library_paths=lambda: ['/mock/libmigraphx.so'])
        with patch.object(worker.importlib, 'import_module', return_value=plugin):
            session = worker.create_session(ort, self.root / 'model.onnx', 8)
        ort.register_execution_provider_library.assert_called_once_with(
            worker.GPU_PROVIDER, '/mock/libmigraphx.so')
        ort.SessionOptions.return_value.add_provider_for_devices.assert_called_once_with([device], {})
        self.assertEqual(ort.InferenceSession.call_args.kwargs['providers'], [])
        session.disable_fallback.assert_called_once()

    def test_registered_plugin_without_gpu_device_fails(self):
        os.environ['TUI_VOICE_TTS_PROVIDER'] = 'migraphx'
        ort = fake_ort([], [worker.CPU_PROVIDER])
        ort.get_available_providers.side_effect = [[worker.CPU_PROVIDER], [worker.GPU_PROVIDER]]
        ort.get_ep_devices = Mock(return_value=[])
        plugin = SimpleNamespace(get_ep_names=lambda: [worker.GPU_PROVIDER],
                                 get_library_paths=lambda: ['/mock/libmigraphx.so'])
        with patch.object(worker.importlib, 'import_module', return_value=plugin):
            with self.assertRaisesRegex(RuntimeError, 'no usable GPU device'):
                worker.create_session(ort, self.root / 'model.onnx', 8)
        ort.InferenceSession.assert_not_called()

    def model_manifest(self):
        models = self.root / 'models'
        models.mkdir()
        entries = []
        for name in ('kokoro-v1.0.onnx', 'kokoro-v1.0.int8.onnx', 'voices-v1.0.bin'):
            content = name.encode()
            (models / name).write_bytes(content)
            entries.append({'file': name, 'sha256': hashlib.sha256(content).hexdigest()})
        (self.root / 'neural-models.json').write_text(json.dumps(entries))
        return entries

    def test_selected_model_and_voice_must_both_be_pinned(self):
        entries = self.model_manifest()
        model = self.root / 'models/kokoro-v1.0.onnx'
        worker.verify_models(model)
        for missing in ('kokoro-v1.0.onnx', 'voices-v1.0.bin'):
            with self.subTest(missing=missing):
                (self.root / 'neural-models.json').write_text(json.dumps(
                    [entry for entry in entries if entry['file'] != missing]))
                with self.assertRaisesRegex(ValueError, 'SHA256-pinned'):
                    worker.verify_models(model)
        (self.root / 'neural-models.json').write_text(json.dumps(entries))
        model.unlink()
        with self.assertRaises(FileNotFoundError):
            worker.verify_models(model)

    def test_hash_check_still_covers_unselected_manifest_assets(self):
        self.model_manifest()
        (self.root / 'models/kokoro-v1.0.int8.onnx').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            worker.verify_models(self.root / 'models/kokoro-v1.0.onnx')

    def test_profile_ignores_registration_and_non_node_events(self):
        path = self.root / 'trace.json'
        path.write_text(json.dumps([
            {'cat': 'Session', 'dur': 100, 'args': {'provider': worker.GPU_PROVIDER}},
            {'cat': 'Node', 'dur': 5, 'args': {'provider': worker.CPU_PROVIDER}},
            {'cat': 'Node', 'dur': 7, 'args': {'provider': worker.CPU_PROVIDER}},
            {'cat': 'Node', 'dur': 10, 'args': {}},
        ]))
        self.assertEqual(worker.profile_summary(path), {
            worker.CPU_PROVIDER: {'node_events': 2, 'duration_us': 12}})

    def test_check_cannot_claim_gpu_without_observed_gpu_execution(self):
        trace = self.root / 'trace.json'
        trace.write_text(json.dumps([
            {'cat': 'Node', 'dur': 5, 'args': {'provider': worker.CPU_PROVIDER}}]))
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b'\0\0' * 24000)
        engine = SimpleNamespace(
            provider=worker.GPU_PROVIDER,
            session=SimpleNamespace(end_profiling=lambda: str(trace)),
            describe=lambda: {'providers': [worker.GPU_PROVIDER, worker.CPU_PROVIDER]},
            speak=lambda *_: {'wav': base64.b64encode(audio.getvalue()).decode()},
        )
        with patch.object(worker, 'KokoroEngine', return_value=engine):
            result = worker.check_engine(8)
        self.assertFalse(result['ok'])
        self.assertFalse(result['gpu_execution_verified'])

    def test_gpu_rejects_legacy_cpu_and_missing_audio_responses(self):
        os.environ['TUI_VOICE_TTS_PROVIDER'] = 'migraphx'
        for result in (None, {}, {'wav': 'YQ==', 'engine': 'Kokoro int8 / CPU (local)'}):
            with self.subTest(result=result):
                with patch.object(helper, 'worker_request', return_value=result) as request:
                    with self.assertRaises(RuntimeError):
                        helper.warm_speak('Hello.', 'heart')
                    self.assertTrue(request.call_args.kwargs['required'])

    def test_missing_worker_only_uses_cold_path_in_cpu_mode(self):
        os.environ['TUI_VOICE_TTS_SOCKET'] = str(self.root / 'absent.sock')
        for mode in ('migraphx', 'cpu'):
            with self.subTest(mode=mode), patch.dict(os.environ, {'TUI_VOICE_TTS_PROVIDER': mode}):
                cold = Mock()
                cold.speak.return_value = {'wav': 'YQ==', 'engine': 'cold CPU'}
                with patch.object(helper, 'start_worker', return_value={'ok': True}), \
                     patch.object(helper, 'windows_temp', return_value=self.root), \
                     patch.object(helper, 'voice', return_value=cold) as load_cold, \
                     redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    if mode == 'migraphx':
                        with self.assertRaises(SystemExit) as failure:
                            helper.say('Hello.', should_play=False)
                        self.assertEqual(failure.exception.code, 1)
                        load_cold.assert_not_called()
                    else:
                        result = helper.say('Hello.', should_play=False)
                        self.assertTrue(result['ok'])
                        self.assertEqual(result['warm_chunks'], 0)
                        cold.speak.assert_called_once_with('Hello.', 'heart')


if __name__ == '__main__':
    unittest.main()
