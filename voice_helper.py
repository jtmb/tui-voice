#!/usr/bin/env python3
"""Local dictation helper for the tui-voice OpenCode plugin.

Records through the Windows microphone with ffmpeg, stops on silence, and
returns text from the existing local Whisper stack. No cloud service or TV.
"""
import argparse
import base64
import glob
import json
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave

SERVICE = Path(os.environ.get('TUI_VOICE_SERVICE', str(Path.home() / 'repos/fire-tv-next/lulu-launcher/service')))
POWERSHELL = '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
CMD = '/mnt/c/Windows/System32/cmd.exe'
_current = None


def output(payload, code=0):
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    raise SystemExit(code)


def fail(message):
    output({'ok': False, 'error': str(message)}, 1)


def stop_child(_signum=None, _frame=None):
    global _current
    if _current is not None and _current.poll() is None:
        try:
            _current.terminate()
        except OSError:
            pass
    raise SystemExit(1)


def request_stop(_signum=None, _frame=None):
    global _current
    if _current is not None and _current.poll() is None:
        try:
            if _current.stdin:
                _current.stdin.write('q')
                _current.stdin.flush()
        except (OSError, ValueError):
            try:
                _current.terminate()
            except OSError:
                pass


signal.signal(signal.SIGTERM, stop_child)
signal.signal(signal.SIGINT, stop_child)
signal.signal(signal.SIGUSR1, request_stop)


def run(args, timeout=60):
    global _current
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', errors='replace')
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


def windows_temp():
    code, out, _ = run([CMD, '/c', 'echo', '%TEMP%'], timeout=10)
    value = out.strip()
    match = re.fullmatch(r'([A-Za-z]):\\(.*)', value)
    if code != 0 or not match:
        fail('Could not find the Windows temporary directory')
    return Path('/mnt/' + match.group(1).lower() + '/' + match.group(2).replace('\\', '/'))


def to_windows(path):
    code, out, err = run(['wslpath', '-w', str(path)], timeout=10)
    if code != 0 or not out.strip():
        fail('Could not translate the audio path for Windows: ' + err.strip())
    return out.strip()


def ffmpeg():
    configured = os.environ.get('TUI_VOICE_FFMPEG')
    if configured and Path(configured).is_file():
        return configured
    patterns = [
        '/mnt/c/Users/*/AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg_*/ffmpeg-*/bin/ffmpeg.exe',
        '/mnt/c/Program Files/**/ffmpeg.exe',
        '/mnt/c/ffmpeg/bin/ffmpeg.exe',
    ]
    for pattern in patterns:
        matches = sorted(glob.glob(pattern, recursive=True))
        if matches:
            return matches[0]
    found = shutil.which('ffmpeg.exe') or shutil.which('ffmpeg')
    if found:
        return found
    fail('Windows ffmpeg was not found; set TUI_VOICE_FFMPEG')


def devices():
    code, out, err = run([ffmpeg(), '-hide_banner', '-list_devices', 'true',
                          '-f', 'dshow', '-i', 'dummy'], timeout=20)
    text = out + '\n' + err
    names = []
    for name in re.findall(r'"([^"]+)"\s+\(audio\)', text):
        if name not in names:
            names.append(name)
    if not names:
        fail('No Windows microphone was found (ffmpeg/dshow returned no audio devices)')
    return names


def choose_mic(requested=None):
    names = devices()
    if requested:
        if requested not in names:
            fail('That microphone is not available: ' + requested)
        return requested, names
    preferred = os.environ.get('TUI_VOICE_MIC')
    if preferred and preferred in names:
        return preferred, names

    def score(name):
        lower = name.lower()
        value = 0
        if 'stereo mix' in lower or 'wave out' in lower or 'what u hear' in lower:
            value -= 20
        if 'focusrite' in lower:
            value += 6
        if 'analogue 1 + 2' in lower:
            value += 5
        if 'microphone' in lower:
            value += 3
        if 'steam streaming' in lower:
            value -= 2
        return value

    selected = max(names, key=score)
    return selected, names


