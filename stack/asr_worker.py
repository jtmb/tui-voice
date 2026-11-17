#!/usr/bin/env python3
"""Warm local Whisper worker for tui-voice (installed by setup_stack.py).

Loads one pinned faster-whisper model and serves bounded requests over a
uid-owned AF_UNIX socket. No TCP, no cloud, no audio written to disk.

  asr-worker.py --start          # detached, idle-exits
  asr-worker.py --status
  asr-worker.py --stop
  asr-worker.py --run            # foreground
"""
import argparse
import io
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import sys
import time

MAX_FRAME = 16 * 1024 * 1024
MAX_RESPONSE = 16 * 1024 * 1024
START_TIMEOUT = 45.0
DEFAULT_THREADS = 8
MAX_THREADS = 8
VOICE_DIR = Path(os.environ.get('TUI_VOICE_VOICE_DIR') or Path(__file__).resolve().parent)
MODEL_DIR = Path(os.environ.get('TUI_VOICE_ASR_MODEL') or (VOICE_DIR / 'models/whisper-base.en'))
ENGINE = 'Whisper base.en / faster-whisper / CPU int8 (local)'


def default_socket_path():
    runtime = os.environ.get('XDG_RUNTIME_DIR') or str(Path.home() / '.cache')
    return Path(runtime) / 'firewatch-asr/asr.sock'


def _recv_exact(connection, size):
    parts = []
    while size:
        part = connection.recv(min(size, 65536))
        if not part:
            raise ConnectionError('peer closed mid-frame')
        parts.append(part)
        size -= len(part)
    return b''.join(parts)


def recv_frame(connection):
    size = int.from_bytes(_recv_exact(connection, 4), 'big')
    if not 0 < size <= MAX_FRAME:
        raise ValueError('request outside finite budget')
    return json.loads(_recv_exact(connection, size))


def send_frame(connection, message):
    data = json.dumps(message, separators=(',', ':')).encode()
    if len(data) > MAX_RESPONSE:
        raise ValueError('response outside finite budget')
    connection.sendall(len(data).to_bytes(4, 'big') + data)


def admin(operation, path, timeout=3):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        send_frame(connection, {'operation': operation})
        return recv_frame(connection)


def ping(path, timeout=0.5):
    try:
        return admin('status', path, timeout).get('ok') is True
    except (OSError, ValueError, ConnectionError):
        return False


def rss_mb():
    try:
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return round(int(line.split()[1]) / 1024, 1)
    except (OSError, ValueError):
        pass
    return None


