# Testing

Run the focused provider/fallback regression checks with the standard library:

```bash
python3 -m unittest discover -s tests -v
```

These checks do not require model downloads or GPU hardware, and do not establish
that Kokoro runs on the owner's GPU. For that, use the actual inference/profile
check in [AMD_GPU.md](AMD_GPU.md), then the helper and fresh-TUI gates below.
Reuse these checks; do not build recursive test infrastructure for this plugin.

## Helper checks

```bash
cd ~/repos/tui-voice

python3 -m py_compile voice_helper.py tts_worker.py

SERVICE=~/repos/fire-tv-next/lulu-launcher/service
$SERVICE/.venv/bin/python voice_helper.py devices

$SERVICE/.venv/bin/python voice_helper.py warm --start
$SERVICE/.venv/bin/python voice_helper.py warm --status

$SERVICE/.venv/bin/python voice_helper.py transcribe \
  --wav ~/repos/fire-tv-next/lulu-launcher/voice/fixtures/short-2s.wav
```

Expected:

- `devices` lists the real Windows input first (Focusrite Analogue 1 + 2) and the
  virtual Steam microphone second.
- `warm --status` reports an `asr` worker on CPU and a `tts` worker with the four
  local voices.
- `transcribe` returns “Turn on the living room lights.” with confidence around
  0.93. With the warm worker it completes in about half a second.

## Long-turn and log checks

Build a recording longer than 32 s by repeating two different fixtures, then
transcribe it:

```bash
python3 - <<'PY'
import wave
from pathlib import Path
base = Path.home()/'repos/fire-tv-next/lulu-launcher/voice/fixtures'
out = Path('/tmp/opencode/long-turn.wav')
parts = []
for name in ('long-12s', 'medium-4s', 'short-2s',
             'long-12s', 'medium-4s', 'short-2s', 'long-12s'):
    with wave.open(str(base/(name + '.wav')), 'rb') as wav:
        params = wav.getparams()
        parts.append(wav.readframes(wav.getnframes()))
with wave.open(str(out), 'wb') as wav:
    wav.setparams(params)
    for frames in parts:
        wav.writeframes(frames)
PY
$SERVICE/.venv/bin/python voice_helper.py transcribe --wav /tmp/opencode/long-turn.wav
```

Expected: a transcript with `"chunks": 2` (or more), no “Audio duration outside
finite budget” error, and a `transcribed` line in
`~/.local/state/tui-voice/voice.log` showing the chunk count and elapsed time.
Feed a corrupt WAV to confirm failures are logged, not only toasted:

```bash
head -c 200 /dev/urandom > /tmp/opencode/bad.wav
$SERVICE/.venv/bin/python voice_helper.py transcribe --wav /tmp/opencode/bad.wav
tail -1 ~/.local/state/tui-voice/voice.log
```

## Warm-worker checks

```bash
cd ~/repos/tui-voice
SERVICE=~/repos/fire-tv-next/lulu-launcher/service

# Status must answer even while speech is synthesizing.
$SERVICE/.venv/bin/python voice_helper.py say \
  --text "This is a warm local speech test." --no-play
$SERVICE/.venv/bin/python voice_helper.py warm --status

# Stop and confirm the cold fallback still works, then start again.
$SERVICE/.venv/bin/python voice_helper.py warm --stop
$SERVICE/.venv/bin/python voice_helper.py say --text "Cold fallback." --no-play
$SERVICE/.venv/bin/python voice_helper.py warm --start
```

Expected: `say` reports `"warm_chunks": 1` with a worker running and `0` after
`warm --stop`; both paths return usable audio. The worker sockets are `0600` under
`$XDG_RUNTIME_DIR`.

## AMD GPU gate

CPU is the default and must stay green. GPU mode is a separate, explicit
acceptance gate; run it only after [AMD_GPU.md](AMD_GPU.md) section 1 confirms
the Windows display driver supports ROCm. Installing packages, or seeing a
provider name in a list, does not pass this gate.

