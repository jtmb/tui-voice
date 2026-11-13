#!/usr/bin/env python3
"""Warm local Kokoro speech worker for the tui-voice plugin.

Loads the pinned ONNX voice once and serves bounded text chunks over a
uid-owned AF_UNIX socket. Every request stays on this machine; nothing is
uploaded and no audio is written to disk by the worker itself.

  tts_worker.py --start          # detached, idle-exits after 30 min
  tts_worker.py --status         # JSON status
  tts_worker.py --stop
  tts_worker.py --run            # foreground
  tts_worker.py --check          # synthesize locally and report provider execution
"""
import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave

VOICE_DIR = Path(os.environ.get(
    'TUI_VOICE_VOICE_DIR',
    str(Path(os.environ.get('TUI_VOICE_SERVICE',
                           str(Path.home() / 'repos/fire-tv-next/lulu-launcher/service'))).parent / 'voice')))
MAX_FRAME = 16 * 1024 * 1024
MAX_RESPONSE = 16 * 1024 * 1024
MAX_TEXT = 1200
DEFAULT_THREADS = 8
START_TIMEOUT = 30.0
CPU_PROVIDER = 'CPUExecutionProvider'
GPU_PROVIDER = 'MIGraphXExecutionProvider'
WORKER_BUSY_EXIT = 75
VERIFICATION_TEXT = 'This is a local Kokoro speech execution check.'


class WorkerBusy(RuntimeError):
    pass


def requested_provider():
    value = os.environ.get('TUI_VOICE_TTS_PROVIDER', 'cpu').strip().lower()
    providers = {'cpu': CPU_PROVIDER, 'migraphx': GPU_PROVIDER}
    if value not in providers:
        raise ValueError('TUI_VOICE_TTS_PROVIDER must be cpu or migraphx')
    return providers[value]


def selected_model():
    default = 'kokoro-v1.0.int8.onnx' if requested_provider() == CPU_PROVIDER else 'kokoro-v1.0.onnx'
    path = Path(os.environ.get('TUI_VOICE_TTS_MODEL', default)).expanduser()
    return (path if path.is_absolute() else VOICE_DIR / 'models' / path).resolve()


def startup_timeout():
    # MIGraphX can compile the graph during the first synthesis.
    return 300.0 if requested_provider() == GPU_PROVIDER else START_TIMEOUT


def validate_backend(result):
    """Reject stale workers after a configuration change, including old CPU workers."""
    expected = requested_provider()
    actual = result.get('requested_provider')
    if actual is None and expected == CPU_PROVIDER and not os.environ.get('TUI_VOICE_TTS_MODEL'):
        return  # Backward compatibility with the original CPU worker/cold service.
    providers = result.get('providers')
    if (actual != expected or not isinstance(providers, list) or expected not in providers
            or result.get('model') != str(selected_model())):
        raise RuntimeError(
            'TTS worker does not match the requested provider/model; '
            'run voice_helper.py warm --restart --kind tts with the same environment')
    if expected == GPU_PROVIDER and result.get('gpu_execution_verified') is not True:
        raise RuntimeError(
            'TTS worker has not verified GPU model execution; '
            'run voice_helper.py warm --restart --kind tts with the same environment')


def create_session(ort, model, threads, profile_prefix=None):
    provider = requested_provider()
    options = ort.SessionOptions()
    options.intra_op_num_threads = max(1, int(threads))
    options.inter_op_num_threads = 1
    options.enable_cpu_mem_arena = False
    if profile_prefix is not None:
        options.enable_profiling = True
        options.profile_file_prefix = str(profile_prefix)
    providers = [CPU_PROVIDER]
    if provider == GPU_PROVIDER:
        plugin_registered = False
        if provider not in ort.get_available_providers():
            try:
                plugin = importlib.import_module('onnxruntime_ep_migraphx')
            except ImportError as error:
                raise RuntimeError(
                    'MIGraphX is unavailable in this Python environment. Install the AMD '
                    'ONNX Runtime package matching your ROCm stack; see docs/AMD_GPU.md. '
                    f'Available providers: {ort.get_available_providers()}') from error
            names, paths = plugin.get_ep_names(), plugin.get_library_paths()
            if not names or len(names) != len(paths):
                raise RuntimeError('Invalid MIGraphX plugin library metadata')
            for name, path in zip(names, paths):
                ort.register_execution_provider_library(name, path)
            plugin_registered = True
        if provider not in ort.get_available_providers():
            raise RuntimeError('MIGraphX plugin loaded but its provider is unavailable')

        # New plugin EPs must be attached by device: ORT 1.29 can silently drop
        # a registered MIGraphX plugin when selected only by its provider name.
        devices = ([device for device in ort.get_ep_devices() if device.ep_name == provider]
                   if hasattr(ort, 'get_ep_devices') else [])
        if devices and hasattr(options, 'add_provider_for_devices'):
            options.add_provider_for_devices([devices[0]], {})
            providers = []
        elif plugin_registered:
            raise RuntimeError('MIGraphX plugin found no usable GPU device; check ROCm and /dev/dxg')
        else:
            # Older AMD wheels bundle the EP and use the original provider API.
            providers = [provider, CPU_PROVIDER]

    session = ort.InferenceSession(str(model), sess_options=options, providers=providers)
    if provider not in session.get_providers():
        raise RuntimeError(f'{provider} was requested but the session loaded {session.get_providers()}')
    if provider != CPU_PROVIDER:
        # Prevent whole-session CPU retries after GPU errors. Unsupported graph
        # nodes may still use CPU; startup verifies actual model computation.
        session.disable_fallback()
    return session


