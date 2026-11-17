"""Platform audio backend for tui-voice.

Owns every WSL2-versus-native-Linux difference: platform detection, ffmpeg
discovery, capture device enumeration and selection, ffmpeg input arguments,
output path translation, local playback, and the WAV scratch directory.

Stdlib only. Audio stays on this machine; no device is opened at import time.
"""
import glob
import json
import os
from collections import Counter, namedtuple
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import time

import voice_config

WSL2 = 'wsl2'
LINUX = 'linux'
CAPTURE_ENV = 'TUI_VOICE_CAPTURE'
CMD_ENV = 'TUI_VOICE_CMD'
POWERSHELL_ENV = 'TUI_VOICE_POWERSHELL'

# A capture source: technical id for ffmpeg, public description for the TUI.
Source = namedtuple('Source', 'id description kind monitor')

# Words that mark a virtual or monitored source rather than a real input.
VIRTUAL_WORDS = ('monitor', 'loopback', 'streammix', 'stream mix', 'stereo mix',
                 'wave out', 'what u hear', 'steam', 'virtual')
ALSA_ID = re.compile(r'^(?:plughw|hw):\d+,\d+$')
ARECORD_LINE = re.compile(
    r'^card\s+(\d+):\s*([^\[]*)\[([^\]]*)\],\s*device\s+(\d+):\s*([^\[]*)\[([^\]]*)\]')


class AudioBackendError(RuntimeError):
    """A local audio dependency or device could not be used."""


def _proc_version():
    try:
        return Path('/proc/version').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''


def in_container():
    """Docker/Podman marker files; a container has no Windows interop bridge."""
    return any(Path(marker).exists() for marker in ('/.dockerenv', '/run/.containerenv'))


def detect_platform(environ=None, proc_version=None, container=None):
    """Return 'wsl2' or 'linux' for this host.

    A container running on a WSL2 kernel reports "microsoft" in /proc/version
    but cannot reach Windows audio, so it is treated as native Linux.
    """
    environment = os.environ if environ is None else environ
    if environment.get('WSL_DISTRO_NAME') or environment.get('WSL_INTEROP'):
        return WSL2
    text = _proc_version() if proc_version is None else proc_version
    if 'microsoft' not in str(text).lower():
        return LINUX
    return LINUX if (in_container() if container is None else container) else WSL2


PLATFORM = detect_platform()


def _run(command, timeout=10, env=None):
    """Run a read-only probe; None means the tool is missing or failed."""
    try:
        return subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                              errors='replace', timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def _executable(value):
    return bool(value) and (Path(value).is_file() or shutil.which(value) is not None)


def _windows_to_wsl(value):
    """Convert a Windows path to its WSL mount path without calling wslpath."""
    match = re.fullmatch(r'([A-Za-z]):\\(.*)', str(value).strip())
    if not match:
        return None
    rest = match.group(2).replace('\\', '/')
    return '/mnt/' + match.group(1).lower() + '/' + rest


def windows_path(value, child_env=None):
    """Translate a Windows path to the local mount path (wslpath when present)."""
    completed = _run(['wslpath', '-u', str(value)], timeout=5, env=child_env)
    if completed is not None and completed.returncode == 0 and (completed.stdout or '').strip():
        return completed.stdout.strip()
    converted = _windows_to_wsl(value)
    if converted:
        return converted
    raise AudioBackendError('Could not translate a Windows path: ' + str(value))


def windows_cmd():
    """Windows cmd.exe for interop probes; only meaningful on WSL2."""
    override = os.environ.get(CMD_ENV)
    if override and _executable(override):
        return override
    return shutil.which('cmd.exe') or 'cmd.exe'


def _windows_variable(name, child_env=None):
    """Read a Windows environment variable through cmd.exe; None if unavailable."""
    completed = _run([windows_cmd(), '/c', 'echo', '%' + name + '%'], timeout=5,
                     env=child_env)
    if completed is None or completed.returncode != 0:
        return None
    value = (completed.stdout or '').strip()
    if not re.fullmatch(r'[A-Za-z]:\\.*', value):
        return None
    try:
        return windows_path(value, child_env=child_env)
    except AudioBackendError:
        return None


