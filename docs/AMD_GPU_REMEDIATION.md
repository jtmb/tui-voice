# AMD GPU recovery and acceptance

Use this runbook after reading [AMD_GPU.md](AMD_GPU.md), `AGENTS.md`, and
`rules.md`. It supplies executable preparation and measurement tools. Running
them on a development machine does not qualify another computer's GPU.
This runbook supersedes the earlier generic driver-update instruction and the
optional-only GPU proof gate. A driver mismatch is an actionable prerequisite;
the before/after runtime test establishes whether it was the sole blocker.

## Preserve the working environment

From the existing WSL checkout, preserve private recovery data before restarting:

```bash
export TUI_VOICE_TTS_PYTHON="$(python3 -c 'import voice_helper; print(voice_helper.TTS_PYTHON)')"
export TUI_VOICE_VOICE_DIR="$(python3 -c 'import voice_helper; print(voice_helper.VOICE_DIR)')"
gpu_state="$HOME/.local/state/tui-voice/gpu-remediation/$(date -u +%Y%m%dT%H%M%SZ)"
umask 077
mkdir -p "$gpu_state"
if [ -f /tmp/opencode/tts-venv-before-gpu.txt ]; then
  cp /tmp/opencode/tts-venv-before-gpu.txt "$gpu_state/pre-upgrade-freeze.txt"
fi
uv pip freeze --python "$TUI_VOICE_TTS_PYTHON" > "$gpu_state/current-freeze.txt"
cp "$TUI_VOICE_VOICE_DIR/neural-models.json" "$gpu_state/neural-models.json"
git rev-parse HEAD > "$gpu_state/git-head.txt"
```

A current freeze cannot recreate a missing pre-upgrade snapshot. Preserve the
package indexes needed to reinstall AMD wheels as well. Keep these files, actual
machine inventory, and full logs local; publish only an authorized sanitized
summary. Keep the installed runtime and pinned model unchanged for the first
host-driver experiment.

## Stage and update the Windows driver

