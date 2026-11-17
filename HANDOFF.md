# tui-voice handoff

## 2026-11-16 — README product GIF

- Owner asked for a visual GIF built with the `game-readme` approach. The
  plugin has no game footage, so the product UI itself is the subject: a real
  OpenCode TUI session (110x32, truecolor, plugin loaded globally) was captured
  over a pty and replayed into frames.
- Capture used the existing background service and a throwaway session id; it
  pressed `ctrl+p`, typed `voice`, closed the palette, opened Voice settings and
  closed it. No dictation was started, so **no transcript and no listening
  state** appear — the GIF shows chrome and settings only.
- `docs/media/gameplay.gif`: 960x531, 8.1 s loop, six held states (idle footer
  panel with startup toast, command palette, palette filtered to the six
  `Voice:` commands, composer, Voice settings, closed footer), 136,923 B.
  `docs/media/gameplay-poster.png` is the settings frame, 81,463 B.
- README shows the GIF under the badges with a "Real capture" caption. No
  release was cut for this media commit.
- Renderer: no `asciinema`/`agg`/`ffmpeg` and no PyPI, so `render_tui.py` parses
  the emitted CSI/SGR subset into a character grid and draws it with Pillow
  16.61 px DejaVu Sans Mono (exactly 10 px per cell) plus Noto Color Emoji.
  Escape sequences that straddle read chunks are carried in a pending buffer;
  cross-fades were dropped because they produced double-exposure ghosts.
- Verified: `https://raw.githubusercontent.com/jtmb/tui-voice/main/docs/media/gameplay.gif`
  returns `content-type: image/gif`, decodes as 6 frames at 960x531 with
  `loop=0`; README local links and both `<img>` targets resolve; inspected the
  idle, filtered-palette and settings frames at full size.
- Tooling stays out of the repository in
  `/mnt/h/AgentArtifacts/projects/tui-voice/runs/storage-setup-20261114/`
  (`tools/capture_tui.py`, `tools/render_tui.py`, `tools/render_demo.py`,
  `test-output/tui-capture.bin` + `-keys.json`). Removed 1,245,850 B of
  sampled/duplicate frames and the H copies of the published media; artifact
  charge after cleanup 6,943,608,583 B of 100,000,000,000 B.
- Not captured, by design: dictation typing into the composer and read-aloud
  playback. A truthful version needs a real spoken turn the owner is willing to
  publish.

## 2026-11-16 — production README and INSTALL guide

- Owner asked for a production, mass-consumption README that is **not**
  documentation, plus a top-of-README line pointing agents to an `INSTALL.md`
  that asks whether the plugin should be installed locally or globally.
- `README.md` is now a product page: centered logo/title/tagline, release and
  privacy badges, an agent callout to `INSTALL.md`, a feature grid, a short
  flow example, privacy statement, and an install CTA. Install commands,
  settings tables, dependencies, troubleshooting and status were removed from
  the README; they live in `INSTALL.md`, `docs/`, and this handoff.
- New `INSTALL.md` carries the installation guide. It starts with an owner
  decision (global vs local), then gives prerequisites, global `cli.json`
  setup, project-local `.opencode/plugins/tui-voice` symlink setup, environment
  overrides, first-run controls, verification, upgrade and uninstall. It states
  that only one scope should be enabled.
- Local discovery was checked with `opencode plugin list` from a throwaway
  project containing `.opencode/plugins/tui-voice` symlinked to this checkout:
  the list reported `.opencode/plugins/tui-voice` as `local`. The temporary
  probe directory was removed. No plugin scope, service or model was changed.
- Updated `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/README.md`, and the
  repository-guide memory note to point at `INSTALL.md` and to keep the README
  as a product page.
- No source, voice runtime, service, model or release change. No release was
  created for this documentation batch.

## 2026-11-16 — owner correction: microphone is an on/off toggle

- Owner observed dictation cutting off mid-speech and explicitly requested
  manual on/off. This supersedes all historical silence/maximum-turn behavior.
  `rules.md`, `AGENTS.md` and current usage/settings/runbook docs now say so.
