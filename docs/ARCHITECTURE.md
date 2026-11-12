# Architecture

`tui-voice` is a TUI-only OpenCode plugin. It has no server part and no network
service of its own.

## Files

| Path | Responsibility |
| --- | --- |
| `tui.tsx` | Plugin registration, footer button, settings dialog, dictation state, pending-draft queue, read-aloud subscription |
| `voice_helper.py` | Windows microphone capture (ffmpeg/dshow), silence detection, RMS report, local Whisper transcription, local Kokoro speech |
| `package.json` | `./tui` entrypoint |
| `README.md` | User overview and install |
| `rules.md` | Current owner contract |
| `HANDOFF.md` | Current state and limits |
| `docs/` | This documentation |

The plugin is loaded globally from `~/.config/opencode/cli.json` by absolute path.
It is TUI-only, so this is the correct loader; do not add it to the server plugin
list as well.

## Load and registration

`tui.tsx` exports a `Plugin.define` with id `tui.voice` and:

- a `prompt.footer` slot containing `◉ Voice`;
- a command layer with `Ctrl+Alt+V`, `F9`, `/dictate`, settings (`Ctrl+Alt+S`),
  and microphone selection;
- a `session.text.ended` subscription for read-aloud;
- no app-wide route and no floating overlay.

The footer button is intentionally in the **prompt footer**. A fresh TUI is
required after adding or moving the plugin; `/reload` does not hot-add a new
TUI-only plugin path.

## Dictation flow

```
footer button / F9 / Ctrl+Alt+V / /dictate
        │
        ▼
voice_helper.py record
  ffmpeg dshow → 16 kHz mono WAV
  silencedetect stops after N seconds of silence
  max turn is a hard stop
        │
        ▼
voice_helper.py transcribe
  local Whisper service
  returns text + confidence
        │
        ▼
queueDraft / flushPending
  if session idle → focus message box, type text
  if reply running → hold draft, poll until idle, then insert
```

- The transcript is never sent. It is typed into the focused prompt using the
  renderer's stdin path.
- A second press while listening only reminds the user to pause; it does not
  destroy the recording.
- If a reply starts or is already running, the draft is inserted when the TUI
  exposes the message box again. Changing sessions discards a pending draft rather
  than inserting it into the wrong chat.

## Read-aloud flow

```
OpenCode session.text.ended
        │
        ▼
current session check + read-aloud setting
        │
        ▼
speechQueue → voice_helper.py say
  local Kokoro WAV → Windows speakers
```

- Only completed assistant text for the currently open session is spoken.
- The speech queue serializes replies and waits while a dictation turn is active.
- Read-aloud can be turned off in settings or by the footer `◉ Mute` / `◉ Unmute`
  button. Muting stops the active helper `say` process immediately. The voice is
  selectable (Heart, Velvet, English Rose, Dora).
- Read-aloud and dictation both use the same local models; no cloud request is
  made.

## Settings

Settings are stored by OpenCode under the `tui-voice` storage key and apply
globally. The dialog writes them through the storage update function; the recorder
reads the current values at the start of each turn.

Fields:

| Field | Meaning |
| --- | --- |
| `silence` | seconds of silence before finishing |
| `seconds` | hard maximum turn |
| `mic` | explicit dshow device, empty = automatic |
| `threshold` | silence detector noise floor in dB |
| `gain` | output gain in dB |
| `readAloud` | speak completed replies |
| `voice` | Kokoro preset id |

The dialog is a centered `large` OpenCode dialog using
`theme.surface("dialog")`, a rounded panel, native selected-row colors, and a
footer with keyboard hints. It is not a full-screen route.

## Dependencies

| Dependency | Default | Override |
| --- | --- | --- |
| Local Whisper/Kokoro service | `~/repos/fire-tv-next/lulu-launcher/service` | `TUI_VOICE_SERVICE` |
| Python with the service | service `.venv/bin/python` | `TUI_VOICE_PYTHON` |
| Windows ffmpeg | WinGet Gyan.FFmpeg auto-discovery | `TUI_VOICE_FFMPEG` |
| Microphone | automatic real-input selection | `TUI_VOICE_MIC` |

The paths are local. The plugin does not use the Fire TV app, ADB, or the Control
Center API.
