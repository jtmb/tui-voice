# tui-voice

Local voice dictation and read-aloud for the **OpenCode TUI**.

Speak into the Windows microphone; the plugin transcribes with local Whisper and
types the text into the OpenCode message box. It never sends the prompt. Completed
assistant replies can also be read aloud with local Kokoro voices.

No cloud service, no TV, no Control Center login.

## Features

- **Dictation into the draft** — one turn, local Whisper, text in the message box.
- **Silence auto-stop** — normal turns end after a pause; a 60 s hard maximum only stops a turn that never pauses.
- **Works during a reply** — transcribes immediately and queues the draft until
  the message box returns.
- **Read-aloud** — speaks completed assistant replies through Windows speakers.
- **Reply sound** — the speaker segment of the same footer panel stops current playback and toggles
  reply read-aloud without opening settings. Unmuting resumes the interrupted reply
  from the current local speech chunk.
- **Settings dialog** — tune timing, microphone, gain, silence threshold, voice;
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
| Reply sound | `🔊` / `🔇` speaker segment in the footer panel |
| Reply sound | `Ctrl+Alt+M` or `F10` |
| Settings | `Ctrl+Alt+S` |
| Settings | Command palette → **Voice: Settings** |

Speak normally. The button shows `Listening`, then `Transcribing`; the transcript
appears in the message box. Edit it and send it yourself.

If a reply is still running, the transcript is held and inserted when the message
box returns. A second press while listening only reminds you to pause; it does not
destroy the turn.

## Settings

The settings screen is a centered, native-sized OpenCode dialog using the active
theme. Keyboard: `↑↓` select, `←→` change, `Enter` choose, `Esc` close.

| Setting | Default | What it does |
| --- | --- | --- |
| Finish after a pause | 1.5 s | Silence before the turn ends |
| Maximum turn | 60 s | Hard stop only if silence is never detected |
| Input | Automatic | Windows microphone; automatic prefers the real input |
| Silence threshold | −35 dB | Higher values (−25) treat more room noise as silence |
| Input gain | Off | Boost a quiet mic; try 6–12 dB |
| Test microphone | — | Records 2 s and reports RMS/peak |
| Read replies aloud | On | Local Kokoro read-back; also toggled by the footer speaker icon |
| Read-aloud voice | Heart | Heart, Velvet, English Rose, Dora |
| Test read-aloud | — | Plays a sample so mute/unmute resume can be checked |
| Reset to defaults | — | Restores the values above |

### If words are missing

1. Increase **Finish after a pause** to 0.6–0.8 s. This is the usual fix for
   clipped sentence endings.
2. Run **Test microphone**. `quiet`/`silent` means gain or device selection is the
   problem.
3. Raise **Input gain** for a quiet input.
4. Adjust **Silence threshold** if room noise prevents auto-stop.
5. Keep **Maximum turn** long; normal turns end on a pause. It is only a safety stop.

The virtual Steam Streaming Microphone is silent on this machine. Automatic
selection prefers the Focusrite Analogue input; choose **Input** explicitly if you
speak into something else.

## How it works

```
footer button / shortcut
        │
        ▼
voice_helper.py record
  ffmpeg dshow → 16 kHz WAV → silence stop
        │
        ▼
voice_helper.py transcribe
  warm Whisper worker → text (cold service fallback)
        │
        ▼
message box draft (never auto-sent)
```

Read-aloud listens to completed assistant text in the current session and runs
`voice_helper.py say` with the selected Kokoro voice. A persistent warm TTS worker
keeps the ONNX voice loaded between chunks and replies.

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

Warm workers are optional. If either is absent or fails, the helper uses the
unchanged cold local path. Manage them with:

```bash
~/repos/fire-tv-next/lulu-launcher/service/.venv/bin/python voice_helper.py warm --status
~/repos/fire-tv-next/lulu-launcher/service/.venv/bin/python voice_helper.py warm --start
~/repos/fire-tv-next/lulu-launcher/service/.venv/bin/python voice_helper.py warm --stop
```

Both keep audio local and exit after 30 idle minutes; the plugin starts them again
before the next dictation or reply.

## Privacy

- Audio is written to the Windows temp directory only for ffmpeg, then deleted
  immediately after transcription.
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
  gain or pause duration.
- **Always waits to the maximum** — room noise is above the silence threshold;
  raise the threshold toward −25 dB.
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
- [`docs/`](docs/README.md) — architecture, settings, testing, troubleshooting, runbook
