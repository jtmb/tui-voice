# Architecture

`tui-voice` is a TUI-only OpenCode plugin. It has no server part and no network
service of its own.

## Files

| Path | Responsibility |
| --- | --- |
| `tui.tsx` | Plugin registration, footer button, settings dialog, dictation state, pending-draft queue, read-aloud subscription |
| `voice_helper.py` | Local microphone capture (ffmpeg), silence detection, RMS report, local Whisper transcription, local Kokoro speech, warm-worker control |
| `audio_backend.py` | Platform audio differences: ffmpeg discovery, device enumeration/selection, capture arguments, output paths, playback tool, WAV scratch directory |
| `tts_worker.py` | Persistent local Kokoro worker: loads the pinned ONNX voice once and serves chunks over a private AF_UNIX socket |
| `package.json` | `./tui` entrypoint |
| `README.md` | Product page for users |
| `INSTALL.md` | Install guide: global vs project-local scope |
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
voice_helper.py dictate
  ffmpeg capture → 16 kHz mono PCM pipe (no audio file)
  WSL2: DirectShow audio; Linux: pulse source or ALSA device
  stays on through pauses and long turns
  second mic press sends SIGUSR1 → ffmpeg stops capture
        │
        ▼
live_dictation.py (concurrent with capture)
  one ASR request in flight, requests capped at eight seconds
  warm AF_UNIX request → Whisper base.en
  cold service fallback if the worker is absent
  commit phrases and replace live hypotheses without preview backlog
        │
        ▼
queueDraft / flushPending
  live_draft.ts → footer-anchored native composer updates
  if composer absent → retain latest text for that session
```

- The transcript is never sent. Native editor mutations revise only owned text;
  no synthetic stdin input or global first-editor search is used.
- A second press while listening sends SIGUSR1 after an explicit readiness
  handshake and finalizes the last hypothesis. No silence/turn-duration/helper
  lifetime cutoff is applied. A no-PCM watchdog reports a genuinely stuck device;
  silent PCM still counts as healthy capture.
- If a reply starts or is already running, the draft is inserted when the TUI
  exposes the message box again. Changing sessions retains the pending draft
  for its originating chat rather than inserting it into another chat.

## Read-aloud flow

```
OpenCode session.text.ended
        │
        ▼
current session check + read-aloud setting
        │
        ▼
speechQueue → voice_helper.py say
  warm TTS chunk request → Kokoro WAV → local speakers
  cold service fallback in CPU mode; explicit GPU mode reports failure
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
| TTS | Kokoro FP32 on CPU; FP32 also used for explicitly selected MIGraphX | `$XDG_RUNTIME_DIR/tui-voice/tts.sock` | 30 min |

- Sockets are AF_UNIX, mode `0600`, inside a uid-owned `0700` directory. Only the
  same user can connect.
- The TTS worker serves status while a chunk is synthesizing, so a second start
  cannot race a busy worker.
- `voice_helper.py warm --start|--status|--stop` manages both. Starts are
  serialized with a lock file.
- ASR and default CPU TTS requests retain the cold service fallback. With
  `TUI_VOICE_TTS_PROVIDER=migraphx`, a missing/failed GPU worker or an older CPU
  worker produces an error; it cannot silently enter the cold CPU TTS path.
- Replies are split into ~420-character chunks before synthesis, so the first
  audio starts sooner, mute/resume is more precise, and one long ONNX request
  cannot inflate worker memory.
- The TTS worker disables the ORT CPU memory arena for the same reason; measured
  RSS stayed near 300 MB across repeated long replies.