- `voice_helper.py dictate` has no silence stop, ffmpeg `-t` cap or lifetime
  timer. `tui.tsx` no longer passes timing settings or applies the five-minute
  helper deadline. F9/the footer mic remains the graceful stop control.
  Removed misleading pause/maximum-turn settings; saved legacy values remain
  intact but are ignored. The separate two-second microphone diagnostic and
  `record` command keep their intentionally bounded behavior.
- Live phrase boundaries still bound individual ASR requests, not recording.
  Added a bounded audio backlog failure for genuinely stalled inference; the
  capture watchdog measures absence of PCM only, so silent input keeps it alive.
  These report failures, not normal automatic recording completion.
- Verification: **28 focused helper/streaming Python tests pass**, Ruff passes,
  full TSX bundle passes, `git diff --check` passes. New regression feeds a
  65-second silent pause between speech bursts (past the old 60-second cap),
  verifies capture remains on, then explicitly switches off and checks graceful
  `q` / final text. This is accelerated PCM coverage, not a 67-second hardware
  capture. No need to repeat the unchanged native-editor/CPU fixture tests.
- Checks used existing H routing, a fresh 2,000,000-B output estimate, 100-GB
  combined allowance and 15-GiB C/H reserves. Removed completed generated
  `build/manual-toggle-tui.mjs` (**40,588 B**); combined charge afterward
  **6,934,885,921 B**. No services/models/config permissions changed; unrelated
  edits and protected cleanup targets preserved.
- Next owner check in a fresh TUI: F9 on → speak → pause → keep speaking →
  F9 off. Words must keep updating and nothing is sent automatically.
- Released **v0.2.0** at `a7c97be` (merge of `origin/main` logo/README work).
  Published https://github.com/jtmb/tui-voice/releases/tag/v0.2.0 with notes and
  compare link to `v0.1.1`. `package.json` bumped 0.1.1 → 0.2.0. Verification on
  the tagged commit: 28 focused Python tests, 5 `live_draft` + 5 `read_last`
  Node tests, Ruff, and full TSX bundle all passed. Remote tag peels to
  `a7c97be`; `main` is clean and matches `origin/main`. Release notes file
  `receipts/release-v0.2.0-notes.md` is retained under the H run root; test
  bundles were removed after use.

## 2026-11-16 — live dictation and intermittent pickup repair

- Owner requested live text as speech is heard, fluent/performance improvements,
  and comparison with `/home/brajam/repos/opencode-ghost`. Inspected its
  `src/composer.ts` and footer registration in `src/tui.tsx` read-only. Kept its
  useful pattern (footer-anchored composer lookup and native editor mutation),
  not its ghost-placeholder behavior; dictation is real editable draft text.
- New `live_dictation.py` runs one local ASR consumer alongside raw Windows
  ffmpeg PCM capture. Natural phrase commits / eight-second caps bound requests;
  preview snapshots are coalesced, not backlogged. No WAV files are created.
  `voice_helper.py dictate` emits starting/listening/partial/transcribing JSON
  events and a final result. Readiness now means PCM actually arrived; second
  press uses an explicit helper signal-handler handshake, not a 700-ms guess.
- `live_draft.ts` replaces only owned live text through native selection/insert
  methods, preserving existing draft, Unicode offsets, typed suffix and caret.
  `tui.tsx` registers per-session footer markers, streams revisions, and retains
  queued hypotheses while the composer is absent/busy. Removed synthetic stdin
  injection and repeated history/API polling. Switching chats cannot target a
  different chat's editor. Manual edits to owned text pause replacement rather
  than overwrite; `/voice-text` explicitly recovers the full last transcript
  for that chat (may duplicate the kept partial). Never auto-send.
- Actual intermittent failure reproduced: inherited
  `/run/WSL/1826573_interop` timed out / returned `UtilAcceptVsock ... accept4
  failed 110`. Existing `/run/WSL/1_interop` passed the same launch probe.
  `windows_env()` verifies/reuses an existing healthy bridge in helper-child
  environments only. No permission/WSL/service/runtime configuration changes.
  Known microphones skip repeated enumeration; discovery has a bounded retry,
  and stale automatic device choices can be rediscovered before any text arrives.
