"""Detect the host OS and install the audio packages the plugin needs.

The plugin must work on a fresh Debian (or Fedora, Arch, openSUSE, Alpine or
WSL2) machine without the user reading a package table first. This module maps
the detected distribution to its package manager, checks the small allow-list of
audio dependencies, and installs the missing ones non-interactively.

Only packages from this allow-list are ever installed, always by argv (never a
shell string), always with the distribution's non-interactive flags. When
privileges are unavailable the exact command is returned instead of failing
silently. Nothing is installed when ``TUI_VOICE_AUTO_INSTALL=0``.

Stdlib only; importing this module runs no command.
"""
from __future__ import annotations

from collections import namedtuple
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import audio_backend

AUTO_INSTALL_ENV = 'TUI_VOICE_AUTO_INSTALL'

OSInfo = namedtuple('OSInfo', 'family name version')
Requirement = namedtuple('Requirement', 'key label required reason')

REQUIREMENTS = (
    Requirement('ffmpeg', 'ffmpeg', True, 'microphone capture and local playback'),
    Requirement('pactl', 'pactl', False, 'descriptive device list'),
    Requirement('arecord', 'arecord', False, 'ALSA fallback device list'),
)

# family -> (manager, install prefix, refresh command, package per requirement)
FAMILIES = {
    'debian': ('apt-get', ['apt-get', 'install', '-y', '--no-install-recommends'],
               ['apt-get', 'update'],
               {'ffmpeg': 'ffmpeg', 'pactl': 'pulseaudio-utils', 'arecord': 'alsa-utils'}),
    'fedora': ('dnf', ['dnf', 'install', '-y'],
               ['dnf', 'makecache'],
               {'ffmpeg': 'ffmpeg', 'pactl': 'pulseaudio-utils', 'arecord': 'alsa-utils'}),
    'arch': ('pacman', ['pacman', '-S', '--needed', '--noconfirm'],
             [],
             {'ffmpeg': 'ffmpeg', 'pactl': 'libpulse', 'arecord': 'alsa-utils'}),
    'suse': ('zypper', ['zypper', '--non-interactive', 'install'],
             ['zypper', '--non-interactive', 'refresh'],
             {'ffmpeg': 'ffmpeg', 'pactl': 'pulseaudio-utils', 'arecord': 'alsa-utils'}),
    'alpine': ('apk', ['apk', 'add', '--no-cache'],
               ['apk', 'update'],
               {'ffmpeg': 'ffmpeg', 'pactl': 'pulseaudio-utils', 'arecord': 'alsa-utils'}),
}

ID_FAMILIES = {
    'debian': 'debian', 'ubuntu': 'debian', 'linuxmint': 'debian', 'pop': 'debian',
    'raspbian': 'debian', 'kali': 'debian', 'devuan': 'debian',
    'fedora': 'fedora', 'rhel': 'fedora', 'centos': 'fedora', 'rocky': 'fedora',
    'almalinux': 'fedora', 'ol': 'fedora',
    'arch': 'arch', 'manjaro': 'arch', 'endeavouros': 'arch', 'cachyos': 'arch',
    'opensuse-leap': 'suse', 'opensuse-tumbleweed': 'suse', 'opensuse': 'suse',
    'sles': 'suse', 'suse': 'suse',
    'alpine': 'alpine',
}


def _os_release(path='/etc/os-release'):
    values = {}
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return values
    for line in text.splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def detect(os_release=None, platform=None):
    """Return the OS family and a readable name for dependency mapping."""
    current = audio_backend.PLATFORM if platform is None else platform
    data = _os_release() if os_release is None else os_release
    name = data.get('PRETTY_NAME') or data.get('NAME') or current
    version = data.get('VERSION_ID', '')
    if current == audio_backend.WSL2:
        return OSInfo('wsl2', data.get('NAME') or 'WSL2', version)
    candidates = [data.get('ID', '')] + (data.get('ID_LIKE') or '').split()
    for candidate in candidates:
        family = ID_FAMILIES.get(candidate.strip().lower())
        if family:
            return OSInfo(family, name, version)
    return OSInfo('unknown', name, version)