- TTS selects its ONNX execution provider explicitly. CPU remains the default;
  MIGraphX requires AMD's compatible runtime and a successful GPU inference
  check. Under WSL, GPU compute uses `/dev/dxg`; absent `/dev/kfd` or `/dev/dri`
  does not establish that an RX 7900 XT is unavailable. See
  [AMD GPU setup](AMD_GPU.md) and [AMD's WSL guide](https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.2.1/docs/install/installrad/wsl/howto_wsl.html).
- The selected ONNX model and `voices-v1.0.bin` must have SHA-256 entries in the
  existing `VOICE_DIR/neural-models.json`. Every manifest entry is still checked
  at startup. No inference path downloads models or packages.


## Settings

Settings are stored by OpenCode under the `tui-voice` storage key and apply
globally. The dialog writes them through the storage update function; the recorder
reads the current values at the start of each turn.

Fields:

| Field | Meaning |
| --- | --- |
| `mic` | explicit capture device (description or raw id), empty = automatic |
| `threshold` | local recognition noise gate in dB; never auto-stops capture |
| `gain` | output gain in dB |
| `readAloud` | speak completed replies |
| `voice` | Kokoro preset id |

Legacy saved `seconds`/`silence` keys are ignored. Their settings controls are
removed; dictation is manual on/off.

The dialog is a centered `large` OpenCode dialog using
`theme.surface("dialog")`, a rounded panel, native selected-row colors, and a
footer with keyboard hints. It is not a full-screen route.

## Configuration

`voice_config.py` is the single source of truth for stack paths, interpreters and
audio overrides. Resolution order: environment variable, then
`~/.config/tui-voice/config.json` (`$TUI_VOICE_CONFIG`), then the plugin-owned
data directory `~/.local/share/tui-voice/{service,voice}`. No repository is
named anywhere in the defaults; a missing stack produces an actionable error
instead of a traceback.

`tui.tsx` calls `voice_config.py --python` so the TUI, helper and workers agree
on the interpreter. `voice_helper.py` and `tts_worker.py` import the same module.

## Audio backend

`audio_backend.py` owns every platform difference; the rest of the helper only
knows public device names and a blocking `play`.

| | WSL2 | Native Linux |
| --- | --- | --- |
| Platform detection | `WSL_DISTRO_NAME`/`WSL_INTEROP`, or `microsoft` in `/proc/version` | everything else |
| ffmpeg | `TUI_VOICE_FFMPEG`, `ffmpeg.exe` on `PATH`, `where.exe`, then WinGet/Program Files under the live `%LOCALAPPDATA%`/`%ProgramFiles%` | `TUI_VOICE_FFMPEG`, then `ffmpeg` on `PATH` |
| Capture input | `-f dshow -i audio=<device>` | `-f pulse -i <source>`, or `-f alsa -i plughw:C,D`; `TUI_VOICE_CAPTURE` forces a backend |
| Device list | DirectShow audio device names | `pactl -f json list sources`, then `pactl list short sources`, then `arecord -l`, then the server default |
| Device names | DirectShow names as-is | source descriptions; duplicates carry ` (technical id)`; raw ids also accepted |
| Output path | `wslpath -w` Windows path | local path unchanged |
| Playback | PowerShell `Media.SoundPlayer` `PlaySync()`; `powershell.exe` found through `%SystemRoot%` | `TUI_VOICE_PLAYER`, `pw-play`, `paplay`, `aplay`, then `ffplay`; all block until done |
| WAV scratch | Windows `%TEMP%` | `$XDG_RUNTIME_DIR/tui-voice` (`0700`), or `$TMPDIR/tui-voice-<uid>` |

Automatic microphone selection prefers real hardware inputs: focusrite and
explicit `mic`/`microphone` names gain weight, while monitor, loopback,
StreamMix, stereo-mix, wave-out and steam sources are penalised. On native Linux
the OS default source (`pactl get-default-source`) gains a scoring bonus when it
is a real input, while a virtual default (monitor, loopback, StreamMix) still
loses to hardware. Every player and capture process is a child of the helper, so
stopping read-aloud still works by stopping that helper.

PipeWire/PulseAudio detection does not require `pactl`: a `PULSE_SERVER` or
`$XDG_RUNTIME_DIR/pulse/native` socket is enough, which keeps minimal Debian
installs working through `paplay` and `-f pulse`.

## Dependencies

| Dependency | Default | Override |
| --- | --- | --- |
| Local speech service | `~/.local/share/tui-voice/service` | `TUI_VOICE_SERVICE` or `service` in the config file |
| Voice models and worker runtimes | `~/.local/share/tui-voice/voice` | `TUI_VOICE_VOICE_DIR` or `voice` |
| Python with the service | `service/.venv/bin/python` when present, else `python3` | `TUI_VOICE_PYTHON` |
| Warm ASR Python | `voice/.asr-venv/bin/python` | `TUI_VOICE_ASR_PYTHON` |
| Warm TTS Python | `voice/.venv/bin/python` | `TUI_VOICE_TTS_PYTHON` |
| TTS execution provider | `cpu`; opt in with `migraphx` | `TUI_VOICE_TTS_PROVIDER` |
| TTS model | CPU and MIGraphX: `kokoro-v1.0.onnx` (FP32); INT8 is opt-in | `TUI_VOICE_TTS_MODEL` (absolute or relative to `voice/models`) |
| Worker idle lifetime | 1800 seconds | `TUI_VOICE_WARM_IDLE` |
| ASR worker threads | 8 | `TUI_VOICE_ASR_THREADS` |
| TTS worker threads | 8 | `TUI_VOICE_TTS_THREADS` |
| ASR socket | `$XDG_RUNTIME_DIR/firewatch-asr/asr.sock` | `TUI_VOICE_ASR_SOCKET` |
| TTS socket | `$XDG_RUNTIME_DIR/tui-voice/tts.sock` | `TUI_VOICE_TTS_SOCKET` |
| ffmpeg | WSL2: WinGet auto-discovery. Linux: system `ffmpeg` | `TUI_VOICE_FFMPEG` |
| Playback tool | Linux: auto-detected local player | `TUI_VOICE_PLAYER` |
| Capture backend | pulse when a server is reachable, else ALSA | `TUI_VOICE_CAPTURE` (`pulse`/`alsa`) |
| Windows interop tools | Discovered from the live install | `TUI_VOICE_CMD`, `TUI_VOICE_POWERSHELL` |
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
