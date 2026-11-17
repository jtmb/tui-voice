# AMD GPU read-aloud on WSL2

The RX 7900 XT can run AMD GPU compute under WSL2. This plugin can select
`MIGraphXExecutionProvider` for its existing Kokoro ONNX worker. CPU remains the
default; GPU mode is explicit and reports failure instead of silently using the
cold CPU speech path. Whisper dictation remains on its current CPU worker.

**Actual Kokoro synthesis and performance on the owner's RX 7900 XT are still
unverified.** The attempt on 2026-11-12 installed a complete ROCm 10.1 stack and
then failed at the Windows display driver, before ONNX Runtime could build a
session; see [section 1](#1-check-the-windows-driver-first). Complete the
inference check below before treating the setup as working. All synthesis stays
local over the existing AF_UNIX socket.

## 1. Check the Windows driver first

The Windows display driver supplies `/dev/dxg` to WSL. A driver that predates
AMD's Radeon WSL ROCm support can still create `/dev/dxg` while rejecting the
GPU virtual-address reservation ROCm requires. Check the driver **before**
installing or debugging any ROCm package:

```powershell
Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, DriverDate
```

Compare that version with the Windows driver column in the WSL support matrix
for the ROCm release you intend to install. Measured on this owner's machine:

| Item | Measured |
| --- | --- |
| GPU | AMD Radeon RX 7900 XT |
| Windows | 11 26H2, build 26300.9974 |
| WSL | 2.6.2.0, kernel `6.6.87.2-microsoft-standard-WSL2` |
| Display driver | `31.0.14000.58004`, 2022-12-02, Radeon Software `22.40.00.58` |
| Outcome | blocked |

That driver is from December 2022, before AMD's Radeon WSL ROCm support (the
earliest Radeon WSL matrix entry is ROCm 6.1.3 with Adrenalin 24.10.21.01;
current ROCDXG documentation pairs with Adrenalin 26.x). Symptoms with a
complete ROCm 10.1 install on this host:

- `dmesg` repeats `misc dxg: dxgkio_reserve_gpu_va: Ioctl failed: -75`.
- `rocminfo`, `import migraphx`, and `tts_worker.py --check` all exit 139
  (SIGSEGV) in `librocdxg` after
  `[ReserveSvmSpace] ... fail to reserve gpu va` and
  `fail to reserve Local Heap Space!`.
- `rocm-sdk test` runs 19 tests with 2 errors (`amdflang --help` exits 255,
  `rocminfo` segfaults).

No userspace package or environment variable compensates for this. Install a
current AMD Adrenalin driver (use the WSL2 build when AMD publishes one for the
GPU), reboot Windows, and confirm `rocminfo` lists `gfx1100` before continuing.

The driver install needs an elevated (UAC) prompt. An administrator account
running in a normal, unelevated session cannot install the display driver, and
no Python or environment change will fix the failure. Only proceed when
`rocminfo` succeeds.

## 2. Check the WSL and Python environments

Run from the existing checkout. Adjust the overrides if the voice stack lives
elsewhere; the model directory and TTS interpreter must refer to the same stack.

```bash
cd ~/repos/tui-voice
export TUI_VOICE_VOICE_DIR="${TUI_VOICE_VOICE_DIR:-$HOME/.local/share/tui-voice/voice}"
export TUI_VOICE_TTS_PYTHON="${TUI_VOICE_TTS_PYTHON:-$TUI_VOICE_VOICE_DIR/.venv/bin/python}"

cat /etc/os-release
uname -r
ls -l /dev/dxg
"$TUI_VOICE_TTS_PYTHON" --version
"$TUI_VOICE_TTS_PYTHON" -c "import onnxruntime, kokoro_onnx; print(onnxruntime.__version__, onnxruntime.get_available_providers())"
uv pip freeze --python "$TUI_VOICE_TTS_PYTHON" 2>/dev/null || "$TUI_VOICE_TTS_PYTHON" -m pip freeze
```

