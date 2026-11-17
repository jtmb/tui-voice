#!/usr/bin/env python3
"""Local dictation helper for the tui-voice OpenCode plugin.

Records through the local microphone (Windows on WSL2; PipeWire, PulseAudio or
ALSA on native Linux) with ffmpeg, stops on silence, and returns text from the
existing local Whisper stack. No cloud service or TV.
"""
import argparse
import base64
from contextlib import contextmanager
import fcntl
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import struct
import subprocess
import sys
import threading
import time
import traceback
import wave

import audio_backend
from tts_worker import CPU_PROVIDER, requested_provider, startup_timeout, validate_backend
from live_dictation import FRAME_BYTES, LiveTranscriber

SERVICE = Path(os.environ.get('TUI_VOICE_SERVICE', str(Path.home() / 'repos/fire-tv-next/lulu-launcher/service')))
VOICE_DIR = Path(os.environ.get('TUI_VOICE_VOICE_DIR', str(SERVICE.parent / 'voice')))
HERE = Path(__file__).resolve().parent
ASR_WORKER = VOICE_DIR / 'asr-worker.py'
TTS_WORKER = HERE / 'tts_worker.py'
ASR_PYTHON = Path(os.environ.get('TUI_VOICE_ASR_PYTHON', str(VOICE_DIR / '.asr-venv/bin/python')))
TTS_PYTHON = Path(os.environ.get('TUI_VOICE_TTS_PYTHON', str(VOICE_DIR / '.venv/bin/python')))


def _bounded_env_int(name, default, maximum):
    """Keep worker arguments inside the range its own CLI accepts."""
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def _env_seconds(name, default):
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


WARM_IDLE = _env_seconds('TUI_VOICE_WARM_IDLE', 1800)

# asr-worker.py accepts 1..8 and tts_worker.py accepts 1..24. An out-of-range
# override otherwise makes every start die with a bare argparse status 2.
ASR_THREADS = _bounded_env_int('TUI_VOICE_ASR_THREADS', 8, 8)
TTS_THREADS = _bounded_env_int('TUI_VOICE_TTS_THREADS', 8, 24)
CMD = '/mnt/c/Windows/System32/cmd.exe'
_current = None
_capture_path = None
_stop_requested = False
_cold_voice = None
_windows_environment = None

LOG_DIR = Path(os.environ.get('TUI_VOICE_LOG_DIR') or
               (Path(os.environ.get('XDG_STATE_HOME') or (Path.home() / '.local/state')) / 'tui-voice'))
LOG_FILE = LOG_DIR / 'voice.log'
LOG_LIMIT = 1_000_000
ASR_CHUNK_SECONDS = 28.0
MAX_SPEECH_CHUNKS = 400


def log_event(level, message, **fields):
    """Append one JSON line to the local voice log. Logging never breaks a turn."""
    try:
        LOG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        if LOG_FILE.is_file() and LOG_FILE.stat().st_size > LOG_LIMIT:
            os.replace(LOG_FILE, LOG_FILE.with_name('voice.log.1'))
        record = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'pid': os.getpid(),
                  'level': level, 'message': str(message)}
        record.update({key: value for key, value in fields.items() if value is not None})
        with open(LOG_FILE, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    except (OSError, ValueError, TypeError):
        pass


def output(payload, code=0):
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    raise SystemExit(code)


def fail(message, **fields):
    log_event('error', message, **fields)
    output({'ok': False, 'error': str(message)}, 1)


def stop_child(_signum=None, _frame=None):
    global _current, _capture_path
    if _current is not None and _current.poll() is None:
        try:
            _current.terminate()
        except OSError:
            pass
    if _capture_path:
        try:
            Path(_capture_path).unlink()
        except OSError:
            pass
        _capture_path = None
    raise SystemExit(1)


def request_stop(_signum=None, _frame=None):
    global _current, _stop_requested
    # Remember the request even while microphone enumeration runs: that child
    # has no stdin, so it cannot receive ffmpeg's 'q' yet.
    _stop_requested = True
    if _current is not None and _current.poll() is None:
        try:
            if _current.stdin:
                _current.stdin.write('q' if _current.text_mode else b'q')
                _current.stdin.flush()
        except (OSError, ValueError):
            try:
                _current.terminate()
            except OSError:
                pass


signal.signal(signal.SIGTERM, stop_child)
signal.signal(signal.SIGINT, stop_child)
signal.signal(signal.SIGUSR1, request_stop)


def windows_env():
    """Repair a stale inherited WSL interop bridge for our children only.

    A long-lived OpenCode service can retain a dead terminal's bridge. Reuse an
    existing healthy WSL bridge; never restart WSL, change permissions or daemons.
    """
    global _windows_environment
    if _windows_environment is not None:
        return _windows_environment
    inherited = os.environ.get('WSL_INTEROP')
    candidates = list(dict.fromkeys(filter(None, [
        inherited, '/run/WSL/1_interop', '/run/WSL/2_interop',
    ])))
    for value in candidates:
        path = Path(value)
        try:
            if not stat.S_ISSOCK(path.stat().st_mode):
                continue
            pid = path.name.removesuffix('_interop')
            if pid.isdigit() and not Path('/proc', pid).exists():
                continue
            env = {**os.environ, 'WSL_INTEROP': value}
            probe = subprocess.run([CMD, '/c', 'exit', '0'], env=env, cwd='/mnt/c',
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, timeout=1.5)
            if probe.returncode != 0:
                continue
        except (OSError, subprocess.TimeoutExpired):
            continue
        _windows_environment = env
        if value != inherited:
            log_event('warning', 'recovered stale Windows interop bridge',
                      previous=inherited, selected=value)
        return env
    raise RuntimeError('No healthy Windows interop bridge; microphone/speakers are unavailable')


def run(args, timeout=60):
    global _current
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', errors='replace',
                               env=windows_env() if str(args[0]).lower().endswith('.exe') else None)
    _current = process
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise TimeoutError('Command timed out')
    finally:
        _current = None
    return process.returncode, stdout, stderr