The supplied tool targets AMD's ROCm 10.1 package: Adrenalin **26.10.41.05**,
Windows Driver Store **32.0.31041.5005**. The
[official release page](https://www.amd.com/en/resources/support-articles/release-notes/RN-RAD-ROCM-10-01.html)
lists RX 7900-series compatibility and links the exact installer.

From WSL, stage the package:

```bash
powershell.exe -NoProfile -File "$(wslpath -w "$PWD/tools/prepare_amd_driver.ps1")"
```

Default behavior inventories the selected AMD device, downloads the package,
checks its PE header and valid AMD Authenticode signature, records a SHA-256,
and writes local JSON, a transcript, and `RESUME.txt` under Windows LocalAppData.
The default device pattern is `*RX 7900*`; use `-DevicePattern` only to select
another documented compatible device. It does not install or reboot.

If AMD redirects the download to HTML, the tool rejects it. Download the exact
package through AMD's release page, then supply its Windows path with
`-InstallerPath`. The signature proves signer/integrity; it does not identify the
driver release by itself. Keep the exact official filename and release origin.
The locally computed SHA-256 is an audit identifier, not an AMD-published digest.

After saving work and completing preparation, request normal elevation:

```bash
powershell.exe -NoProfile -File "$(wslpath -w "$PWD/tools/prepare_amd_driver.ps1")" -Install
```

This opens AMD's normal installer through UAC and waits for it. Approve UAC and
complete any required installer interaction. Choose an ordinary update; do not
start with Factory Reset. No undocumented silent switches, execution-policy
bypass, UAC bypass, or automatic reboot is used. If local policy rejects the
script, use the organization's permitted execution path; do not disable policy.

The tool saves the launcher exit code and copies AMD's
[documented installer log](https://www.amd.com/en/resources/support-articles/faqs/gpu-kb195.html)
when readable, with its original timestamp. A log may predate this attempt;
absence of a fresh log or exit zero does not prove installation succeeded.
`RESUME.txt` includes a Windows-native copy of the verification command, so it
remains usable when WSL is stopped.

After a controlled restart, run the saved command or:

```bash
powershell.exe -NoProfile -File "$(wslpath -w "$PWD/tools/prepare_amd_driver.ps1")" -Verify
```

`-Verify` checks the active matched display device's driver version and device
error code. Exit 0 means the named target is active; exit 2 means review is
needed. It never downgrades a newer driver to match this target. A newer version
may be compatible: verify AMD's documentation and test the runtime. Other script
failures exit 1. None of these exit codes proves GPU model execution.

## Runtime verification

Reopen the same checkout and restore the configured interpreter/model overrides.
Use the ROCm tools installed beside that interpreter, not an unrelated PATH entry:

```bash
export TUI_VOICE_TTS_PYTHON="$(python3 -c 'import voice_helper; print(voice_helper.TTS_PYTHON)')"
export TUI_VOICE_VOICE_DIR="$(python3 -c 'import voice_helper; print(voice_helper.VOICE_DIR)')"
tts_bin="$(dirname "$TUI_VOICE_TTS_PYTHON")"
"$tts_bin/rocm-sdk" version
"$tts_bin/rocm-sdk" targets
timeout --kill-after=5s 60s "$tts_bin/rocminfo"
timeout --kill-after=5s 60s "$TUI_VOICE_TTS_PYTHON" -c 'import migraphx; print(migraphx.__file__)'
```

Require a clean runtime exit and a real `gfx1100` GPU agent.
`rocm-sdk targets` describes installed target support, not device execution.
The [ROCm Python packaging guide](https://github.com/ROCm/TheRock/blob/main/RELEASES.md)
documents these interpreter-local command shims.

If initialization still fails after the driver update, preserve its exit code and
fresh kernel log, checkpoint work, and use stable `wsl.exe --update`.
A subsequent `wsl.exe --shutdown` ends all running distributions and the agent;
save the continuation before doing so. Record the new kernel/WSL versions and
repeat the same bounded probe. See [Microsoft's commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands).

ROCm 10.1's [installer](https://rocm.docs.amd.com/en/docs-10.1.0/install/rocm.html)
includes Ubuntu 26.04 under WSL as a technical preview. Its
[matrix](https://rocm.docs.amd.com/en/latest/compatibility/compatibility-matrix.html)
names Windows 11 25H2. Another Windows release is a qualification gap to test,
not proof of incompatibility. Do not apply native Ubuntu kernel instructions
to WSL or rebuild the distro solely from this mismatch.

If the matched driver/current WSL still fail, inspect loaded library provenance
and scoped loader variables, then reproduce once in a clean disposable matching
environment. Preserve the working venv. Do not layer legacy ROCDXG packages onto
the current stack or repeatedly reinstall identical packages. A framework rewrite
does not repair a failure below the framework.

## Verify Kokoro and the live worker

Only after runtime initialization succeeds:

```bash
export TUI_VOICE_TTS_PROVIDER=migraphx
export TUI_VOICE_TTS_MODEL=kokoro-v1.0.onnx
"$TUI_VOICE_TTS_PYTHON" tts_worker.py --check
python3 voice_helper.py warm --restart --kind tts
python3 voice_helper.py warm --status
python3 voice_helper.py say --text "Kokoro is using the local AMD GPU." --voice heart --no-play
```

Require `gpu_execution_verified: true`, matching model identity and nonzero
MIGraphX `compute_node_events`. The worker now performs this proof on its actual
serving session before readiness, including with `--no-warmup`. Provider
registration, memory copies alone, and an old unverified worker are rejected.
Individual CPU operations may remain. Normal serving reports
`profiling_active: false`.

If the model fails after runtime health passes, use a small checked ONNX/MIGraphX
compute graph to isolate provider integration from a Kokoro operator/shape issue.
Preserve model pins and give any export experiment its own filename.

## Compare the same model without profiling

Stop the warm TTS worker to avoid concurrent model load, then run sequentially:

```bash
python3 voice_helper.py warm --stop --kind tts
"$TUI_VOICE_TTS_PYTHON" tools/tts_benchmark.py --provider cpu --model kokoro-v1.0.onnx --samples 5
"$TUI_VOICE_TTS_PYTHON" tools/tts_benchmark.py --provider migraphx --model kokoro-v1.0.onnx --samples 5
python3 voice_helper.py warm --start --kind tts
```

Both commands default to identical FP32, text, voice, and thread settings.
Compare the model SHA-256, raw warm samples, median/range, audio duration, and
real-time factor. `preparation_s` includes mandatory GPU startup proof;
`first_measured_synthesis_s` is therefore not cold GPU compilation time.
The JSON distinguishes prior synthesis in preparation. Timed speech is unprofiled;
`--check` explicitly labels its separate diagnostic timings as profiled.
Report GPU speed honestly even when it is slower.

Launch a fresh OpenCode process with the persistent GPU environment, including
outside the repository. Verify audible local read-aloud, all four voices,
mute/resume, short/long replies, idle restart, singleton startup, and dictation.
Test explicit-GPU failure and deliberate CPU rollback, then restore the intended
GPU setting. Programmatic playback success and confirmed audibility are separate
observations. Keep the PR draft until actual device and product acceptance pass.
