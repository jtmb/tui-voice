# tui-voice handoff

## 2026-11-13 EST — independent review fixes

- Dictation now uses an owned turn object reserved synchronously before the first
  await. Each turn owns its session, child, cancellation, and stop request, so an
  old close callback cannot clear a newer turn. Starting from home cannot create
  two concurrent recorders.
- A second press during microphone preparation is remembered and applied when
  the recorder starts; SIGUSR1 is delayed until the helper has installed its
  handler. A stop before capture is treated as a canceled turn, not an error.
- Recording silence handling fixed with real FFmpeg evidence:
  - the detector reports the start of silence immediately, so the configured
    pause is now applied once by the Python timer (`d=0.01`, not `d=silence`);
  - short utterances (0.4 s) now start the pause timer instead of recording to
    the maximum;
  - input gain is applied before silence detection, so the meter and detector
    use the same signal level.
- Read-aloud jobs carry a generation and a private per-job progress file under
  `$XDG_RUNTIME_DIR/tui-voice`. Muting while dictation is finishing cancels
  queued speech, and two TUI instances cannot read each other's resume chunk.
- Speech chunks are synthesized one ahead while the current chunk plays; real
  three-chunk output retained order and left no temp files. The whole-reply
  timeout now scales with remaining chunks and preserves a resume position.
- TTS worker: queued requests are bounded; disconnected clients are discarded
  before inference; idle-exit waits for active/queued work. Real test with
  `--idle-seconds 1` survived a long synthesis and then exited; a canceled
  queued request did not run (`requests=1`).
- Tests: 42 pass, including new cases for filter order, short-utterance silence,
  early stop, prefetch order, unique progress creation, and disconnected clients.
  `ruff`, `py_compile`, and esbuild pass. A fresh TUI double-press went
  listening → transcribing → idle with no recorder left and no cleanup warning.
- CPU benchmark recheck (8 threads, same runtime): INT8 warm 3.159 s short /
  26.466 s medium; FP32 warm 0.591 s short / 5.315 s medium / 15.18 s long.
  FP32 is about 5× faster on this host, but the CPU default remains INT8 pending
  an explicit product decision and a new release.

## 2026-11-13 EST — log-driven reliability fixes

- ASR/TTS worker thread overrides are now clamped to the ranges their CLIs
  accept (ASR 1–8, TTS 1–24). An out-of-range `TUI_VOICE_ASR_THREADS` previously
  made every warm-ASR start die with a bare `status 2`; reproduced with
  `TUI_VOICE_ASR_THREADS=16` and fixed. Invalid `TUI_VOICE_WARM_IDLE` values no
  longer break helper import.
- `warm --stop` is silent and idempotent when a worker is already gone; it no
  longer logs a false "warm worker unavailable" warning. `--stop` exits nonzero
  if a worker responds but does not stop, and `--restart` refuses to start a new
  worker after a failed stop.
- Read-aloud now fails loudly instead of silently doing nothing for markup-only
  replies or an exhausted resume offset. The old 40-chunk cap silently dropped
  the end of long replies (20k chars became 16,760); chunking now covers the
  whole reply up to a documented 400-chunk guard.
- Reply WAVs are removed in a `finally`, including playback failures, and a
  partial recording is deleted when the helper receives SIGTERM (session change
  or cancellation). Stale private WAVs from earlier runs were removed locally.
- Plugin: intentional helper cancellation is no longer logged as a dictation or
  read-aloud error. Dictation now tracks its own child instead of the global
  helper handle, so microphone tests, device listing, or read-aloud cannot be
  signalled by mistake. Queued drafts are kept per session, rechecked against
  the route after awaits, and retried when the reply ends. Control characters
  are stripped before insertion so a transcript cannot submit the prompt.
- Microphone arrows load the device list instead of cycling only "Automatic".
  The footer mic/speaker buttons no longer toggle twice on a press longer than
  500 ms, while still accepting either mouse-down or mouse-up-only hosts.
- A turn that is already transcribing survives a session switch and queues into
  its original session; only an in-progress recording is canceled.
- Verification: 33 standard-library tests pass, `ruff check` and `py_compile`
  pass, ASR starts with the 16-thread override at 8 threads, absent-worker stop
  is silent, CPU read-aloud and dictation fixtures pass, and a fresh tmux TUI
  showed `▐○🎙︎ │🔊︎▌`, opened Voice settings, toggled `🔊`/`🔇`, and quit with no
  plugin cleanup error.

## 2026-11-12 EST — AMD GPU attempt: blocked by the Windows driver

- Environment: Windows 11 26H2 (build 26300.9974), WSL 2.6.2.0, WSL kernel
  `6.6.87.2-microsoft-standard-WSL2`, Ubuntu 26.04 LTS, AMD Radeon RX 7900 XT.
  Windows display driver `31.0.14000.58004` (2022-12-02, Radeon Software
  `22.40.00.58`). `/dev/dxg` exists; `/dev/kfd` and `/dev/dri` do not (expected
  for WSL).
