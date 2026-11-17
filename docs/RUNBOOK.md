# Runbook

## Install / load globally

The plugin is TUI-only, so it lives in the CLI config, not the server config.

`~/.config/opencode/cli.json`:

```json
{
  "plugins": ["/absolute/path/to/tui-voice"]
}
```

Preserve unrelated CLI settings when editing. Verify:

```bash
opencode plugin list
```

The list should include the absolute path of this checkout.

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

- manual on/off recording (no pause or duration cutoff)
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

The speech stack and its interpreters are outside this repository and are
resolved by `voice_config.py`:

```bash
python3 voice_config.py --json   # config path, stack dirs, interpreters, audio
```

Set them in `~/.config/tui-voice/config.json` or with `TUI_VOICE_SERVICE`,
`TUI_VOICE_VOICE_DIR` and `TUI_VOICE_PYTHON`. ffmpeg and the playback tool are
detected per platform; override `TUI_VOICE_FFMPEG` and `TUI_VOICE_PLAYER`.
Microphone override: `TUI_VOICE_MIC`.
