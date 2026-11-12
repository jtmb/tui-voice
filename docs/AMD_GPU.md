# AMD GPU read-aloud on WSL2

The RX 7900 XT can run AMD GPU compute under WSL2. This plugin can select
`MIGraphXExecutionProvider` for its existing Kokoro ONNX worker. CPU remains the
default; GPU mode is explicit and reports failure instead of silently using the
cold CPU speech path. Whisper dictation remains on its current CPU worker.

**Actual Kokoro synthesis and performance on the owner's RX 7900 XT are still
unverified.** Complete the inference check below before treating the setup as
working. All synthesis stays local over the existing AF_UNIX socket.

## 1. Check the WSL and Python environments

Run from the existing checkout. Adjust the overrides if the voice stack lives
elsewhere; the model directory and TTS interpreter must refer to the same stack.

```bash
cd ~/repos/tui-voice
export TUI_VOICE_VOICE_DIR="${TUI_VOICE_VOICE_DIR:-$HOME/repos/fire-tv-next/lulu-launcher/voice}"
export TUI_VOICE_TTS_PYTHON="${TUI_VOICE_TTS_PYTHON:-$TUI_VOICE_VOICE_DIR/.venv/bin/python}"

cat /etc/os-release
uname -r
ls -l /dev/dxg
"$TUI_VOICE_TTS_PYTHON" --version
"$TUI_VOICE_TTS_PYTHON" -m pip show kokoro-onnx onnxruntime onnxruntime-migraphx onnxruntime-ep-migraphx
```

