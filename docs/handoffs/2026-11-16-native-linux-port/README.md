# Native Linux port — handoff

Status: implementation complete on `feat/native-linux-audio`, published as a pull
request. Code commit: `fccdade`. The WSL2 path is preserved; native Linux capture
and playback are verified on a real PipeWire host. Live transcription and
read-aloud remain unverified here because the external Whisper/Kokoro stack is not
installed on this machine.

A raw session transcript was part of the first revision of this branch and was
removed before merge: it was 83% of the diff, contained machine-specific tool
output, and a public repository is not the place for a chat export. This handoff
is the continuation document.

## Why this change exists

`tui-voice` was WSL2-only in four places:

1. `voice_helper.py` hardcoded `/mnt/c/Windows/System32/...` tools, DirectShow
   `ffmpeg.exe`, `/mnt/c` temp paths, `wslpath`, and PowerShell playback.
2. Device discovery and selection only understood DirectShow names.
3. `tui.tsx` hardcoded one machine's speech-service interpreter
   (`/home/brajam/repos/.../.venv/bin/python`), so the plugin could not start its
   helper elsewhere.
4. `INSTALL.md` and `README.md` told users the plugin was WSL2-only.

The plugin itself is local-speech software, so the audio layer can and should use
the host OS. This change moves every platform difference behind one module and
keeps the product contract unchanged: footer placement, manual dictation on/off,
never auto-send, local-only audio, and the settings schema.

## What changed, file by file

### `audio_backend.py` (new)

Owns all platform differences; stdlib only; no device is opened at import.

- **Platform detection:** `WSL_DISTRO_NAME` / `WSL_INTEROP`, otherwise `microsoft`
  in `/proc/version`. Exposes `PLATFORM` (`wsl2` or `linux`) and a pure
  `detect_platform()` for tests.
- **ffmpeg discovery:** WSL2 keeps the WinGet Gyan.FFmpeg / `Program Files` /
  `ffmpeg.exe` search order; Linux uses `TUI_VOICE_FFMPEG` then `ffmpeg` on
  `PATH`, with an actionable error naming the `ffmpeg` package.
- **Device discovery:**
  - WSL2: DirectShow enumeration through `ffmpeg -list_devices`, with the old
    retry behavior and the old Focusrite / virtual-device scoring.
  - Linux: `pactl -f json list sources`, then `pactl list short sources`, then
    `arecord -l`, then `System default input`.
- **Stable ids versus friendly names:** PulseAudio/PipeWire source descriptions
  are shown in the TUI; the technical source id is used for ffmpeg. Duplicate
  descriptions get a deterministic ` (<technical id>)` suffix. A raw technical id
  is accepted unchanged, so saved settings and `TUI_VOICE_MIC` keep working.
- **Automatic selection:** real hardware is preferred. Monitors, loopback,
  StreamMix, stereo mix, wave-out, Steam, and virtual sources are penalized;
  Focusrite and `mic`/`microphone` names gain weight. On native Linux the OS
  default source (`pactl get-default-source`) gets a +4 bonus so the picker
  follows the user's PipeWire/PulseAudio default when it is a real input. A
  virtual default still loses to hardware.
- **Capture arguments:** WSL2 `-f dshow -rtbufsize 64M [-audio_buffer_size 50]
  -i audio=<device>`; Linux `-f pulse -i <source>` or `-f alsa -i plughw:C,D`.
  The existing `volume` and `silencedetect` filters are unchanged.
- **Paths and playback:** `wslpath -w` on WSL2 versus the local path on Linux.
  Playback is PowerShell `Media.SoundPlayer` `PlaySync()` on WSL2, and on Linux
  `TUI_VOICE_PLAYER`, `pw-play` (when `$XDG_RUNTIME_DIR/pipewire-0` exists),
  `paplay` (when `pactl info` works), `aplay`, then `ffplay`. Every choice blocks
  until playback ends, so killing the helper still stops speech.
