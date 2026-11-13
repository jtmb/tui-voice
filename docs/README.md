# tui-voice documentation

Start with [`../README.md`](../README.md) for install/use and
[`../rules.md`](../rules.md) for the current product contract.

| Document | Purpose |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Files, data flow, state, queues, dependencies |
| [SETTINGS.md](SETTINGS.md) | Every setting, defaults, and tuning guidance |
| [TESTING.md](TESTING.md) | Fresh-TUI gate, helper checks, verified evidence |
| [AMD_GPU.md](AMD_GPU.md) | RX 7900 XT / WSL runtime setup and proof of GPU inference |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Plugin not loading, no audio, no transcript, theme issues |
| [RUNBOOK.md](RUNBOOK.md) | Install, update, restart, and rollback steps |
| [BACKLOG.md](BACKLOG.md) | Known limits and possible follow-up work |

The repository is intentionally small: the plugin is `tui.tsx`, the Windows
microphone/voice helper `voice_helper.py`, and the optional warm Kokoro worker
`tts_worker.py`.
