# Generated output and storage setup

## Owner-authorized roots

- Source: this checkout; no extra checkout or dependency copy.
- All project runs share one 100,000,000,000-byte ceiling, not a fresh allowance
  per run. The executable wrapper measures the entire H project root plus known
  legacy output and conservatively charges the existing shared uv cache.
- This setup and future per-process development output reuse
  `H:\AgentArtifacts\projects\tui-voice\runs\storage-setup-20261114`
  (`/mnt/h/AgentArtifacts/projects/tui-voice/runs/storage-setup-20261114`).
- No model, voice bundle, interpreter, service or socket is moved. The
  speech stack (`service/`, `voice/`) is runtime data under the configured or
  default stack paths, not disposable build output.

## Per-process usage

From this checkout, set destinations **before** invoking a development tool:

```bash
tools/artifact_env.py --report
tools/artifact_env.py --reserve-bytes 1000000 -- python3 -B -c 'print("focused check")'
```

The wrapper is an executable Python script using existing system dependencies.
It verifies `/mnt/h` is mounted from `H:` (refuses a bare Linux directory or
linked destination), checks the combined allowance plus the caller's additional
peak, and configures only its child's environment. Before creating destinations
or launching an output-producing child, C must have at least **15 GiB** free
(16,106,127,360 bytes), and H must retain at least **15 GiB** after the requested
additional peak plus a 65,536-byte metadata allowance. These drive floors are
separate from the project's 50-GB allowance.

| Output | Destination under the run root / behavior |
| --- | --- |
| Python temporary files / Windows child TEMP and TMP | `tmp/`; `WSLENV` path translation |
| Bytecode | imports disabled with `PYTHONDONTWRITEBYTECODE=1`; explicit `py_compile` uses `cache/pycache/` |
| Ruff, uv, pip, npm caches | dedicated `cache/` children; package managers offline, no downloads |
| npm cache / logs | uppercase `NPM_CONFIG_CACHE=cache/npm`, `NPM_CONFIG_LOGS_DIR=logs/npm`; conflicting lowercase aliases removed |
| Build-only Windows AppData | `APPDATA=build-appdata/roaming`, `LOCALAPPDATA=build-appdata/local`; user/runtime AppData unchanged |
| pytest temp / cache, coverage | `tmp/pytest-PID`, `cache/pytest`, `test-output/.coverage` |
| Helper and future plugin logs | `state/tui-voice/` via `TUI_VOICE_LOG_DIR` / `XDG_STATE_HOME` |
| Compiler/build output | `build/` via `TUI_VOICE_BUILD_DIR`; **pass the tool's explicit output flag** |
| Test reports | `test-output/` via `TUI_VOICE_TEST_OUTPUT_DIR`; select a report flag explicitly |

`WSLENV` includes `/p` conversion for `TEMP`, `TMP`, `APPDATA`, `LOCALAPPDATA`,
`NPM_CONFIG_CACHE` and `NPM_CONFIG_LOGS_DIR`, so Windows build children receive
`H:\...` paths. Unrelated `WSLENV` entries and model/runtime/socket settings are
preserved. Do not use the build-only AppData environment to migrate/start the
user's normal OpenCode or voice runtime.

For example, a future esbuild invocation must use an already installed executable
and `--outfile="$TUI_VOICE_BUILD_DIR/tui.js"` inside the wrapper's child shell.
Do not use network-capable `npx` to obtain another tool copy. This setup does not
invoke a build or install anything. Reserve the full expected additional peak
(including reports and caches), not just final output size.

The wrapper does **not** impose a filesystem quota, sandbox arbitrary programs,
redirect explicit hardcoded paths or synchronize reservations across chats.
Coordinate concurrent writers and inspect actual output after each step. A new
output location outside the enumerated roots needs an accounting update first.
Never override `XDG_RUNTIME_DIR` to H/DrvFS for AF_UNIX sockets.

## Preservation and concrete follow-ups

The initial hash receipt and bounded measurements live in `receipts/` under the
H run root; current assignment status and exact bytes are in `HANDOFF.md`.
No raw transcript or model is copied into memory.

- Existing source, releases, lockfiles, model pins, interpreters and active work
  are preserved. Pre-existing memory/config wiring is not replaced.
- Legacy caches/fixtures are retained because the cleanup check could not
  inspect `/proc/469/fd` (`sd-pam`). Do not retry the same failed cleanup through
  another tool, identity or alias. The exact paths are recorded in the handoff.
- The staged AMD installer/recovery records are counted as generated output but
  are not installed, revalidated, moved or deleted. James declined the driver
  installation; disposition of the package is a separate coordinator decision.
- Shared `.cache/uv` attribution is unresolved, so its **entire** measured size
  is charged as a conservative guard; no new shared-cache exemption is claimed.
- Already running helpers/TUIs retain their existing log/temp destinations.
  Worker startup logs and speech progress remain beside Linux AF_UNIX sockets.
  Changing these requires separate runtime work, not a service restart in this
  setup. Do not claim that all live runtime I/O has moved to H.

Only remove verified obsolete artifacts after consumers end, exact ownership and
containment checks pass, and no process/container/mount references remain.
Never remove deliberately versioned generated assets solely because they appear
to be output. No recurring cleanup or automatic migration is installed.
