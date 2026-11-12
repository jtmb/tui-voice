# Architecture

`tui-voice` is a TUI-only OpenCode plugin. It has no server part and no network
service of its own.

## Files

| Path | Responsibility |
| --- | --- |
| `tui.tsx` | Plugin registration, footer button, settings dialog, dictation state, pending-draft queue, read-aloud subscription |
| `voice_helper.py` | Windows microphone capture (ffmpeg/dshow), silence detection, RMS report, local Whisper transcription, local Kokoro speech, warm-worker control |
| `tts_worker.py` | Persistent local Kokoro worker: loads the pinned ONNX voice once and serves chunks over a private AF_UNIX socket |
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

- a `prompt.footer` slot containing one compact rounded panel in the existing footer row: status
  circle, `🎙`, a divider, and the speaker;
- a command layer with `Ctrl+Alt+V`, `F9`, `/dictate`, settings (`Ctrl+Alt+S`),
  and microphone selection;
- a `session.text.ended` subscription for read-aloud;
- no app-wide route and no floating overlay.

The footer button is intentionally in the **prompt footer**. A fresh TUI is
required after adding or moving the plugin; `/reload` does not hot-add a new
TUI-only plugin path.

## Dictation flow

```
footer button / F9 / Ctrl+Alt+V / /dictate (home or `/new`)
        │
        ▼
ensure a session exists (create + navigate if needed)
        │
        ▼
prewarm ASR worker (fire-and-forget)
        │
        ▼
voice_helper.py record
  ffmpeg dshow → 16 kHz mono WAV
  silencedetect stops after N seconds of silence
  second mic press sends SIGUSR1 → ffmpeg finalizes the WAV
  max turn is a hard stop
        │
        ▼
voice_helper.py transcribe
  split above 28 s and cut at a quiet point, keeping each chunk under 32 s
  warm AF_UNIX request → Whisper base.en
  cold service fallback if the worker is absent
  join chunk text in order
        │
        ▼
queueDraft / flushPending
  if session idle → focus message box, type text
  if reply running → hold draft, poll until idle, then insert
```

- The transcript is never sent. It is typed into the focused prompt using the
  renderer's stdin path.
- A second press while listening sends SIGUSR1, finalizes the WAV, and transcribes
  everything recorded up to that point.
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
  warm TTS chunk request → Kokoro WAV → Windows speakers
  cold service fallback if the worker is absent
```

- Only completed assistant text for the currently open session is spoken.
- The speech queue serializes replies and waits while a dictation turn is active.
- Read-aloud can be turned off in settings or by the footer `🔊` / `🔇`
  button. Muting stops the active helper `say` process immediately and records the
  current speech chunk; unmuting resumes with `say --start-chunk N`. The voice is
  selectable (Heart, Velvet, English Rose, Dora).
- `voice_helper.py say` writes `tui-voice-speech.json` before each chunk so the
  plugin can resume the interrupted position.
- Read-aloud and dictation both use the same local models; no cloud request is
  made.

## Warm workers

Both models stay loaded between turns. The plugin starts them in the background
at setup and again before each dictation or reply, so an idle-exited worker is
restored before the model is needed.

| Worker | Model | Socket | Idle exit |
| --- | --- | --- | --- |
| ASR | Whisper `base.en` int8, 8 CPU threads | `$XDG_RUNTIME_DIR/firewatch-asr/asr.sock` | 30 min |
| TTS | Kokoro `v1.0.int8`, 8 CPU threads | `$XDG_RUNTIME_DIR/tui-voice/tts.sock` | 30 min |

- Sockets are AF_UNIX, mode `0600`, inside a uid-owned `0700` directory. Only the
  same user can connect.
- The TTS worker serves status while a chunk is synthesizing, so a second start
  cannot race a busy worker.
- `voice_helper.py warm --start|--status|--stop` manages both. Starts are
  serialized with a lock file.
- Warm requests fall back to the unchanged cold service path on any error. The
  cold path still works with no worker running.
- Replies are split into ~420-character chunks before synthesis, so the first
  audio starts sooner, mute/resume is more precise, and one long ONNX request
  cannot inflate worker memory.
- The TTS worker disables the ORT CPU memory arena for the same reason; measured
  RSS stayed near 300 MB across repeated long replies.
- The workers are CPU-only. WSL exposes no ROCm/CUDA device for this AMD host, so
  GPU acceleration is not silently attempted.


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
| Voice models and worker runtimes | `SERVICE/../voice` | `TUI_VOICE_VOICE_DIR` |
| Python with the service | service `.venv/bin/python` | `TUI_VOICE_PYTHON` |
| Warm ASR Python | `VOICE_DIR/.asr-venv/bin/python` | `TUI_VOICE_ASR_PYTHON` |
| Warm TTS Python | `VOICE_DIR/.venv/bin/python` | `TUI_VOICE_TTS_PYTHON` |
| Worker idle lifetime | 1800 seconds | `TUI_VOICE_WARM_IDLE` |
| ASR worker threads | 8 | `TUI_VOICE_ASR_THREADS` |
| TTS worker threads | 8 | `TUI_VOICE_TTS_THREADS` |
| ASR socket | `$XDG_RUNTIME_DIR/firewatch-asr/asr.sock` | `TUI_VOICE_ASR_SOCKET` |
| TTS socket | `$XDG_RUNTIME_DIR/tui-voice/tts.sock` | `TUI_VOICE_TTS_SOCKET` |
| Windows ffmpeg | WinGet Gyan.FFmpeg auto-discovery | `TUI_VOICE_FFMPEG` |
| Microphone | automatic real-input selection | `TUI_VOICE_MIC` |
| Error log directory | `~/.local/state/tui-voice` | `TUI_VOICE_LOG_DIR` |

The paths are local. The plugin does not use the Fire TV app, ADB, or the Control
Center API.

## Error log

Both the plugin and the helper append JSON lines to
`~/.local/state/tui-voice/voice.log` (`$XDG_STATE_HOME` respected). The file rolls
to `voice.log.1` at 1 MB. Logged fields include the time, PID, level, message,
recording path, chunk counts, and a bounded traceback for helper failures.
`TUI_VOICE_LOG_DIR` overrides the directory.

Typical entries:

- `recorded` — capture result, device, bytes, RMS.
- `transcribed` — chunks, warm/cold count, duration, elapsed, characters.
- `error` — helper crash, chunk failure, worker start failure, plugin catch.
