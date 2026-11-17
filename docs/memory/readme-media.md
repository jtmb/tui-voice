---
{"version":1,"owner":"opencode-memory","scope":"repo","repository":"/home/brajam/repos/tui-voice","id":"readme-media","title":"README media provenance","tags":["readme","media","gif","provenance"],"kind":"decision","updated":"2026-11-16","verified":"2026-11-16","sources":["README.md","docs/media/gameplay.gif","docs/media/gameplay-poster.png"]}
---

# README media provenance

## Decision

The product page hero media is `docs/media/gameplay.gif` (960x531, 9.2 s loop,
17 frames sampled from a real capture timeline) with
`docs/media/gameplay-poster.png` as its still. The README frames it as a real
capture and must keep doing so.

## What it actually shows

Rendered from the bytes a live OpenCode TUI session emitted with the local
plugin loaded: the idle footer panel with the startup toast, the command palette
being filtered letter by letter as `voice` is typed, the palette selection
moving through the six `Voice:` commands, the composer again, and the Voice
settings dialog with its selection moving. No frame is staged, recreated from
memory, or edited to add UI.

## Constraints

- Do not invent UI states, transcripts, or settings values. If a state was not
  captured, leave it out and say so; a real dictation turn is **not** in the GIF.
- Keep speech local: a demo must never introduce a cloud STT/TTS path.
- Raw capture bytes and the capture/render scripts stay out of the repository;
  they live in the H run tree recorded in `HANDOFF.md`.
- Re-capture after any footer, palette, or settings-dialog change so the GIF
  cannot drift from the shipped UI.

## Regeneration

`capture_live.py` drives a real TUI over a pty, typing one key at a time and
moving selections, and stores raw bytes plus chunk timings; `render_live.py`
replays that stream with `render_tui.py` (grid parser plus Pillow) at 10 fps,
merging identical frames into holds. Re-run both through
`tools/artifact_env.py --reserve-bytes ... --` and copy only the two media files
into `docs/media/`. Do not replace the timeline with a handful of keyframes: the
owner asked for it to read as playback, not a slideshow.
