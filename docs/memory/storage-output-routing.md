---
{
  "version": 1,
  "scope": "repository",
  "repository": "tui-voice",
  "id": "storage-output-routing",
  "title": "H-backed development output; preserve the existing voice runtime",
  "tags": ["storage", "wsl", "artifacts", "preservation"],
  "kind": "decision",
  "updated": "2026-11-16",
  "verified": "2026-11-16",
  "sources": ["tools/artifact_env.py", "docs/STORAGE.md", "HANDOFF.md", "/home/brajam/repos/AGENTS.md"]
}
---

James authorized the bounded storage setup to keep the existing checkout and use
`H:\AgentArtifacts\projects\tui-voice\runs\storage-setup-20261114` on the same
physical `/mnt/h` storage. Reuse that root for future generated development
output; all project runs/caches together share the 100,000,000,000-byte ceiling
(the owner-approved November 2026 update, superseding the original 50 GB).

Use `tools/artifact_env.py --reserve-bytes N -- command ...` before invoking
compiler/tests/tools. It sets supported temporary, cache, bytecode, report and
log destinations per process, checks the H mount and combined accounting, and
does not download dependencies or copy a checkout. Compiler/report output flags
must still be supplied. This is **not** filesystem quota enforcement.

The coordinator-required drive floors are 15 GiB free on C and 15 GiB remaining
on H after the command's additional peak plus metadata allowance. Build children
use H-backed uppercase npm cache/log variables and build-only AppData; `WSLENV`
translates these and temp paths. The parent/user runtime AppData is preserved.

Source, deliberately versioned assets, released work, existing memory, shared
Whisper/Kokoro runtimes/models and active tasks stay in place. No driver install,
runtime migration, model change or service restart is authorized by this setup.
AF_UNIX sockets remain on the existing Linux runtime filesystem. Read the
handoff for retained legacy paths, safety-check gaps and current byte counts.
