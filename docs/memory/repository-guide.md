---
{"version":1,"owner":"opencode-memory","scope":"repo","repository":"/home/brajam/repos/tui-voice","id":"repository-guide","title":"Repository guide","tags":["project","entrypoints","commands"],"kind":"guide","updated":"2026-11-15","verified":"2026-11-15","sources":["README.md","AGENTS.md"]}
---

# Repository guide

## Purpose

A standalone OpenCode TUI plugin for local voice dictation into the message draft and local read-aloud of completed replies.

## Start points

- `tui.tsx` is the plugin entrypoint; `voice_helper.py` provides microphone, transcription, and speech helpers; `tts_worker.py` is the persistent local Kokoro worker.
- Read `rules.md` for the product contract and `README.md` for setup, controls, and troubleshooting.
- The README documents `opencode plugin list` as a way to verify plugin discovery.

## Authority

This guide is an orientation aid. Current project instructions, README files, and canonical documentation take precedence when details change.
