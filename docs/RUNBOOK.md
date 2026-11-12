# Runbook

## Install / load globally

The plugin is TUI-only, so it lives in the CLI config, not the server config.

`~/.config/opencode/cli.json`:

```json
{
  "plugins": ["/home/brajam/repos/tui-voice"]
}
```

Preserve unrelated CLI settings when editing. Verify:

```bash
opencode plugin list
```

The list should include `/home/brajam/repos/tui-voice`.

## Start / restart

Close the TUI completely and reopen it. `/reload` does not hot-add a newly
configured TUI-only plugin path.

From a session:

- `Ctrl+Alt+V` or `F9` — dictate
- `/dictate` — dictation command
- `Ctrl+Alt+S` — settings dialog

## Update

1. Edit files in `~/repos/tui-voice`.
2. Check the helper:
   ```bash
   python3 -m py_compile voice_helper.py
   ```
3. Reopen the TUI so the TUI plugin generation is fresh.
4. Run the [fresh-TUI gate](TESTING.md#fresh-tui-gate).

## Settings reset

Open **Voice: Settings** and choose **Reset to defaults**:

- 1.5 s pause
- 60 s maximum
- automatic microphone
- −35 dB threshold
- 0 dB gain
- read-aloud On
- Heart

## Rollback

The repository uses local Git commits:

```bash
cd ~/repos/tui-voice
git log --oneline
git checkout <previous-commit>
```

Reopen the TUI after switching commits. Do not reset the user's OpenCode storage
unless settings are actually corrupt.

## Dependencies

The default local service and Python are outside this repository:

```bash
~/repos/fire-tv-next/lulu-launcher/service
~/repos/fire-tv-next/lulu-launcher/service/.venv/bin/python
```

Override with `TUI_VOICE_SERVICE` and `TUI_VOICE_PYTHON`. Windows ffmpeg is
auto-discovered; override `TUI_VOICE_FFMPEG`. Microphone override:
`TUI_VOICE_MIC`.
