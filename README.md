<p align="center">
  <img src="docs/media/tui-voice-logo.png" width="168" alt="TUI Voice logo: a cyan terminal prompt and waveform with an amber cursor">
</p>

<h1 align="center">tui-voice</h1>

<p align="center">
  <strong>Speak your prompts. Hear your replies.</strong><br>
  Local voice dictation and read-aloud for the OpenCode TUI.
</p>

<p align="center">
  <a href="docs/SETTINGS.md">Settings</a> ·
  <a href="docs/TROUBLESHOOTING.md">Troubleshooting</a> ·
  <a href="docs/AMD_GPU.md">AMD GPU setup</a>
</p>

Speak into the Windows microphone; the plugin transcribes with local Whisper and
types the text into the OpenCode message box. It never sends the prompt. Completed
assistant replies can also be read aloud with local Kokoro voices.

No cloud service, no TV, no Control Center login.

## Features

- **Dictation into the draft** — one turn, local Whisper, text in the message box. Works from the home screen and `/new`; the first press opens a session.
- **Manual on/off** — press the microphone/F9 to start, then again to stop.
  Pauses and long turns do not switch it off.
- **Works during a reply** — transcribes immediately and queues the draft until
  the message box returns.
- **Read-aloud** — speaks completed assistant replies through Windows speakers.
- **Reply sound** — the speaker segment of the same footer panel stops current playback and toggles
  reply read-aloud without opening settings. Unmuting resumes the interrupted reply
  from the current local speech chunk.
- **Settings dialog** — tune microphone, gain, speech threshold, voice;
  test the mic without recording a prompt.
- **Global** — loaded once from `~/.config/opencode/cli.json`, across all projects.

## Install

This is a TUI-only OpenCode plugin, so it belongs in the CLI config, not the
server plugin list.

Add the repository to `~/.config/opencode/cli.json`:

```json
{
  "plugins": ["/home/brajam/repos/tui-voice"]
}
```

Preserve any unrelated CLI settings in that file.

**Restart the TUI completely.** A TUI-only plugin added or moved on disk is not
picked up by `/reload` alone.

Verify:

```bash
opencode plugin list
```

The list should include `/home/brajam/repos/tui-voice`.

## Use

Open a chat session and use the bottom footer button or a shortcut:

| Action | Control |
| --- | --- |
| Dictate | status circle + `🎙` microphone in a compact rounded footer panel; the circle changes: `○` idle, amber `●` listening, cyan `◐` transcribing, `✕` error |
| Dictate | `Ctrl+Alt+V` |
| Dictate | `F9` |
| Dictate | `/dictate` |
| Recover dictation after editing | `/voice-text` inserts the last transcript for this chat |
| Reply sound | `🔊` / `🔇` speaker segment in the footer panel |
| Reply sound | `Ctrl+Alt+M` or `F10` |
| Replay last reply | `/read-last` or command palette → **Voice: Read last reply aloud** |
| Settings | `Ctrl+Alt+S` |
| Settings | Command palette → **Voice: Settings** |

Speak normally. Once the microphone is actually receiving audio, the button shows
`Listening` and words appear in the message box as you speak. The newest phrase
may correct itself as Whisper hears more context. Press F9 or the mic again to
finish; review the draft and send it yourself. Nothing is sent automatically.

If a reply is still running, the transcript is held and inserted when the message
box returns. A second press while listening stops the recording at that point and
finishes everything captured so far. When the message box is unavailable, live
recognition continues locally and the latest transcript is held for that chat.
Manual edits to the dictated region pause live replacement rather than overwrite
your work; `/voice-text` can insert the full retained transcript afterward.
Recognition and this recovery buffer stay local and in memory.

Missed a reply? Use `/read-last` to replay the newest completed assistant message
in this chat from the beginning, replacing any current/queued speech. It uses
your selected local voice and skips unfinished replies, reasoning and tool output.
Reply sound must be on (`F10` toggles it); replay does not change that setting.

## Settings

The settings screen is a centered, native-sized OpenCode dialog using the active
theme. Keyboard: `↑↓` select, `←→` change, `Enter` choose, `Esc` close.