- **Scratch WAVs:** Windows `%TEMP%` on WSL2; `$XDG_RUNTIME_DIR/tui-voice` with
  mode `0700` on Linux, falling back to `$TMPDIR/tui-voice-<uid>`.

### `voice_helper.py`

- `ffmpeg()`, `devices()`, and `choose_mic()` now delegate to `audio_backend`;
  their public names and JSON output are unchanged.
- `audio_env()` applies the existing Windows interop-bridge repair only on WSL2;
  Linux children inherit the normal environment.
- `scratch_dir()` replaces direct `windows_temp()` calls in `record`, `say`, and
  `transcribe` scratch detection. `windows_temp()` and `to_windows()` remain for
  the WSL2 path.
- `record()` and `dictate()` build the ffmpeg input from the backend and write to
  the backend's output path. `windows_path` is returned only on WSL2; the TUI
  never read it.
- `play()` uses the backend's player and reports `Windows playback failed` or
  `Local playback failed` with the player's stderr.
- Two small robustness fixes found during Linux testing: `_current` is cleared as
  soon as capture ends, so a late `SIGUSR1` cannot write into the finished
  capture's pipe during transcription, and pipe cleanup tolerates a pipe already
  closed by that signal. Capture events, the stop handshake, the watchdog, and
  result fields are unchanged.
- No changes to warm ASR/TTS workers, ONNX providers, MIGraphX, settings, or the
  dictation/read-aloud flow.

### `tui.tsx`

- Removed the machine-specific interpreter fallback. The plugin now asks
  `voice_config.py --python`, so the TUI, helper and workers resolve the same
  interpreter: `TUI_VOICE_PYTHON`, then `service/.venv/bin/python`, then
  `python3`.
- No UI, footer, command, or settings changes.

### Tests

- `tests/test_audio_backend.py` (new, 25 tests): platform detection, Pulse JSON
  and monitor parsing, short/ALSA/default fallbacks, scoring and OS-default
  preference, duplicate-name resolution, raw-id acceptance, dshow/pulse/alsa
  capture argv, output paths, player selection order, scratch directory mode,
  and the Linux ffmpeg failure message. No real device or player is touched.
- Existing tests now patch `scratch_dir` instead of `windows_temp`; every
  assertion is preserved.
- Full suite: **76 tests pass** (51 before).

### Documentation

- `INSTALL.md`: native Linux prerequisite, per-platform ffmpeg, interpreter
  resolution, `TUI_VOICE_PLAYER`, and the unchanged global/local install rule.
- `README.md`: platform badge is now WSL2 + Linux.
- `docs/ARCHITECTURE.md`: new "Audio backend" matrix, capture/playback flows, and
  dependency rows.
- `docs/SETTINGS.md`: input list is platform-neutral; automatic-selection note.
- `docs/TROUBLESHOOTING.md`: native Linux section (no sources, ffmpeg missing,
  PipeWire/PulseAudio/ALSA, wrong input, silent device, playback override).
- `AGENTS.md`: environment rows, repository map, and capture description updated.

## Verification performed

Host: CachyOS, kernel 7.3.4, PipeWire running, OpenCode v2.2.0, Python 3.14.

| Check | Result |
| --- | --- |
| `python3 -m py_compile audio_backend.py voice_helper.py tts_worker.py live_dictation.py` | pass |
| `python3 -m unittest discover -s tests -p 'test_*.py'` | 76 tests pass |
| `python3 voice_helper.py devices` | 11 sources; automatic selection `C920 PRO HD Webcam Analog Stereo` |
| `record --seconds 2 --silence 0` on the C920 | valid WAV: `pcm_s16le`, 16 kHz, mono, 2.03 s; RMS 556.4, peak 1620 |
| `record --seconds 4 --silence 1.5` | silence filter ran; RMS 3522.5, peak 32078 |
| `play --wav <tone>` | exit 0 through `pw-play` |
| `TUI_VOICE_PLAYER=paplay play` | exit 0 |
| `TUI_VOICE_PLAYER=/nonexistent/player play` | exit 1 with `Local playback failed: [Errno 2] ...` |
| `timeout -s USR1 4 voice_helper.py dictate` | `starting` → `listening` (C920) → capture stops → expected ASR failure |
| Install + `opencode plugin list` | `/home/shak/repos/tui-voice` listed as `local` |
| Isolated TUI run (`script`, OpenCode v2.2.0) | rendered `tui-voice` and `Ready · F9 or Ctrl+Alt+V`, no plugin load error |

