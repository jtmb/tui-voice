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
- Optional **Live conversation** is a separate explicit toggle. Only that mode
  automatically submits spoken turns, to the selected local model in the same
  chat. Read [docs/LIVE_CONVERSATION.md](docs/LIVE_CONVERSATION.md) before changing it.
- Dictation is manual on/off: pauses and long turns must not auto-stop it.
- Recording works while a reply is running; transcribe immediately and queue the
  draft until the message box returns.
- Read-aloud is a setting with an on/off switch and a local voice choice. A
  `🔊` / `🔇` footer button next to Voice toggles the same setting and
  stops current playback; unmuting resumes the interrupted reply. It must remain
  functional and local.
- Voice settings use a native-sized OpenCode dialog with the active theme, not a
  custom full-screen route.
- Load globally from `~/.config/opencode/cli.json`; survive a TUI restart.
- Local only: no cloud STT/TTS, no TV/ADB, no upload.

## Scope

`tui-voice` is a standalone **OpenCode TUI plugin** for local voice dictation and
read-aloud. It is self-contained: it provisions and drives a local
Whisper/Kokoro stack (`setup_stack.py`) that can be pointed elsewhere; no other
repository is assumed and all product/UI names here are `tui-voice` / `tui.voice`.

## Environment

| Item | Value |
| --- | --- |
| Repository | this checkout (`~/repos/tui-voice` here) |
| Plugin loader | `~/.config/opencode/cli.json` → this checkout |
| Entrypoint | `./tui` → `tui.tsx` |
| Helper | `voice_helper.py` |
| Configuration | env vars, then `~/.config/tui-voice/config.json`, then `~/.local/share/tui-voice` (`voice_config.py`) |
| Speech models | Installed by `setup_stack.py` into `service/` and `voice/` (pinned models, three venvs) |
| Speech service | `service` setting: directory providing `web_voice.py` and `.venv` |
| Voice directory | `voice` setting: models, `asr-worker.py`, `.asr-venv`, `.venv` |
| Python | `TUI_VOICE_PYTHON`, then `service/.venv/bin/python`, then `python3` |
| Warm ASR / TTS Python | `voice/.asr-venv/bin/python` and `voice/.venv/bin/python` |
| ffmpeg | Auto-installed for the detected OS (`platform_deps.py`); WSL2 uses WinGet, Linux the distro manager (override `TUI_VOICE_FFMPEG`) |
| Playback tool | Linux: `pw-play`/`paplay`/`aplay`/`ffplay` auto-detected (override `TUI_VOICE_PLAYER`) |
| Microphone | Automatic; override `TUI_VOICE_MIC` |
| Error log | `~/.local/state/tui-voice/voice.log` (override `TUI_VOICE_LOG_DIR`) |
| Platform | WSL2 (Windows microphone/speakers) or native Linux (PipeWire/PulseAudio, ALSA fallback) |

Services do not auto-start. The plugin needs the configured stack's
interpreters/models and a local `ffmpeg`.

## Repository map

```
tui.tsx                 OpenCode TUI plugin: footer button, settings dialog, queues
live_draft.ts           Footer-anchored native composer updates and draft ownership
live_dictation.py       Single-flight bounded streaming ASR and live hypotheses
live_conversation.py    Continuous microphone turn detection and bounded local ASR
live_controller.ts     Live turn scheduling, streamed phrases, cancellation
opencode_live.ts        Verified OpenCode v2 API and local-model selection bridge
live_mode.ts            Live microphone/session lifecycle and draft recovery
voice_helper.py         Local mic capture, VAD, Whisper transcription, Kokoro TTS
audio_backend.py        Platform audio: ffmpeg, devices, capture argv, playback
voice_config.py         Portable settings: stack dirs, interpreters, audio overrides
platform_deps.py        OS detection and audio package installation
setup_stack.py          Downloads models, creates venvs, writes the stack adapters
stack/                  Adapter sources installed into the stack (web_voice, asr-worker, voices)
tts_worker.py           Persistent local Kokoro worker (AF_UNIX, no cloud)
README.md               Product page for users (not documentation)
INSTALL.md              Install guide: global vs project-local plugin scope
rules.md                Current product contract
HANDOFF.md              Current state, evidence, open work
docs/                   Architecture, settings, testing, troubleshooting, runbook
docs/media/             README logo, product GIF, and its poster still
```

## Common commands

For development checks/builds, first use the executable per-process setup in
[`docs/STORAGE.md`](docs/STORAGE.md):
`tools/artifact_env.py --reserve-bytes N -- command ...`. It routes supported
generated output to the authorized H root without changing the existing voice
runtime or starting services. See `MEMORY.md`; do not relocate AF_UNIX sockets
or treat this setup as a physical disk quota.