def peer_uid(connection):
    try:
        _pid, uid, _gid = struct.unpack(
            '3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        return uid
    except OSError:
        return None


def summarize(segments):
    words, texts = [], []
    for segment in segments:
        texts.append(segment.text)
        for word in (segment.words or ()):
            if word.probability is not None:
                words.append(float(word.probability))
    return {
        'text': ' '.join(''.join(texts).split())[:6000],
        'confidence': round(sum(words) / len(words), 4) if words else 0.0,
        'confidence_kind': 'Whisper mean word probability; heuristic, not calibrated accuracy',
        'engine': ENGINE,
        'raw_audio_retained': False,
    }


def load_model(threads):
    model_dir = MODEL_DIR
    missing = [name for name in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')
               if not (model_dir / name).is_file()]
    if missing:
        raise SystemExit(f'Missing Whisper model files in {model_dir}: {", ".join(missing)}. '
                         'Run: python3 setup_stack.py --install')
    from faster_whisper import WhisperModel
    return WhisperModel(str(model_dir), device='cpu', compute_type='int8',
                        cpu_threads=max(1, min(threads, MAX_THREADS)))


def transcribe(model, raw):
    if not raw.startswith(b'RIFF') or raw[8:12] != b'WAVE':
        raise ValueError('Expected a PCM WAV recording')
    segments, _info = model.transcribe(
        io.BytesIO(raw), language='en', beam_size=5, temperature=0,
        condition_on_previous_text=False, word_timestamps=True, vad_filter=True,
        vad_parameters={'min_silence_duration_ms': 500})
    return summarize(segments)


def serve(path, model, idle_seconds, threads):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.stat().st_uid != os.getuid():
        raise SystemExit(f'Socket directory {path.parent} is not owned by this user')
    os.chmod(path.parent, 0o700)
    if path.exists():
        if ping(path):
            raise SystemExit(f'Warm ASR already running at {path}')
        existing = path.lstat()
        if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != os.getuid():
            raise SystemExit(f'Refusing to replace non-socket path {path}')
        path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    started = time.monotonic()
    last = started
    requests = 0
    try:
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.listen(4)
        server.settimeout(1)
        while True:
            try:
                connection, _ = server.accept()
            except socket.timeout:
                if idle_seconds and time.monotonic() - last > idle_seconds:
                    break
                continue
            with connection:
                owner = peer_uid(connection)
                if owner is not None and owner != os.getuid():
                    continue
                connection.settimeout(120)
                try:
                    request = recv_frame(connection)
                except (OSError, ValueError, ConnectionError):
                    continue
                if not isinstance(request, dict):
                    send_frame(connection, {'ok': False, 'error': 'Malformed request'})
                    continue
                operation = request.get('operation')
                if operation == 'shutdown':
                    send_frame(connection, {'ok': True, 'result': {'stopping': True}})
                    break
                if operation == 'status':
                    send_frame(connection, {'ok': True, 'result': {
                        'pid': os.getpid(), 'socket': str(path), 'device': 'cpu',
                        'threads': threads, 'requests': requests,
                        'uptime_s': round(time.monotonic() - started, 1),
                        'idle_seconds': idle_seconds, 'rss_mb': rss_mb()}})
                    continue
                if operation != 'transcribe':
                    send_frame(connection, {'ok': False, 'error': 'Unsupported operation'})
                    continue
                last = time.monotonic()
                try:
                    import base64
                    response = {'ok': True, 'result': transcribe(
                        model, base64.b64decode(request.get('wav') or '', validate=True))}
                    requests += 1
                except Exception as error:  # bounded; no traceback to callers
                    response = {'ok': False, 'error': f'{type(error).__name__}: {error}'[:400]}
                try:
                    send_frame(connection, response)
                except OSError:
                    pass
    finally:
        server.close()
        try:
            if stat.S_ISSOCK(path.lstat().st_mode):
                path.unlink()
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', help='AF_UNIX path (default: XDG_RUNTIME_DIR/firewatch-asr/asr.sock)')
    parser.add_argument('--threads', type=int, default=DEFAULT_THREADS)
    parser.add_argument('--idle-seconds', type=int, default=900, help='idle exit; 0 = never')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--start', action='store_true')
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    threads = max(1, min(args.threads, MAX_THREADS))
    path = Path(args.socket) if args.socket else default_socket_path()
    if args.status:
        try:
            print(json.dumps(admin('status', path)['result'], indent=2))
        except (OSError, ValueError, KeyError, ConnectionError) as error:
            print(f'warm ASR not running at {path} ({error})')
            raise SystemExit(1)
        return
    if args.stop:
        try:
            print(json.dumps(admin('shutdown', path), indent=2))
        except (OSError, ValueError, ConnectionError) as error:
            print(f'warm ASR not running at {path} ({error})')
            raise SystemExit(1)
        return
    if args.start:
        command = [sys.executable, str(Path(__file__).resolve()), '--run',
                   '--socket', str(path), '--threads', str(threads),
                   '--idle-seconds', str(args.idle_seconds)]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True, close_fds=True)
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if process.poll() is not None:
                print(f'warm ASR exited during startup (status {process.returncode})',
                      file=sys.stderr)
                raise SystemExit(1)
            if ping(path):
                print(f'warm ASR started at {path} (pid {process.pid})')
                return
            time.sleep(0.1)
        print(f'warm ASR did not become ready within {START_TIMEOUT:.0f}s', file=sys.stderr)
        raise SystemExit(1)
    if not args.run:
        parser.print_help()
        return
    model = load_model(threads)
    print(f'warm ASR ready: threads={threads} rss={rss_mb()}MB socket={path}',
          file=sys.stderr, flush=True)
    serve(path, model, args.idle_seconds, threads)


if __name__ == '__main__':
    main()
