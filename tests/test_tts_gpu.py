"""Small hardware-free regression checks; these do not qualify AMD inference."""
import base64
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
import math
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
_ENGINE_TYPE = worker.KokoroEngine

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
            worker.CPU_PROVIDER: {'node_events': 2, 'compute_node_events': 0,
                                  'duration_us': 12, 'compute_ops': []}})

    def profile_engine(self, path, events):
        """An engine with an ORT-shaped trace, without optional model dependencies."""
        path.write_text(json.dumps(events))
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b'\0\0' * 24000)
        engine = _ENGINE_TYPE.__new__(_ENGINE_TYPE)
        engine.provider = worker.GPU_PROVIDER
        engine.model = worker.selected_model()
        engine.model_sha256 = 'a' * 64
        engine.session_id = 'test-session'
        engine.ort_version = 'test'
        engine.profiling_active = True
        engine.execution_profile = {}
        engine.gpu_execution_verified = False
        engine.session = SimpleNamespace(
            get_providers=lambda: [worker.GPU_PROVIDER, worker.CPU_PROVIDER],
            end_profiling=Mock(return_value=str(path)))
        engine.speak = Mock(return_value={'wav': base64.b64encode(audio.getvalue()).decode()})
        return engine

    @staticmethod
    def kernel_event(provider, operation, name='model_kernel_time'):
        return {'cat': 'Node', 'name': name, 'ph': 'X', 'dur': 5,
                'args': {'provider': provider, 'op_name': operation}}

    def test_check_cannot_claim_gpu_without_observed_gpu_execution(self):
        trace = self.root / 'trace.json'
        engine = self.profile_engine(trace, [self.kernel_event(worker.CPU_PROVIDER, 'Conv')])
        with patch.object(worker, 'KokoroEngine', return_value=engine):
            result = worker.check_engine(8)
        self.assertFalse(result['ok'])
        self.assertFalse(result['gpu_execution_verified'])
        self.assertTrue(result['timings_profiled'])
        self.assertFalse(result['profiling_active'])

    def test_gpu_startup_requires_computation_even_without_optional_warmup(self):
        os.environ['TUI_VOICE_TTS_PROVIDER'] = 'migraphx'
        cases = {
            'all_cpu': [self.kernel_event(worker.CPU_PROVIDER, 'Conv')],
            'copy_only': [self.kernel_event(worker.GPU_PROVIDER, 'MemcpyFromHost'),
                          self.kernel_event(worker.CPU_PROVIDER, 'Conv')],
            'fence_only': [self.kernel_event(worker.GPU_PROVIDER, 'MGXKernel_graph_0',
                                             name='model_fence_before')],
        }
        for name, events in cases.items():
            with self.subTest(name=name):
                engine = self.profile_engine(self.root / 'trace.json', events)
                with patch.object(worker, 'KokoroEngine', return_value=engine), \
                     patch.object(worker, 'serve') as serve, \
                     patch.object(worker.signal, 'signal'), \
                     patch.object(sys, 'argv', ['tts_worker.py', '--run', '--no-warmup',
                                               '--socket', str(self.root / 'run/tts.sock')]):
                    with self.assertRaisesRegex(RuntimeError, 'no GPU model computation'):
                        worker.main()
                engine.speak.assert_called_once_with(worker.VERIFICATION_TEXT, 'heart')
                engine.session.end_profiling.assert_called_once()
                serve.assert_not_called()
                self.assertFalse(engine.gpu_execution_verified)
                self.assertFalse(engine.profiling_active)

    def test_verified_startup_keeps_the_session_and_removes_profile_overhead(self):
        os.environ['TUI_VOICE_TTS_PROVIDER'] = 'migraphx'
        captured = {}

        def construct(_threads, profile_prefix):
            trace = profile_prefix.with_suffix('.json')
            engine = self.profile_engine(trace, [
                self.kernel_event(worker.GPU_PROVIDER, 'MemcpyToHost'),
                self.kernel_event(worker.GPU_PROVIDER, 'MGXKernel_graph_0'),
                self.kernel_event(worker.CPU_PROVIDER, 'Shape'),
            ])
            captured.update(engine=engine, trace=trace, session=engine.session)
            return engine

        with patch.object(worker, 'KokoroEngine', side_effect=construct):
            engine = worker.prepare_engine(8, warmup=False)
        self.assertIs(engine, captured['engine'])
        self.assertIs(engine.session, captured['session'])
        self.assertFalse(captured['trace'].parent.exists())
        metadata = engine.describe()
        self.assertTrue(metadata['gpu_execution_verified'])
        self.assertFalse(metadata['profiling_active'])
        self.assertEqual(metadata['model_sha256'], 'a' * 64)
        self.assertEqual(metadata['session_id'], 'test-session')
        gpu = metadata['execution_profile'][worker.GPU_PROVIDER]
        self.assertEqual(gpu['node_events'], 2)
        self.assertEqual(gpu['compute_node_events'], 1)
        self.assertEqual(gpu['compute_ops'], ['MGXKernel_graph_0'])
        worker.validate_backend(metadata)
        engine.speak('A production request.', 'heart')
        engine.session.end_profiling.assert_called_once()

    def test_helper_rejects_gpu_provider_without_live_execution_proof(self):
        os.environ['TUI_VOICE_TTS_PROVIDER'] = 'migraphx'
        metadata = {'wav': 'YQ==', 'requested_provider': worker.GPU_PROVIDER,
                    'providers': [worker.GPU_PROVIDER, worker.CPU_PROVIDER],
                    'model': str(worker.selected_model())}
        for proof in (None, False, 1, True):
            result = dict(metadata)
            if proof is not None:
                result['gpu_execution_verified'] = proof
            with self.subTest(proof=proof), \
                 patch.object(helper, 'worker_request', return_value=result):
                if proof is True:
                    self.assertIs(helper.warm_speak('Hello.', 'heart'), result)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'not verified GPU'):
                        helper.warm_speak('Hello.', 'heart')

    def test_bad_model_audio_is_rejected_before_pcm_conversion(self):
        engine = worker.KokoroEngine.__new__(worker.KokoroEngine)
        engine.voices = {'heart': {'model': 'af_heart', 'speed': 1, 'lang': 'en-us'}}
        for values in ([], [float('nan')], [float('inf')], [-float('inf')]):
            with self.subTest(values=values):
                array = SimpleNamespace(ndim=1, size=len(values))
                engine.np = SimpleNamespace(
                    asarray=lambda _samples: array,
                    isfinite=lambda _samples: SimpleNamespace(
                        all=lambda: all(math.isfinite(x) for x in values)),
                    clip=Mock())
                engine.kokoro = SimpleNamespace(create=lambda *_args, **_kwargs: (values, 24000))
                with self.assertRaisesRegex(ValueError, 'empty, non-finite'):
                    engine.speak('Hello.', 'heart')
                engine.np.clip.assert_not_called()

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

    def test_warm_start_exits_nonzero_when_worker_start_fails(self):
        for ok in (True, False):
            with self.subTest(ok=ok), \
                 patch.object(sys, 'argv', ['voice_helper.py', 'warm', '--start', '--kind', 'tts']), \
                 patch.object(helper, 'start_worker', return_value={'ok': ok}), \
                 redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as failure:
                    helper.main()
                self.assertEqual(failure.exception.code, 0 if ok else 1)

    def test_successful_spawn_does_not_accept_unverified_gpu_status(self):
        os.environ['TUI_VOICE_TTS_PROVIDER'] = 'migraphx'
        os.environ['TUI_VOICE_TTS_SOCKET'] = str(self.root / 'runtime/tts.sock')
        status = {'requested_provider': worker.GPU_PROVIDER,
                  'providers': [worker.GPU_PROVIDER, worker.CPU_PROVIDER],
                  'model': str(worker.selected_model()), 'gpu_execution_verified': False}
        completed = SimpleNamespace(returncode=0, stdout='', stderr='')
        with patch.object(helper, 'worker_request', side_effect=[None, status]), \
             patch.object(helper, 'TTS_PYTHON', Path(sys.executable)), \
             patch.object(helper, 'TTS_WORKER', Path(worker.__file__)), \
             patch.object(helper.subprocess, 'run', return_value=completed):
            result = helper.start_worker('tts')
        self.assertFalse(result['ok'])
        self.assertIn('not verified GPU', result['error'])

    def test_say_retains_verified_live_backend_evidence(self):
        os.environ['TUI_VOICE_TTS_PROVIDER'] = 'migraphx'
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b'\0\0' * 240)
        backend = {'requested_provider': worker.GPU_PROVIDER,
                   'providers': [worker.GPU_PROVIDER, worker.CPU_PROVIDER],
                   'model': str(worker.selected_model()), 'model_sha256': 'a' * 64,
                   'session_id': 'live-session', 'gpu_execution_verified': True}
        response = {**backend, 'wav': base64.b64encode(audio.getvalue()).decode('ascii')}
        with patch.object(helper, 'start_worker', return_value={'ok': True}), \
             patch.object(helper, 'worker_request', return_value=response), \
             patch.object(helper, 'windows_temp', return_value=self.root):
            result = helper.say('Hello.', should_play=False)
        self.assertEqual(result['warm_chunks'], 1)
        self.assertIs(result['gpu_execution_verified'], True)
        self.assertEqual(result['backends'], [backend])
        self.assertFalse(list(self.root.glob('*.wav')))

    def test_gpu_startup_crash_hint_is_actionable(self):
        os.environ['TUI_VOICE_TTS_PROVIDER'] = 'migraphx'
        path = self.root / 'runtime/tts.sock'
        crashed = SimpleNamespace(poll=Mock(return_value=-signal.SIGSEGV))
        stderr = io.StringIO()
        with patch.object(sys, 'argv',
                          ['tts_worker.py', '--start', '--socket', str(path)]), \
             patch.object(worker.subprocess, 'Popen', return_value=crashed), \
             patch.object(worker, '_ping', return_value=False), \
             redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as failure:
                worker.main()
        self.assertEqual(failure.exception.code, 1)
        self.assertIn('Windows AMD driver', stderr.getvalue())
        self.assertIn('docs/AMD_GPU.md', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