- Verification:
  - **27 focused Python tests** pass (existing helper + live streaming), Ruff
    passes. **5 live-draft + 5 replay Node tests** pass; full TSX bundles.
  - Existing CPU ASR worker, eight threads, real-time-paced 9.109-s fixture:
    first text **1.406 s**, about one update/sec, capture ended **9.219 s**,
    final text **9.912 s**. Ten hypotheses; requests **0.399–0.589 s**.
    Full expected fixture sentence returned; no raw audio file written.
  - Actual Focusrite two-second PCM capture succeeded after bridge repair.
    This was quiet input, not an owner-room spoken-accuracy test.
  - Fresh exclusively task-owned TUI loaded the actual plugin and registered
    dictate/recovery/replay commands. Host-native textarea assertions passed:
    live replacement, Unicode prefix, typed suffix/caret, footer lookup and no
    submit. Used the existing authenticated shared server, not a replacement.
  - Broader initial 49-test suite had three failure entries and three errors
    in TTS fixture checks: H/DrvFS test directories do not satisfy Linux uid/
    chmod safety requirements. Those unrelated tests/security guards were not
    weakened. Direct Node native probe lacked Bun FFI; the actual OpenCode host
    subsequently passed native checks. No misleading all-suite pass claim.
- All output used the existing H root and 100-GB / 15-GiB C/H admission checks.
  Initial `opencode --help` unexpectedly extracted an 8,524,432-B Bun library
  beyond its 1-MB estimate; subsequent native stages admitted 100-MB full peaks.
  After consumers exited, removed **8,673,540 B** of task-owned bundles, probe
  package, extracted library and Bun transform cache (exact paths in the new
  receipt). Final combined charge **6,934,885,022 B**; retained H run **24,415 B**.
  Retained compact results at
  `receipts/live-dictation-verification.json`, native assertion JSON under
  `test-output/`, and diagnostic log lines under `state/tui-voice/voice.log`.
  Existing storage receipts, protected cleanup exceptions and source remain.
- Updated product contract, README, testing guide and agent architecture map.
  Prior `/read-last` and storage/memory edits are preserved; branch is still
  `main`, behind origin by two commits, with uncommitted work. No pull, commit,
  release, model/provider change or unrelated service restart performed.
- Remaining owner checks: physical room/microphone accuracy, and live dictation
  during a real model reply end-to-end. Draft blocking/remount/ownership paths
  have regression coverage, but no model reply was sent for this test. Use a
  fresh TUI, press F9, speak several seconds, verify text appears while listening,
  then pause / F9 to finish and send manually.

## 2026-11-15 — `/read-last` command

- Added `/read-last` and palette action **Voice: Read last reply aloud**.
  `read_last.ts` refreshes current-session message data, selects the newest
  completed assistant text (no reasoning/tool output), and checks the route,
  mute state and speech generation after refresh. No session/empty history/mute
  produce explanatory feedback; refresh errors are logged and shown.
- `tui.tsx` reuses local `speakReply` with the selected voice, cancels old speech
  and resumes from the beginning. Replay queued behind dictation rechecks the
  session before playback. Mute remains authoritative; replay never changes the
  saved read-aloud setting. No service/model/provider changes or release bump.
- Verified five Node regression tests in `tests/read_last.test.ts`: selection
  and text-part joining; refreshed history; no-session/empty/muted feedback;
  route/mute/cancel races; refresh failure. Existing esbuild bundled the full
  TSX plugin successfully. No physical speaker/TUI invocation was tested.
- Checks used `tools/artifact_env.py --reserve-bytes 1000000`, the existing H
  root, 100-GB combined guard and 15-GiB C/H reserves. Combined charge after
  checks: 6,934,905,489 bytes. Only disposable test/plugin bundles were generated;
  they are removed after checking. Existing storage/memory edits, protected
  cleanup candidates and runtime/model data remain untouched.
- User check in a fresh TUI: run `/read-last` after a completed reply; confirm
  selected-voice audio, then F10 stop/resume. If muted, the command explains
  how to enable sound. Also available from the palette while a reply is running.

## 2026-11-14 — bounded storage/memory setup (coordinator follow-through)

Owner work is complete for this bounded setup; coordinator verification/closure
is still pending. No release, GPU, dependency, model, service or runtime work was
resumed. Existing checkout/HEAD remains `404e7121b1aacfd1502df5dbe0458638ee43046c`.

