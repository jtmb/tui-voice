# tui-voice handoff

## 2026-11-12 EST — long dictation and error logging

- Turns are no longer capped by the ASR stack's 32 s limit. `voice_helper.py`
  splits longer recordings into chunks under 28 s, cuts at the quietest point near
  the boundary so a word is not split, transcribes each chunk (warm, then cold),
  and joins the text in order.
- A 45.5 s recording was verified with `"chunks": 2` and a valid transcript; the
  previous code raised `ValueError: Audio duration outside finite budget`.
- Added a local JSON-lines log at `~/.local/state/tui-voice/voice.log`
  (`TUI_VOICE_LOG_DIR` override, 1 MB roll). The helper logs recordings,
  transcripts, worker-start failures, chunk failures, and full tracebacks; the
  plugin logs helper timeouts, spawn/exit failures, dictation, read-aloud,
  microphone selection, and microphone-test errors.
- Cached one cold `WebVoice` so chunked cold transcription does not rebuild the
  stack per chunk.
- Verified: a corrupt WAV returns a clear error and appends an error line; a valid
  2 s fixture logs `transcribed` with `chunks: 1`.

## 2026-11-12 EST — home/slash-new dictation and press-to-stop

- Pressing the mic from the home screen or after `/new` now creates a session,
  navigates to it, and starts recording. The old "Open a chat session first."
  refusal is gone.
- A second press while listening sends `SIGUSR1` to the helper. ffmpeg finalizes
  the WAV, the original flow transcribes everything recorded up to that point, and
  the draft is inserted normally. It no longer kills the helper and drops audio.
- Verified from home and `/new`: the first press opens a session and starts
  `voice_helper.py record --seconds 60 --silence 1.5`; a manual `SIGUSR1` stop
  returns a valid WAV JSON result.

## 2026-11-11 EST — dictation no longer cuts off mid-speech

- Raised the normal pause threshold from 0.4 s to **1.5 s** and the hard maximum
  from 4 s to **60 s**. The maximum is now a safety stop only; normal turns still
  end on a pause.
- Existing stored settings below 15 s / 0.8 s are migrated automatically on
  plugin setup, so the old 5 s cutoff does not persist.
- Raised the plugin record timeout from 45 s to 120 s so the helper cannot kill
  a long turn before the 60 s hard stop.
- Settings choices now expose 0.8–2.5 s pause and 15–90 s maximum.
- Verified in a fresh TUI: record runs with `--seconds 60 --silence 1.5`, and the
  settings dialog shows `1.5 s` / `60 s`.

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
- Measured with the CPU-only worker configuration at that time:
  - Warm ASR fixture: ~0.6 s total helper run (cold ~1.5 s), same
    transcript/confidence.
  - Warm TTS short sentence: ~1.1 s (cold ~3.2 s); 8 CPU threads and a disabled
    ORT CPU arena keep it fast without retaining memory across turns.
  - Mute killed the active `say`; unmute started a new helper with
    `--start-chunk` and the warm worker request count increased.
- Historical GPU assessment (corrected 2026-11-12): the worker was hard-coded to
  `CPUExecutionProvider`. Missing `/dev/dri`, `/dev/kfd`, CUDA, or `nvidia-smi`
  did not establish that the Windows host's AMD RX 7900 XT was unusable in WSL.
  AMD's WSL compute interface uses `/dev/dxg`; see the GPU entry below.

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
- AMD GPU TTS is opt-in through MIGraphX in the existing WSL worker. The owner's
  Windows driver, WSL ROCm installation, and actual RX 7900 XT inference remain
  unverified. A Windows-side DirectML helper is not required by this path.
- Dictation during a running reply is implemented and code-reviewed, but a full
  physically spoken queue test during a live model turn was not repeated.
- Read-aloud and dictation can overlap if the user starts speaking during playback;
  headphones are recommended.
- Focused standard-library regression tests cover provider selection, model
  integrity, GPU fallback refusal, and singleton startup. Hardware and fresh-TUI
  verification still require the owner's environment.
- The settings dialog is custom JSX. Host dialog sizing/theming can change between
  OpenCode releases.
- The plugin depends on the local voice stack at
  `~/repos/fire-tv-next/lulu-launcher/service`; use `TUI_VOICE_SERVICE` and
  `TUI_VOICE_PYTHON` to relocate it.
- Settings values may still reflect manual tuning from acceptance (for example,
  0.5 s / 5 s). **Reset to defaults** restores 1.5 s / 60 s / −35 dB / 0 dB /
  read-aloud On / Heart.

## Preservation

- No cloud STT/TTS or public upload.
- No TV, ADB, or Control Center management was added.
- Temporary capture files are deleted after transcription.
- Git repository initialized locally with commits; no remote configured/pushed.

## 2026-11-12 EST — optional AMD GPU TTS

- Added explicit CPU/MIGraphX provider selection to the existing local Kokoro
  ONNX worker. CPU stays the default. GPU mode defaults to the FP32
  `kokoro-v1.0.onnx`; selected models and voices must be pinned in the external
  `neural-models.json`, and all existing manifest hashes remain enforced.
- GPU selection fails clearly if the AMD provider cannot load or an old CPU
  worker is still active. The helper does not substitute cold CPU TTS when GPU
  was explicitly requested. Restart with `warm --restart --kind tts` after
  configuration changes. Initial GPU compilation can take up to 300 seconds;
  use `tts_worker.py --run` to see startup errors in the foreground.
- `tts_worker.py --check` synthesizes a fixed phrase twice and reports session
  providers, per-provider executed-node counts/times, and synthesis timing. A
  GPU request fails the check if no GPU nodes execute. No playback or user
  recording is needed for that diagnostic.
- The actual worker holds a lock before model loading and throughout its
  lifetime, so muting/retrying during GPU compilation cannot load a second model
  on the same socket. Initialization errors are appended to the private
  `tts-startup.log` beside that socket.
- Validation here: 13 focused standard-library tests, Python syntax checks,
  and a source review. No ROCm runtime, Kokoro model, Windows audio device, or
  RX 7900 XT is connected to this development environment.
- [AMD setup and validation](docs/AMD_GPU.md) documents matching runtime routes,
  FP32 model preparation, the WSL `/dev/dxg` check, activation, and CPU rollback.
- **Hardware verification pending:** these changes do not establish that the
  owner's installed driver/runtime can run Kokoro or that GPU inference is faster.
  Run the documented check and normal read-aloud on the RX 7900 XT before marking
  GPU support verified. Whisper and the TUI controls retain their existing paths.
