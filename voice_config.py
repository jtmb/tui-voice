"""Portable configuration for tui-voice.

The plugin never assumes a particular checkout or operating system. Every path
comes from, in order:

1. an explicit override passed to a function or set in the environment,
2. ``$TUI_VOICE_CONFIG`` or ``$XDG_CONFIG_HOME/tui-voice/config.json``,
3. the plugin-owned data directory ``$XDG_DATA_HOME/tui-voice``.

A local speech stack is external to this repository: point ``service`` at the
directory that provides the ``web_voice`` module and ``voice`` at the directory
that holds the ASR/TTS workers and models. Nothing here is required at import
time, so the helper can report a usable error instead of crashing.

Stdlib only; no device, model or network is touched.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

# One environment variable per setting, kept stable for existing installs.
ENV_KEYS = {
    'service': 'TUI_VOICE_SERVICE',
    'voice': 'TUI_VOICE_VOICE_DIR',
    'python': 'TUI_VOICE_PYTHON',
    'asr_python': 'TUI_VOICE_ASR_PYTHON',
    'tts_python': 'TUI_VOICE_TTS_PYTHON',
    'ffmpeg': 'TUI_VOICE_FFMPEG',
    'player': 'TUI_VOICE_PLAYER',
    'mic': 'TUI_VOICE_MIC',
}
CONFIG_ENV = 'TUI_VOICE_CONFIG'
_PATHS = ('service', 'voice', 'python', 'asr_python', 'tts_python')
_PROBLEM: str | None = None


def _environ(environ=None):
    return os.environ if environ is None else environ


def _expand(value):
    return os.path.expandvars(os.path.expanduser(str(value)))


def data_home(environ=None):
    """Directory owned by the plugin for stack data."""
    environment = _environ(environ)
    base = environment.get('XDG_DATA_HOME') or str(Path.home() / '.local/share')
    return Path(_expand(base)) / 'tui-voice'


def config_path(environ=None):
    """Where the optional JSON config lives for this process."""
    environment = _environ(environ)
    explicit = environment.get(CONFIG_ENV)
    if explicit:
        return Path(_expand(explicit))
    base = environment.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(_expand(base)) / 'tui-voice' / 'config.json'


def load(environ=None):
    """Read the JSON config; a missing or broken file means defaults only."""
    global _PROBLEM
    path = config_path(environ)
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        _PROBLEM = None
        return {}
    except OSError as error:
        _PROBLEM = f'cannot read {path}: {error}'
        return {}
    try:
        payload = json.loads(text)
    except ValueError as error:
        _PROBLEM = f'{path} is not valid JSON: {error}'
        return {}
    if not isinstance(payload, dict):
        _PROBLEM = f'{path} must contain a JSON object'
        return {}
    _PROBLEM = None
    return payload


def problem(environ=None):
    """Last config-file problem, or None. Recomputed by load()."""
    load(environ)
    return _PROBLEM


def get(key, environ=None):
    """Resolve one setting: environment first, then the config file."""
    environment = _environ(environ)
    value = environment.get(ENV_KEYS.get(key, ''))
    if value:
        return _expand(value)
    payload = load(environment)
    value = payload.get(key)
    if isinstance(value, (str, int)) and str(value).strip():
        return _expand(value)
    return None


def service(environ=None):
    """Directory that provides the speech module and its virtualenv."""
    return Path(get('service', environ) or data_home(environ) / 'service')


def voice(environ=None):
    """Directory that holds the ASR/TTS workers and their models."""
    return Path(get('voice', environ) or data_home(environ) / 'voice')


def python(environ=None):
    """Interpreter that runs voice_helper.py (needs the stack's dependencies)."""
    explicit = get('python', environ)
    if explicit:
        return explicit
    venv = service(environ) / '.venv/bin/python'
    if venv.is_file():
        return str(venv)
    return sys.executable or shutil.which('python3') or 'python3'


def asr_python(environ=None):
    """Interpreter for the warm ASR worker, falling back to python()."""
    explicit = get('asr_python', environ)
    if explicit:
        return explicit
    venv = voice(environ) / '.asr-venv/bin/python'
    return str(venv) if venv.is_file() else python(environ)


def tts_python(environ=None):
    """Interpreter for the warm TTS worker, falling back to python()."""
    explicit = get('tts_python', environ)
    if explicit:
        return explicit
    venv = voice(environ) / '.venv/bin/python'
    return str(venv) if venv.is_file() else python(environ)


def audio(key, environ=None):
    """Optional audio override (ffmpeg, player, mic); None means autodetect."""
    return get(key, environ)


def describe(environ=None):
    """Resolved view for diagnostics and the TUI; safe to call when unset."""
    return {
        'config': str(config_path(environ)),
        'config_error': problem(environ),
        'service': str(service(environ)),
        'voice': str(voice(environ)),
        'python': python(environ),
        'asr_python': asr_python(environ),
        'tts_python': tts_python(environ),
        'ffmpeg': audio('ffmpeg', environ),
        'player': audio('player', environ),
        'mic': audio('mic', environ),
        'service_exists': service(environ).is_dir(),
        'voice_exists': voice(environ).is_dir(),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Resolve tui-voice paths without touching audio or models.')
    parser.add_argument('--json', action='store_true', help='print every resolved value')
    parser.add_argument('--python', action='store_true', help='print the helper interpreter')
    parser.add_argument('--service', action='store_true', help='print the speech service directory')
    parser.add_argument('--voice', action='store_true', help='print the models directory')
    args = parser.parse_args(argv)
    if args.python:
        print(python())
        return 0
    if args.service:
        print(service())
        return 0
    if args.voice:
        print(voice())
        return 0
    print(json.dumps(describe(), indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
