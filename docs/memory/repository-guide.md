---
{"version":1,"owner":"opencode-memory","scope":"repo","repository":"/home/brajam/repos/tui-voice","id":"repository-guide","title":"Repository guide","tags":["project","entrypoints","commands"],"kind":"guide","updated":"2026-11-16","verified":"2026-11-16","sources":["README.md","INSTALL.md","AGENTS.md"]}
---

# Repository guide

## Purpose

A standalone OpenCode TUI plugin for local voice dictation into the message draft and local read-aloud of completed replies.

## Start points

- `tui.tsx` is the plugin entrypoint; `voice_helper.py` provides microphone, transcription, and speech helpers; `tts_worker.py` is the persistent local Kokoro worker.
- Read `rules.md` for the product contract, `INSTALL.md` for global vs project-local installation, and `docs/README.md` for the documentation index.
- `README.md` is a user-facing product page, not a manual. Keep install steps and reference material in `INSTALL.md` and `docs/`.
- `INSTALL.md` documents `opencode plugin list` as the discovery check for both scopes.

## Authority

This guide is an orientation aid. Current project instructions, README/INSTALL files, and canonical documentation take precedence when details change.