def audio_env():
    """Child environment for audio tools: the Windows bridge on WSL only."""
    return windows_env() if audio_backend.PLATFORM == audio_backend.WSL2 else None


def windows_temp():
    code, out, _ = run([CMD, '/c', 'echo', '%TEMP%'], timeout=10)
    value = out.strip()
    match = re.fullmatch(r'([A-Za-z]):\\(.*)', value)
    if code != 0 or not match:
        fail('Could not find the Windows temporary directory')
    return Path('/mnt/' + match.group(1).lower() + '/' + match.group(2).replace('\\', '/'))


def scratch_dir():
    """Private directory for temporary WAVs on this platform."""
    if audio_backend.PLATFORM == audio_backend.WSL2:
        return windows_temp()
    return audio_backend.scratch_dir()


def to_windows(path):
    try:
        return audio_backend.output_path(path, child_env=audio_env())
    except audio_backend.AudioBackendError as error:
        fail(str(error))


def ffmpeg():
    try:
        return audio_backend.ffmpeg()
    except audio_backend.AudioBackendError as error:
        fail(str(error))


def devices():
    try:
        return audio_backend.devices(child_env=audio_env())
    except audio_backend.AudioBackendError as error:
        fail(str(error))


def choose_mic(requested=None):
    try:
        return audio_backend.choose_mic(requested, child_env=audio_env())
    except audio_backend.AudioBackendError as error:
        fail(str(error))


def capture_filters(silence, threshold, gain):
    """Build the FFmpeg chain: gain first, then immediate silence reports."""
    filters = []
    if gain:
        filters.append(f'volume={gain}dB')
    if silence > 0:
        filters.append(f'silencedetect=noise={threshold}dB:d=0.01')
    return filters


def silence_decision(kind, at, heard_sound, pause):
    """Return (heard_sound, timer_seconds) for one silencedetect event.

    The detector reports the start of a silence immediately. The Python timer
    owns the configured pause duration, so it must not be applied twice.
    """
    if kind == 'start':
        if at > 0.1:
            heard_sound = True
        return heard_sound, (pause if heard_sound and pause > 0 else None)
    if kind == 'end':
        return True, None
    return heard_sound, None


