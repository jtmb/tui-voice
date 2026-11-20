# Plan — last-word cutoff and read-aloud seek controls

Status: **not implemented** (saved 2026-11-20 at the owner's request).
Owner answers recorded: cut-off happens in **both** read-aloud and dictation; seek
step is **one sentence**; controls are **F7/F8 + mouse wheel over the speaker icon**
(`/read-back`, `/read-forward` too).

Owner rules that apply: `rules.md` (one-row footer, fixed widths, no silent removal
of the speaker), and the new "Working with the owner" rule below: ask questions only
with the question tool.

## 1. Last word cut off

### 1a. Read-aloud (voice reading replies)
- Cause: each sentence group is a separate WAV that Kokoro ends on the final sound;
  on WSL2 it plays through Windows `SoundPlayer.PlaySync`, which clips the last
  fraction of a second. The end of each group, and of the whole reply, is lost.
- Change: add ~250 ms of trailing silence to every generated clip in **one shared
  function** used by both `tts_worker.py` (`_wav_bytes`) and the fallback
  `stack/web_voice.py`, so neither path can drift. Keep the 8 MB per-clip budget
  check after padding.
- Verify:
  - unit test: a generated clip's final 50 ms is silent, and the header/sample rate
    are unchanged; a clip at the size limit fails rather than silently truncating;
  - real playback of a reply ending in a short word ("…and that's it.") through
    Windows SoundPlayer on WSL2 and through the detected Linux player;
  - owner listens once.

### 1b. Dictation (last word missing after stopping the mic)
- Cause 1: stop closes ffmpeg's stdin (`q`) immediately, dropping audio still in
  the Windows capture buffer — often exactly the last word.
- Cause 2: Whisper drops a trailing word when the audio ends abruptly with no
  trailing silence; the 8 s forced splits cut mid-word the same way.
- Change:
  - after a stop request, keep reading capture for ~400 ms before closing (the UI
    is already showing "transcribing", so no extra wait is felt);
  - append ~400 ms of silence to the final phrase and to each 8 s forced split in
    `live_dictation.py` before transcribing. Trailing silence must not add words:
    verify with the existing fixture tests.
- Verify:
  - fixture that ends immediately after a word: stop right away, last word kept;
  - existing `tests/test_live_dictation.py` still passes (sealing, ordering,
    backpressure);
  - one real turn where F9 is pressed straight after the last word.

## 2. Rewind and fast-forward (footer stays one row)

- Keys: `F7` rewind, `F8` forward (beside F9 mic, F10 speaker); slash commands
  `/read-back` and `/read-forward`. Mouse wheel over the 🔊 icon seeks (up = back,
  down = forward) via the footer box's `onMouseScroll`; no width change.
- Step: one sentence. `chunks()` keeps sentence boundaries and merges very short
  sentences below a minimum length.
- Behaviour:
  - rewind restarts the current sentence if > ~2 s into it, else the previous one;
  - forward moves to the next sentence; past the last, playback stops;
  - presses within ~300 ms accumulate (3 presses = 3 sentences) and issue one
    restart;
  - while muted, seeking moves the resume point and the toast says so;
  - toast: "⏪ Sentence 3 of 12" / "⏩ Sentence 5 of 12";
  - restart reuses the already-loaded voice model (~1 s); caching generated
    sentences is a later option if that latency is noticeable.
- Code touch points: `voice_helper.py` (`chunks`, `say` start/progress),
  `tui.tsx` (speech state, bindings, wheel handler, toasts, mute/resume),
  `voice_config` unchanged.
- Verify:
  - unit tests for sentence splitting (abbreviations, code blocks, long sentences)
    and the seek arithmetic (accumulation, boundaries, muted);
  - TUI sim scenario with a fake `say` that records its start sentence: F7, F8 and
    wheel must request the expected sentence;
  - real reply on the owner's machine.

## 3. Rules update
- `rules.md`: new "Working with the owner" section — ask questions only with the
  question tool, never as plain text in a reply; give concrete options with the
  recommendation first.

## 4. Cleanup found while planning
- The v0.3.1 re-exec test writes a fake "could not switch to the stack interpreter"
  line into the owner's real `voice.log`. Patch logging in that test.
- The 2026-11-18 13:54 "voice helper crashed" burst was a temporary WSL interop
  outage (no interop bridge); it recovered and is not a plugin bug.

## Out of scope
- No new footer buttons, no change to footer width or height.
- No cloud speech, no new dependency.
- No release unless the owner asks.

## Order of work
1. `rules.md` question rule (small, independent).
2. Dictation padding + stop delay, with tests.
3. Read-aloud padding (shared function), with tests.
4. Sentence seek: helper first, then TUI bindings and wheel, then sim scenario.
5. Docs: `INSTALL.md`, `docs/SETTINGS.md`, `docs/TESTING.md`, README controls.
6. Handoff entry with evidence, including which distros/players were exercised.