### Not verified here

- **Live transcription and read-aloud.** No external Whisper/Kokoro stack was
  configured. No fake or cloud engine was added. After the portability revision
  the helper reports a missing stack by name instead of importing `web_voice`.
- **WSL2 execution.** No WSL host was available. The WSL2 argv, dshow discovery,
  `wslpath`, `%TEMP%`, PowerShell playback, and interop-env repair are preserved
  by construction and unit tests, not re-run on Windows.
- **Human TUI acceptance.** The footer/settings/capture flow was loaded in an
  isolated terminal but not clicked through by a person.
- **TypeScript typecheck.** The checkout has no `node_modules`; no `tsc`/`bun` was
  available. The TS diff is limited to `existsSync` and `helperPython()`.

## Environment on the contributing machine

- Global install: `/home/shak/repos/tui-voice` added to
  `~/.config/opencode/cli.json`; existing goal plugin and settings preserved.
  Backup: `~/.config/opencode/cli.json.bak-20261116-213054` (`0600`).
- No project-local `.opencode/plugins` entry exists, so the plugin loads once.
- Shell overrides used during tests: `TUI_VOICE_PLAYER`,
  `TUI_VOICE_FFMPEG` only for failure-path checks.

## How to continue

```bash
git clone git@github.com:shakiata/tui-voice.git
cd tui-voice
git switch feat/native-linux-audio

# Baseline checks
python3 -m py_compile audio_backend.py voice_helper.py tts_worker.py live_dictation.py
python3 -m unittest discover -s tests -p 'test_*.py'

# Real Linux audio
python3 voice_helper.py devices
python3 voice_helper.py record --seconds 3 --silence 0 --out /tmp/tui-voice-test.wav
python3 voice_helper.py play --wav /tmp/tui-voice-test.wav

# Dictation capture without the speech stack
timeout -s USR1 5 python3 voice_helper.py dictate
```

To finish the unverified work:

1. Point `service` and `voice` in `~/.config/tui-voice/config.json` (or
   `TUI_VOICE_SERVICE`/`TUI_VOICE_VOICE_DIR`) at a real local Whisper/Kokoro
   checkout and confirm `transcribe` and `say` on Linux.
2. Run the same flow on WSL2 to confirm no regression.
3. Restart the TUI with the plugin installed and run the acceptance checklist in
   `AGENTS.md` (footer, settings, dictation, queueing, read-aloud, mute/resume).
4. If OpenCode is older than v2.2.6, confirm the local plugin still loads; the
   contributing host loaded it on v2.2.0.

## Known limits and edge cases

- ALSA fallback uses `plughw:C,D`, which is exclusive; a device held by PipeWire
  can be busy. Prefer PipeWire/PulseAudio when available.
- A renamed PipeWire/PulseAudio source that is still stored in settings surfaces
  as an ffmpeg source error rather than a pre-flight message.
- Automatic selection follows the OS default only as a bonus; a default device
  that is connected but muted (hardware gain) can still be chosen. The settings
  **Input** list or `TUI_VOICE_MIC` overrides it.
- `pactl` is queried at each capture start to resolve descriptive names; the
  query is best-effort and falls back to the raw value.
- OpenCode v2.2.0's `opencode session export` wrote assistant messages with empty
  text, so [session-export.md](session-export.md) was reconstructed read-only
  from `session_message`. Tool outputs are clipped; model reasoning is omitted.

## Session export

[session-export.md](session-export.md) contains the conversation, tool inputs and
clipped outputs, subagent reports, and the final verification evidence. It is the
handoff requested by the contributor so the next maintainer can resume directly.
