# tui-voice

Local voice dictation for the **OpenCode TUI**. It records one spoken turn,
transcribes it with local Whisper, and types the text into the message box. It
never sends automatically.

Standalone repository: `~/repos/tui-voice`.

## Use

1. Open a chat session and wait until it is idle.
2. Click `◉ Voice` in the bottom footer, press `F9`, `Ctrl+Alt+V`, or run
   `/dictate`.
3. Speak. It stops about 0.4 s after you pause, then shows `Transcribing`.
4. The transcript appears in the message box. Edit it and send it yourself.

While listening, a second press reminds you to pause; it does not cut off the
transcription. If a reply is still running, it transcribes anyway and holds the
draft until the message box returns, then inserts it automatically.

## Settings

Open **Voice: Settings** from the command palette, or press **Ctrl+Alt+S**.

The settings screen tunes:

- **Finish after a pause** — 0.3–1.2 s. Increase this if words at pauses are cut.
- **Maximum turn** — 3–12 s hard stop.
- **Input** — automatic or an explicit Windows microphone.
- **Silence threshold** — -25 to -45 dB. Higher values treat more room noise as
  silence.
- **Input gain** — Off to +18 dB for quiet microphones.
- **Test microphone** — records 2 s and reports the input level.
- **Read replies aloud** — on/off switch for local Kokoro read-back.
- **Read-aloud voice** — Heart, Velvet, English Rose, or Dora.
- **Reset to defaults**.

Keyboard: `↑↓` select, `←→` change, `Enter` choose, `Esc` back. The screen uses
the active OpenCode theme and the same panel/row conventions as the rest of the
TUI. Read-aloud speaks completed assistant replies through the Windows speakers;
use headphones if the microphone would otherwise pick up the playback.

## Install

The plugin is TUI-only, so it is loaded from `cli.json`:

```json
{
  "plugins": ["/home/brajam/repos/tui-voice"]
}
```

Package entrypoint: `./tui` → `tui.tsx`.

## Dependencies

- Windows `ffmpeg.exe` with DirectShow microphone support. Auto-discovers the
  WinGet Gyan.FFmpeg path; override with `TUI_VOICE_FFMPEG`.
- The local Whisper service at
  `~/repos/fire-tv-next/lulu-launcher/service` by default. Override the service
  directory with `TUI_VOICE_SERVICE`, and the Python executable with
  `TUI_VOICE_PYTHON`.
- Override the microphone with `TUI_VOICE_MIC`.

Those paths point at the existing local voice stack; no cloud service is used.

## Privacy

- Audio is written to the Windows temp directory only for ffmpeg, then deleted
  immediately after local transcription.
- The transcript goes only into the local OpenCode message box.
- Read-aloud uses the existing local Kokoro model and Windows speakers.
- No TV, no Control Center login, no automatic send, no upload.

## Repository docs

- [`AGENTS.md`](AGENTS.md) — agent rules and verification checklist
- [`rules.md`](rules.md) — current product contract
- [`HANDOFF.md`](HANDOFF.md) — current state, evidence, limits
- [`docs/`](docs/README.md) — architecture, settings, testing, troubleshooting, runbook
