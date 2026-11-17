# Install tui-voice

`tui-voice` is a **TUI-only** OpenCode plugin. It loads in the terminal client,
not the OpenCode server, and it needs a local Whisper/Kokoro speech stack.

Installing takes about a minute. The only real decision is **where** the plugin
should load.

## 1. Ask the owner: global or local?

> **Agent:** ask before changing configuration. Suggested prompt:
> *"Should tui-voice load in **every** project on this machine, or **only** in
> this project?"*

| | **Global** (recommended on a personal machine) | **Local** (one project) |
| --- | --- | --- |
| Available in | every OpenCode TUI session | that project only |
| Configured in | `~/.config/opencode/cli.json` | `<project>/.opencode/plugins/tui-voice` |
| Best for | your own daily setup | trying it out, shared machines, one repo |
| Restart needed | full TUI restart | full TUI restart from that project |

**Enable exactly one scope.** If both a global entry and a project plugin exist,
the plugin is discovered twice and its footer/commands can appear twice.

## 2. Prerequisites

| Requirement | Notes |
| --- | --- |
| **WSL2** | The supported platform; the microphone and speakers come from Windows. |
| **OpenCode TUI** with TUI plugin support | Verified on v2.2.6. |
| **Windows `ffmpeg.exe`** with DirectShow | WinGet Gyan.FFmpeg is discovered automatically. |
| **Local Whisper/Kokoro stack** | `tui-voice` drives an existing stack; it does not bundle models. Default: `~/repos/fire-tv-next/lulu-launcher/service` with `../voice` beside it. |

No cloud account, API key, or network service is used for speech.

## 3a. Global install

1. Get the absolute path of this checkout:

   ```bash
   cd /path/to/tui-voice
   pwd
   ```

2. Add that path to `plugins` in `~/.config/opencode/cli.json` (or
   `$XDG_CONFIG_HOME/opencode/cli.json`). Create the file if it does not exist,
   and preserve every unrelated setting and existing plugin entry:

   ```json
   {
     "$schema": "https://opencode.ai/v2/cli.json",
     "plugins": ["/absolute/path/to/tui-voice"]
   }
   ```

3. **Fully quit and reopen the TUI.** `/reload` is not enough for a newly added
   or moved TUI-only plugin path.

4. Verify discovery:

   ```bash
   opencode plugin list
   ```

   The checkout path should be listed as `local`.

## 3b. Local install (one project)

Project plugins are discovered from `<project>/.opencode/plugins/`. Reuse this
single checkout with a symlink so the two locations never drift apart; do not
copy the repository.

1. From the project root, point the discovery directory at this checkout:

   ```bash
   mkdir -p .opencode/plugins
   ln -s /absolute/path/to/tui-voice .opencode/plugins/tui-voice
   ```

2. Remove the checkout's entry from `~/.config/opencode/cli.json` if it is there,
   so the plugin is not loaded globally as well.

3. Start the TUI from that project and fully restart it.

4. Verify discovery from the project root:

   ```bash
   opencode plugin list
   ```

   `.opencode/plugins/tui-voice` should be listed.

An absolute symlink is machine-specific: don't commit it unless the checkout
lives at the same path on every machine that uses the project.

## 4. Point it at your speech stack

The defaults work when the local stack is in its usual place. Override only the
values that differ, in the environment that launches OpenCode:

| Dependency | Default | Override |
| --- | --- | --- |
| Speech service | `~/repos/fire-tv-next/lulu-launcher/service` | `TUI_VOICE_SERVICE` |
| Models and worker runtimes | `voice` beside the service directory | `TUI_VOICE_VOICE_DIR` |
| Helper interpreter | `.../service/.venv/bin/python` | `TUI_VOICE_PYTHON` |
| Warm Whisper interpreter | `voice/.asr-venv/bin/python` | `TUI_VOICE_ASR_PYTHON` |
| Warm Kokoro interpreter | `voice/.venv/bin/python` | `TUI_VOICE_TTS_PYTHON` |
| Windows ffmpeg | Auto-discovered from WinGet / PATH | `TUI_VOICE_FFMPEG` |
| Microphone | Automatic (prefers a real input) | `TUI_VOICE_MIC` |
| Kokoro backend | `cpu` | `TUI_VOICE_TTS_PROVIDER` |
| Kokoro model | `kokoro-v1.0.onnx` (FP32) | `TUI_VOICE_TTS_MODEL` |

Set `TUI_VOICE_PYTHON` explicitly when you relocate the service:
`TUI_VOICE_SERVICE` does not change the TUI's built-in interpreter path.

## 5. First run

Open a chat session. The footer shows one compact panel: status circle, `🎙`, a
divider, then `🔊`.

| Action | Control |
| --- | --- |
| Start / stop dictation | Footer `🎙`, `F9`, `Ctrl+Alt+V`, or `/dictate` |
| Toggle read-aloud and stop playback | Footer `🔊`/`🔇`, `F10`, or `Ctrl+Alt+M` |
| Replay the last reply | `/read-last` |
| Recover the last transcript | `/voice-text` |
| Settings | `Ctrl+Alt+S` |

Press `F9`, speak, and watch the words appear in the message box as you speak.
Dictation stays on until you press `F9` or the mic again — pauses do not stop it.
Review the draft and send it yourself; **nothing is sent automatically**.

## 6. Verify

- `opencode plugin list` shows the checkout (global) or
  `.opencode/plugins/tui-voice` (local).
- A fresh TUI shows the rounded footer panel.
- `F9` turns the circle amber `●` and inserts text into the message box.
- `F10` stops any current read-aloud and toggles the `🔊`/`🔇` icon.

If something fails, see [Troubleshooting](docs/TROUBLESHOOTING.md) or the log at
`~/.local/state/tui-voice/voice.log`.

## Upgrade

```bash
cd /path/to/tui-voice
git pull
```

Then fully restart the TUI. Settings are preserved; saved pause/maximum-turn
values from before v0.2.0 are ignored because dictation is now manual on/off.

## Uninstall

- **Global:** remove the checkout path from `~/.config/opencode/cli.json`.
- **Local:** delete `.opencode/plugins/tui-voice`.
- Then fully restart the TUI. Optionally remove `~/.local/state/tui-voice/`.

Enabling one scope only: if you switch from global to local (or back), remove the
other entry in the same step.