```bash
cd ~/repos/tui-voice

# Syntax/helper check
python3 -m py_compile voice_config.py audio_backend.py voice_helper.py tts_worker.py

# Plugin discovery
opencode plugin list

# What the plugin resolved (config file, stack dirs, interpreters)
python3 voice_config.py --json

# OS dependency report; --install acts on it (needs root or passwordless sudo)
python3 voice_helper.py doctor

# Speech stack: status, install, or verify end to end
python3 setup_stack.py
python3 setup_stack.py --install
python3 setup_stack.py --install --selftest

# Local voice helper checks (the helper runs in the stack interpreter)
python3 voice_helper.py devices
$(python3 voice_config.py --python) voice_helper.py \
  transcribe --wav /path/to/16k-mono.wav
python3 voice_helper.py warm --status
python3 voice_helper.py warm --start
```

A fresh TUI loads the plugin globally. A path change or a newly added TUI-only
plugin requires closing and reopening the TUI; `/reload` alone is not sufficient.

## Architecture

- `tui.tsx` registers the plugin, footer button, settings command, and assistant
  text-ended subscription. Its `dictate` helper streams JSON-line hypotheses
  into the native composer, anchored to the current session's footer.
- `live_dictation.py` keeps one ASR request in flight, coalesces previews and
  commits bounded phrases while capture continues. `live_draft.ts` revises only
  owned dictated text. Anything the user types or fixes is kept and dictation
  continues after it (it never freezes); `/voice-text` re-inserts the transcript.
- `voice_helper.py dictate` streams platform capture audio (WSL2 `dshow`, Linux
  `pulse`/`alsa`) until explicitly toggled off. Its separate `record` diagnostic
  remains bounded. Both use local audio.
- `tts_worker.py` keeps one Kokoro ONNX session loaded and serves chunks over
  `$XDG_RUNTIME_DIR/tui-voice/tts.sock`. The warm ASR worker serves
  Whisper over `$XDG_RUNTIME_DIR/tui-voice/asr.sock`.
- The plugin starts both workers in the background at setup and before dictation
  or read-aloud. ASR and default CPU TTS retain the cold service fallback.
  Explicit `TUI_VOICE_TTS_PROVIDER=migraphx` must fail when GPU TTS is unavailable,
  including when a stale CPU worker is still running.
- AMD GPU TTS uses the existing Kokoro ONNX worker and local AF_UNIX transport.
  Follow [`docs/AMD_GPU.md`](docs/AMD_GPU.md); preserve SHA-256 verification of all
  model-manifest entries. Do not treat missing `/dev/kfd` as a WSL GPU check.
- Settings are stored under the `tui-voice` key and apply globally.
- Dictation is queued if the session is replying; read-aloud is a separate queued
  speech path.
- Live conversation listens while speaking, consumes `session.text.delta`, and
  uses `session.prompt` / `session.interrupt` through the separate v2 adapter.
  It requires the existing warm local ASR worker. No new model download is added.

## Verification checklist

- `opencode plugin list` includes this checkout.
- Fresh TUI from outside this repo shows the compact rounded footer panel: status circle, `🎙`, divider, and speaker
  in the same one-row footer bar; widths do not shift between states.
- `Ctrl+Alt+V` changes its status circle from `○` to amber `●` (`Listening`).
- A short spoken turn inserts text into the message box and does not send.
- Dictation while a reply runs transcribes and inserts after the reply.
- `Ctrl+Alt+S` opens a centered settings dialog; arrows change values; `Esc` closes.
- Footer `🔊` / `🔇` (or `Ctrl+Alt+M`/`F10`) toggles read-aloud, stops current
  playback, and resumes the interrupted reply on unmute.
- `voice_helper.py warm --status` reports a CPU ASR worker and a TTS worker; a
  fixture transcript and `say --no-play` report `warm_chunks`.
- For explicit MIGraphX TTS, run `tts_worker.py --check` with the configured TTS
  interpreter and inspect actual GPU node execution in the profiling summary.
  A registered provider alone is insufficient. Restart the TTS worker after
  changing its provider, model, or interpreter. Record hardware results honestly;
  source-level checks cannot establish RX 7900 XT acceleration or speed.
- Read-aloud On speaks a completed assistant reply; Off stays silent.
- `voice_helper.py devices` lists real inputs with the automatic selection first:
  on WSL2 the Focusrite before the virtual Steam microphone; on Linux a hardware
  source before monitors, StreamMix, and other virtual sources.
- No audio file remains after transcription; no cloud request is made.

## Open work and limits

- Mouse-click activation of the footer button is not separately automated; the
  keyboard shortcuts are the guaranteed path.
- Physical microphone clarity and room noise are owner-dependent; use **Input
  gain**, **Speech threshold**, and **Test microphone**.
- Read-aloud and dictation can overlap if the user starts speaking during playback;
  headphones are recommended.
- The settings dialog is custom JSX; host dialog sizing/theming can change between
  OpenCode releases.

## Safety

- Keep audio local. Never add a cloud STT/TTS provider without explicit owner
  approval.
- Dictation never sends a transcript automatically. Explicitly enabled Live is
  the scoped exception in `rules.md`; keep its local-model check and OFF guard.
- Do not add TV/ADB/Control Center management to this plugin.
- Keep secrets out of source; paths/overrides belong in environment variables.
- Do not commit `.wav` files, recordings, or private transcripts.
