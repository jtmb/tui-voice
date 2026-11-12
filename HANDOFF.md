# tui-voice handoff

## 2026-11-11 EST — compact footer panel

- Replaced the two separated footer buttons with one compact rounded panel that
  stays in the existing **one-row** footer: half-cell rounded caps, the status
  circle + studio microphone `🎙`, a recessed `│` divider, then the speaker.
- Kept the studio microphone (the handheld `🎤` read as a flashlight) and used
  the text-presentation form so it fits the one-row line. The status circle sits
  directly against the microphone; one cell is reserved after the microphone so
  the divider cannot clip it. The half-cell caps provide the end gutters.
- Explicitly rejected a three-row rounded border: the footer bar must not grow.
- State styling is the status circle only: blue `○` idle, amber `●` listening,
  cyan `◐` transcribing, red `✕` error. No tinted block.
- The panel hides while the settings dialog is open, returning an empty one-row
  spacer so the footer does not jump when the dialog closes.
- Verified in a fresh TUI at 80, 100, and 140 columns: `▐○🎙 │🔊▌`,
  `▐●🎙 │🔊▌`, and `▐○🎙 │🔇▌` keep the same columns and the footer stays
  one row.

## 2026-11-11 EST — persistent warm ASR and TTS workers

- Added `tts_worker.py`: one Kokoro `v1.0.int8` ONNX session stays loaded and
  serves bounded text chunks over a uid-owned AF_UNIX socket. It has
  `--start/--status/--stop/--run`, a 30-minute idle exit, and a threaded request
  loop so `status` answers even while a chunk is synthesizing.
- `voice_helper.py transcribe` now asks the existing warm Whisper worker first;
  `say` asks the warm TTS worker for each chunk. Any error or missing worker falls
  through to the unchanged cold service path.
- Added `voice_helper.py warm --start|--status|--stop [--kind asr|tts|both]`.
  Starts are serialized with a lock file, so a plugin reload cannot race a busy
  worker. The plugin prewarms at setup and before each dictation/read-aloud; the
  workers survive TUI restarts.
- Measured on this CPU-only WSL host:
  - Warm ASR fixture: ~0.6 s total helper run (cold ~1.5 s), same
    transcript/confidence.
  - Warm TTS short sentence: ~1.1 s (cold ~3.2 s); 8 CPU threads and a disabled
    ORT CPU arena keep it fast without retaining memory across turns.
  - Mute killed the active `say`; unmute started a new helper with
    `--start-chunk` and the warm worker request count increased.
- GPU remains unavailable: WSL exposes no `/dev/dri`, `/dev/kfd`, ROCm, CUDA, or
  `nvidia-smi`; the Windows host has an AMD RX 7900 XT. `faster-whisper` needs
  CUDA, and Kokoro runs on `CPUExecutionProvider`.

## 2026-11-12 UTC — standalone plugin, native settings, footer button

- Owner moved the plugin out of `fire-tv-next` into the standalone repository
  `~/repos/tui-voice` and asked for global loading. `~/.config/opencode/cli.json`
  now contains `/home/brajam/repos/tui-voice`; the old plugin directory was
  removed. `opencode plugin list` reports the new path. Load was verified from
  `/home/brajam`, outside the repository.
- The voice button is back in the **prompt footer** after the owner explicitly
  rejected moving it. It uses mouse-down/up with a debounce, is focusable, and
  keeps `Ctrl+Alt+V`, `F9`, and `/dictate` as keyboard paths. A fresh TUI from
  outside the repo showed the microphone icon and `Ctrl+Alt+V` changed it to the Listening state.
- `Ctrl+Alt+S` opens a **centered, native-sized (`large`) OpenCode dialog** using
  `theme.surface("dialog")`, dialog border/background, and native
  `background.action.primary.focused` / `text.action.primary.focused` selected-row
  colors. It is no longer a full-screen plugin route.
- Settings cover pause duration, maximum turn, input device, silence threshold,
  input gain, microphone test, read-aloud On/Off, read-aloud voice, and reset.
  Read-aloud defaults On; voices are Heart, Velvet, English Rose, Dora.
- Added a footer 🔊/🔇 speaker icon next to Voice, with `Ctrl+Alt+M`
  and `F10` bindings. It toggles the read-aloud setting and terminates the active
  `say` helper process; the settings row stays in sync. Muting records the current
  speech chunk and unmuting resumes via `say --start-chunk`; verified with the
  **Test read-aloud** sample (process stopped, then restarted on unmute).
- Dictation no longer refuses while a reply is running. It records and transcribes
  immediately, then queues the draft until the message box returns and inserts it.
  `session.text.ended` drives read-aloud for completed assistant text in the
  current session.
- Verified evidence:
  - Fixture transcript `Turn on the living room lights.` inserted into the message
    box without sending.
  - `voice_helper.py say --text "read aloud final check." --voice heart` was
    spawned for a disposable session reply.
  - Settings dialog opened, values changed with arrows, read-aloud toggled
    On/Off, `Test microphone` reported `Input level 87 · quiet · peak 1724`.
  - Silence auto-stop was tested with a synthetic recording; it stopped after the
    speech segment rather than waiting for the full limit.
  - `voice_helper.py devices` returns Focusrite Analogue 1 + 2 before the virtual
    Steam microphone; the Steam input is silent.

## Open work / limits

- Mouse-click activation of the footer button is not separately automated. The
  keyboard paths are the guaranteed acceptance path; the button now accepts both
  mouse down and up with a debounce.
- Physical speech clarity, missing words, room noise, and perceived latency were
  not physically qualified. Use **Input gain**, **Silence threshold**, and **Test
  microphone**; 6–12 dB gain may help a quiet Focusrite input.
- Warm workers are CPU-only. A Windows-side DirectML helper would be needed to use
  the AMD GPU; it was not added.
- Dictation during a running reply is implemented and code-reviewed, but a full
  physically spoken queue test during a live model turn was not repeated.
- Read-aloud and dictation can overlap if the user starts speaking during playback;
  headphones are recommended.
- No automated test suite exists. Checks are helper commands plus a fresh-TUI
  manual gate.
- The settings dialog is custom JSX. Host dialog sizing/theming can change between
  OpenCode releases.
- The plugin depends on the local voice stack at
  `~/repos/fire-tv-next/lulu-launcher/service`; use `TUI_VOICE_SERVICE` and
  `TUI_VOICE_PYTHON` to relocate it.
- Settings values may still reflect manual tuning from acceptance (for example,
  0.5 s / 5 s). **Reset to defaults** restores 0.4 s / 4 s / −35 dB / 0 dB /
  read-aloud On / Heart.

## Preservation

- No cloud STT/TTS or public upload.
- No TV, ADB, or Control Center management was added.
- Temporary capture files are deleted after transcription.
- Git repository initialized locally with commits; no remote configured/pushed.