def record(seconds, path=None, mic=None, silence=0.9, threshold=-35, gain=0):
    device, names = choose_mic(mic)
    seconds = max(1, min(20, int(seconds)))
    if not path:
        path = windows_temp() / ('tui-voice-' + str(os.getpid()) + '.wav')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    windows_path = to_windows(path)
    command = [
        ffmpeg(), '-hide_banner', '-loglevel', 'info' if silence > 0 else 'error', '-f', 'dshow',
        '-rtbufsize', '64M', '-i', 'audio=' + device,
        '-t', str(seconds), '-ac', '1', '-ar', '16000', '-sample_fmt', 's16',
    ]
    filters = []
    if silence > 0:
        filters.append(f'silencedetect=noise={threshold}dB:d={silence}')
    if gain:
        filters.append(f'volume={gain}dB')
    if filters:
        command += ['-af', ','.join(filters)]
    command += ['-y', windows_path]

    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, text=True, bufsize=1,
                               encoding='utf-8', errors='replace')
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

    def read_levels():
        for line in process.stderr:
            match = re.search(r'silence_start:\s*([0-9.]+)', line)
            if match:
                at = float(match.group(1))
                # Ignore the initial silent lead-in; stop only after speech + pause.
                if at >= 0.8 and silence > 0:
                    with lock:
                        if timer['handle']:
                            timer['handle'].cancel()
                        handle = threading.Timer(silence, stop_after_silence)
                        handle.daemon = True
                        timer['handle'] = handle
                        handle.start()
            elif 'silence_end' in line:
                cancel_timer()

    reader = threading.Thread(target=read_levels, daemon=True)
    reader.start()

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
    if not path.is_file() or path.stat().st_size <= 44:
        fail('Microphone capture failed: ffmpeg returned no audio')
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
    return {'ok': True, 'path': str(path), 'windows_path': windows_path,
            'device': device, 'devices': names, 'seconds': seconds,
            'silence': silence, 'threshold': threshold, 'gain': gain,
            'rms': round(rms, 1), 'peak': peak, 'bytes': path.stat().st_size}


def voice():
    sys.path.insert(0, str(SERVICE))
    from web_voice import WebVoice
    return WebVoice()


def transcribe(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size <= 44:
        fail('The recording is missing or empty')
    temporary = path.parent == windows_temp()
    data = base64.b64encode(path.read_bytes()).decode('ascii')
    try:
        result = voice().transcribe(data)
    except Exception as error:
        fail('Local transcription failed: ' + str(error))
    finally:
        if temporary:
            try:
                path.unlink()
            except OSError:
                pass
    return {'ok': True, **result}


def play(path):
    windows_path = to_windows(path)
    escaped = windows_path.replace("'", "''")
    code, out, err = run([
        POWERSHELL, '-NoProfile', '-NonInteractive', '-Command',
        "(New-Object Media.SoundPlayer '" + escaped + "').PlaySync()",
    ], timeout=60)
    if code != 0:
        fail('Windows playback failed: ' + (err.strip() or out.strip()))
    return {'ok': True, 'path': str(path), 'windows_path': windows_path}


def chunks(text, limit=900):
    clean = re.sub(r'```.*?```', ' ', text, flags=re.S)
    clean = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', clean)
    clean = re.sub(r'https?://\S+', '', clean)
    clean = re.sub(r'[*_`#>]', ' ', clean)
    clean = ' '.join(clean.split())
    result = []
    while clean and len(result) < 40:
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


def say(text, voice_id='heart', should_play=True):
    text = str(text or '').strip()
    if not text:
        fail('Nothing to speak')
    engine = voice()
    directory = windows_temp()
    spoken = 0
    try:
        for index, chunk in enumerate(chunks(text)):
            result = engine.speak(chunk, voice_id)
            path = directory / ('tui-voice-reply-' + str(os.getpid()) + '-' + str(index) + '.wav')
            path.write_bytes(base64.b64decode(result['wav']))
            spoken += 1
            if should_play:
                play(path)
            try:
                path.unlink()
            except OSError:
                pass
    except Exception as error:
        fail('Local speech failed: ' + str(error))
    return {'ok': True, 'voice': voice_id, 'chunks': spoken, 'played': bool(should_play)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('devices')
    capture = sub.add_parser('record')
    capture.add_argument('--seconds', type=int, default=7)
    capture.add_argument('--out')
    capture.add_argument('--mic')
    capture.add_argument('--silence', type=float, default=0.9)
    capture.add_argument('--threshold', type=float, default=-35)
    capture.add_argument('--gain', type=float, default=0)

    transcribe_parser = sub.add_parser('transcribe')
    transcribe_parser.add_argument('--wav', required=True)

    play_parser = sub.add_parser('play')
    play_parser.add_argument('--wav', required=True)

    say_parser = sub.add_parser('say')
    say_parser.add_argument('--text', required=True)
    say_parser.add_argument('--voice', default='heart')
    say_parser.add_argument('--no-play', action='store_true')

    args = parser.parse_args()
    if args.command == 'devices':
        names = devices()
        selected, _ = choose_mic()
        output({'ok': True, 'devices': names, 'selected': selected})
    elif args.command == 'record':
        output(record(args.seconds, args.out, args.mic, args.silence, args.threshold, args.gain))
    elif args.command == 'transcribe':
        output(transcribe(args.wav))
    elif args.command == 'play':
        output(play(args.wav))
    elif args.command == 'say':
        output(say(args.text, args.voice, not args.no_play))


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as error:
        fail(error)