def powershell():
    """powershell.exe path for playback, resolved from the live Windows install."""
    override = os.environ.get(POWERSHELL_ENV)
    if override:
        if not _executable(override):
            raise AudioBackendError('TUI_VOICE_POWERSHELL is not executable: ' + override)
        return override
    root = _windows_variable('SystemRoot')
    if root:
        candidate = Path(root) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        if candidate.is_file():
            return str(candidate)
    raise AudioBackendError(
        'powershell.exe was not found; set TUI_VOICE_POWERSHELL to its full path')


def _where_exe(name):
    """First hit from Windows where.exe, translated for WSL."""
    completed = _run(['where.exe', name], timeout=5)
    if completed is None or completed.returncode != 0:
        return None
    lines = [line.strip() for line in (completed.stdout or '').splitlines() if line.strip()]
    if not lines:
        return None
    try:
        return windows_path(lines[0])
    except AudioBackendError:
        return None


def ffmpeg():
    """Locate ffmpeg for this platform (the system package is named ffmpeg)."""
    configured = voice_config.audio('ffmpeg')
    if PLATFORM == WSL2:
        if configured:
            if not Path(configured).is_file():
                raise AudioBackendError(
                    'TUI_VOICE_FFMPEG does not name an executable: ' + configured)
            return configured
        found = shutil.which('ffmpeg.exe') or _where_exe('ffmpeg.exe')
        if found:
            return found
        # Discover the real Windows profile instead of assuming drive C:.
        for base in (_windows_variable('LOCALAPPDATA'), _windows_variable('ProgramFiles')):
            if not base:
                continue
            for pattern in (base + '/Microsoft/WinGet/Links/ffmpeg.exe',
                            base + '/Microsoft/WinGet/Packages/Gyan.FFmpeg_*/ffmpeg-*/bin/ffmpeg.exe',
                            base + '/ffmpeg*/bin/ffmpeg.exe'):
                matches = sorted(glob.glob(pattern))
                if matches:
                    return matches[0]
        found = shutil.which('ffmpeg')
        if found:
            return found
        raise AudioBackendError('Windows ffmpeg was not found; set TUI_VOICE_FFMPEG')
    if configured:
        if not _executable(configured):
            raise AudioBackendError(
                'TUI_VOICE_FFMPEG does not name an executable: ' + configured)
        return configured
    found = shutil.which('ffmpeg')
    if found:
        return found
    raise AudioBackendError(
        'ffmpeg was not found; install the ffmpeg package (or set TUI_VOICE_FFMPEG)')


def _dshow_devices(child_env=None):
    command = [ffmpeg(), '-hide_banner', '-list_devices', 'true', '-f', 'dshow', '-i', 'dummy']
    for attempt in range(2):
        try:
            completed = subprocess.run(command, capture_output=True, text=True,
                                       encoding='utf-8', errors='replace',
                                       timeout=6, env=child_env)
            names = list(dict.fromkeys(
                re.findall(r'"([^"]+)"\s+\(audio\)',
                           (completed.stdout or '') + '\n' + (completed.stderr or ''))))
            if names:
                return names
        except (OSError, subprocess.TimeoutExpired):
            pass
        if not attempt:
            time.sleep(0.2)
    raise AudioBackendError(
        'Windows microphone discovery failed. Check the input device and WSL Windows interop.')


def _pactl_json_sources():
    completed = _run(['pactl', '-f', 'json', 'list', 'sources'])
    if completed is None or completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout or '[]')
    except ValueError:
        return None
    if not isinstance(payload, list):
        return None
    sources = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '').strip()
        if not name:
            continue
        description = str(item.get('description') or '').strip() or name
        properties = item.get('properties') if isinstance(item.get('properties'), dict) else {}
        monitor = (bool(str(item.get('monitor_source') or '').strip())
                   or str(properties.get('device.class') or '').strip() == 'monitor'
                   or description.lower().startswith('monitor of'))
        sources.append(Source(name, description, 'pulse', monitor))
    return sources


def _pactl_short_sources():
    completed = _run(['pactl', 'list', 'short', 'sources'])
    if completed is None or completed.returncode != 0:
        return None
    sources = []
    for line in (completed.stdout or '').splitlines():
        parts = line.split('\t')
        if len(parts) < 2:
            continue
        name = parts[1].strip()
        if not name:
            continue
        sources.append(Source(name, name, 'pulse', '.monitor' in name))
    return sources