### Exact paths and configuration

- No previously declared H root was found in project/coordinator records.
  Declared the expressly authorized
  `H:\AgentArtifacts\projects\tui-voice\runs\storage-setup-20261114`
  (`/mnt/h/AgentArtifacts/projects/tui-voice/runs/storage-setup-20261114`).
- Added executable `tools/artifact_env.py` and `docs/STORAGE.md`; updated
  `AGENTS.md`, `MEMORY.md`, this handoff and added the source-backed version-1
  decision `docs/memory/storage-output-routing.md`. Changes remain uncommitted
  for coordinator review; no branch, tag or released file was replaced.
- Existing `opencode.json` already includes `/home/brajam/repos/AGENTS.md` and
  `MEMORY.md`; preserved it and `docs/memory/README.md` byte-for-byte.
- Before a future tool invocation, use
  `tools/artifact_env.py --reserve-bytes N -- command ...`. It checks the H
  mount, aggregates all project runs plus known legacy output, and sets temp,
  Python bytecode, pytest/coverage, Ruff/uv/pip/npm cache and helper/plugin log
  destinations under the H root. Compiler/report output flags still need the
  explicit H destination. It neither installs dependencies nor copies caches.
- `XDG_RUNTIME_DIR`, AF_UNIX sockets, models/interpreters and existing processes
  are not changed. No physical quota/sandbox or cross-chat reservation lock is
  claimed; 50 GB remains an operating ceiling requiring coordinated writers.

### Coordinator source-review corrections (2026-11-14)

- Corrected `tools/artifact_env.py`, `docs/STORAGE.md` and the indexed storage
  decision; no unrelated source/config changes or inventory rerun.
- Child-only uppercase `NPM_CONFIG_CACHE` is
  `H:\AgentArtifacts\projects\tui-voice\runs\storage-setup-20261114\cache\npm`;
  `NPM_CONFIG_LOGS_DIR` is the same root's `logs\npm`. Conflicting inherited
  lowercase npm cache/log aliases are removed from the child environment.
- Build-only child `APPDATA` and `LOCALAPPDATA` use that root's
  `build-appdata\roaming` and `build-appdata\local`, respectively. Parent/user
  runtime AppData is not changed. `WSLENV` applies `/p` to these two settings,
  both npm paths, `TEMP` and `TMP`; unrelated entries are preserved.
- Before any destination creation or output-producing command, require C free
  space >= **16,106,127,360 B (15 GiB)** and H free space minus the declared
  additional peak and **65,536 B** metadata allowance >= **16,106,127,360 B**.
  These checks are separate from the combined 50-GB project budget.
- **10 focused checks passed**: valid child environment and actual Windows
  translation, parent/runtime/model/socket preservation, exact-floor acceptance,
  and C/H reserve rejection before inventory, destination setup or command
  execution. Actual free-space check: C **221,216,837,632 B**, H
  **424,965,324,800 B**, projected H after the 100,000-B check peak plus metadata
  **424,965,159,264 B**. No builds/downloads/restarts/deletions occurred.
- Updated the same `receipts/focused-verification.json` and
  `receipts/storage-report.json` on H. Their known growth is **4,916 B**;
  accepted H-run bytes plus those edits are **18,984 B**, and accepted combined
  charge plus receipt growth is **6,934,861,019 B**. These are arithmetic updates
  of the accepted snapshot below, **not** a refreshed filesystem inventory.
  Follow-up additional output ceiling: **100,000 B**, inside the original
  1,000,000-B setup peak ceiling. All failed-cleanup candidates remain untouched.

### Measurements and focused verification

- Snapshot: `2026-11-14T07:00:42Z`, `du -sb` apparent bytes (no link following).
  All H project runs currently total **14,068 bytes**; the largest observed
  checkpoint was also **14,068 bytes**. New-output peak ceiling for the entire
  setup was **1,000,000 bytes**, not an allocation target.
- Known project-generated output, including legacy paths: **943,275,624 bytes**.
  Conservatively charging the entire shared `~/.cache/uv` (**5,991,580,479
  bytes**) gives **6,934,856,103 bytes** current against **50,000,000,000**.
  Pre-setup charge plus the reserved setup peak: **6,935,842,035 bytes**.