AMD's WSL bridge uses **`/dev/dxg`**, provided through the Windows driver. Missing
`/dev/kfd`, `/dev/dri`, CUDA, or `nvidia-smi` does not establish that an AMD GPU is
unavailable. After installing ROCm, `rocminfo` must identify the GPU (`gfx1100`
for the RX 7900 XT). See [AMD's WSL explanation](https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.2.1/docs/install/installrad/wsl/howto_wsl.html)
and [GPU specifications](https://rocm.docs.amd.com/en/latest/reference/gpu-specs.html).

## 2. Install one matching AMD runtime

Use the AMD instructions for the complete stack you select. Its Windows driver,
WSL support, ROCm libraries, Python version, and ONNX package must agree.

| Route | Documented combination | Setup reference |
| --- | --- | --- |
| Current ROCm 10.1 | ONNX Runtime 1.29, MIGraphX 2.18, Python 3.12; gfx1100 is listed. WSL support is a **Technical Preview**. | [ROCm installer](https://rocm.docs.amd.com/en/docs-10.1.0/install/rocm.html), [compatibility matrix](https://rocm.docs.amd.com/en/latest/compatibility/compatibility-matrix.html), [ONNX installation](https://rocm.docs.amd.com/projects/ai-ecosystem/en/latest/inference/onnxruntime.html?rocm-ver=10.1.0) |
| Existing ROCm 7.2 installation | RX 7900 XT, WSL Ubuntu 22.04/24.04, Adrenalin 26.1.1 for WSL2, ONNX Runtime 1.23.1. | [Versioned WSL matrix](https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.2/docs/compatibility/compatibilityrad/wsl/wsl_compatibility.html), [WSL ONNX setup](https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.2/docs/install/installrad/wsl/install-onnx.html) |

The 7.2 WSL matrix lists production ONNX support, while the later 7.2.1 overview
contains a conflicting MIGraphX limitation. Match the versioned installation
instructions and validate this model on the actual machine; do not infer that
every ROCm/ONNX combination works from hardware support alone. There is no need
to downgrade an already working newer stack to 7.2.

For **ROCm 10.1 and Python 3.12**, after completing AMD's ROCm installation, run
these commands in the worker environment. Keep its existing Kokoro dependencies.

```bash
"$TUI_VOICE_TTS_PYTHON" -m pip install \
  --index-url https://stable.repo.amd.com/rocm/migraphx/whl-next/ \
  "migraphx==2.18.0+rocm10.1.0" "migraphx-libs==2.18.0+rocm10.1.0"

"$TUI_VOICE_TTS_PYTHON" -m pip install \
  --extra-index-url https://stable.repo.amd.com/rocm/onnxruntime/whl-next/ \
  "onnxruntime-ep-migraphx==1.0.0+rocm10.1.0"
```

The new plugin requires MIGraphX's **pip** libraries. The worker registers
`onnxruntime_ep_migraphx` when installed before checking providers. If the
interpreter above is not Python 3.12, select a compatible environment through
`TUI_VOICE_TTS_PYTHON` before installing these wheels. See
[AMD's MIGraphX pip instructions](https://rocm.docs.amd.com/projects/ai-ecosystem/en/latest/inference/migraphx.html?rocm-ver=10.1.0).

For an existing **ROCm 7.2** stack, its
[versioned ONNX instructions](https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.2/docs/install/installrad/native_linux/install-onnx.html)
use the bundled `onnxruntime-migraphx` wheel from
`https://repo.radeon.com/rocm/manylinux/rocm-rel-7.2/`. Follow that route's Python
and NumPy constraints rather than installing the 10.1 plugin into it.

The usual PyPI `onnxruntime-gpu` package and Kokoro's `[gpu]` extra target
**NVIDIA CUDA**, so they do not enable this AMD path. Also, the old
`ROCMExecutionProvider` was removed in ONNX Runtime 1.23; MIGraphX is its successor.
Sources: [ONNX packages](https://onnxruntime.ai/docs/install/),
[ROCm provider retirement](https://onnxruntime.ai/docs/execution-providers/ROCm-ExecutionProvider.html).

## 3. Prepare and pin the FP32 model

GPU mode defaults to `models/kokoro-v1.0.onnx`; CPU mode keeps
`models/kokoro-v1.0.int8.onnx`. FP32 is the initial GPU candidate so that CPU-oriented
INT8 optimization does not determine the GPU test. No RX 7900 XT speedup is
assumed. The existing `models/voices-v1.0.bin` is reused.

Use the **model-files-v1.0 release** matching the existing worker format:

```bash
# Publish the file only after a successful download; preserve an existing model.
if [ ! -f "$TUI_VOICE_VOICE_DIR/models/kokoro-v1.0.onnx" ]; then
  curl --fail --location \
    --output "$TUI_VOICE_VOICE_DIR/models/kokoro-v1.0.onnx.part" \
    https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx &&
    mv "$TUI_VOICE_VOICE_DIR/models/kokoro-v1.0.onnx.part" \
       "$TUI_VOICE_VOICE_DIR/models/kokoro-v1.0.onnx"
fi
```

Do not substitute the newer `model-files-v1.1` exports without checking their
interface against the installed Kokoro package. Downloads are a setup step;
the worker never downloads models while starting or synthesizing.

The selected model **and** `voices-v1.0.bin` must appear in the existing
`neural-models.json` list with SHA-256 values. Preserve all original entries;
startup continues checking every one. After reviewing the downloaded model's
source, add a local integrity pin:

```bash
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

voice_dir = Path(os.environ["TUI_VOICE_VOICE_DIR"])
manifest = voice_dir / "neural-models.json"
entries = json.loads(manifest.read_text())
name = "kokoro-v1.0.onnx"
digest = hashlib.sha256((voice_dir / "models" / name).read_bytes()).hexdigest()
assert any(item["file"] == "voices-v1.0.bin" for item in entries), "Keep the existing pinned voices entry"
existing = [item for item in entries if item["file"] == name]
if existing:
    assert all(item["sha256"] == digest for item in existing), "Existing model pin differs; investigate before replacing it"
else:
    entries.append({"file": name, "sha256": digest})
    manifest.write_text(json.dumps(entries, indent=2) + "\n")
print(name, digest)
PY
```

This computed checksum pins the local file against later changes; it is **not**
an independently verified publisher checksum. Compare against a trusted expected
digest first if one is available. The setup instructions do not supply an
upstream SHA-256.

To select another prepared model, set `TUI_VOICE_TTS_MODEL` to an absolute path
or a path relative to `VOICE_DIR/models`. Its resolved file still needs a
matching manifest entry. Keep model and package changes separate from the first
GPU activation so results are attributable.

## 4. Prove inference, then restart the worker

```bash
export TUI_VOICE_TTS_PROVIDER=migraphx
unset TUI_VOICE_TTS_MODEL

"$TUI_VOICE_TTS_PYTHON" tts_worker.py --check
python3 voice_helper.py warm --restart --kind tts
python3 voice_helper.py warm --status
python3 voice_helper.py say --text "Kokoro is testing the local AMD GPU." --voice heart --no-play
```

`--check` loads the actual selected model, synthesizes a fixed phrase twice, and
reports session providers, synthesis timings, and executed-node counts/times
from the ONNX Runtime profile. Profile durations are ORT operator-event time,
not a hardware GPU-kernel timing measurement. It exits unsuccessfully if MIGraphX was requested
but **no GPU nodes executed**. It does not play or retain speech recordings.
Provider registration alone is insufficient: ONNX can execute unsupported nodes
on the CPU. CPU phonemization and audio handling also remain normal. See
[ONNX provider selection](https://onnxruntime.ai/docs/api/python/api_summary.html)
and [profiling](https://onnxruntime.ai/docs/performance/tune-performance/profiling-tools.html).

After a successful check, the restarted worker's status must show MIGraphX and
the selected FP32 model. A previously running CPU worker does not change when
environment variables change; `--restart --kind tts` replaces it. Initial GPU
model compilation can take longer than CPU startup; the helper allows up to
300 seconds. To inspect initialization errors directly, stop TTS and run it in
the foreground (or read `tts-startup.log` beside the TTS socket):

```bash
python3 voice_helper.py warm --stop --kind tts
"$TUI_VOICE_TTS_PYTHON" tts_worker.py --run
```

Launch OpenCode from a shell carrying the same environment variables. Put the
chosen exports in that shell's profile if GPU mode should persist, then fully
exit and reopen the TUI. Test normal read-aloud and mute/resume. Compare warmed
CPU and GPU inference using the same text/model when assessing speed; model
initialization and GPU compilation are separate costs.

## Return to CPU

```bash
export TUI_VOICE_TTS_PROVIDER=cpu
unset TUI_VOICE_TTS_MODEL
python3 voice_helper.py warm --restart --kind tts
```

Update any persisted provider/model exports and reopen OpenCode from that
environment. This selects the original INT8 model and restores normal CPU
fallback behavior.