def manager(family):
    return FAMILIES.get(family)


def present(key, environ=None):
    """Whether one requirement is already satisfied on this host."""
    if key == 'ffmpeg':
        try:
            audio_backend.ffmpeg()
            return True
        except audio_backend.AudioBackendError:
            return False
    return shutil.which(key) is not None


def check(environ=None, os_release=None, platform=None):
    """Read-only dependency report for this host."""
    os_info = detect(os_release=os_release, platform=platform)
    requirements = REQUIREMENTS if os_info.family != 'wsl2' else \
        tuple(item for item in REQUIREMENTS if item.key == 'ffmpeg')
    rows = []
    for requirement in requirements:
        rows.append({
            'key': requirement.key,
            'label': requirement.label,
            'required': requirement.required,
            'reason': requirement.reason,
            'present': present(requirement.key, environ),
        })
    family = os_info.family
    return {
        'ok': True,
        'os': {'family': family, 'name': os_info.name, 'version': os_info.version},
        'manager': manager(family)[0] if manager(family) else None,
        'requirements': rows,
        'missing': [row['key'] for row in rows if not row['present']],
        'required_missing': [row['key'] for row in rows
                             if not row['present'] and row['required']],
        'auto_install': auto_install_enabled(environ),
    }


def auto_install_enabled(environ=None):
    environment = os.environ if environ is None else environ
    return str(environment.get(AUTO_INSTALL_ENV, '1')).strip().lower() not in (
        '0', 'false', 'no', 'off')


def _privilege_prefix(environ=None):
    """['sudo', '-n'] when needed and available, else None when manual action is required."""
    if os.geteuid() == 0:
        return []
    sudo = shutil.which('sudo')
    if not sudo:
        return None
    check_command = [sudo, '-n', 'true']
    try:
        completed = subprocess.run(check_command, capture_output=True, text=True,
                                   timeout=5, env=_env(environ))
    except (OSError, subprocess.TimeoutExpired):
        return None
    return [sudo, '-n'] if completed.returncode == 0 else None


def _env(environ=None):
    return None if environ is None else {**os.environ, **environ}


def plan(keys, environ=None, os_release=None, platform=None):
    """Exact argv list that would install the requested requirement keys."""
    os_info = detect(os_release=os_release, platform=platform)
    if os_info.family == 'wsl2':
        return plan_windows(keys, environ)
    selected = manager(os_info.family)
    if selected is None:
        return {'ok': False, 'error': f'No package manager mapping for {os_info.name}',
                'commands': []}
    _, prefix, _, packages = selected
    names = sorted({packages[key] for key in keys if key in packages})
    if not names:
        return {'ok': True, 'commands': []}
    privilege = _privilege_prefix(environ)
    if privilege is None:
        command = ['sudo'] + prefix + names
        return {'ok': False, 'commands': [command],
                'error': 'Root or passwordless sudo is required to install packages',
                'manual': True}
    return {'ok': True, 'commands': [privilege + prefix + names]}


def plan_windows(keys, environ=None):
    """WSL2: install the Windows ffmpeg build with winget when it is missing."""
    if 'ffmpeg' not in keys:
        return {'ok': True, 'commands': []}
    cmd = audio_backend.windows_cmd()
    command = [cmd, '/c', 'winget', 'install', '--id', 'Gyan.FFmpeg', '-e',
               '--accept-package-agreements', '--accept-source-agreements', '--silent']
    probe = audio_backend._run([cmd, '/c', 'where', 'winget'], timeout=10)
    if probe is None or probe.returncode != 0:
        return {'ok': False, 'commands': [command], 'manual': True,
                'error': 'winget is not available in Windows; install ffmpeg from '
                         'https://www.gyan.dev/ffmpeg/builds/ and set TUI_VOICE_FFMPEG'}
    return {'ok': True, 'commands': [command]}