| Setting | Default | What it does |
| --- | --- | --- |
| Input | Automatic | Windows microphone; automatic prefers the real input |
| Speech threshold | −35 dB | Recognition gate for quiet audio; does not stop recording |
| Input gain | Off | Boost a quiet mic; try 6–12 dB |
| Test microphone | — | Records 2 s and reports RMS/peak |
| Read replies aloud | On | Local Kokoro read-back; also toggled by the footer speaker icon |
| Read-aloud voice | Heart | Heart, Velvet, English Rose, Dora |
| Test read-aloud | — | Plays a sample so mute/unmute resume can be checked |
| Reset to defaults | — | Restores the values above |

### If words are missing

1. Run **Test microphone**. `quiet`/`silent` means gain or device selection is the
   problem.
2. Raise **Input gain** for a quiet input.
3. Lower **Speech threshold** for quieter speech, or raise it to ignore room noise.
4. Stop with F9/the mic when finished. Pauses do not end recording.

The virtual Steam Streaming Microphone is silent on this machine. Automatic
selection prefers the Focusrite Analogue input; choose **Input** explicitly if you
speak into something else.

## How it works

```
footer button / shortcut (home or `/new` opens a session)
        │
        ▼
voice_helper.py dictate
  ffmpeg dshow → 16 kHz PCM pipe
        │
        ▼
bounded local Whisper requests while recording
  one in flight; replaceable live phrase
  warm worker (cold service fallback)
        │
        ▼
native message draft updates (never auto-sent)
```

Read-aloud listens to completed assistant text in the current session and runs
`voice_helper.py say` with the selected Kokoro voice. A persistent warm TTS worker
keeps the ONNX voice loaded between chunks and replies. CPU uses the pinned FP32
`kokoro-v1.0.onnx` by default; INT8 remains available with
`TUI_VOICE_TTS_MODEL=kokoro-v1.0.int8.onnx`.

Dictation streams 16-kHz microphone PCM directly from Windows ffmpeg to one local
ASR consumer—no recording WAV or separate post-recording helper. Previews update
roughly once per second; natural phrase boundaries and an eight-second request
cap keep work bounded. Slow inference skips obsolete previews instead of queuing
them. On the existing CPU fixture, first text appeared at 1.4 s; room/mic latency
and recognition quality still depend on signal level and CPU load.

The plugin depends on the existing local voice stack:

| Dependency | Default | Override |
| --- | --- | --- |
| Whisper/Kokoro service | `~/repos/fire-tv-next/lulu-launcher/service` | `TUI_VOICE_SERVICE` |
| Voice models / worker runtimes | `lulu-launcher/voice` | `TUI_VOICE_VOICE_DIR` |
| Python | service `.venv/bin/python` | `TUI_VOICE_PYTHON` |
| Warm ASR Python | `voice/.asr-venv/bin/python` | `TUI_VOICE_ASR_PYTHON` |
| Warm TTS Python | `voice/.venv/bin/python` | `TUI_VOICE_TTS_PYTHON` |
| Windows ffmpeg | WinGet Gyan.FFmpeg auto-discovery | `TUI_VOICE_FFMPEG` |
| Microphone | automatic | `TUI_VOICE_MIC` |

Warm workers are optional in the default CPU configuration. If either is absent
or fails, the helper uses the cold local path. Explicit GPU selection reports an
error instead of silently running TTS on the CPU. Manage workers with:

```bash
~/repos/fire-tv-next/lulu-launcher/service/.venv/bin/python voice_helper.py warm --status
~/repos/fire-tv-next/lulu-launcher/service/.venv/bin/python voice_helper.py warm --start
~/repos/fire-tv-next/lulu-launcher/service/.venv/bin/python voice_helper.py warm --stop
```

Both keep audio local and exit after 30 idle minutes; the plugin starts them again
before the next dictation or reply.

### AMD GPU read-aloud on WSL2

Kokoro can request AMD's `MIGraphXExecutionProvider` with
`TUI_VOICE_TTS_PROVIDER=migraphx`; `cpu` remains the default. GPU mode selects
`kokoro-v1.0.onnx` (FP32) and requires a matching ROCm/MIGraphX installation in the
TTS worker's Python environment. The selected model and voice bundle must be
pinned in the existing local model manifest.

