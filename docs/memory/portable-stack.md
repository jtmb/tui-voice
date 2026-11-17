---
{"version":1,"owner":"opencode-memory","scope":"repo","repository":"/home/brajam/repos/tui-voice","id":"portable-stack","title":"Speech stack configuration","tags":["configuration","portability","audio","debian"],"kind":"decision","updated":"2026-11-16","verified":"2026-11-16","sources":["voice_config.py","INSTALL.md","docs/ARCHITECTURE.md"]}
---

# Speech stack configuration

## Decision

The plugin never names another repository, user or drive in a default. All
external paths come from `voice_config.py`:

1. environment variable (`TUI_VOICE_SERVICE`, `TUI_VOICE_VOICE_DIR`,
   `TUI_VOICE_PYTHON`, `TUI_VOICE_ASR_PYTHON`, `TUI_VOICE_TTS_PYTHON`,
   `TUI_VOICE_FFMPEG`, `TUI_VOICE_PLAYER`, `TUI_VOICE_MIC`),
2. `~/.config/tui-voice/config.json` (or `$TUI_VOICE_CONFIG`),
3. `~/.local/share/tui-voice/{service,voice}`.

`voice_helper.py`, `tts_worker.py` and `tui.tsx` all resolve through it, so the
TUI, helper and workers cannot disagree about the interpreter.

## Stack contract

- `service`: directory with `web_voice.py` (`WebVoice`) and an optional
  `.venv/bin/python`.
- `voice`: models plus `asr-worker.py`, `.asr-venv`, `.venv` and
  `neural-models.json`.

Anything that satisfies this contract can be used; the plugin ships no models.

## Consequences

- A missing or broken stack yields a named error with the config path, not an
  `ImportError`.
- This machine keeps `service`/`voice` in `~/.config/tui-voice/config.json`,
  outside the repository. Do not move those paths back into code.
- Audio tooling follows the platform: DirectShow + PowerShell on WSL2;
  PipeWire/PulseAudio (`PULSE_SERVER` or the pulse socket, `pactl` optional) with
  ALSA fallback on Linux. `TUI_VOICE_CAPTURE` forces pulse/alsa.
- `TUI_VOICE_PLAYER` may place `{file}` anywhere in the command.

## Dependency installation

`platform_deps.py` detects the OS family and installs the allow-listed audio
packages itself: `apt-get` (Debian family), `dnf` (Fedora family), `pacman`
(Arch family), `zypper` (SUSE), `apk` (Alpine) or WinGet on WSL2. Only `ffmpeg`
is required; `pactl`/`arecord` are optional. Installs are argv lists, need root
or passwordless `sudo -n`, and fall back to printing the exact command.
`TUI_VOICE_AUTO_INSTALL=0` disables it. Do not add package upgrades, third-party
repositories or anything outside the allow-list to this path.