def _run_commands(commands, environ=None, timeout=900):
    """Run install argv lists, refreshing package metadata once if needed."""
    results = []
    for command in commands:
        try:
            completed = subprocess.run(command, capture_output=True, text=True,
                                       timeout=timeout, env=_env(environ))
        except (OSError, subprocess.TimeoutExpired) as error:
            results.append({'command': command, 'ok': False, 'detail': str(error)[:400]})
            continue
        detail = ((completed.stdout or '') + (completed.stderr or '')).strip()[-600:]
        results.append({'command': command, 'ok': completed.returncode == 0,
                        'code': completed.returncode, 'detail': detail})
    return results


def install(keys, environ=None, os_release=None, platform=None, timeout=900):
    """Install the requested requirement keys non-interactively."""
    wanted = [key for key in keys if not present(key, environ)]
    if not wanted:
        return {'ok': True, 'installed': [], 'skipped': list(keys)}
    planned = plan(wanted, environ=environ, os_release=os_release, platform=platform)
    if not planned.get('ok'):
        return {'ok': False, 'installed': [], 'commands': planned.get('commands', []),
                'error': planned.get('error'), 'manual': planned.get('manual', False)}
    results = _run_commands(planned['commands'], environ=environ, timeout=timeout)
    if any(not row['ok'] for row in results):
        # A fresh container or image can have empty package indexes; refresh once.
        selected = manager(detect(os_release=os_release, platform=platform).family)
        privilege = _privilege_prefix(environ)
        if selected and selected[2] and privilege is not None:
            _run_commands([privilege + selected[2]], environ=environ, timeout=300)
            results += _run_commands(planned['commands'], environ=environ, timeout=timeout)
    installed = []
    for row in results:
        if row['ok']:
            for key in wanted:
                if present(key, environ) and key not in installed:
                    installed.append(key)
    ok = all(present(key, environ) for key in wanted)
    return {'ok': ok, 'installed': installed, 'results': results,
            'commands': planned['commands'],
            'missing': [key for key in wanted if not present(key, environ)],
            'error': None if ok else 'Install command did not provide every dependency'}


def ensure(install_missing=True, include_optional=False, environ=None,
           os_release=None, platform=None, timeout=900):
    """Check, install the allow-listed packages, and report the final state."""
    report = check(environ=environ, os_release=os_release, platform=platform)
    keys = list(report['required_missing'])
    if include_optional:
        keys += [row['key'] for row in report['requirements']
                 if not row['present'] and row['key'] not in keys]
    report['install_attempted'] = False
    if not keys:
        report['ok'] = True
        return report
    if not install_missing or not report['auto_install']:
        report['ok'] = False
        report['error'] = ('Automatic installation is disabled; run '
                           'python3 voice_helper.py doctor --install')
        return report
    report['install_attempted'] = True
    report['install'] = install(keys, environ=environ, os_release=os_release,
                                platform=platform, timeout=timeout)
    for row in report['requirements']:
        row['present'] = present(row['key'], environ)
    report['missing'] = [row['key'] for row in report['requirements']
                         if not row['present']]
    report['required_missing'] = [key for key in report['missing']
                                  if any(row['key'] == key and row['required']
                                         for row in report['requirements'])]
    report['ok'] = not report['required_missing']
    if not report['ok']:
        report['error'] = report['install'].get('error') or (
            'Missing required dependencies: ' + ', '.join(report['required_missing']))
    return report


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(
        description='Detect this OS and install tui-voice audio dependencies.')
    parser.add_argument('--install', action='store_true', help='install missing packages')
    parser.add_argument('--all', action='store_true', help='include optional helpers')
    parser.add_argument('--json', action='store_true', help='machine-readable output')
    args = parser.parse_args(argv)
    report = ensure(install_missing=args.install, include_optional=args.all) \
        if args.install else check()
    print(json.dumps(report, indent=2))
    if not report.get('ok'):
        return 1
    return 1 if report.get('required_missing') else 0


if __name__ == '__main__':
    sys.exit(main())
