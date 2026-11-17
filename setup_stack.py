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
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

import platform_deps
import voice_config

HERE = Path(__file__).resolve().parent
STACK = HERE / 'stack'
MANIFESTS = ('neural-models.json', 'asr-models.json')
ADAPTERS = {
    'voices.py': 'voice/voices.py',
    'asr_worker.py': 'voice/asr-worker.py',
    'neural-models.json': 'voice/neural-models.json',
    'asr-models.json': 'voice/asr-models.json',
    'web_voice.py': 'service/web_voice.py',
}
VENVS = (
    ('service/.venv', ('faster-whisper==1.2.1', 'kokoro-onnx==0.6.1',
                       'onnxruntime==1.29.0', 'numpy>=1.26')),
    ('voice/.venv', ('kokoro-onnx==0.6.1', 'onnxruntime==1.29.0', 'numpy>=1.26')),
    ('voice/.asr-venv', ('faster-whisper==1.2.1',)),
)
MARKER = '.tui-voice-ready'
# kokoro-onnx 0.6.1 publishes wheels for CPython 3.10..3.13 only.
SUPPORTED_PYTHON = ((3, 13), (3, 12), (3, 11), (3, 10))
USER_AGENT = 'tui-voice-setup/1.0'


class SetupError(RuntimeError):
    pass


def paths(root=None):
    if root:
        base = Path(root).expanduser().resolve()
        return base / 'service', base / 'voice'
    return voice_config.service(), voice_config.voice()


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


def status(root=None, verify=False):
    """Cheap by default: existence and size. verify=True also hashes models."""
    service, voice = paths(root)
    entries = model_entries()
    models = []
    for item in entries:
        path = voice / item['dir'] / item['file']
        row = {'file': str(path), 'present': path.is_file(),
               'bytes_ok': _size_ok(path, item['bytes'])}
        if verify and row['bytes_ok']:
            row['hash_ok'] = sha256(path) == item['sha256']
        models.append(row)
    adapters = []
    for name, relative in ADAPTERS.items():
        path = (service.parent / relative) if relative.startswith('service/') else voice.parent / relative
        adapters.append({'file': str(path), 'present': path.is_file()})
    venvs = []
    for relative, _ in VENVS:
        directory = (voice.parent / relative) if relative.startswith('voice/') else service.parent / relative
        python = directory / 'bin/python'
        version = venv_version(directory)
        venvs.append({'path': str(directory), 'python': python.is_file(),
                      'version': '.'.join(str(part) for part in version) if version else None,
                      'ready': python.is_file() and (
                          (directory / MARKER).is_file() or version in SUPPORTED_PYTHON)})
    missing = [row['file'] for row in models if not (row['present'] and row['bytes_ok'])]
    missing += [row['file'] for row in adapters if not row['present']]
    missing += [row['path'] for row in venvs if not row['ready']]
    if verify:
        missing += [row['file'] for row in models if row.get('hash_ok') is False]
    return {
        'ok': True,
        'ready': not missing,
        'root': {'service': str(service), 'voice': str(voice)},
        'models': models,
        'adapters': adapters,
        'venvs': venvs,
        'missing': missing,
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
    except (urllib.error.URLError, OSError, ValueError) as error:
        temporary.unlink(missing_ok=True)
        raise SetupError(f'Could not download {item["url"]}: {error}') from None
    print('', file=sys.stderr)
    if digest.hexdigest() != item['sha256']:
        temporary.unlink(missing_ok=True)
        raise SetupError(f'Downloaded {item["file"]} failed its SHA-256 check')
    os.chmod(temporary, 0o644)
    os.replace(temporary, destination)


def find_local(models_from, item):
    base = Path(models_from).expanduser()
    for candidate in (base / item['file'],
                      base / item['dir'] / item['file'],
                      base / Path(item['dir']).name / item['file']):
        if candidate.is_file():
            return candidate
    return None


def install_models(voice, models_from=None, force=False):
    installed = []
    for item in model_entries():
        destination = voice / item['dir'] / item['file']
        if (not force and _size_ok(destination, item['bytes'])
                and sha256(destination) == item['sha256']):
            installed.append({'file': str(destination), 'action': 'kept'})
            continue
        local = find_local(models_from, item) if models_from else None
        if local is not None:
            if destination.is_file() and sha256(local) != item['sha256']:
                raise SetupError(f'{local} does not match the pinned SHA-256')
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local, destination)
            action = f'copied from {local}'
        else:
            download(item, destination)
            action = 'downloaded'
        if sha256(destination) != item['sha256']:
            raise SetupError(f'{destination} failed its SHA-256 check')
        installed.append({'file': str(destination), 'action': action})
    return installed