```bash
cd ~/repos/tui-voice
VOICE=~/repos/fire-tv-next/lulu-launcher/voice
export TUI_VOICE_TTS_PYTHON="$VOICE/.venv/bin/python"

# 1. The runtime must see the GPU instead of crashing.
rocminfo | grep -m1 gfx

# 2. Actual inference plus executed-node accounting.
TUI_VOICE_TTS_PROVIDER=migraphx "$TUI_VOICE_TTS_PYTHON" tts_worker.py --check

# 3. Real worker and real speech.
TUI_VOICE_TTS_PROVIDER=migraphx python3 voice_helper.py warm --restart --kind tts
python3 voice_helper.py warm --status
TUI_VOICE_TTS_PROVIDER=migraphx python3 voice_helper.py say \
  --text "Read aloud is using the local AMD GPU." --voice heart --no-play
```

Pass requires all of these:

- `rocminfo` reports the GPU (`gfx1100` for the RX 7900 XT).
- `--check` exits 0 with `"ok": true`, `"gpu_execution_verified": true`, a
  `providers` list containing `MIGraphXExecutionProvider`, and a `profile`
  summary with nonzero MIGraphX `node_events`.
- `warm --status` reports `requested_provider: MIGraphXExecutionProvider`, a
  `providers` list that still contains it, and the FP32
  `kokoro-v1.0.onnx` model.
- `say` returns `warm_chunks` for the sentence and produces playable audio.
- From a fresh TUI: a completed reply is spoken by that worker, mute/resume
  works, and a TUI restart leaves exactly one TTS worker.
- Failure is loud: with MIGraphX requested but unavailable, `--check` and
  `warm --restart` exit nonzero and no CPU speech is produced. Record the exact
  error, relevant `dmesg` line, and driver version instead of claiming speedup.

Measured on 2026-11-12, the gate **failed before ONNX Runtime built a session**:
`--check` exited 139 (SIGSEGV) in `librocdxg`, `dmesg` reported
`dxgkio_reserve_gpu_va: Ioctl failed: -75`, and `warm --restart` exited 1 with
`status -11`. The Windows driver was `31.0.14000.58004` (2022-12-02). See
[AMD_GPU.md](AMD_GPU.md) for the prerequisite and fix.

## Fresh-TUI gate

Close the current TUI completely and start a new one from outside the repository
(`cd ~`). `/reload` is not enough after a plugin path change.

1. `opencode plugin list` includes `/home/brajam/repos/tui-voice`.
2. From the home screen, press `F9`/the mic. It must create a session and start
   recording instead of showing "Open a chat session first."
3. Press the mic again while listening. It must gracefully stop at that point and
   transcribe the recorded audio into the draft.
4. Open a chat session. The bottom footer shows one compact rounded panel
   (one footer row) with `▐○🎙 │🔊▌`-style half-cell caps and a divider.
5. Press `Ctrl+Alt+V`. The status circle turns amber `●`; a helper `record` process
   starts. The panel does not move or resize.
6. Wait for silence / the maximum. It changes to `Transcribing`, then a transcript
   appears in the message box. Nothing is sent.
7. Press `Ctrl+Alt+S`. A centered native-sized settings dialog opens; the footer
   chip hides cleanly behind the modal, and returns when `Esc` closes it. Arrows
   change values.
8. With read-aloud On, send a short prompt in a disposable session and confirm a
   `voice_helper.py say` process starts with the completed assistant text.
9. Open settings, run **Test read-aloud**, then toggle footer `🔊` → `🔇`; the
   current `say` process stops. Toggle back to `🔊`; a new `say` process starts
   with `--start-chunk` and the interrupted sample continues.
10. Set read-aloud Off in settings, send another prompt, and confirm no `say`
   process starts.
11. Stop dictation and confirm no `voice_helper.py` process remains.

