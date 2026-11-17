#!/usr/bin/env python3
"""Install the local Whisper/Kokoro speech stack that tui-voice drives.

Users should not have to find models or wire virtualenvs. This script detects
the configured stack location, downloads the pinned models with SHA-256 checks,
creates the three virtualenvs, and writes the small adapter modules.

  python3 setup_stack.py                 # status
  python3 setup_stack.py --install       # download, create venvs, write adapters
  python3 setup_stack.py --install --models-from DIR   # copy existing models
  python3 setup_stack.py --install --root DIR          # install elsewhere (testing)

Everything stays on this machine; no account or API key is used.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

import platform_deps
import voice_config

HERE = Path(__file__).resolve().parent
STACK = HERE / 'stack'
MANIFESTS = ('neural-models.json', 'asr-models.json')
# (source in stack/, which configured folder it lives in, installed file name)
ADAPTERS = (
    ('voices.py', 'voice', 'voices.py'),
    ('asr_worker.py', 'voice', 'asr-worker.py'),
    ('neural-models.json', 'voice', 'neural-models.json'),
    ('asr-models.json', 'voice', 'asr-models.json'),
    ('web_voice.py', 'service', 'web_voice.py'),
)
# faster-whisper's audio stack is pinned too: an unpinned PyAV drifted to a release
# whose open() rejected faster-whisper 1.2.1's arguments, so every transcription failed.
WHISPER = ('faster-whisper==1.2.1', 'ctranslate2==4.8.5', 'tokenizers==0.23.3', 'av==16.0.1')
# (configured folder, venv directory name, interpreter setting, pinned requirements)
VENVS = (
    ('service', '.venv', 'python', (*WHISPER, 'kokoro-onnx==0.6.1',
                                    'onnxruntime==1.29.0', 'numpy>=1.26')),
    ('voice', '.venv', 'tts_python', ('kokoro-onnx==0.6.1', 'onnxruntime==1.29.0',
                                      'numpy>=1.26')),
    ('voice', '.asr-venv', 'asr_python', WHISPER),
)
# JSON written only after a successful pip install: {"requirements", "python", "installed"}.
MARKER = '.tui-voice-ready'
# kokoro-onnx 0.6.1 publishes wheels for CPython 3.10..3.13 only.
SUPPORTED_PYTHON = ((3, 13), (3, 12), (3, 11), (3, 10))
USER_AGENT = 'tui-voice-setup/1.0'
# Standalone uv, used only when the host has no CPython 3.10-3.13. It can fetch
# CPython itself, so a distribution that ships only 3.14+ still works.
UV_VERSION = '0.11.31'
UV_ARCHIVES = {
    'x86_64': ('uv-x86_64-unknown-linux-gnu.tar.gz',
               '8cc1cd82d434ec565376f98bd938d4b715b5791a80ff2d3aa78821cf85091b4b'),
    'aarch64': ('uv-aarch64-unknown-linux-gnu.tar.gz',
                'd74f23949fd07be4970f293d06ca99d87cd2a78a341c3d7b7fc0df7bc2d8a145'),
}
UV_URL = 'https://github.com/astral-sh/uv/releases/download/{version}/{name}'


class SetupError(RuntimeError):
    pass


def paths(root=None):
    if root:
        base = Path(root).expanduser().resolve()
        return base / 'service', base / 'voice'
    return voice_config.service(), voice_config.voice()


def folder(kind, service, voice):
    return service if kind == 'service' else voice


def venv_label(entry):
    return f'{entry[0]}/{entry[1]}'


def venv_dir(entry, service, voice):
    return folder(entry[0], service, voice) / entry[1]


def external_python(entry, root=None):
    """Interpreter the user configured for this slot, or None.

    An explicit ``--root`` is a self-contained install, so overrides are ignored.
    """
    if root:
        return None
    return voice_config.get(entry[2])


def managed(service, voice, root=None):
    """True when the plugin owns both folders (explicit root or its data home)."""
    if root:
        return True
    home = voice_config.data_home().resolve()
    for path in (service, voice):
        try:
            Path(path).resolve().relative_to(home)
        except ValueError:
            return False
    return True


def log_event(level, message, **fields):
    """Append one JSON line to the shared voice log; never raises."""
    try:
        directory = Path(os.environ.get('TUI_VOICE_LOG_DIR') or (
            Path(os.environ.get('XDG_STATE_HOME') or (Path.home() / '.local/state'))
            / 'tui-voice'))
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        record = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'pid': os.getpid(),
                  'level': level, 'source': 'setup_stack', 'message': str(message)}
        record.update({key: value for key, value in fields.items() if value is not None})
        with open(directory / 'voice.log', 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    except (OSError, ValueError, TypeError):
        pass


@contextmanager
def install_lock():
    """Non-blocking per-user lock; yields False when another install holds it."""
    directory = Path(tempfile.gettempdir()) / f'tui-voice-{os.getuid()}'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle = open(directory / 'stack.lock', 'a+')
    acquired = False
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            acquired = False
        yield acquired
    finally:
        if acquired:
            fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def platform_check(libc=None, machine=None, musl_loaders=None):
    """Reason the pinned speech wheels cannot install on this host, or None."""
    if sys.platform != 'linux':
        return None
    arch = (machine or platform.machine()).lower()
    if arch not in ('x86_64', 'amd64', 'aarch64', 'arm64'):
        return (f'No prebuilt speech packages for {arch}; the pinned onnxruntime and '
                'ctranslate2 wheels cover x86_64 and aarch64 only.')
    if musl_loaders is None:
        musl_loaders = [path for pattern in ('/lib/ld-musl-*', '/usr/lib/ld-musl-*')
                        for path in Path('/').glob(pattern.lstrip('/'))]
    if musl_loaders:
        return ('This distribution uses musl libc (for example Alpine); the pinned '
                'onnxruntime and ctranslate2 wheels need glibc 2.28+. Use a glibc '
                'distribution, or point tui-voice at your own speech stack.')
    name, version = libc if libc is not None else platform.libc_ver()
    if name == 'glibc':
        try:
            numbers = tuple(int(part) for part in version.split('.')[:2])
        except ValueError:
            return None
        if numbers < (2, 28):
            return (f'glibc {version} is older than 2.28, which the pinned speech '
                    'wheels require. Use a newer distribution or your own stack.')
    return None


def model_entries():
    entries = []
    for name in MANIFESTS:
        for item in json.loads((STACK / name).read_text(encoding='utf-8')):
            entries.append(item)
    return entries


def _size_ok(path, expected):
    try:
        return path.is_file() and path.stat().st_size == int(expected)
    except OSError:
        return False


def venv_version(directory):
    """CPython version recorded in pyvenv.cfg, or None."""
    try:
        text = (Path(directory) / 'pyvenv.cfg').read_text(encoding='utf-8')
    except OSError:
        return None
    for line in text.splitlines():
        key, _, value = line.partition('=')
        if key.strip() in ('version_info', 'version'):
            try:
                parts = value.strip().split('.')
                return (int(parts[0]), int(parts[1]))
            except (IndexError, ValueError):
                continue
    return None


def read_marker(directory):
    """Parsed install marker, or None when absent or unreadable."""
    try:
        data = json.loads((Path(directory) / MARKER).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def marker_matches(directory, requirements):
    marker = read_marker(directory)
    return bool(marker) and marker.get('requirements') == list(requirements)


def venv_ready(directory, requirements, owned):
    """Whether one virtualenv is usable without touching it."""
    python = Path(directory) / 'bin/python'
    version = venv_version(directory)
    if not python.is_file() or version not in SUPPORTED_PYTHON:
        return False
    if read_marker(directory) is not None or owned:
        return marker_matches(directory, requirements)
    return True  # a user-provided venv is trusted as it is


def interpreter_exists(value):
    return bool(value) and (Path(value).is_file() or shutil.which(value) is not None)


def status(root=None, verify=False):
    """Cheap by default: existence and size. verify=True also hashes models."""
    service, voice = paths(root)
    owned = managed(service, voice, root)
    models = []
    for item in model_entries():
        path = voice / item['dir'] / item['file']
        row = {'file': str(path), 'present': path.is_file(),
               'bytes_ok': _size_ok(path, item['bytes']), 'bytes': int(item['bytes'])}
        if verify and row['bytes_ok']:
            row['hash_ok'] = sha256(path) == item['sha256']
        models.append(row)
    adapters = []
    for _source, kind, name in ADAPTERS:
        path = folder(kind, service, voice) / name
        adapters.append({'file': str(path), 'present': path.is_file()})
    venvs = []
    for entry in VENVS:
        directory = venv_dir(entry, service, voice)
        external = external_python(entry, root)
        if external:
            venvs.append({'path': external, 'python': interpreter_exists(external),
                          'version': None, 'external': True,
                          'ready': interpreter_exists(external)})
            continue
        version = venv_version(directory)
        venvs.append({'path': str(directory), 'python': (directory / 'bin/python').is_file(),
                      'version': '.'.join(str(part) for part in version) if version else None,
                      'external': False,
                      'ready': venv_ready(directory, entry[3], owned)})
    missing = [row['file'] for row in models if not (row['present'] and row['bytes_ok'])]
    missing += [row['file'] for row in adapters if not row['present']]
    missing += [row['path'] for row in venvs if not row['ready']]
    if verify:
        missing += [row['file'] for row in models if row.get('hash_ok') is False]
    reason = platform_check()
    return {
        'ok': True,
        'ready': not missing,
        'supported': reason is None,
        'reason': reason,
        'managed': owned,
        'root': {'service': str(service), 'voice': str(voice)},
        'models': models,
        'adapters': adapters,
        'venvs': venvs,
        'missing': missing,
        'download_bytes': sum(row['bytes'] for row in models
                              if not (row['present'] and row['bytes_ok'])),
        'fix': [sys.executable, str(HERE / 'setup_stack.py'), '--install'],
        'verified': bool(verify),
    }


def sha256(path, block=1024 * 1024):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(block), b''):
            digest.update(chunk)
    return digest.hexdigest()


_PROGRESS = {}


def _progress(label, done, total):
    """Print at most twenty progress lines per file."""
    if not total:
        return
    step = max(1, int(total) // 20)
    if done < total and done - _PROGRESS.get(label, 0) < step:
        return
    _PROGRESS[label] = done
    percent = min(100, int(done * 100 / total))
    print(f'  {label}: {percent:3d}%  {done / 1e6:7.1f} / {total / 1e6:.1f} MB',
          file=sys.stderr, flush=True)


def download(item, destination, timeout=60):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.part')
    digest = hashlib.sha256()
    done = 0
    request = urllib.request.Request(item['url'], headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, \
                temporary.open('wb') as output:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                output.write(block)
                digest.update(block)
                done += len(block)
                _progress(item['file'], done, int(item['bytes']))
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as error:
        temporary.unlink(missing_ok=True)
        raise SetupError(f'Could not download {item["url"]}: {error}') from None
    print('', file=sys.stderr)
    if digest.hexdigest() != item['sha256']:
        temporary.unlink(missing_ok=True)
        raise SetupError(f'Downloaded {item["file"]} failed its SHA-256 check')
    os.chmod(temporary, 0o644)
    os.replace(temporary, destination)


def find_local(models_from, item):
    """Every candidate copy of one model under a user-supplied directory."""
    base = Path(models_from).expanduser()
    found = []
    for candidate in (base / item['file'],
                      base / item['dir'] / item['file'],
                      base / Path(item['dir']).name / item['file']):
        if candidate.is_file() and candidate not in found:
            found.append(candidate)
    return found


def copy_verified(source, destination, expected):
    """Copy via a .part file; the destination only ever holds a verified file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.part')
    try:
        shutil.copyfile(source, temporary)
        if sha256(temporary) != expected:
            return False
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
        return True
    except OSError as error:
        raise SetupError(f'Could not copy {source}: {error}') from None
    finally:
        temporary.unlink(missing_ok=True)


