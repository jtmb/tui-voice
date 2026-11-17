# tui-voice documentation

Start with [`../INSTALL.md`](../INSTALL.md) to install and
[`../rules.md`](../rules.md) for the current product contract.

| Document | Purpose |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Files, data flow, state, queues, dependencies |
| [SETTINGS.md](SETTINGS.md) | Every setting, defaults, and tuning guidance |
| [TESTING.md](TESTING.md) | Fresh-TUI gate, helper checks, verified evidence |
| [AMD_GPU.md](AMD_GPU.md) | RX 7900 XT runtime setup and proof of GPU inference |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Plugin not loading, no audio, no transcript, theme issues |
| [RUNBOOK.md](RUNBOOK.md) | Install, update, restart, and rollback steps |
| [STORAGE.md](STORAGE.md) | H-backed development output, admission checks, and limits |
| [BACKLOG.md](BACKLOG.md) | Known limits and possible follow-up work |

The repository is intentionally small: the plugin is `tui.tsx`, streaming ASR and
draft ownership live in `live_dictation.py` and `live_draft.ts`, and the
microphone/voice helper is `voice_helper.py` with the optional warm Kokoro worker
`tts_worker.py`.