- Models are not disposable output: the three pinned voice model/bundle files
  total **467,866,112 bytes** in the existing shared voice directory. Their
  size/mtime and manifest SHA-256 stayed unchanged; existing voice/service
  interpreters were not copied, installed into or migrated.
- Eight focused routing assertions passed: actual Python temp/write locations,
  H bytecode routing, Windows `TEMP`/`TMP` translation and a cross-OS write/read,
  non-H refusal and preservation of model/interpreter settings. Ruff passed for
  the new script; an oversized 50-GB additional reservation was rejected before
  launching its command. No application/model test suite or full build ran.
- Baseline/after SHA-256 checks preserved all 20 application/source/config
  files outside the two intentionally edited instruction/handoff files. No
  source/lockfile or deliberately versioned asset was deleted. Memory metadata,
  source links and existing OpenCode instruction entries were checked.
- Local receipts: `receipts/baseline.json`, `focused-verification.json`,
  `preservation-and-budget.json`, `storage-report.json` under the H run root.
  Only this run's completed write/bytecode probes were removed: **245 bytes**;
  exact paths/sizes are in `focused-verification.json`. No pre-existing data
  was deleted or moved (**0 bytes**).

### Concrete remaining gaps / retained paths

- Cleanup safety check could not inspect `/proc/469/fd` (`sd-pam`). Preserve the
  exact failed targets; no retry using another tool/identity. Repo caches:
  `__pycache__/` **193,761 B**, `tests/__pycache__/` **73,432 B**,
  `tools/__pycache__/` **7,926 B**, `.ruff_cache/` **1,270 B**.
  Synthetic fixtures under `C:\Users\james\AppData\Local\Temp`:
  `sd-04s-speech.wav` **192,044 B**, `sd-1s-speech.wav` **256,044 B**,
  `sd-gain.wav` **160,044 B**, `sd-gain2.wav` **160,044 B**.
  Combined retained cleanup candidates: **1,044,565 B**.
- `C:\Users\james\AppData\Local\tui-voice\gpu-remediation` (**931,844,838 B**)
  stays intact/uninstalled pending a separate coordinator disposition of the
  staged installer/recovery record. This setup does not revisit driver work.
- Existing `~/.local/state/tui-voice` logs/recovery (**48,560 B**) and
  `/run/user/1000/tui-voice/tts-startup.log` (**276 B**) are counted and retained.
  Active TUIs/helpers keep their existing output destinations. Speech progress
  and startup logs tied to Linux sockets need separate runtime work to redirect;
  no restart or migration was performed here.
- Existing esbuild cache `~/.npm/_npx/821a89c635d995f5` (**10,323,317 apparent B**)
  is counted, reused in place and not copied. Shared uv ownership is unresolved;
  its whole footprint is charged rather than asserting a shared-cache exemption.
- `/tmp/opencode` contained other projects' `rs-*`/`weapon-cal` work, no retained
  `tui-voice` output; untouched. No unrelated task/configuration was changed.

## 2026-11-13 EST — v0.1.1 CPU default moves to FP32

- Branch `codex/cpu-fp32-default`; `package.json` is `0.1.1`.
- CPU read-aloud now defaults to the already pinned FP32
  `kokoro-v1.0.onnx`, the same artifact used by explicit MIGraphX mode.
  INT8 remains available with
  `TUI_VOICE_TTS_MODEL=kokoro-v1.0.int8.onnx`.
- Reason: measured on the owner's runtime (8 threads, same text/voice,
  profiling disabled):
  - FP32 warm medians: 0.591 s short, 5.315 s medium, 15.18 s long.
  - INT8 warm medians: 3.159 s short, 26.466 s medium (about 5× slower).
- Verified: `warm --status` reports `CPUExecutionProvider` with
  `kokoro-v1.0.onnx` and digest `7d5df8ec…36a6c5`; `say --no-play` reports
  `warm_chunks: 1`; an explicit INT8 restart selects
  `kokoro-v1.0.int8.onnx`, and the default restart returns to FP32.
- 43 tests pass; `py_compile`, `ruff`, `esbuild`, and `git diff --check` pass.
- Release not created yet; this branch is the v0.1.1 candidate.

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