def install_models(voice, models_from=None, force=False):
    installed = []
    for item in model_entries():
        destination = voice / item['dir'] / item['file']
        if (not force and _size_ok(destination, item['bytes'])
                and sha256(destination) == item['sha256']):
            installed.append({'file': str(destination), 'action': 'kept'})
            continue
        action = None
        rejected = []
        for candidate in (find_local(models_from, item) if models_from else []):
            if copy_verified(candidate, destination, item['sha256']):
                action = f'copied from {candidate}'
                break
            rejected.append(str(candidate))
        if action is None:
            download(item, destination)
            action = 'downloaded'
            if rejected:
                action += ' (local copy failed SHA-256: ' + ', '.join(rejected) + ')'
        if sha256(destination) != item['sha256']:
            raise SetupError(f'{destination} failed its SHA-256 check')
        installed.append({'file': str(destination), 'action': action})
    return installed


def write_adapters(service, voice, force=False):
    written = []
    for source, kind, name in ADAPTERS:
        destination = folder(kind, service, voice) / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not force:
            written.append({'file': str(destination), 'action': 'kept'})
            continue
        shutil.copyfile(STACK / source, destination)
        os.chmod(destination, 0o644)
        written.append({'file': str(destination), 'action': 'written'})
    return written


def compatible_python():
    """Interpreter that can install the pinned speech packages, or None."""
    if sys.version_info[:2] in SUPPORTED_PYTHON:
        return sys.executable
    for minor in (13, 12, 11, 10):
        path = shutil.which(f'python3.{minor}')
        if path:
            return path
    return None


