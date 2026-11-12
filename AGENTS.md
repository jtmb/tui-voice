# AGENTS.md

Guidance for agents working in `~/repos/tui-voice`.

## Mandatory owner rules

**Read and follow [`rules.md`](rules.md) before work.** It is the product contract
for this plugin and overrides historical notes, not system/developer safety
instructions.

In particular:

- The voice button stays in the **bottom footer**. Do not move it to the composer,
  a dialog, or a floating overlay.
- Dictation **types into the message box** and never sends automatically.
- Recording works while a reply is running; transcribe immediately and queue the
  draft until the message box returns.
- Read-aloud is a setting with an on/off switch and a local voice choice. A
  `🔊 Replies` / `🔇 Replies` footer button next to Voice toggles the same setting and
  stops current playback; it must remain functional and local.
- Voice settings use a native-sized OpenCode dialog with the active theme, not a
  custom full-screen route.
- Load globally from `~/.config/opencode/cli.json`; survive a TUI restart.
- Local only: no cloud STT/TTS, no TV/ADB, no upload.

## Scope

`tui-voice` is a standalone **OpenCode TUI plugin** for local voice dictation and
read-aloud. It is not part of the Fire TV app source. It depends on the existing
local Whisper/Kokoro service in `~/repos/fire-tv-next/lulu-launcher/service`, but
all product/UI names here are `tui-voice` / `tui.voice`.

## Environment

| Item | Value |
| --- | --- |
| Repository | `~/repos/tui-voice` |
| Plugin loader | `~/.config/opencode/cli.json` → `/home/brajam/repos/tui-voice` |
| Entrypoint | `./tui` → `tui.tsx` |
| Helper | `voice_helper.py` |
| Whisper service | `~/repos/fire-tv-next/lulu-launcher/service` (override `TUI_VOICE_SERVICE`) |
| Python | `.../service/.venv/bin/python` (override `TUI_VOICE_PYTHON`) |
| Windows ffmpeg | WinGet Gyan.FFmpeg, auto-discovered (override `TUI_VOICE_FFMPEG`) |
| Microphone | Automatic; override `TUI_VOICE_MIC` |
| Platform | WSL2 + Windows microphone/speakers |

Services do not auto-start. The plugin needs the voice service's Python/models and
Windows ffmpeg available.

## Repository map

```
tui.tsx                 OpenCode TUI plugin: footer button, settings dialog, queues
voice_helper.py         Windows mic capture, VAD, Whisper transcription, Kokoro TTS
README.md               User-facing overview and install
rules.md                Current product contract
HANDOFF.md              Current state, evidence, open work
docs/                   Architecture, settings, testing, troubleshooting, runbook
```

## Common commands

```bash
cd ~/repos/tui-voice

# Syntax/helper check
python3 -m py_compile voice_helper.py

# Plugin discovery
opencode plugin list

# Local voice helper checks
~/repos/fire-tv-next/lulu-launcher/service/.venv/bin/python voice_helper.py devices
~/repos/fire-tv-next/lulu-launcher/service/.venv/bin/python voice_helper.py \
  transcribe --wav ~/repos/fire-tv-next/lulu-launcher/voice/fixtures/short-2s.wav
```

A fresh TUI loads the plugin globally. A path change or a newly added TUI-only
plugin requires closing and reopening the TUI; `/reload` alone is not sufficient.

## Architecture

- `tui.tsx` registers the plugin, footer button, settings command, and assistant
  text-ended subscription. It calls `voice_helper.py` for capture and transcription.
- `voice_helper.py` records Windows `dshow` audio with ffmpeg, stops after silence,
  runs local Whisper, and can synthesize/play Kokoro speech.
- Settings are stored under the `tui-voice` key and apply globally.
- Dictation is queued if the session is replying; read-aloud is a separate queued
  speech path.

## Verification checklist

- `opencode plugin list` includes `/home/brajam/repos/tui-voice`.
- Fresh TUI from outside this repo shows `◉ Voice` in the bottom footer.
- `Ctrl+Alt+V` changes it to `Listening`.
- A short spoken turn inserts text into the message box and does not send.
- Dictation while a reply runs transcribes and inserts after the reply.
- `Ctrl+Alt+S` opens a centered settings dialog; arrows change values; `Esc` closes.
- Footer `🔊 Replies` / `🔇 Replies` (or `Ctrl+Alt+M`/`F10`) toggles read-aloud and stops
  current playback.
- Read-aloud On speaks a completed assistant reply; Off stays silent.
- `voice_helper.py devices` lists the real Windows input (Focusrite before the
  virtual Steam microphone).
- No audio file remains after transcription; no cloud request is made.

## Open work and limits

- Mouse-click activation of the footer button is not separately automated; the
  keyboard shortcuts are the guaranteed path.
- Physical microphone clarity and room noise are owner-dependent; use **Input
  gain**, **Silence threshold**, and **Test microphone**.
- Read-aloud and dictation can overlap if the user starts speaking during playback;
  headphones are recommended.
- The settings dialog is custom JSX; host dialog sizing/theming can change between
  OpenCode releases.

## Safety

- Keep audio local. Never add a cloud STT/TTS provider without explicit owner
  approval.
- Never send a transcript automatically; only insert into the draft.
- Do not add TV/ADB/Control Center management to this plugin.
- Keep secrets out of source; paths/overrides belong in environment variables.
- Do not commit `.wav` files, recordings, or private transcripts.