- TTS interpreter:
  `/home/brajam/repos/fire-tv-next/lulu-launcher/voice/.venv/bin/python`
  (Python 3.12.13). Installed the ROCm 10.1 pip stack there:
  `onnxruntime==1.29.0`, `onnxruntime-ep-migraphx==1.0.0+rocm10.1.0`,
  `migraphx==2.18.0+rocm10.1.0`, `migraphx-libs==2.18.0+rocm10.1.0`,
  `rocm==10.1.0`, `rocm-sdk-core/libraries/device-gfx1100==10.1.0`.
  `rocm-sdk targets` includes `gfx1100`. Kokoro remains `kokoro-onnx==0.6.1`
  and `numpy==2.5.4`. The venv is uv-managed and has no `pip` module, so the
  install used `uv pip install --python ...`.
- Downloaded FP32 `models/kokoro-v1.0.onnx` (model-files-v1.0, 310.4 MB) and
  added a locally computed integrity pin to the external `neural-models.json`:
  SHA-256 `7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5`.
  Existing INT8 (`ae315a…`) and voices (`bca610…`) pins are preserved and still
  enforced. This FP32 digest is a local integrity pin, not an independently
  published checksum.
- **No GPU session could be created on this host.** Every HSA/MIGraphX attempt
  fails before ONNX Runtime builds the graph:
  - `TUI_VOICE_TTS_PROVIDER=migraphx tts_worker.py --check` exits 139 (SIGSEGV)
    in `librocdxg` after repeated
    `[ReserveSvmSpace] ... fail to reserve gpu va ...` and
    `fail to reserve Local Heap Space!`.
  - `dmesg` reports `misc dxg: dxgkio_reserve_gpu_va: Ioctl failed: -75`.
  - `rocminfo` and `import migraphx` crash the same way.
  - `rocm-sdk test`: 19 tests, 2 errors (`amdflang --help` exit 255,
    `rocminfo` SIGSEGV).
- Cause: the installed December 2022 display driver predates AMD's Radeon WSL
  ROCm support. The earliest Radeon WSL matrix entry is ROCm 6.1.3 with
  Adrenalin 24.10.21.01 for WSL 2; current ROCDXG documentation pairs with
  Adrenalin 26.x. The host rejects the GPU virtual-address reservation the ROCm
  runtime requires.
- Smallest remaining action: on Windows, install a current AMD Adrenalin driver
  (prefer the "for WSL2" build), reboot, then rerun
  `TUI_VOICE_TTS_PROVIDER=migraphx tts_worker.py --check`. This needs an elevated
  (UAC) install and a desktop reboot; the Windows account is an administrator but
  the session is unelevated, and this unattended run must not take over the
  desktop.
- CPU remains the active backend and is verified:
  - Direct CPU benchmark, 8 threads, same fixed phrase, ORT profiling disabled:

    | Model | Load | First synthesis | Warm synthesis | Audio | Warm RTF |
    | --- | --- | --- | --- | --- | --- |
    | INT8 | 0.781 s | 3.057 s | 2.930 s | 2.572 s | 1.139 |
    | FP32 | 0.597 s | 0.558 s | 0.455 s | 2.411 s | 0.189 |

    The CPU FP32 export is faster than INT8 on this CPU/ORT build. This is a
    measured CPU result, not a GPU speed claim. `tts_worker.py --check` enables
    ORT profiling, which inflates its timings (INT8 warm 6.715 s).
  - CPU worker restarted under `onnxruntime==1.29.0`; `warm --status` reports
    `requested_provider: CPUExecutionProvider`,
    `model: .../kokoro-v1.0.int8.onnx`. `say --no-play` reports
    `warm_chunks: 1`; playback through Windows `SoundPlayer` exits 0 (audibility
    was not independently verified).
- Product/failure behavior verified:
  - An explicit GPU request against a running CPU worker fails with
    `TTS worker does not match the requested provider/model...` and exits 1; it
    never speaks with CPU.
  - `warm --restart --kind tts` with `migraphx` exits 1 and its error now
    includes the GPU-crash driver hint; `warm --status` leaves `tts: null`.
  - `warm --start/--restart` returns a nonzero exit code when any worker fails
    (new regression test).
  - Worker lifecycle still behaves: a worker started with
    `warm --start --kind tts --idle-seconds 3` exited on its own, after which
    `warm --status` reported `tts: null` and no `tts_worker.py` process remained.
    Two concurrent `warm --start` calls produced exactly one worker (the second
    reported `already running`).
  - `say --voice velvet --no-play` returned `warm_chunks: 1`, so local voice
    selection still works after the interpreter upgrade. TUI-level read-aloud,
    mute/resume, and a fresh-TUI restart were not re-run this session; the plugin
    code for those paths is unchanged, and earlier evidence is in
    `docs/TESTING.md`.
- A pre-GPU package freeze was captured at
  `/tmp/opencode/tts-venv-before-gpu.txt`. It is temporary: capture a new
  `uv pip freeze` before any retry, since `/tmp` may be cleared.
- Dictation remains intact: the short fixture returns
  `Turn on the living room lights.` (confidence 0.9322); a 37 s fixture returns
  `chunks: 2`; a corrupt WAV returns a clear error and appends an `error` line to
  `~/.local/state/tui-voice/voice.log`. `devices` still lists the Focusrite
  Analogue 1 + 2 first.
- 15 focused standard-library tests pass; `py_compile` and `git diff --check`
  pass. GPU inference, GPU timings, and hardware acceptance remain **unverified**;
  the PR stays draft.

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
