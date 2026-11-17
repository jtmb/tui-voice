# Troubleshooting

## Plugin is missing or looks stale

- Confirm `~/.config/opencode/cli.json` contains the absolute path of this
  checkout (run `pwd` in the repository).
- Run `opencode plugin list`.
- A TUI-only plugin added or moved on disk is not picked up by `/reload` alone.
  **Close the TUI completely and reopen it.**
- If a TUI was already running when the path changed, it may still hold the old
  generation. Reopening is the reliable fix.

## Button is in the wrong place

The owner requirement is fixed: `🎙` microphone icon must stay in the **bottom prompt footer**.
Do not move it to the composer, a dialog, or an overlay.

## Button click does nothing

- Use `Ctrl+Alt+V`, `F9`, or `/dictate`; these are the verified paths.
- The footer button now handles mouse-down and mouse-up with a debounce and is
  focusable, but mouse delivery depends on the terminal/tmux mouse mode.
- If the footer does not change to `Listening` on the keyboard shortcut, the plugin
  has not loaded in that TUI; reopen it.

## No transcript

1. Check the footer changes to `Listening` and then `Transcribing`.
2. Run **Voice: Settings → Test microphone**. `silent` means the wrong device or no
   input; `quiet` means raise **Input gain**.
3. `voice_helper.py devices` lists the inputs (DirectShow names on WSL2, source
   descriptions on Linux). Pick the real microphone, not a monitor or StreamMix;
   on WSL2 the Steam Streaming Microphone is silent here, so use the Focusrite
   input.
4. Lower **Speech threshold** or raise gain for quiet words. Recording is manual
   on/off; no pause or maximum-turn setting should stop it.
5. Confirm your speech stack is still where the plugin expects it:
   `python3 voice_config.py --json` prints both directories, the interpreters,
   and `config_error` when the config file is unreadable.

## "No local speech stack at ..."

The plugin provisions its own stack on first run. If it is missing:

```bash
python3 setup_stack.py                 # list what is missing
python3 setup_stack.py --install       # download models, create the venvs
python3 setup_stack.py --install --selftest
```

`--install` needs network access for the pinned models and packages. If you
already have the models, `--models-from /path/to/models` copies and verifies them
instead. If you point the plugin at a custom stack, set `service` and `voice` in
`~/.config/tui-voice/config.json` or with `TUI_VOICE_SERVICE` /
`TUI_VOICE_VOICE_DIR`; `python3 voice_config.py --json` shows what was resolved.

The pinned speech packages need Python 3.10–3.13. The installer picks a
compatible interpreter and, when `uv` is available, can fetch CPython 3.12; on a
3.14-only host without `uv`, install `python3.12`/`python3-venv` first.

## Native Linux audio

- **No sources listed.** `voice_helper.py devices` falls back from
  `pactl -f json list sources` to `pactl list short sources` to `arecord -l`, then
  to `System default input`. If you only see the default, check that PipeWire or
  PulseAudio is running (`pactl info`) and that the device is not muted in
  `pavucontrol`.
- **`ffmpeg was not found`.** The plugin installs it for the detected OS at
  startup and retries the capture once. If that could not happen, run
  `python3 voice_helper.py doctor` to see the OS, manager and missing packages,
  then `python3 voice_helper.py doctor --install` (needs root or passwordless
  `sudo`). Set `TUI_VOICE_FFMPEG` to use a build outside the package manager, or
  `TUI_VOICE_AUTO_INSTALL=0` to stop automatic installs.
- **PipeWire vs PulseAudio vs ALSA.** PipeWire and PulseAudio both answer `pactl`
  and are used through `-f pulse`. When neither answers, capture uses the ALSA
  `plughw:C,D` devices from `arecord -l`; those are exclusive, so close other
  capture programs if a device is busy.
- **Silent capture on the right device.** A USB interface can be connected but
  muted at the hardware gain. Record a diagnostic WAV and check its RMS:
  `voice_helper.py record --seconds 2 --silence 0 --mic "<device name>"`.
- **Wrong input is used.** Automatic selection follows the PipeWire/PulseAudio
  default source when it is a real input. Choose the right device with **Voice:
  Settings → Input**, or set `TUI_VOICE_MIC` to a device description or raw source
  id, to override it.
- **Playback uses the wrong device or fails.** Playback tries `pw-play` (when
  `$XDG_RUNTIME_DIR/pipewire-0` exists), then `paplay` (when a PulseAudio/PipeWire
  socket answers, with or without `pactl`), then `aplay`, then `ffplay`. Override
  it with `TUI_VOICE_PLAYER`, for example `TUI_VOICE_PLAYER="mpv --no-video"`;
  the WAV path is appended last, or substituted for `{file}`.
- **No `pactl`/`arecord` installed.** That is supported: the plugin detects a
  PulseAudio/PipeWire socket directly, captures `-f pulse -i default`, and falls
  back to ALSA when no server answers. Install the helpers only if you want a
  named device list.

## "Audio duration outside finite budget"

Interactive dictation keeps ASR requests at eight seconds or less, independently
of how long the microphone stays on. The separate file-transcription diagnostic
splits long WAVs under 28 s. If it appears again, the TUI may be running an older
plugin generation; close and reopen it.

## Where errors are logged

The plugin and `voice_helper.py` append JSON lines to
`~/.local/state/tui-voice/voice.log` (one backup at `voice.log.1`). Include the last
entries when reporting a failure. The log contains recording paths and error
details, never audio or cloud data. Override the directory with
`TUI_VOICE_LOG_DIR`.

## It waits too long

- The microphone intentionally stays on through pauses. Press F9/the mic again
  to switch it off. Legacy pause/maximum-turn values no longer affect dictation.
- Text updates while recording; switching off finalizes the last phrase.

## Read-aloud does not work

- Enable **Read replies aloud** in settings and select a voice.
- It speaks only completed assistant replies in the currently open session.
- Verify the helper directly:
  `voice_helper.py say --text "test" --voice heart`.
- Check the warm workers:
  `voice_helper.py warm --status`; use `warm --start` if either is absent or
  `warm --restart` after changing models.
- Read-aloud uses your local speakers; check the default output device.
- If dictation is active, speech waits for the turn to finish.

## Settings theme looks wrong

- The dialog uses `theme.surface("dialog")` and native selected-row tokens.
- Reopen the dialog after changing the TUI theme.
- If it still looks like a full-screen custom page, the TUI is running an older
  plugin generation; reopen it.

## Audio or privacy

- Temporary WAVs are deleted after transcription; on WSL2 they live in the
  Windows temp directory, on Linux in `$XDG_RUNTIME_DIR/tui-voice` (`0700`). If a
  run was cancelled, a temp file may remain; remove stale `tui-voice-*.wav` files.
- No cloud request is made. Keep it that way.