def runtime_base():
    value = os.environ.get('XDG_RUNTIME_DIR')
    return Path(value) if value else Path.home() / '.cache'


def default_socket_path():
    return runtime_base() / 'tui-voice/tts.sock'


def socket_path():
    value = os.environ.get('TUI_VOICE_TTS_SOCKET')
    return Path(value) if value else default_socket_path()


def rss_mb():
    try:
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return None


def _recv_exact(connection, size):
    chunks = []
    while size:
        chunk = connection.recv(min(size, 65536))
        if not chunk:
            raise ConnectionError('Warm TTS peer closed mid-frame')
        chunks.append(chunk)
        size -= len(chunk)
    return b''.join(chunks)


def send_frame(connection, message):
    data = json.dumps(message, separators=(',', ':')).encode()
    if not 0 < len(data) <= MAX_FRAME:
        raise ValueError('TTS frame outside finite budget')
    connection.sendall(len(data).to_bytes(4, 'big') + data)


def recv_frame(connection, limit=MAX_FRAME):
    size = int.from_bytes(_recv_exact(connection, 4), 'big')
    if not 0 < size <= min(limit, MAX_FRAME):
        raise ValueError('TTS frame outside finite budget')
    return json.loads(_recv_exact(connection, size))


def peer_uid(connection):
    try:
        credentials = connection.getsockopt(
            socket.SOL_SOCKET, getattr(socket, 'SO_PEERCRED', 17), struct.calcsize('3i'))
        _pid, uid, _gid = struct.unpack('3i', credentials)
        return uid
    except OSError:
        return None


def admin_request(operation, path, timeout=3.0):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        send_frame(connection, {'operation': operation})
        return recv_frame(connection, MAX_RESPONSE)


def _ping(path, timeout=0.5):
    try:
        return admin_request('status', path, timeout).get('ok') is True
    except (OSError, ValueError, ConnectionError):
        return False


def _prepare_socket_dir(path):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.parent.stat()
    if info.st_uid != os.getuid():
        raise RuntimeError(f'TTS socket directory {path.parent} is not owned by uid {os.getuid()}')
    if info.st_mode & 0o077:
        os.chmod(path.parent, 0o700)