def _default_source():
    """Best-effort id of the OS default input source; None when unavailable."""
    completed = _run(['pactl', 'get-default-source'])
    if completed is None or completed.returncode != 0:
        return None
    return (completed.stdout or '').strip() or None


def _alsa_sources():
    completed = _run(['arecord', '-l'])
    if completed is None or completed.returncode != 0:
        return None
    sources = []
    for line in (completed.stdout or '').splitlines():
        match = ARECORD_LINE.match(line.strip())
        if not match:
            continue
        card = int(match.group(1))
        card_name = (match.group(3) or match.group(2)).strip()
        device = int(match.group(4))
        device_name = match.group(5).strip()
        description = f'{card_name}: {device_name}' if card_name else device_name
        sources.append(Source(f'plughw:{card},{device}', description, 'alsa', False))
    return sources


def _linux_sources():
    """PulseAudio/PipeWire sources first, then ALSA hardware, then 'default'."""
    for loader in (_pactl_json_sources, _pactl_short_sources):
        sources = loader()
        if sources:
            return sources
    sources = _alsa_sources()
    if sources:
        return sources
    # No enumeration tool: capture 'default' through whichever server is live.
    return [Source('default', 'System default input',
                   'pulse' if _pulse_reachable() else 'alsa', False)]


def _registry(sources=None):
    """Public descriptions with deterministic, unique names."""
    entries = _linux_sources() if sources is None else list(sources)
    counts = Counter(entry.description for entry in entries)
    registry = []
    for entry in entries:
        name = entry.description
        if counts[entry.description] > 1:
            name = f'{entry.description} ({entry.id})'
        registry.append(entry._replace(description=name))
    return registry


def _match(value, entries):
    for entry in entries:
        if value == entry.description or value == entry.id:
            return entry
    lowered = str(value).lower()
    for entry in entries:
        if lowered in (entry.description.lower(), entry.id.lower()):
            return entry
    return None


def resolve_device(device, sources=None):
    """Map a public description (or raw id) to a capture Source.

    Raw technical ids pass through unchanged so an explicit settings value or
    TUI_VOICE_MIC keeps working when enumeration is temporarily incomplete.
    Enumeration here is best effort: capture never fails just because a listing
    query did.
    """
    value = str(device or '').strip()
    if PLATFORM == WSL2:
        return Source(value, value, 'dshow', False)
    if not value:
        value = 'default'
    if sources is None:
        try:
            entries = _linux_sources()
        except Exception:
            entries = [Source('default', 'System default input', 'pulse', False)]
    else:
        entries = list(sources)
    match = _match(value, _registry(entries))
    if match is not None:
        return match
    return Source(value, value, 'alsa' if ALSA_ID.match(value) else 'pulse', False)


def _is_virtual(source):
    text = f'{source.description} {source.id}'.lower()
    return source.monitor or any(word in text for word in VIRTUAL_WORDS)


def source_score(source, default_id=None):
    """Prefer the OS default hardware input, then a real hardware microphone.

    A virtual default (monitor, loopback, StreamMix) still loses to real
    hardware because the -20 virtual penalty outweighs the +4 default bonus.
    """
    text = f'{source.description} {source.id}'.lower()
    value = 0
    if _is_virtual(source):
        value -= 20
    if default_id and source.id == default_id:
        value += 4
    if 'focusrite' in text:
        value += 6
    if 'analogue 1 + 2' in text:
        value += 5
    if re.search(r'\bmic\b', text) or 'microphone' in text:
        value += 3
    if 'analog' in text or 'analogue' in text:
        value += 1
    if 'webcam' in text:
        value += 1
    return value


def _wsl_mic_score(name):
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


def devices(child_env=None):
    """Public input device names for the current platform."""
    if PLATFORM == WSL2:
        return _dshow_devices(child_env=child_env)
    return [entry.description for entry in _registry()]


