#!/usr/bin/env python3
"""Warm local Kokoro speech worker for the tui-voice plugin.

Loads the pinned ONNX voice once and serves bounded text chunks over a
uid-owned AF_UNIX socket. Every request stays on this machine; nothing is
uploaded and no audio is written to disk by the worker itself.

  tts_worker.py --start          # detached, idle-exits after 30 min
  tts_worker.py --status         # JSON status
  tts_worker.py --stop
  tts_worker.py --run            # foreground
"""
import argparse
import base64
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
import threading
import time
import wave

VOICE_DIR = Path(os.environ.get(
    'TUI_VOICE_VOICE_DIR',
    str(Path.home() / 'repos/fire-tv-next/lulu-launcher/voice')))
MAX_FRAME = 16 * 1024 * 1024
MAX_RESPONSE = 16 * 1024 * 1024
MAX_TEXT = 1200
DEFAULT_THREADS = 8
START_TIMEOUT = 30.0


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


def verify_models():
    """Verify the pinned voice model identity once at startup, not per request."""
    import hashlib
    for item in json.loads((VOICE_DIR / 'neural-models.json').read_text()):
        path = VOICE_DIR / 'models' / item['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Neural voice model hash mismatch')


class KokoroEngine:
    """One loaded ONNX session; reused for every chunk."""

    def __init__(self, threads=DEFAULT_THREADS):
        sys.path.insert(0, str(VOICE_DIR))
        import numpy as np
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        verify_models()
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, int(threads))
        options.inter_op_num_threads = 1
        # Long replies otherwise leave a large retained CPU arena in a long-lived
        # worker. Disabling it keeps warm memory stable across turns.
        options.enable_cpu_mem_arena = False
        session = ort.InferenceSession(
            str(VOICE_DIR / 'models/kokoro-v1.0.int8.onnx'),
            sess_options=options, providers=['CPUExecutionProvider'])
        self.np = np
        self.kokoro = Kokoro.from_session(session, str(VOICE_DIR / 'models/voices-v1.0.bin'))
        self.voices = {key: dict(value) for key, value in _presets().items()}

    def speak(self, text, identifier):
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            raise ValueError(f'Speak 1..{MAX_TEXT} characters per chunk')
        if identifier not in self.voices:
            raise ValueError('Unknown local voice')
        voice = self.voices[identifier]
        samples, rate = self.kokoro.create(
            text.strip(), voice=voice['model'], speed=voice['speed'], lang=voice['lang'])
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
                'chars': len(text.strip()), 'engine': 'Kokoro int8 / CPU (local)'}


def _presets():
    sys.path.insert(0, str(VOICE_DIR))
    from voices import PRESETS
    return PRESETS


def serve(path, engine, idle_seconds=1800):
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
                    'idle_seconds': idle_seconds, 'rss_mb': rss_mb()}})
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
    parser.add_argument('--no-warmup', action='store_true')
    parser.add_argument('--run', action='store_true', help='run in the foreground')
    parser.add_argument('--start', action='store_true', help='start detached and wait for readiness')
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()

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
            print(f'warm TTS already running at {path}')
            return
        command = [sys.executable, str(Path(__file__).resolve()), '--run', '--socket', str(path),
                   '--threads', str(args.threads), '--idle-seconds', str(args.idle_seconds)]
        if args.no_warmup:
            command.append('--no-warmup')
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if process.poll() is not None:
                print(f'warm TTS exited during startup (status {process.returncode})', file=sys.stderr)
                raise SystemExit(1)
            if _ping(path):
                print(f'warm TTS started at {path} (pid {process.pid})')
                return
            time.sleep(0.1)
        print(f'warm TTS did not become ready within {START_TIMEOUT:.0f}s', file=sys.stderr)
        raise SystemExit(1)

    if not args.run:
        parser.print_help()
        return
    if not 1 <= args.threads <= 24:
        parser.error('--threads must be 1..24')
    engine = KokoroEngine(threads=args.threads)
    if not args.no_warmup:
        engine.speak('Ready.', 'heart')
    print(f'warm TTS ready: rss={rss_mb()}MB socket={path}', file=sys.stderr, flush=True)

    def stop(_signum=None, _frame=None):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    serve(path, engine, idle_seconds=args.idle_seconds)


if __name__ == '__main__':
    main()