Follow [`docs/AMD_GPU.md`](docs/AMD_GPU.md) for RX 7900 XT setup, model preparation,
worker restart, and a real inference check. GPU availability is checked through
WSL's `/dev/dxg` and the AMD runtime. **RX 7900 XT synthesis has not yet been
verified on the owner's machine.** The 2026-11-12 attempt installed the ROCm 10.1
stack but stopped at the Windows display driver (`31.0.14000.58004`, 2022-12-02),
which rejects the GPU address reservation ROCm needs; CPU remains the working
backend. Whisper dictation continues using its current CPU worker.

## Privacy

- Dictation streams microphone PCM in memory; no recording file is created.
  The separate microphone-test/`record` diagnostic writes a temporary WAV that is
  deleted immediately after use.
- The transcript goes only into the local OpenCode message box.
- Read-aloud uses the local Kokoro model and Windows speakers.
- No cloud STT/TTS, no TV/ADB, no automatic send, no upload.
- No recordings, `.wav` files, or transcripts are committed.

## Requirements

- OpenCode TUI with TUI plugin support (verified on v2.2.6).
- Windows `ffmpeg.exe` with DirectShow microphone support.
- The local Whisper/Kokoro service and its `.venv` at the default path, or the
  environment overrides above.
- WSL2 is the supported environment.

## Troubleshooting

- **Plugin missing** — confirm the `cli.json` path, then close and reopen the TUI.
  `/reload` is not enough for a new TUI-only plugin path.
- **Button click does nothing** — use `Ctrl+Alt+V`, `F9`, or `/dictate`; the
  keyboard paths are verified. Mouse delivery depends on terminal/tmux mouse mode.
- **Footer never changes to Listening** — the plugin is not loaded in that TUI.
- **No transcript** — run **Test microphone**, check the selected input, then raise
   gain or lower the speech threshold. Dictation works from home and `/new`; if it still
  refuses, restart the TUI to load the current plugin.
- **Something failed** — failures are appended to
  `~/.local/state/tui-voice/voice.log` with the recording path and traceback.
  Include the last entries in a bug report. No audio or cloud data is logged.
- **Microphone stays on during pauses** — intentional. Press F9/the mic again
  to switch it off; there is no automatic silence or turn-duration cutoff.
- **Read-aloud is silent** — check for `🔇` in the footer, enable it in
  settings, select a voice, and check the Windows default output device. Unmuting
  resumes an interrupted reply rather than dropping it.
- **Theme looks wrong** — reopen the dialog after changing the TUI theme; the
  dialog uses `theme.surface("dialog")`.

## Development

```bash
cd ~/repos/tui-voice

python3 -m py_compile voice_helper.py

SERVICE=~/repos/fire-tv-next/lulu-launcher/service
$SERVICE/.venv/bin/python voice_helper.py devices
$SERVICE/.venv/bin/python voice_helper.py transcribe \
  --wav ~/repos/fire-tv-next/lulu-launcher/voice/fixtures/short-2s.wav
```

After changes, reopen the TUI and run the fresh-TUI gate in
[`docs/TESTING.md`](docs/TESTING.md#fresh-tui-gate).

## Status

Verified: fixture transcript inserted without sending; settings dialog and values;
read-aloud helper spawned with the correct reply; footer 🔊/🔇 speaker icon stops
playback and resumes it on unmute; microphone test; global load from outside the
repository.

Not verified: physical speech accuracy/latency, mouse-click activation of the
footer button, and a physical dictation queued behind a live model turn. See
[`HANDOFF.md`](HANDOFF.md).

## Documentation

- [`AGENTS.md`](AGENTS.md) — agent rules and verification checklist
- [`rules.md`](rules.md) — current product contract
- [`HANDOFF.md`](HANDOFF.md) — current state, evidence, limits
- [`docs/AMD_GPU.md`](docs/AMD_GPU.md) — AMD GPU setup and inference verification on WSL2
- [`docs/`](docs/README.md) — architecture, settings, testing, troubleshooting, runbook