def bootstrap_uv():
    """Download the pinned standalone uv into the plugin data directory.

    Returns its path, or raises SetupError. The archive is SHA-256 verified.
    """
    machine = platform.machine().lower()
    name, digest = UV_ARCHIVES.get('x86_64' if machine in ('x86_64', 'amd64') else
                                   'aarch64' if machine in ('aarch64', 'arm64') else '',
                                   (None, None))
    if not name:
        raise SetupError(f'No pinned uv build for {machine}')
    directory = voice_config.data_home() / 'tools' / f'uv-{UV_VERSION}'
    target = directory / 'uv'
    if target.is_file() and os.access(target, os.X_OK):
        return str(target)
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / name
    item = {'file': name, 'bytes': 0, 'sha256': digest,
            'url': UV_URL.format(version=UV_VERSION, name=name)}
    print(f'  downloading uv {UV_VERSION} (needed to provide a compatible Python)…',
          file=sys.stderr, flush=True)
    try:
        download(item, archive)
        with tarfile.open(archive) as bundle:
            member = next((entry for entry in bundle.getmembers()
                           if entry.isfile() and Path(entry.name).name == 'uv'), None)
            if member is None:
                raise SetupError('The uv archive did not contain a uv binary')
            source = bundle.extractfile(member)
            temporary = target.with_name('uv.part')
            with temporary.open('wb') as output:
                shutil.copyfileobj(source, output)
        os.chmod(temporary, 0o755)
        os.replace(temporary, target)
    except (tarfile.TarError, OSError) as error:
        raise SetupError(f'Could not unpack uv: {error}') from None
    finally:
        archive.unlink(missing_ok=True)
    return str(target)


def _venv_ok(directory):
    """A venv is usable only when its interpreter is supported and has pip."""
    python = directory / 'bin/python'
    if not python.is_file():
        return False
    try:
        probe = subprocess.run(
            [str(python), '-c', 'import sys; print(*sys.version_info[:2])'],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if probe.returncode != 0:
        return False
    try:
        version = tuple(int(part) for part in (probe.stdout or '').split()[:2])
    except ValueError:
        return False
    if version not in SUPPORTED_PYTHON:
        return False
    try:
        pip = subprocess.run([str(python), '-m', 'pip', '--version'],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return pip.returncode == 0


def _has_content(directory):
    try:
        return directory.is_dir() and any(directory.iterdir())
    except OSError:
        return True


def create_venv(directory, replace=False):
    """Create a virtualenv with pip, using every tool the host already has.

    Debian splits ensurepip into python3-venv, so `-m venv` can fail on a stock
    image. `virtualenv` and `uv venv --seed` both work without it, and the last
    resort asks the detected package manager for the missing package.

    Existing content is only ever removed with ``replace=True`` (a venv this
    plugin owns); otherwise a non-empty directory is an error.
    """
    if _venv_ok(directory):
        return
    if _has_content(directory) and not replace:
        raise SetupError(f'{directory} already exists and is not a usable virtualenv; '
                         'refusing to replace it')
    directory.parent.mkdir(parents=True, exist_ok=True)
    attempts = []

    def try_command(command, label):
        shutil.rmtree(directory, ignore_errors=True)
        try:
            completed = subprocess.run(command, capture_output=True, text=True,
                                       timeout=1800)
        except (OSError, subprocess.TimeoutExpired) as error:
            attempts.append(f'{label}: {type(error).__name__}: {str(error)[:150]}')
            return False
        if completed.returncode == 0 and _venv_ok(directory):
            return True
        detail = (completed.stderr or completed.stdout or '').strip()[-200:]
        attempts.append(f'{label}: {detail or "exit " + str(completed.returncode)}')
        return False

    base = compatible_python()
    if base:
        if try_command([base, '-m', 'venv', str(directory)], 'venv'):
            return
        if try_command([base, '-m', 'virtualenv', str(directory)], 'virtualenv'):
            return
    uv = shutil.which('uv')
    if uv is None and base is None:
        try:
            uv = bootstrap_uv()
        except SetupError as error:
            attempts.append(f'uv bootstrap: {error}')
    if uv and try_command([uv, 'venv', '--seed', '--python', base or '3.12',
                           str(directory)], 'uv venv' + ('' if base else ' (downloads 3.12)')):
        return
    if base:
        installed = platform_deps.install(['venv'], python=base)
        if installed.get('ok') and try_command([base, '-m', 'venv', str(directory)],
                                               'venv after package install'):
            return
        if installed.get('commands') and not installed.get('ok'):
            attempts.append('package install: ' + ' '.join(installed['commands'][0]))
    raise SetupError('Could not create ' + str(directory) + '; tried '
                     + (' | '.join(attempts) or 'no usable Python 3.10-3.13 or uv')
                     + '. The speech models need Python '
                     + '3.10-3.13; install python3.12 and python3-venv, or uv, then '
                     'run this again.')


def install_venvs(service, voice, force=False, owned=False, root=None):
    """Create and fill the virtualenvs.

    ``owned`` marks a plugin-managed stack. In a user-provided stack an existing
    virtualenv is never deleted or modified unless ``force`` is set.
    """
    installed = []
    for entry in VENVS:
        label = venv_label(entry)
        requirements = entry[3]
        external = external_python(entry, root)
        if external:
            installed.append({'venv': external, 'action': 'external'})
            continue
        directory = venv_dir(entry, service, voice)
        ours = owned or force or read_marker(directory) is not None
        if _has_content(directory) and not ours:
            if venv_ready(directory, requirements, False):
                installed.append({'venv': str(directory), 'action': 'user-owned'})
                continue
            raise SetupError(
                f'{directory} is a user-provided virtualenv that is not usable '
                f'(needs CPython 3.10-3.13 with pip). Fix or remove it, or rerun with '
                '--force-user-stack to let tui-voice replace it.')
        if not force and venv_ready(directory, requirements, True) and _venv_ok(directory):
            installed.append({'venv': str(directory), 'action': 'kept'})
            continue
        create_venv(directory, replace=ours)
        marker = directory / MARKER
        marker.unlink(missing_ok=True)  # stale until pip finishes
        python = directory / 'bin/python'
        command = [str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                   '--no-input', *requirements]
        print(f'  installing {label} dependencies…', file=sys.stderr, flush=True)
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=1800)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SetupError(f'pip install failed for {label}: {error}') from None
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or '').strip()[-500:]
            raise SetupError(f'pip install failed for {label}: {detail}')
        version = venv_version(directory)
        marker.write_text(json.dumps({
            'requirements': list(requirements),
            'python': '.'.join(str(part) for part in version) if version else None,
            'installed': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }) + '\n')
        os.chmod(marker, 0o644)
        installed.append({'venv': str(directory), 'action': 'installed'})
    return installed


