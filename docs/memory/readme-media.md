---
{"version":1,"owner":"opencode-memory","scope":"repo","repository":"/home/brajam/repos/tui-voice","id":"readme-media","title":"README media provenance","tags":["readme","media","gif","provenance"],"kind":"decision","updated":"2026-11-16","verified":"2026-11-16","sources":["README.md","docs/media/gameplay.gif","docs/media/gameplay-poster.png"]}
---

# README media provenance

## Decision

The product page hero media is `docs/media/gameplay.gif` (960x531, 8.1 s loop,
six held states) with `docs/media/gameplay-poster.png` as its still. The README
frames it as a real capture and must keep doing so.

## What it actually shows

Rendered from the bytes a live OpenCode TUI session emitted with the local
plugin loaded: idle footer panel with the startup toast, the command palette,
the palette filtered to the six `Voice:` commands, the composer again, the
Voice settings dialog, and the closed footer. No frame is staged, recreated
from memory, or edited to add UI.

## Constraints

- Do not invent UI states, transcripts, or settings values. If a state was not
  captured, leave it out and say so; a real dictation turn is **not** in the GIF.
- Keep speech local: a demo must never introduce a cloud STT/TTS path.
- Raw capture bytes and the capture/render scripts stay out of the repository;
  they live in the H run tree recorded in `HANDOFF.md`.
- Re-capture after any footer, palette, or settings-dialog change so the GIF
  cannot drift from the shipped UI.

## Regeneration

`capture_tui.py` drives a real TUI over a pty and stores raw bytes plus chunk
timings; `render_demo.py` replays that stream with `render_tui.py` (grid parser
plus Pillow) and writes the GIF and poster. Re-run both through
`tools/artifact_env.py --reserve-bytes ... --` and copy only the two media files
into `docs/media/`.