def write_adapters(service, voice, force=False):
    written = []
    for name, relative in ADAPTERS.items():
        destination = Path(service.parent / relative) if relative.startswith('service/') \
            else Path(voice.parent / relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not force:
            written.append({'file': str(destination), 'action': 'kept'})
            continue
        shutil.copyfile(STACK / name, destination)
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


def create_venv(directory):
    """Create a virtualenv with pip, using every tool the host already has.

    Debian splits ensurepip into python3-venv, so `-m venv` can fail on a stock
    image. `virtualenv` and `uv venv --seed` both work without it, and the last
    resort asks the detected package manager for the missing package.
    """
    if _venv_ok(directory):
        return
    directory.parent.mkdir(parents=True, exist_ok=True)
    attempts = []

    def try_command(command, label):
        shutil.rmtree(directory, ignore_errors=True)
        completed = subprocess.run(command, capture_output=True, text=True, timeout=1800)
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
    if uv and try_command([uv, 'venv', '--seed', '--python', base or '3.12',
                           str(directory)], 'uv venv' + ('' if base else ' (downloads 3.12)')):
        return
    if base:
        installed = platform_deps.install(['venv'])
        if installed.get('ok') and try_command([base, '-m', 'venv', str(directory)],
                                               'venv after package install'):
            return
    raise SetupError('Could not create ' + str(directory) + '; tried '
                     + ' | '.join(attempts) + '. The speech models need Python '
                     + '3.10-3.13; install python3.12 and python3-venv, or uv, then '
                     'run this again.')


def install_venvs(service, voice, force=False):
    installed = []
    for relative, requirements in VENVS:
        directory = (voice.parent / relative) if relative.startswith('voice/') \
            else service.parent / relative
        marker = directory / MARKER
        if not force and marker.is_file() and _venv_ok(directory):
            # Our own completed install; a user-provided venv is only updated
            # when it is missing the pinned packages.
            installed.append({'venv': str(directory), 'action': 'kept'})
            continue
        create_venv(directory)
        python = directory / 'bin/python'
        command = [str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                   '--no-input', *requirements]
        print(f'  installing {relative} dependencies…', file=sys.stderr, flush=True)
        completed = subprocess.run(command, capture_output=True, text=True, timeout=1800)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or '').strip()[-500:]
            raise SetupError(f'pip install failed for {relative}: {detail}')
        marker.write_text('installed ' + time.strftime('%Y-%m-%dT%H:%M:%S') + '\n')
        os.chmod(marker, 0o644)
        installed.append({'venv': str(directory), 'action': 'installed'})
    return installed


def selftest(service, voice):
    checks = []
    for relative, module in (('voice/.asr-venv', 'faster_whisper'),
                             ('voice/.venv', 'kokoro_onnx'),
                             ('service/.venv', 'faster_whisper')):
        python = (voice.parent / relative / 'bin/python') if relative.startswith('voice/') \
            else service.parent / relative / 'bin/python'
        completed = subprocess.run([str(python), '-c', f'import {module}'],
                                   capture_output=True, text=True, timeout=120)
        checks.append({'venv': relative, 'import': module, 'ok': completed.returncode == 0})
    python = service.parent / 'service/.venv/bin/python'
    speak = ('import base64, web_voice; '
             "print(len(base64.b64decode(web_voice.WebVoice().speak('Local speech check.', 'heart')['wav'])))")
    completed = subprocess.run([str(python), '-c', speak], capture_output=True, text=True,
                               cwd=str(service), timeout=300)
    checks.append({'check': 'synthesize', 'ok': completed.returncode == 0,
                   'bytes': (completed.stdout or '').strip()[-12:]})
    return checks


def install(root=None, models_from=None, force=False, run_selftest=False):
    service, voice = paths(root)
    service.mkdir(parents=True, exist_ok=True)
    voice.mkdir(parents=True, exist_ok=True)
    os.chmod(service.parent, 0o700)
    adapters = write_adapters(service, voice, force=force)
    models = install_models(voice, models_from=models_from, force=force)
    venvs = install_venvs(service, voice, force=force)
    report = {'ok': True, 'root': {'service': str(service), 'voice': str(voice)},
              'adapters': adapters, 'models': models, 'venvs': venvs}
    if run_selftest:
        report['selftest'] = selftest(service, voice)
        report['ok'] = all(row['ok'] for row in report['selftest'])
    report['status'] = status(root)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Install the local Whisper/Kokoro stack for tui-voice.')
    parser.add_argument('--install', action='store_true', help='download models and create venvs')
    parser.add_argument('--root', help='install under this directory (service/, voice/)')
    parser.add_argument('--models-from', help='copy models from this directory instead of downloading')
    parser.add_argument('--force', action='store_true', help='rewrite adapters and reinstall venvs')
    parser.add_argument('--verify', action='store_true', help='hash every model file')
    parser.add_argument('--selftest', action='store_true', help='import both engines and synthesize once')
    parser.add_argument('--json', action='store_true', help='machine-readable output')
    args = parser.parse_args(argv)
    try:
        if args.install:
            report = install(root=args.root, models_from=args.models_from,
                             force=args.force, run_selftest=args.selftest)
        else:
            report = status(root=args.root, verify=args.verify)
    except SetupError as error:
        report = {'ok': False, 'error': str(error)}
    if args.json:
        print(json.dumps(report, indent=2))
    elif report.get('ok') is False:
        print(f'error: {report["error"]}', file=sys.stderr)
    else:
        root = report.get('root', {})
        print(f"service: {root.get('service')}\nvoice:   {root.get('voice')}")
        print('ready' if report.get('ready', report.get('status', {}).get('ready')) else
              f"missing: {len(report.get('status', report).get('missing', []))} item(s)")
        for item in report.get('status', report).get('missing', []):
            print('  -', item)
    return 0 if report.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