def choose_mic(requested=None, environ=None, child_env=None):
    """Return (selected public name, all names); automatic picks a real input."""
    environment = os.environ if environ is None else environ
    if PLATFORM == WSL2:
        names = devices(child_env=child_env)
        if requested:
            if requested not in names:
                raise AudioBackendError('That microphone is not available: ' + requested)
            return requested, names
        preferred = voice_config.audio('mic', environment)
        if preferred and preferred in names:
            return preferred, names
        return max(names, key=_wsl_mic_score), names

    entries = _registry()
    names = [entry.description for entry in entries]
    if requested:
        if _match(requested, entries) is None:
            raise AudioBackendError('That microphone is not available: ' + requested)
        return requested, names
    preferred = voice_config.audio('mic', environment)
    if preferred:
        preferred_match = _match(preferred, entries)
        if preferred_match is not None:
            return preferred_match.description, names
    default_id = _default_source()
    selected = entries[0]
    for entry in entries[1:]:
        if source_score(entry, default_id) > source_score(selected, default_id):
            selected = entry
    return selected.description, names


def capture_args(device, streaming=False):
    """ffmpeg input arguments for one capture device."""
    if PLATFORM == WSL2:
        arguments = ['-f', 'dshow', '-rtbufsize', '64M']
        if streaming:
            arguments += ['-audio_buffer_size', '50']
        return arguments + ['-i', 'audio=' + str(device)]
    source = resolve_device(device)
    kind = os.environ.get(CAPTURE_ENV, '').strip().lower()
    if kind not in ('pulse', 'alsa'):
        kind = source.kind
    return ['-f', kind, '-i', source.id]


def output_path(path, child_env=None):
    """Path ffmpeg and the player use: a Windows path on WSL, local on Linux."""
    if PLATFORM != WSL2:
        return str(path)
    completed = _run(['wslpath', '-w', str(path)], timeout=10, env=child_env)
    if completed is None or completed.returncode != 0 or not (completed.stdout or '').strip():
        detail = '' if completed is None else (completed.stderr or '').strip()
        raise AudioBackendError(
            'Could not translate the audio path for Windows: ' + detail)
    return completed.stdout.strip()


def _pactl_available():
    completed = _run(['pactl', 'info'])
    return completed is not None and completed.returncode == 0


def _pulse_reachable(environ=None):
    """True when a PipeWire/PulseAudio server is reachable.

    The socket check keeps capture and playback working on minimal Debian
    installs where `pactl` (pulseaudio-utils) is not installed.
    """
    environment = os.environ if environ is None else environ
    if environment.get('PULSE_SERVER'):
        return True
    runtime = environment.get('XDG_RUNTIME_DIR')
    if runtime and (Path(runtime) / 'pulse/native').exists():
        return True
    return _pactl_available()


def playback_command(path, environ=None, windows_path=None):
    """Blocking local player command for one WAV file."""
    environment = os.environ if environ is None else environ
    if PLATFORM == WSL2:
        value = windows_path or output_path(path)
        escaped = value.replace("'", "''")
        return [powershell(), '-NoProfile', '-NonInteractive', '-Command',
                "(New-Object Media.SoundPlayer '" + escaped + "').PlaySync()"]
    override = voice_config.audio('player', environment)
    if override:
        try:
            parts = shlex.split(override)
        except ValueError as error:
            raise AudioBackendError(f'TUI_VOICE_PLAYER is not a valid command: {error}')
        if not parts:
            raise AudioBackendError('TUI_VOICE_PLAYER is empty; set a player command or unset it')
        if any('{file}' in part for part in parts):
            return [part.replace('{file}', str(path)) for part in parts]
        return parts + [str(path)]
    runtime = environment.get('XDG_RUNTIME_DIR')
    if shutil.which('pw-play') and runtime and (Path(runtime) / 'pipewire-0').exists():
        return ['pw-play', str(path)]
    if shutil.which('paplay') and _pulse_reachable(environment):
        return ['paplay', str(path)]
    if shutil.which('aplay'):
        return ['aplay', str(path)]
    if shutil.which('ffplay'):
        return ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'error', str(path)]
    raise AudioBackendError(
        'No local audio player was found; install PipeWire (pw-play), PulseAudio (paplay), '
        'ALSA (aplay), or ffmpeg (ffplay), or set TUI_VOICE_PLAYER')


def scratch_dir(environ=None):
    """Private local directory for temporary WAV files (native Linux)."""
    environment = os.environ if environ is None else environ
    runtime = environment.get('XDG_RUNTIME_DIR')
    if runtime:
        directory = Path(runtime) / 'tui-voice'
    else:
        directory = Path(tempfile.gettempdir()) / f'tui-voice-{os.getuid()}'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory
