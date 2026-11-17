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
| **WSL2 or Linux** | WSL2 uses the Windows microphone and speakers. Any current Linux distribution works; Debian 12+, Ubuntu 22.04+, Fedora and Arch are the tested shapes. |
| **OpenCode TUI** with TUI plugin support | Verified on v2.2.6. |
| **ffmpeg** | Debian/Ubuntu: `sudo apt install ffmpeg`. WSL2: a Windows `ffmpeg.exe` with DirectShow, for example WinGet Gyan.FFmpeg. |
| **Local audio server** | Linux: PipeWire or PulseAudio. ALSA is the fallback. Nothing is uploaded. |
| **Python 3.9+** | Runs the helper. The speech stack's own virtualenv is used when one is configured. |
| **A local speech stack** | `tui-voice` drives an existing local Whisper/Kokoro stack; it bundles no models and assumes no repository. You point it at your stack in step 4. |

No cloud account, API key, or network service is used for speech.

On Debian, the audio packages are already present on a desktop install. If
`ffmpeg` is missing:

```bash
sudo apt update && sudo apt install ffmpeg
# optional device listing / playback helpers, detected when present:
sudo apt install pulseaudio-utils alsa-utils
```

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

`tui-voice` does not ship models and does not assume any particular checkout.
It needs two directories from your local stack:

| Setting | What it must provide |
| --- | --- |
| `service` | A directory on `PYTHONPATH` with `web_voice.py` exposing `WebVoice`, plus an optional `.venv/bin/python`. |
| `voice` | The models directory plus the `asr-worker.py` script, `.asr-venv/bin/python`, `.venv/bin/python` and `neural-models.json`. |

Create `~/.config/tui-voice/config.json` (or
`$XDG_CONFIG_HOME/tui-voice/config.json`):

```json
{
  "service": "/srv/speech/service",
  "voice": "/srv/speech/voice"
}
```

```bash
mkdir -p ~/.config/tui-voice
chmod 700 ~/.config/tui-voice
$EDITOR ~/.config/tui-voice/config.json
```

Environment variables override the file. When neither is set, the plugin looks
in its own data directory, `~/.local/share/tui-voice/service` and
`~/.local/share/tui-voice/voice` (or `$XDG_DATA_HOME`), so you can also symlink
a stack there instead of writing a config file.

| Setting | Environment variable | Default |
| --- | --- | --- |
| Speech service directory | `TUI_VOICE_SERVICE` | `~/.local/share/tui-voice/service` |
| Models and worker runtimes | `TUI_VOICE_VOICE_DIR` | `~/.local/share/tui-voice/voice` |
| Config file | `TUI_VOICE_CONFIG` | `~/.config/tui-voice/config.json` |
| Helper interpreter | `TUI_VOICE_PYTHON` | `service/.venv/bin/python`, else `python3` |
| Warm Whisper interpreter | `TUI_VOICE_ASR_PYTHON` | `voice/.asr-venv/bin/python` |
| Warm Kokoro interpreter | `TUI_VOICE_TTS_PYTHON` | `voice/.venv/bin/python` |
| ffmpeg | `TUI_VOICE_FFMPEG` | WSL2: WinGet / `where.exe`. Linux: `ffmpeg` on `PATH` |
| Playback tool | `TUI_VOICE_PLAYER` | Linux: `pw-play`, `paplay`, `aplay`, `ffplay` |
| Capture backend | `TUI_VOICE_CAPTURE` | `pulse` when a server is reachable, otherwise `alsa` |
| Microphone | `TUI_VOICE_MIC` | Automatic; prefers a real hardware input |
| Kokoro backend | `TUI_VOICE_TTS_PROVIDER` | `cpu` |
| Kokoro model | `TUI_VOICE_TTS_MODEL` | `kokoro-v1.0.onnx` (FP32) |
| WSL interop tools | `TUI_VOICE_CMD`, `TUI_VOICE_POWERSHELL` | Discovered from the live Windows install |

Check what the plugin resolved before starting the TUI:

```bash
python3 voice_config.py --json
```

It prints both directories, the interpreters it will use, any audio overrides,
and `config_error` when the config file is unreadable.

`TUI_VOICE_PLAYER` is split into a command and its arguments. The WAV path is
appended last, or substituted for `{file}` anywhere in the command:

```bash
TUI_VOICE_PLAYER="mpv --no-video"          # mpv --no-video <wav>
TUI_VOICE_PLAYER="mpv {file} --no-video"   # mpv <wav> --no-video
```

Any player you choose must block until playback finishes, so stopping read-aloud
can stop it. Leave it unset to use the detected local player.

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

- `python3 voice_config.py --json` shows both stack directories, no
  `config_error`, and the interpreters you expect.
- `python3 voice_helper.py devices` lists your inputs; the first one is the
  automatic choice.
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
