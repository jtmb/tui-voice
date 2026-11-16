#!/usr/bin/env -S python3 -B
"""Run a development command with generated output on the authorized H drive.

No dependency install, service startup, migration or automatic cleanup occurs.
--reserve-bytes is the caller's estimated *additional* peak, not a disk quota.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
PROJECT = Path('/mnt/h/AgentArtifacts/projects/tui-voice')
RUN = PROJECT / 'runs/storage-setup-20261114'
LIMIT = 100_000_000_000
MIN_FREE_BYTES = 15 * 1024 ** 3
METADATA_ALLOWANCE = 65_536
LEGACY = (
    REPO / '__pycache__', REPO / 'tests/__pycache__',
    REPO / 'tools/__pycache__', REPO / '.ruff_cache',
    Path.home() / '.local/state/tui-voice',
    Path('/run/user/1000/tui-voice/tts-startup.log'),
    Path('/mnt/c/Users/james/AppData/Local/tui-voice/gpu-remediation'),
    *(Path('/mnt/c/Users/james/AppData/Local/Temp') / name for name in (
        'sd-04s-speech.wav', 'sd-1s-speech.wav', 'sd-gain.wav', 'sd-gain2.wav')),
    Path.home() / '.npm/_npx/821a89c635d995f5',
)
# Attribution of this existing shared cache is unresolved. Charge the entire
# footprint conservatively rather than silently exempting project-owned wheels.
SHARED_GUARD = Path.home() / '.cache/uv'


def apparent_bytes(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return 0
    if path.is_symlink():
        raise RuntimeError(f'Reconcile linked accounting path before use: {path}')
    # No link following. A timeout/error is a failed budget check, not zero.
    result = subprocess.run(['du', '-sb', '--', str(path)], check=True,
                            capture_output=True, text=True, timeout=30)
    return int(result.stdout.split('\t', 1)[0])


def verify_h_mount():
    result = subprocess.run(
        ['findmnt', '-J', '-T', '/mnt/h', '-o', 'TARGET,SOURCE,FSTYPE'],
        check=True, capture_output=True, text=True, timeout=10)
    mount = json.loads(result.stdout)['filesystems'][0]
    if mount['target'] != '/mnt/h' or mount['source'] != 'H:':
        raise RuntimeError(f'Refusing non-H output mount: {mount}')
    if PROJECT.resolve() != PROJECT or RUN.resolve() != RUN:
        raise RuntimeError('Artifact root is linked or has moved; reconcile first')
    return mount


def inventory():
    rows = [{'path': str(PROJECT), 'apparent_bytes': apparent_bytes(PROJECT),
             'classification': 'all H project runs/caches'}]
    rows.extend({'path': str(p), 'apparent_bytes': apparent_bytes(p),
                 'classification': 'legacy generated; preserve pending safety/disposition'}
                for p in LEGACY)
    guard = apparent_bytes(SHARED_GUARD)
    return {'project_paths': rows, 'known_generated_bytes': sum(
                row['apparent_bytes'] for row in rows),
            'shared_cache_guard': {'path': str(SHARED_GUARD), 'apparent_bytes': guard},
            'charged_bytes': sum(row['apparent_bytes'] for row in rows) + guard,
            'limit_bytes': LIMIT,
            'accounting': 'du apparent bytes, no symlink following; not physical quota enforcement'}


def check_reserves(reserve):
    """Keep C's safety floor and H's floor after the full additional peak."""
    c_free = shutil.disk_usage('/mnt/c').free
    h_free = shutil.disk_usage('/mnt/h').free
    planned = reserve + METADATA_ALLOWANCE
    if c_free < MIN_FREE_BYTES:
        raise RuntimeError(
            f'C reserve below 15 GiB: free={c_free}, required={MIN_FREE_BYTES}')
    if h_free - planned < MIN_FREE_BYTES:
        raise RuntimeError(
            f'H remaining reserve below 15 GiB: free={h_free}, '
            f'planned={planned}, required_remaining={MIN_FREE_BYTES}')
    return {'floor_bytes_each_drive': MIN_FREE_BYTES,
            'C_free_bytes': c_free, 'H_free_bytes': h_free,
            'H_remaining_after_planned_bytes': h_free - planned}


def environment():
    env = os.environ.copy()
    # npm prefers lowercase aliases in some child contexts. Remove conflicting
    # inherited aliases and use one consistent uppercase Windows/WSL contract.
    for name in ('npm_config_cache', 'npm_config_logs_dir', 'npm_config_offline'):
        env.pop(name, None)
    locations = {
        'TUI_VOICE_ARTIFACT_ROOT': RUN,
        'TUI_VOICE_BUILD_DIR': RUN / 'build',
        'TUI_VOICE_TEST_OUTPUT_DIR': RUN / 'test-output',
        'TMPDIR': RUN / 'tmp', 'TMP': RUN / 'tmp', 'TEMP': RUN / 'tmp',
        'XDG_CACHE_HOME': RUN / 'cache', 'XDG_STATE_HOME': RUN / 'state',
        'TUI_VOICE_LOG_DIR': RUN / 'state/tui-voice',
        'PYTHONPYCACHEPREFIX': RUN / 'cache/pycache',
        'RUFF_CACHE_DIR': RUN / 'cache/ruff',
        'UV_CACHE_DIR': RUN / 'cache/uv', 'PIP_CACHE_DIR': RUN / 'cache/pip',
        'NPM_CONFIG_CACHE': RUN / 'cache/npm',
        'NPM_CONFIG_LOGS_DIR': RUN / 'logs/npm',
        # Build children only: the parent environment/user AppData are untouched.
        'APPDATA': RUN / 'build-appdata/roaming',
        'LOCALAPPDATA': RUN / 'build-appdata/local',
    }
    for key, path in locations.items():
        if path.resolve() != path or not path.is_relative_to(PROJECT):
            raise RuntimeError(f'Linked or escaped destination: {path}')
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not os.access(path, os.W_OK):
            raise RuntimeError(f'Unwritable destination: {path}')
        env[key] = str(path)
    # Main/test imports need no cache; explicit py_compile still uses the prefix.
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['COVERAGE_FILE'] = str(RUN / 'test-output/.coverage')
    args = shlex.split(env.get('PYTEST_ADDOPTS', ''))
    args += ['--basetemp', str(RUN / f'tmp/pytest-{os.getpid()}'),
             '-o', f'cache_dir={RUN / "cache/pytest"}']
    env['PYTEST_ADDOPTS'] = shlex.join(args)
    # Explicit output flags remain required for tools such as esbuild.
    # Fail offline rather than obtaining another dependency/build copy.
    env.update(UV_OFFLINE='1', PIP_NO_INDEX='1', NPM_CONFIG_OFFLINE='true')
    # Translate every Windows-child destination, not just TEMP/TMP.
    translated = ('TEMP', 'TMP', 'APPDATA', 'LOCALAPPDATA',
                  'NPM_CONFIG_CACHE', 'NPM_CONFIG_LOGS_DIR')
    wslenv = [item for item in env.get('WSLENV', '').split(':')
              if item and item.split('/')[0].upper() not in translated]
    env['WSLENV'] = ':'.join(wslenv + [name + '/p' for name in translated])
    # Deliberately preserve XDG_RUNTIME_DIR, socket/model/interpreter settings.
    # AF_UNIX socket migration and active runtime changes are outside this task.
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', action='store_true', help='show budget and destinations only')
    parser.add_argument('--reserve-bytes', type=int, help='additional planned peak for this command')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not args.report and (not command or args.reserve_bytes is None):
        parser.error('provide --report, or --reserve-bytes N -- command ...')
    reserve = args.reserve_bytes if args.reserve_bytes is not None else METADATA_ALLOWANCE
    if reserve < 0:
        parser.error('--reserve-bytes must be nonnegative')
    os.umask(0o077)
    mount = verify_h_mount()
    # Reject low-drive space before creating destinations or starting a child.
    space = check_reserves(reserve)
    usage = inventory()
    # Allow small directory/metadata overhead as well as the command's peak.
    peak = usage['charged_bytes'] + reserve + METADATA_ALLOWANCE
    if peak > LIMIT:
        raise RuntimeError(f'Insufficient budget: planned charge {peak}')
    env = environment()
    if args.report:
        print(json.dumps({'mount': mount, 'budget': usage,
                          'drive_reserves': space,
                          'reserved_peak_charge_bytes': peak,
                          'destinations': {k: v for k, v in env.items()
                                           if v.startswith(str(RUN))},
                          'runtime_dir_unchanged': env.get('XDG_RUNTIME_DIR'),
                          'wslenv': env['WSLENV']}, indent=2))
        return 0
    result = subprocess.run(command, env=env, cwd=REPO)
    after = inventory()
    print(json.dumps({'artifact_charge_after_bytes': after['charged_bytes'],
                      'limit_bytes': LIMIT, 'command_exit': result.returncode}), file=sys.stderr)
    if after['charged_bytes'] > LIMIT:
        return 2
    return result.returncode


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'artifact-env: {error}', file=sys.stderr)
        raise SystemExit(1)