def selftest(service, voice, root=None):
    environment = {**os.environ, 'TUI_VOICE_SERVICE': str(service),
                   'TUI_VOICE_VOICE_DIR': str(voice)}
    checks = []

    def python_for(entry):
        return external_python(entry, root) or str(venv_dir(entry, service, voice) / 'bin/python')

    for entry, module in ((VENVS[2], 'faster_whisper'), (VENVS[1], 'kokoro_onnx'),
                          (VENVS[0], 'faster_whisper')):
        try:
            completed = subprocess.run([python_for(entry), '-c', f'import {module}'],
                                       capture_output=True, text=True, timeout=120,
                                       env=environment)
            ok = completed.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        checks.append({'venv': venv_label(entry), 'import': module, 'ok': ok})
    speak = ('import base64, web_voice; '
             "print(len(base64.b64decode(web_voice.WebVoice().speak('Local speech check.', 'heart')['wav'])))")
    try:
        completed = subprocess.run([python_for(VENVS[0]), '-c', speak], capture_output=True,
                                   text=True, cwd=str(service), timeout=300, env=environment)
        ok, size = completed.returncode == 0, (completed.stdout or '').strip()[-12:]
    except (OSError, subprocess.TimeoutExpired):
        ok, size = False, ''
    checks.append({'check': 'synthesize', 'ok': ok, 'bytes': size})
    return checks