@contextmanager
def worker_lock(path):
    """Own the model lifetime, including startup before the socket is ready."""
    path = Path(path)
    _prepare_socket_dir(path)
    descriptor = os.open(path.with_suffix('.lock'), os.O_WRONLY | os.O_CREAT, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise WorkerBusy(f'Warm TTS is already starting or running at {path}') from error
        # Closing this handle releases the lock even when startup fails or the
        # worker is killed. A cancelled helper cannot release the worker's lock.
        yield


def verify_models(model):
    """Verify the pinned voice model identity once at startup, not per request."""
    model = Path(model).resolve()
    required = {model, (VOICE_DIR / 'models/voices-v1.0.bin').resolve()}
    verified = set()
    model_digest = None
    for item in json.loads((VOICE_DIR / 'neural-models.json').read_text()):
        path = (VOICE_DIR / 'models' / item['file']).resolve()
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(block)
        actual_digest = digest.hexdigest()
        if actual_digest != item['sha256']:
            raise ValueError(f'Neural voice model hash mismatch: {path.name}')
        verified.add(path)
        if path == model:
            model_digest = actual_digest
    missing = required - verified
    if missing:
        names = ', '.join(sorted(str(path) for path in missing))
        raise ValueError(f'Models must be SHA256-pinned in {VOICE_DIR / "neural-models.json"}: {names}')
    return model_digest


class KokoroEngine:
    """One loaded ONNX session; reused for every chunk."""

    def __init__(self, threads=DEFAULT_THREADS, profile_prefix=None):
        sys.path.insert(0, str(VOICE_DIR))
        import numpy as np
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        self.model = selected_model()
        self.model_sha256 = verify_models(self.model)
        self.provider = requested_provider()
        self.ort_version = ort.__version__
        self.session_id = uuid.uuid4().hex
        self.execution_profile = {}
        self.gpu_execution_verified = False
        self.profiling_active = profile_prefix is not None
        session = create_session(ort, self.model, threads, profile_prefix)
        self.session = session
        self.np = np
        self.kokoro = Kokoro.from_session(session, str(VOICE_DIR / 'models/voices-v1.0.bin'))
        self.voices = {key: dict(value) for key, value in _presets().items()}

    def describe(self):
        return {'requested_provider': self.provider,
                'providers': self.session.get_providers(),
                'model': str(self.model), 'model_sha256': self.model_sha256,
                'session_id': self.session_id, 'onnxruntime_version': self.ort_version,
                'gpu_execution_verified': self.gpu_execution_verified,
                'execution_profile': self.execution_profile,
                'profiling_active': self.profiling_active}

    def record_execution(self, profile):
        self.execution_profile = profile
        self.gpu_execution_verified = (
            profile.get(GPU_PROVIDER, {}).get('compute_node_events', 0) > 0)

    def require_gpu_execution(self):
        if self.provider == GPU_PROVIDER and not self.gpu_execution_verified:
            raise RuntimeError(
                'MIGraphX was requested but no GPU model computation was verified; '
                'provider registration or memory copies alone are insufficient. '
                'Run tts_worker.py --check and inspect docs/AMD_GPU.md.')

    def speak(self, text, identifier):
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            raise ValueError(f'Speak 1..{MAX_TEXT} characters per chunk')
        if identifier not in self.voices:
            raise ValueError('Unknown local voice')
        voice = self.voices[identifier]
        samples, rate = self.kokoro.create(
            text.strip(), voice=voice['model'], speed=voice['speed'], lang=voice['lang'])
        samples = self.np.asarray(samples)
        if samples.ndim != 1 or samples.size == 0 or not self.np.isfinite(samples).all():
            raise ValueError('Kokoro returned empty, non-finite, or non-mono audio')
        if not isinstance(rate, (int, self.np.integer)) or not 0 < rate <= 192000:
            raise ValueError('Kokoro returned an invalid sample rate')
        if samples.size * 2 + 44 > 8 * 1024 * 1024:
            raise ValueError('Speech output outside finite budget')
        pcm = (self.np.clip(samples, -.98, .98) * 32767).astype('<i2').tobytes()
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(pcm)
        data = output.getvalue()
        if not data or len(data) > 8 * 1024 * 1024:
            raise ValueError('Speech output outside finite budget')
        return {'wav': base64.b64encode(data).decode('ascii'), 'rate': rate,
                'voice': voice['name'], 'id': identifier,
                'chars': len(text.strip()),
                'engine': f'Kokoro {self.model.name} / {self.provider} (local)',
                **self.describe()}


def profile_summary(path):
    """Separate model computation from provider registration, copies and fences.

    ORT records executed kernels as Node / <node>_kernel_time events with an
    op_name. Durations are ORT operator-event times, not hardware GPU timings.
    """
    summary = {}
    for event in json.loads(Path(path).read_text()):
        args = event.get('args', {})
        provider = args.get('provider')
        if event.get('cat') != 'Node' or not provider:
            continue
        entry = summary.setdefault(provider, {
            'node_events': 0, 'compute_node_events': 0, 'duration_us': 0,
            'compute_ops': []})
        entry['node_events'] += 1
        entry['duration_us'] += event.get('dur', 0)
        operation = args.get('op_name', '')
        name = event.get('name', '')
        if (not isinstance(operation, str) or not operation
                or not isinstance(name, str) or not name.endswith('_kernel_time')
                or event.get('ph', 'X') != 'X'
                or operation.lower().startswith(('memcpy', 'copy', 'memset'))):
            continue
        entry['compute_node_events'] += 1
        if operation not in entry['compute_ops'] and len(entry['compute_ops']) < 16:
            entry['compute_ops'].append(operation)
    return summary


def _end_profiling(engine):
    # EndProfiling disables collection on this session. Keep the same session
    # for serving; constructing another one would discard the verified proof.
    path = engine.session.end_profiling()
    engine.profiling_active = False
    return path


def prepare_engine(threads=DEFAULT_THREADS, warmup=True):
    """Create a ready serving session; GPU proof cannot be skipped by --no-warmup."""
    if requested_provider() == CPU_PROVIDER:
        engine = KokoroEngine(threads)
        if warmup:
            engine.speak('Ready.', 'heart')
        return engine
    with tempfile.TemporaryDirectory(prefix='tui-voice-startup-') as directory:
        engine = KokoroEngine(threads, profile_prefix=Path(directory) / 'ort')
        try:
            engine.speak(VERIFICATION_TEXT, 'heart')
        finally:
            profile_path = _end_profiling(engine)
        engine.record_execution(profile_summary(profile_path))
        engine.require_gpu_execution()
        return engine


def check_engine(threads):
    # No recording or generated WAV is saved. The temporary ORT trace contains
    # graph metadata and is removed after summarizing it.
    with tempfile.TemporaryDirectory(prefix='tui-voice-check-') as directory:
        started = time.perf_counter()
        engine = KokoroEngine(threads, profile_prefix=Path(directory) / 'ort')
        load_seconds = time.perf_counter() - started
        timings = []
        try:
            for _ in range(2):
                started = time.perf_counter()
                result = engine.speak(VERIFICATION_TEXT, 'heart')
                timings.append(time.perf_counter() - started)
        finally:
            profile_path = _end_profiling(engine)
        profile = profile_summary(profile_path)
        engine.record_execution(profile)
        with wave.open(io.BytesIO(base64.b64decode(result['wav'])), 'rb') as wav:
            duration = wav.getnframes() / wav.getframerate()
        gpu_executed = engine.gpu_execution_verified
        ok = duration > 0 and (engine.provider == CPU_PROVIDER or gpu_executed)
        return {'ok': ok, **engine.describe(), 'load_s': round(load_seconds, 3),
                'timings_profiled': True,
                'first_synthesis_s': round(timings[0], 3),
                'warm_synthesis_s': round(timings[1], 3),
                'audio_seconds': round(duration, 3),
                'warm_real_time_factor': round(timings[1] / duration, 3) if duration else None,
                'gpu_execution_verified': gpu_executed, 'profile': profile,
                'error': None if ok else 'No GPU model computation was observed, or audio was empty'}


def _presets():
    sys.path.insert(0, str(VOICE_DIR))
    from voices import PRESETS
    return PRESETS


def serve(path, engine, idle_seconds=1800):
    engine.require_gpu_execution()
    path = Path(path)
    _prepare_socket_dir(path)
    if path.exists():
        if _ping(path):
            raise RuntimeError(f'Warm TTS already running at {path}')
        existing = path.lstat()
        if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != os.getuid():
            raise RuntimeError(f'Refusing to replace non-socket path {path}')
        path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    started = time.monotonic()
    state = {'requests': 0, 'last': started, 'stopping': False}
    speak_lock = threading.Lock()

    def handle(connection):
        with connection:
            owner = peer_uid(connection)
            if owner is not None and owner != os.getuid():
                return
            connection.settimeout(120)
            try:
                request = recv_frame(connection, MAX_FRAME)
            except (OSError, ValueError, ConnectionError):
                return
            if not isinstance(request, dict):
                send_frame(connection, {'ok': False, 'error': 'Malformed request'})
                return
            operation = request.get('operation')
            if operation == 'shutdown':
                send_frame(connection, {'ok': True, 'result': {'stopping': True}})
                state['stopping'] = True
                return
            if operation == 'status':
                send_frame(connection, {'ok': True, 'result': {
                    'pid': os.getpid(), 'socket': str(path), 'requests': state['requests'],
                    'voices': sorted(engine.voices),
                    'uptime_s': round(time.monotonic() - started, 1),
                    'idle_seconds': idle_seconds, 'rss_mb': rss_mb(), **engine.describe()}})
                return
            if operation != 'speak':
                send_frame(connection, {'ok': False, 'error': 'Unsupported operation'})
                return
            state['last'] = time.monotonic()
            try:
                with speak_lock:
                    result = engine.speak(request.get('text'), request.get('voice', 'heart'))
                response = {'ok': True, 'result': result}
                state['requests'] += 1
            except Exception as error:  # bounded, no traceback sent to callers
                response = {'ok': False, 'error': f'{type(error).__name__}: {error}'[:400]}
            try:
                send_frame(connection, response)
            except OSError:
                pass

    try:
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.listen(8)
        server.settimeout(1)
        while not state['stopping']:
            try:
                connection, _ = server.accept()
            except socket.timeout:
                if idle_seconds and time.monotonic() - state['last'] > idle_seconds:
                    break
                continue
            threading.Thread(target=handle, args=(connection,), daemon=True).start()
    finally:
        server.close()
        try:
            if stat.S_ISSOCK(path.lstat().st_mode):
                path.unlink()
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--socket', help='AF_UNIX path (default: XDG_RUNTIME_DIR/tui-voice/tts.sock)')
    parser.add_argument('--threads', type=int, default=DEFAULT_THREADS)
    parser.add_argument('--idle-seconds', type=int, default=1800, help='idle exit; 0 = never')
    parser.add_argument('--no-warmup', action='store_true',
                        help='skip optional CPU warmup; required GPU verification still runs')
    parser.add_argument('--run', action='store_true', help='run in the foreground')
    parser.add_argument('--start', action='store_true', help='start detached and wait for readiness')
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--check', action='store_true', help='profile two local syntheses, then exit')
    args = parser.parse_args()

    if not 1 <= args.threads <= 24:
        parser.error('--threads must be 1..24')
    if args.check:
        try:
            result = check_engine(args.threads)
        except Exception as error:
            result = {'ok': False, 'error': f'{type(error).__name__}: {error}'}
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result['ok'] else 1)

    path = Path(args.socket) if args.socket else socket_path()
    if args.status:
        try:
            print(json.dumps(admin_request('status', path)['result'], indent=2))
        except (OSError, ValueError, KeyError, ConnectionError) as error:
            print(f'warm TTS not running at {path} ({error})')
            raise SystemExit(1)
        return
    if args.stop:
        try:
            print(json.dumps(admin_request('shutdown', path), indent=2))
        except (OSError, ValueError, ConnectionError) as error:
            print(f'warm TTS not running at {path} ({error})')
            raise SystemExit(1)
        return
    if args.start:
        if _ping(path):
            validate_backend(admin_request('status', path)['result'])
            print(f'warm TTS already running at {path}')
            return
        command = [sys.executable, str(Path(__file__).resolve()), '--run', '--socket', str(path),
                   '--threads', str(args.threads), '--idle-seconds', str(args.idle_seconds)]
        if args.no_warmup:
            command.append('--no-warmup')
        _prepare_socket_dir(path)
        log_path = path.parent / 'tts-startup.log'
        descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w') as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=log, start_new_session=True, close_fds=True)
        timeout = startup_timeout()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            code = process.poll()
            if code is not None and code != WORKER_BUSY_EXIT:
                hint = ''
                if code in (-signal.SIGABRT, -signal.SIGSEGV) and requested_provider() == GPU_PROVIDER:
                    hint = (' The GPU worker crashed before serving. On WSL2, verify the '
                            'Windows AMD driver supports ROCm; see docs/AMD_GPU.md.')
                print(f'warm TTS exited during startup (status {code}); '
                      f'see {log_path} or run tts_worker.py --run.{hint}', file=sys.stderr)
                raise SystemExit(1)
            if _ping(path):
                status = admin_request('status', path)['result']
                validate_backend(status)
                print(f'warm TTS ready at {path} (pid {status["pid"]})')
                return
            time.sleep(0.1)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        print(f'warm TTS did not become ready within {timeout:.0f}s; see {log_path}', file=sys.stderr)
        raise SystemExit(1)

    if not args.run:
        parser.print_help()
        return
    def stop(_signum=None, _frame=None):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        with worker_lock(path):
            engine = prepare_engine(threads=args.threads, warmup=not args.no_warmup)
            print(f'warm TTS ready: rss={rss_mb()}MB socket={path}', file=sys.stderr, flush=True)
            serve(path, engine, idle_seconds=args.idle_seconds)
    except WorkerBusy as error:
        print(error, file=sys.stderr)
        raise SystemExit(WORKER_BUSY_EXIT)


if __name__ == '__main__':
    main()