def record(seconds, path=None, mic=None, silence=1.5, threshold=-35, gain=0):
    device, names = choose_mic(mic)
    seconds = max(1, min(120, int(seconds)))
    if not path:
        path = scratch_dir() / ('tui-voice-' + str(os.getpid()) + '.wav')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    global _current, _capture_path, _stop_requested
    _capture_path = str(path)
    output_file = audio_backend.output_path(path, child_env=audio_env())
    command = [
        ffmpeg(), '-hide_banner', '-loglevel', 'info' if silence > 0 else 'error',
        *audio_backend.capture_args(device),
        '-t', str(seconds), '-ac', '1', '-ar', '16000', '-sample_fmt', 's16',
    ]
    filters = capture_filters(silence, threshold, gain)
    if filters:
        command += ['-af', ','.join(filters)]
    command += ['-y', output_file]

    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, text=True, bufsize=1,
                               encoding='utf-8', errors='replace', env=audio_env())
    global _current
    _current = process
    timer = {'handle': None}
    lock = threading.Lock()

    def cancel_timer():
        with lock:
            handle = timer['handle']
            timer['handle'] = None
            if handle:
                handle.cancel()

    def stop_after_silence():
        try:
            if process.stdin:
                process.stdin.write('q')
                process.stdin.flush()
            else:
                process.terminate()
        except (OSError, ValueError):
            try:
                process.terminate()
            except OSError:
                pass

    tail = []
    heard_sound = False

    def read_levels():
        nonlocal heard_sound
        for line in process.stderr:
            tail.append(line.rstrip())
            del tail[:-40]
            match = re.search(r'silence_start:\s*([0-9.]+)', line)
            kind = 'start' if match else ('end' if 'silence_end' in line else None)
            if not kind:
                continue
            at = float(match.group(1)) if match else 0.0
            heard_sound, wait = silence_decision(kind, at, heard_sound, silence)
            if wait:
                with lock:
                    if timer['handle']:
                        timer['handle'].cancel()
                    handle = threading.Timer(wait, stop_after_silence)
                    handle.daemon = True
                    timer['handle'] = handle
                    handle.start()
            elif kind == 'end':
                cancel_timer()

    reader = threading.Thread(target=read_levels, daemon=True)
    reader.start()
    if _stop_requested:
        _stop_requested = False
        stop_after_silence()

    try:
        process.wait(timeout=seconds + 8)
    except subprocess.TimeoutExpired:
        try:
            process.terminate()
        except OSError:
            pass
        process.wait()
    cancel_timer()
    reader.join(timeout=1)
    _current = None
    _stop_requested = False
    if not path.is_file() or path.stat().st_size <= 44:
        try:
            path.unlink()
        except OSError:
            pass
        _capture_path = None
        fail('Microphone capture failed: ffmpeg returned no audio',
             device=device, detail='\n'.join(tail[-20:]))
    rms = 0.0
    peak = 0
    try:
        with wave.open(str(path), 'rb') as wav:
            frames = wav.readframes(wav.getnframes())
        if frames:
            samples = struct.unpack('<%dh' % (len(frames) // 2), frames)
            rms = (sum(value * value for value in samples) / len(samples)) ** 0.5
            peak = max(abs(value) for value in samples)
    except (wave.Error, OSError):
        pass
    log_event('info', 'recorded', path=str(path), device=device, seconds=seconds,
              bytes=path.stat().st_size, rms=round(rms, 1))
    _capture_path = None
    result = {'ok': True, 'path': str(path), 'device': device, 'devices': names,
              'seconds': seconds, 'silence': silence, 'threshold': threshold, 'gain': gain,
              'rms': round(rms, 1), 'peak': peak, 'bytes': path.stat().st_size}
    if audio_backend.PLATFORM == audio_backend.WSL2:
        result['windows_path'] = output_file
    return result


def dictate(mic=None, threshold=-35, gain=0, emit=None):
    """Stream local hypotheses until explicitly switched off; no silence/time cutoff."""
    global _current, _stop_requested
    emit = emit or (lambda value: print(json.dumps(value, ensure_ascii=False), flush=True))
    emit({'event': 'starting'})  # SIGUSR1 is safe only after this handshake.
    if _stop_requested:
        _stop_requested = False
        return {'ok': True, 'text': ''}
    # A known device needs no repeated device enumeration on every turn.
    device = mic or choose_mic()[0]
    if _stop_requested:
        _stop_requested = False
        return {'ok': True, 'text': ''}
    command = ([ffmpeg(), '-hide_banner', '-loglevel', 'error']
               + audio_backend.capture_args(device, streaming=True)
               + ['-ac', '1', '-ar', '16000'])
    if gain:
        command += ['-af', f'volume={gain}dB']
    command += ['-f', 's16le', 'pipe:1']
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=audio_env())
    _current = process
    tail = bytearray()

    def errors():
        for block in iter(lambda: process.stderr.read(1024), b''):
            tail.extend(block)
            del tail[:-4000]

    reader = threading.Thread(target=errors, daemon=True)
    reader.start()

    def decode(pcm):
        encoded = _encode_wav(pcm, 1, 2, 16000)
        result = warm_transcribe(encoded)
        if result is None:
            result = cold_voice().transcribe(encoded)
        return result.get('text', '') if result else ''

    live = LiveTranscriber(decode, emit, threshold=threshold)
    live.start()
    received = 0
    stopped = False
    started = time.monotonic()
    last_pcm = [started]
    watchdog_done = threading.Event()

    def stop_capture():
        nonlocal stopped
        if stopped:
            return
        stopped = True
        try:
            process.stdin.write(b'q')
            process.stdin.flush()
        except (OSError, ValueError):
            process.terminate()

    def watch_capture():
        # This is a device-failure watchdog, not a recording limit: silent PCM
        # counts as healthy input and keeps the microphone on indefinitely.
        while not watchdog_done.wait(1):
            if time.monotonic() - last_pcm[0] > 10:
                if process.poll() is None:
                    process.kill()
                return

    watchdog = threading.Thread(target=watch_capture, daemon=True)
    watchdog.start()
    try:
        while True:
            block = process.stdout.read(FRAME_BYTES // 10)
            if not block:
                break
            if len(block) % 2:
                raise ValueError('Microphone returned a truncated PCM sample')
            if not received:
                emit({'event': 'listening', 'device': device})
            received += len(block)
            last_pcm[0] = time.monotonic()
            live.feed(block)
            if live.error:
                raise live.error
            if _stop_requested:
                stop_capture()
        process.wait(timeout=5)
        watchdog_done.set()
        reader.join(timeout=1)
        # Capture is over: a later stop signal must not touch its pipes while
        # local ASR is still running on the audio already received.
        _current = None
        if not received or (process.returncode and not stopped):
            raise RuntimeError('Microphone capture failed: ' +
                               (tail.decode('utf-8', errors='replace').strip() or 'no audio received'))
        emit({'event': 'transcribing'})
        text = live.finish()
        log_event('info', 'live dictation', device=device, bytes=received,
                  seconds=round(received / FRAME_BYTES, 2),
                  elapsed=round(time.monotonic() - started, 2), characters=len(text))
        return {'ok': True, 'text': text, 'device': device, 'raw_audio_retained': False}
    finally:
        watchdog_done.set()
        live.cancel()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for pipe in (process.stdin, process.stdout, process.stderr):
            try:
                pipe.close()
            except (OSError, ValueError, RuntimeError):
                # A late stop signal can close the pipe first; cleanup must not
                # replace the real capture or ASR failure with a pipe error.
                pass
        _current = None
        _stop_requested = False


def voice():
    sys.path.insert(0, str(SERVICE))
    from web_voice import WebVoice
    return WebVoice()


def cold_voice():
    """Reuse one cold WebVoice so a chunked turn does not reload the stack."""
    global _cold_voice
    if _cold_voice is None:
        _cold_voice = voice()
    return _cold_voice


def runtime_base():
    value = os.environ.get('XDG_RUNTIME_DIR')
    return Path(value) if value else Path.home() / '.cache'


def asr_socket():
    value = os.environ.get('TUI_VOICE_ASR_SOCKET')
    if value:
        return Path(value)
    return runtime_base() / 'firewatch-asr/asr.sock'


def tts_socket():
    value = os.environ.get('TUI_VOICE_TTS_SOCKET')
    if value:
        return Path(value)
    return runtime_base() / 'tui-voice/tts.sock'


def _recv_exact(connection, size):
    parts = []
    while size:
        part = connection.recv(min(size, 65536))
        if not part:
            raise ConnectionError('Warm worker closed mid-frame')
        parts.append(part)
        size -= len(part)
    return b''.join(parts)


def _frame(connection, message):
    data = json.dumps(message, separators=(',', ':')).encode()
    connection.sendall(len(data).to_bytes(4, 'big') + data)


def _frame_in(connection):
    size = int.from_bytes(_recv_exact(connection, 4), 'big')
    if not 0 < size <= 16 * 1024 * 1024:
        raise ValueError('Warm worker frame outside finite budget')
    return json.loads(_recv_exact(connection, size))


def worker_request(path, request, timeout=180, quiet=False, required=False):
    """Talk to a worker; required=True raises instead of allowing cold fallback."""
    try:
        import socket
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(path))
            connection.settimeout(timeout)
            _frame(connection, request)
            response = _frame_in(connection)
    except (OSError, ValueError, ConnectionError) as error:
        if required:
            raise RuntimeError(f'Required TTS worker unavailable at {path}: {error}') from error
        if not quiet:
            print(f'Warm worker unavailable at {path} ({error}); using cold path', file=sys.stderr)
            log_event('warning', 'warm worker unavailable', socket=str(path), detail=str(error))
        return None
    if not isinstance(response, dict) or response.get('ok') is not True:
        detail = response.get('error') if isinstance(response, dict) else 'malformed response'
        if required:
            raise RuntimeError(f'Required TTS worker rejected request: {detail}')
        if not quiet:
            print(f'Warm worker rejected request ({detail}); using cold path', file=sys.stderr)
            log_event('warning', 'warm worker rejected request', socket=str(path), detail=str(detail))
        return None
    result = response.get('result')
    return result if isinstance(result, dict) else None


def warm_transcribe(encoded):
    result = worker_request(asr_socket(), {'operation': 'transcribe', 'wav': encoded}, timeout=120)
    if (result is None or not isinstance(result.get('text'), str)
            or not isinstance(result.get('confidence'), (int, float))
            or not isinstance(result.get('engine'), str)
            or result.get('raw_audio_retained') is not False):
        return None
    return result


def warm_speak(text, voice_id):
    required = requested_provider() != CPU_PROVIDER
    result = worker_request(tts_socket(), {'operation': 'speak', 'text': text, 'voice': voice_id},
                            required=required)
    if result is None or not isinstance(result.get('wav'), str):
        if required:
            raise RuntimeError('Required TTS worker returned no audio; CPU fallback is disabled')
        return None
    validate_backend(result)
    return result


def warm_status():
    status = {'asr': None, 'tts': None}
    for kind, path in (('asr', asr_socket()), ('tts', tts_socket())):
        status[kind] = worker_request(path, {'operation': 'status'}, timeout=3, quiet=True)
    return status


@contextmanager
def warm_lock():
    """Serialize worker starts so concurrent plugin reloads cannot race sockets."""
    directory = tts_socket().parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / 'warm.lock'
    handle = open(path, 'a+')
    try:
        os.chmod(path, 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def start_worker(kind, idle_seconds=None):
    with warm_lock():
        path = asr_socket() if kind == 'asr' else tts_socket()
        status = worker_request(path, {'operation': 'status'}, timeout=3, quiet=True)
        if status is not None:
            if kind == 'tts':
                try:
                    validate_backend(status)
                except (RuntimeError, ValueError) as error:
                    return {'ok': False, 'error': str(error)}
            return {'ok': True, 'state': 'already running', 'socket': str(path)}
        python = ASR_PYTHON if kind == 'asr' else TTS_PYTHON
        script = ASR_WORKER if kind == 'asr' else TTS_WORKER
        if not python.is_file():
            log_event('error', f'missing {kind} worker Python', path=str(python))
            return {'ok': False, 'error': f'Missing {kind} worker Python: {python}'}
        if not script.is_file():
            log_event('error', f'missing {kind} worker script', path=str(script))
            return {'ok': False, 'error': f'Missing {kind} worker script: {script}'}
        idle = WARM_IDLE if idle_seconds is None else max(0, int(idle_seconds))
        threads = ASR_THREADS if kind == 'asr' else TTS_THREADS
        threads = max(1, min(threads, 8 if kind == 'asr' else 24))
        command = [str(python), str(script), '--start', '--socket', str(path),
                   '--idle-seconds', str(idle), '--threads', str(threads)]
        try:
            timeout = startup_timeout() + 15 if kind == 'tts' else 45
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            log_event('error', f'{kind} worker start failed', detail=str(error))
            return {'ok': False, 'error': f'{kind} worker start failed: {error}'}
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or '').strip()[-400:]
            log_event('error', f'{kind} worker start failed', detail=detail)
            return {'ok': False, 'error': f'{kind} worker start failed: {detail}'}
        status = worker_request(path, {'operation': 'status'}, timeout=3, quiet=True)
        running = status is not None
        if running and kind == 'tts':
            try:
                validate_backend(status)
            except (RuntimeError, ValueError) as error:
                log_event('error', 'TTS worker failed readiness validation', detail=str(error))
                return {'ok': False, 'error': str(error), 'socket': str(path)}
        if not running:
            log_event('error', f'{kind} worker did not answer after start', socket=str(path))
        return {'ok': running, 'state': 'started' if running else 'not running',
                'socket': str(path), 'output': (completed.stdout or '').strip()[-200:]}


def stop_worker(kind):
    path = asr_socket() if kind == 'asr' else tts_socket()
    # A missing worker is the normal outcome of stopping an idle service; it is
    # not a fallback warning and should not be logged as one.
    result = worker_request(path, {'operation': 'shutdown'}, timeout=5, quiet=True)
    if result is None:
        # Distinguish an idle-exited worker from one that is still answering but
        # rejected shutdown.
        alive = worker_request(path, {'operation': 'status'}, timeout=1, quiet=True)
        if alive is not None:
            return {'ok': False, 'stopping': False, 'state': 'stop rejected',
                    'socket': str(path)}
        return {'ok': True, 'stopping': False, 'state': 'not running',
                'socket': str(path)}
    stopped = True
    for _ in range(50):
        time.sleep(0.1)
        if worker_request(path, {'operation': 'status'}, timeout=0.3, quiet=True) is None:
            break
    else:
        stopped = False
    return {'ok': stopped, 'stopping': stopped, 'socket': str(path)}


def _encode_wav(payload, channels, width, rate):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(payload)
    return base64.b64encode(buffer.getvalue()).decode('ascii')


def _quietest_cut(raw, channels, width, rate, start_frame, end_frame):
    """Pick a low-energy point near the end so a chunk does not split a word."""
    if width != 2 or channels not in (1, 2):
        return end_frame
    frame_size = width * channels
    window = max(1, int(0.04 * rate))
    step = max(1, int(0.02 * rate))
    position = max(start_frame, end_frame - int(5 * rate))
    best = end_frame
    best_level = None
    while position + window <= end_frame:
        offset = position * frame_size
        samples = struct.unpack('<%dh' % (window * channels),
                                raw[offset:offset + window * frame_size])
        level = sum(sample * sample for sample in samples) / len(samples)
        if best_level is None or level < best_level:
            best_level = level
            best = position + window
        position += step
    if best_level is None or best < start_frame + int(0.5 * rate):
        return end_frame
    return best


def wav_chunks(path, limit=ASR_CHUNK_SECONDS):
    """Split a long recording into ASR-sized WAV chunks under the 32 s budget."""
    with wave.open(str(path), 'rb') as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.getnframes()
        duration = frames / float(rate) if rate else 0.0
        raw = wav.readframes(frames)
    if duration <= limit:
        yield base64.b64encode(path.read_bytes()).decode('ascii'), duration
        return
    frame_size = max(1, channels * width)
    block = max(1, int(limit * rate))
    position = 0
    while position < frames:
        end = min(frames, position + block)
        if end < frames:
            end = _quietest_cut(raw, channels, width, rate, position, end)
            if end <= position:
                end = min(frames, position + block)
        payload = raw[position * frame_size:end * frame_size]
        if payload:
            yield _encode_wav(payload, channels, width, rate), (end - position) / float(rate)
        position = end


def transcribe(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size <= 44:
        fail('The recording is missing or empty', path=str(path))
    temporary = path.parent == scratch_dir()
    started = time.monotonic()
    try:
        try:
            parts = list(wav_chunks(path))
        except (wave.Error, OSError) as error:
            fail('Could not read the recording: ' + str(error), path=str(path))
        texts = []
        confidences = []
        engine = ''
        warm_chunks = 0
        for index, (encoded, duration) in enumerate(parts):
            try:
                result = warm_transcribe(encoded)
                if result is not None:
                    warm_chunks += 1
                else:
                    result = cold_voice().transcribe(encoded)
                if not result:
                    raise ValueError('Local dictation returned no usable result')
            except Exception:
                log_event('error', 'transcription chunk failed', path=str(path),
                          chunk=index + 1, chunks=len(parts), seconds=round(duration, 2),
                          detail=traceback.format_exc()[-3000:])
                raise
            text = str(result.get('text') or '').strip()
            if text:
                texts.append(text)
            confidence = result.get('confidence')
            if isinstance(confidence, (int, float)) and confidence > 0:
                confidences.append(float(confidence))
            engine = engine or str(result.get('engine') or 'local ASR')
        merged = {
            'text': ' '.join(texts),
            'confidence': round(sum(confidences) / len(confidences), 4) if confidences else 0,
            'confidence_kind': 'Whisper mean word probability; heuristic, not calibrated accuracy',
            'engine': engine or 'local ASR',
            'raw_audio_retained': False,
        }
        log_event('info', 'transcribed', path=str(path), chunks=len(parts),
                  warm_chunks=warm_chunks,
                  seconds=round(sum(duration for _, duration in parts), 2),
                  elapsed=round(time.monotonic() - started, 2),
                  characters=len(merged['text']))
        if len(parts) > 1:
            merged['chunks'] = len(parts)
        return {'ok': True, **merged}
    except SystemExit:
        raise
    except Exception as error:
        fail('Local transcription failed: ' + str(error), detail=traceback.format_exc()[-4000:])
    finally:
        if temporary:
            try:
                path.unlink()
            except OSError:
                pass


def play(path):
    windows_path = to_windows(path) if audio_backend.PLATFORM == audio_backend.WSL2 else None
    try:
        command = audio_backend.playback_command(path, windows_path=windows_path)
    except audio_backend.AudioBackendError as error:
        fail(str(error))
    label = 'Windows' if audio_backend.PLATFORM == audio_backend.WSL2 else 'Local'
    try:
        code, out, err = run(command, timeout=60)
    except OSError as error:
        fail(label + ' playback failed: ' + str(error))
    if code != 0:
        fail(label + ' playback failed: ' + (err.strip() or out.strip()))
    result = {'ok': True, 'path': str(path)}
    if windows_path:
        result['windows_path'] = windows_path
    return result


def chunks(text, limit=420):
    clean = re.sub(r'```.*?```', ' ', text, flags=re.S)
    clean = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', clean)
    clean = re.sub(r'https?://\S+', '', clean)
    clean = re.sub(r'[*_`#>]', ' ', clean)
    clean = ' '.join(clean.split())
    result = []
    while clean:
        if len(result) >= MAX_SPEECH_CHUNKS:
            raise ValueError('Reply is too long to read aloud')
        if len(clean) <= limit:
            result.append(clean)
            break
        cut = max(clean.rfind('. ', 0, limit), clean.rfind('? ', 0, limit),
                  clean.rfind('! ', 0, limit))
        cut = cut + 1 if cut >= limit // 2 else clean.rfind(' ', 0, limit)
        if cut <= 0:
            cut = limit
        result.append(clean[:cut].strip())
        clean = clean[cut:].strip()
    return [chunk for chunk in result if chunk]


def say(text, voice_id='heart', should_play=True, start_chunk=0, progress_file=None):
    text = str(text or '').strip()
    if not text:
        fail('Nothing to speak')
    if requested_provider() != CPU_PROVIDER:
        # Wait for background GPU prewarming (or restore an idle-exited worker)
        # before requesting audio. Never silently switch an explicit GPU request
        # to the cold CPU service.
        started = start_worker('tts')
        if not started['ok']:
            fail(started.get('error', 'TTS worker stopped before becoming ready'))
    engine = None
    warmed = 0
    engines = set()
    providers = set()
    backends = {}
    gpu_verified = requested_provider() != CPU_PROVIDER
    spoken = 0
    parts = chunks(text)
    if not parts:
        fail('Nothing to speak after removing markup')
    start_chunk = max(0, int(start_chunk or 0))
    if start_chunk >= len(parts):
        fail('No speech remains after the last played chunk')
    directory = scratch_dir()
    engine_lock = threading.Lock()

    def synthesize(chunk):
        nonlocal engine
        result = warm_speak(chunk, voice_id)
        if result is not None:
            return result, True
        with engine_lock:
            if engine is None:
                engine = voice()
        return engine.speak(chunk, voice_id), False

    prefetch = None

    def start_prefetch(index, chunk):
        slot = {'index': index, 'done': threading.Event(),
                'result': None, 'warmed': False, 'error': None}

        def run():
            try:
                slot['result'], slot['warmed'] = synthesize(chunk)
            except BaseException as error:
                slot['error'] = error
            finally:
                slot['done'].set()

        threading.Thread(target=run, daemon=True).start()
        return slot

    try:
        for index, chunk in enumerate(parts):
            if index < start_chunk:
                continue
            if progress_file:
                try:
                    progress_path = Path(progress_file)
                    progress_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    progress_path.write_text(json.dumps({'chunk': index, 'chunks': len(parts)}))
                except OSError:
                    pass
            if prefetch is not None and prefetch['index'] == index:
                prefetch['done'].wait()
                if prefetch['error'] is not None:
                    raise prefetch['error']
                result = prefetch['result']
                was_warm = prefetch['warmed']
                prefetch = None
            else:
                result, was_warm = synthesize(chunk)
            if was_warm:
                warmed += 1
            if result.get('engine'):
                engines.add(result['engine'])
            providers.update(result.get('providers', []))
            gpu_verified = gpu_verified and result.get('gpu_execution_verified') is True
            if result.get('session_id'):
                backends[result['session_id']] = {
                    key: result[key] for key in (
                        'session_id', 'requested_provider', 'model', 'model_sha256',
                        'providers', 'gpu_execution_verified', 'execution_profile')
                    if key in result
                }
            # Synthesize the next chunk while this one plays. The worker still
            # serializes inference; playback is a separate process.
            if index + 1 < len(parts):
                prefetch = start_prefetch(index + 1, parts[index + 1])
            path = directory / ('tui-voice-reply-' + str(os.getpid()) + '-' + str(index) + '.wav')
            try:
                path.write_bytes(base64.b64decode(result['wav']))
                spoken += 1
                if should_play:
                    play(path)
            finally:
                # Also covers playback/SystemExit failures: no private audio is
                # left behind in the Windows temp directory.
                try:
                    path.unlink()
                except OSError:
                    pass
    except Exception as error:
        fail('Local speech failed: ' + str(error), traceback=traceback.format_exc())
    return {'ok': True, 'voice': voice_id, 'chunks': spoken, 'played': bool(should_play),
            'start': start_chunk, 'total': len(parts), 'warm_chunks': warmed,
            'engines': sorted(engines), 'providers': sorted(providers),
            'gpu_execution_verified': bool(spoken and gpu_verified),
            'backends': list(backends.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('devices')
    capture = sub.add_parser('record')
    capture.add_argument('--seconds', type=int, default=60)
    capture.add_argument('--out')
    capture.add_argument('--mic')
    capture.add_argument('--silence', type=float, default=1.5)
    capture.add_argument('--threshold', type=float, default=-35)
    capture.add_argument('--gain', type=float, default=0)

    live = sub.add_parser('dictate', help='stream local dictation as JSON lines')
    live.add_argument('--mic')
    live.add_argument('--threshold', type=float, default=-35)
    live.add_argument('--gain', type=float, default=0)

    transcribe_parser = sub.add_parser('transcribe')
    transcribe_parser.add_argument('--wav', required=True)

    play_parser = sub.add_parser('play')
    play_parser.add_argument('--wav', required=True)

    say_parser = sub.add_parser('say')
    say_parser.add_argument('--text', required=True)
    say_parser.add_argument('--voice', default='heart')
    say_parser.add_argument('--no-play', action='store_true')
    say_parser.add_argument('--start-chunk', type=int, default=0)
    say_parser.add_argument('--progress-file')

    warm_parser = sub.add_parser('warm', help='manage persistent warm ASR/TTS workers')
    warm_parser.add_argument('--start', action='store_true')
    warm_parser.add_argument('--stop', action='store_true')
    warm_parser.add_argument('--status', action='store_true')
    warm_parser.add_argument('--restart', action='store_true')
    warm_parser.add_argument('--kind', choices=('asr', 'tts', 'both'), default='both')
    warm_parser.add_argument('--idle-seconds', type=int)

    args = parser.parse_args()
    if args.command == 'warm':
        kinds = ('asr', 'tts') if args.kind == 'both' else (args.kind,)
        if args.status:
            output({'ok': True, 'workers': warm_status()})
        if args.stop or args.restart:
            stopped = {kind: stop_worker(kind) for kind in kinds}
            if args.stop:
                result = {'ok': all(item['ok'] for item in stopped.values()), 'stopped': stopped}
                output(result, 0 if result['ok'] else 1)
            if not all(item['ok'] for item in stopped.values()):
                result = {'ok': False, 'stopped': stopped,
                          'error': 'A worker did not stop; refusing to report a clean restart'}
                output(result, 1)
            time.sleep(0.2)
        started = {kind: start_worker(kind, args.idle_seconds) for kind in kinds}
        result = {'ok': all(item['ok'] for item in started.values()), 'started': started}
        output(result, 0 if result['ok'] else 1)
    if args.command == 'devices':
        names = devices()
        selected, _ = choose_mic()
        output({'ok': True, 'devices': names, 'selected': selected})
    elif args.command == 'record':
        output(record(args.seconds, args.out, args.mic, args.silence, args.threshold, args.gain))
    elif args.command == 'transcribe':
        output(transcribe(args.wav))
    elif args.command == 'dictate':
        output(dictate(args.mic, args.threshold, args.gain))
    elif args.command == 'play':
        output(play(args.wav))
    elif args.command == 'say':
        output(say(args.text, args.voice, not args.no_play, args.start_chunk, args.progress_file))


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as error:
        log_event('error', 'voice helper crashed', detail=traceback.format_exc()[-4000:])
        fail(error)