## Verified evidence

- Fixture transcript inserted into the message box without sending.
- Rounded footer panel verified at 80, 100, and 140 columns: `▐○🎙 │🔊▌`,
  `▐●🎙 │🔊▌`, and `▐○🎙 │🔇▌` keep identical cell widths and columns; the
  footer stays one row, with no clipping, overlap, or layout shift.
- Warm ASR returned the same fixture transcript in ~0.6 s (cold ~1.5 s); the TTS
  worker kept one ONNX session and served repeated chunks (warm ~1.1 s vs cold
  ~3.2 s for the same short sentence, 8 threads).
- Stopping a worker waits for the socket to disappear; two concurrent starts
  produced one worker.
- Settings dialog opened, values changed, read-aloud toggled, `Test microphone`
  returned `Input level 87 · quiet · peak 1724`.
- Read-aloud spawned:
  `voice_helper.py say --text "read aloud final check." --voice heart`.
- Read-aloud mute stopped the helper; unmute started a new helper with
  `--start-chunk` and incremented the warm worker request count.
- Silence auto-stop stopped a synthetic recording after the speech segment rather
  than using the full maximum.
- Global load verified from `/home/brajam`, not from this repository.
- Dictation fixtures on 2026-11-12: `short-2s.wav` returned
  `Turn on the living room lights.` (confidence 0.9322); a 37 s synthetic turn
  returned `"chunks": 2`; a corrupt WAV returned a clear error and appended an
  `error` line to `~/.local/state/tui-voice/voice.log`.
- CPU TTS baseline measured directly (no ORT profiling, 8 threads, one fixed
  sentence, same voice):

  | Model | Load | First synthesis | Warm synthesis | Audio | Warm RTF |
  | --- | --- | --- | --- | --- | --- |
  | INT8 | 0.781 s | 3.057 s | 2.930 s | 2.572 s | 1.139 |
  | FP32 | 0.597 s | 0.558 s | 0.455 s | 2.411 s | 0.189 |

  The FP32 export is faster than INT8 on this CPU/ONNX Runtime build. This is a
  CPU measurement, not a GPU claim. `tts_worker.py --check` enables ORT
  profiling, which inflates its timings (INT8 warm 6.715 s), so compare with the
  direct benchmark, not the check output.
- v0.1.1 rechecked CPU FP32 on short/medium/long replies before changing the
  default: FP32 warm medians were 0.591 s, 5.315 s, and 15.18 s; INT8 was
  3.159 s and 26.466 s on the first two (about 5× slower). INT8 remains
  selectable with `TUI_VOICE_TTS_MODEL=kokoro-v1.0.int8.onnx`.
- CPU worker restarted under `onnxruntime==1.29.0`: status reported
  `requested_provider: CPUExecutionProvider` and
  `model: .../kokoro-v1.0.onnx`; `say --no-play` reported
  `warm_chunks: 1`; playback through Windows `SoundPlayer` exited 0.
- An explicit GPU request against the running CPU worker failed with
  `TTS worker does not match the requested provider/model...` and exit 1; no CPU
  speech was produced.
- `warm --start`/`--restart` now exits nonzero when any worker fails to start,
  and a GPU worker that dies at startup adds a hint naming the Windows AMD driver
  and `docs/AMD_GPU.md` (two regression tests).

## Not verified

- Kokoro inference on the RX 7900 XT. The 2026-11-12 attempt stopped at the
  Windows display driver (`31.0.14000.58004`, 2022-12-02); no MIGraphX session,
  GPU node execution, GPU audio, or GPU timing exists yet.
- Mouse-click activation of the footer button (keyboard paths are verified).
- Physical speech accuracy, latency, echo, and the practical gain/threshold
  settings for the owner’s room.
- Audibility of Windows playback through the actual speakers; only the process
  exit status and generated PCM were checked.
- Dictation queued behind a real model turn was not physically repeated.