AMD's WSL bridge uses **`/dev/dxg`**, provided through the Windows driver. Missing
`/dev/kfd`, `/dev/dri`, CUDA, or `nvidia-smi` does not establish that an AMD GPU is
unavailable. After installing ROCm, `rocminfo` must identify the GPU (`gfx1100`
for the RX 7900 XT). See [AMD's WSL explanation](https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.2.1/docs/install/installrad/wsl/howto_wsl.html)
and [GPU specifications](https://rocm.docs.amd.com/en/latest/reference/gpu-specs.html).

## 3. Install one matching AMD runtime

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

A uv-managed venv has no `pip` module, so `-m pip` fails with `No module named
pip`. Use uv against that same interpreter; requirements and indexes are
unchanged:

```bash
uv pip install --python "$TUI_VOICE_TTS_PYTHON" \
  --index-url https://stable.repo.amd.com/rocm/migraphx/whl-next/ \
  "migraphx==2.18.0+rocm10.1.0" "migraphx-libs==2.18.0+rocm10.1.0"
uv pip install --python "$TUI_VOICE_TTS_PYTHON" \
  --extra-index-url https://stable.repo.amd.com/rocm/onnxruntime/whl-next/ \
  "onnxruntime-ep-migraphx==1.0.0+rocm10.1.0"
```

Also install the `rocm==10.1.0` and `rocm-sdk-*` wheels (core, libraries, and
`rocm-sdk-device-gfx1100`) from the index named in AMD's ROCm 10.1 instructions,
then confirm `rocm-sdk targets` lists `gfx1100`. Record
`uv pip freeze --python "$TUI_VOICE_TTS_PYTHON"` **before** installing so the CPU
stack can be restored.

The measured install produced `onnxruntime==1.29.0`,
`onnxruntime-ep-migraphx==1.0.0+rocm10.1.0`, `migraphx==2.18.0+rocm10.1.0`,
`migraphx-libs==2.18.0+rocm10.1.0`, `rocm==10.1.0`, `rocm-sdk-core==10.1.0`,
`rocm-sdk-libraries==10.1.0`, and `rocm-sdk-device-gfx1100==10.1.0`, alongside
the existing `kokoro-onnx==0.6.1` and `numpy==2.5.4`. Do not mix routes: keep
the legacy bundled `onnxruntime-migraphx` wheel out of an interpreter that uses
the `onnxruntime-ep-migraphx` plugin, or provider registration and library
versions can disagree.

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

## 4. Prepare and pin the FP32 model

GPU mode and the CPU default both use `models/kokoro-v1.0.onnx` (FP32); INT8
remains available only by explicitly setting `TUI_VOICE_TTS_MODEL` to
`kokoro-v1.0.int8.onnx`. FP32 measured faster than INT8 on the owner's CPU/ORT
runtime, and using one artifact keeps CPU and GPU comparisons identical. No
RX 7900 XT speedup is assumed. The existing `models/voices-v1.0.bin` is reused.

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

## 5. Prove inference, then restart the worker

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
300 seconds. If the GPU worker dies during startup, `warm --restart` exits 1 and
names the startup log; on WSL2 a SIGSEGV/SIGABRT hint points back to the Windows
driver and this document. To inspect initialization errors directly, stop TTS
and run it in the foreground (or read `tts-startup.log` beside the TTS socket):

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
python3 voice_helper.py warm --status
```

`warm --status` must report `requested_provider: CPUExecutionProvider` and the
`kokoro-v1.0.onnx` model. Confirm speech with
`say --text "CPU read aloud." --voice heart --no-play`; it should report
`warm_chunks: 1` and no MIGraphX provider.

Update any persisted provider/model exports and reopen OpenCode from that
environment. This selects the pinned FP32 model and restores normal CPU
fallback behavior. Set `TUI_VOICE_TTS_MODEL=kokoro-v1.0.int8.onnx` only if you
deliberately want the older INT8 path.

The AMD packages do not need to be removed for CPU use, but to return the
interpreter to its pre-GPU state reinstall a captured freeze:

```bash
uv pip sync --python "$TUI_VOICE_TTS_PYTHON" /path/to/pre-gpu-freeze.txt
```

That removes the `onnxruntime-ep-migraphx`, `migraphx`, and `rocm-sdk-*` wheels
and restores CPU-only ONNX Runtime. Keep the FP32 model file and its manifest pin;
both CPU and GPU use it, while INT8 remains opt-in.