def _prepare_directories(service, voice, owned, root):
    """Create the stack folders; only tighten permissions on plugin-owned ones."""
    try:
        if owned:
            top = Path(root).expanduser().resolve() if root else voice_config.data_home()
            created = not top.exists()
            top.mkdir(parents=True, exist_ok=True)
            if created:
                os.chmod(top, 0o700)
        service.mkdir(parents=True, exist_ok=True)
        voice.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SetupError(f'Cannot create the speech stack folders '
                         f'({error.filename or service}): {error.strerror or error}') from None


def install(root=None, models_from=None, force=False, run_selftest=False,
            force_user_stack=False):
    reason = platform_check()
    if reason:
        raise SetupError(reason)
    service, voice = paths(root)
    with install_lock() as acquired:
        if not acquired:
            return {'ok': True, 'state': 'busy',
                    'message': 'Another tui-voice install is already running'}
        owned = managed(service, voice, root)
        log_event('info', 'speech install started', service=str(service), voice=str(voice),
                  managed=owned)
        _prepare_directories(service, voice, owned, root)
        rewrite = force and (owned or force_user_stack)
        adapters = write_adapters(service, voice, force=rewrite)
        models = install_models(voice, models_from=models_from, force=rewrite)
        venvs = install_venvs(service, voice, force=rewrite, owned=owned, root=root)
        report = {'ok': True, 'state': 'done',
                  'root': {'service': str(service), 'voice': str(voice)},
                  'adapters': adapters, 'models': models, 'venvs': venvs}
        if run_selftest:
            report['selftest'] = selftest(service, voice, root)
            report['ok'] = all(row['ok'] for row in report['selftest'])
            if not report['ok']:
                failed = [row.get('import') or row.get('check')
                          for row in report['selftest'] if not row['ok']]
                report['error'] = 'selftest failed: ' + ', '.join(failed)
        report['status'] = status(root)
        log_event('info' if report['ok'] else 'error', 'speech install finished',
                  ready=report['status']['ready'], error=report.get('error'))
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Install the local Whisper/Kokoro stack for tui-voice.')
    parser.add_argument('--install', action='store_true', help='download models and create venvs')
    parser.add_argument('--root', help='install under this directory (service/, voice/)')
    parser.add_argument('--models-from', help='copy models from this directory instead of downloading')
    parser.add_argument('--force', action='store_true', help='rewrite adapters and reinstall venvs')
    parser.add_argument('--force-user-stack', action='store_true',
                        help='allow --force to modify a stack outside the plugin data directory')
    parser.add_argument('--verify', action='store_true', help='hash every model file')
    parser.add_argument('--selftest', action='store_true', help='import both engines and synthesize once')
    parser.add_argument('--json', action='store_true', help='machine-readable output')
    args = parser.parse_args(argv)
    try:
        if args.install:
            report = install(root=args.root, models_from=args.models_from,
                             force=args.force, run_selftest=args.selftest,
                             force_user_stack=args.force_user_stack)
        else:
            report = status(root=args.root, verify=args.verify)
    except SetupError as error:
        log_event('error', 'speech install failed', detail=str(error)[-1500:])
        report = {'ok': False, 'error': str(error)}
    if args.json:
        print(json.dumps(report, indent=2))
    elif report.get('ok') is False:
        print(f'error: {report.get("error", "install failed")}', file=sys.stderr)
    elif report.get('state') == 'busy':
        print(report['message'])
    else:
        info = report.get('status', report)
        root = report.get('root', {})
        print(f"service: {root.get('service')}\nvoice:   {root.get('voice')}")
        print('ready' if info.get('ready') else f"missing: {len(info.get('missing', []))} item(s)")
        for item in info.get('missing', []):
            print('  -', item)
    return 0 if report.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
