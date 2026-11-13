<p align="center">
  <img src="docs/media/tui-voice-logo.png" width="168" alt="TUI Voice logo: a cyan terminal prompt and waveform with an amber cursor">
</p>

<h1 align="center">tui-voice</h1>

<p align="center">
  <strong>Speak your prompts. Hear your replies.</strong><br>
  Local voice dictation and read-aloud for the OpenCode TUI.
</p>

<p align="center">
  <a href="docs/SETTINGS.md">Settings</a> ·
  <a href="docs/TROUBLESHOOTING.md">Troubleshooting</a> ·
  <a href="docs/AMD_GPU.md">AMD GPU setup</a>
</p>

## Quick start

Requires **WSL2**, OpenCode with TUI plugins (verified on **v2.2.6**), Windows
**FFmpeg / DirectShow**, and an installed local **Whisper/Kokoro** stack.
[Runtime paths](#runtime-paths) cover its external models and Python environments.

**1. Register your checkout.** Add its absolute path (`pwd`) to `plugins` in
`~/.config/opencode/cli.json`, preserving existing entries. Current installation:

```json
{
  "plugins": ["/home/brajam/repos/tui-voice"]
}
```

**2. Set your voice service path** in the WSL shell that launches OpenCode:

```bash
export TUI_VOICE_SERVICE="$HOME/repos/fire-tv-next/lulu-launcher/service"
export TUI_VOICE_PYTHON="$TUI_VOICE_SERVICE/.venv/bin/python"
opencode plugin list
```

Persist these exports for future launches. The plugin list should show your checkout.

**3. Fully restart the TUI, then press `F9`.** `/reload` alone will not load a new
plugin path.

## Controls

The microphone and speaker share one compact panel in the bottom footer.

| Action | Shortcut | Footer |
| --- | --- | --- |
| Dictate / finish the recording | `F9` or `Ctrl+Alt+V` | Microphone |
| Mute / resume read-aloud | `F10` or `Ctrl+Alt+M` | Speaker |
| Open voice settings | `Ctrl+Alt+S` | — |

Or use `/dictate`. Speak, pause, and review your draft. **You decide when to send.**
Dictate from home or during a reply; transcripts wait for the message box.

Choose **Heart**, **Velvet**, **English Rose**, or **Dora** for completed replies.
Defaults: **1.5 s** pause, **60 s** maximum turn, read-aloud **on**. Settings are
saved globally.

## Local speech, ready between turns

Warm workers reuse loaded models and prepare speech one chunk ahead. Recognition,
synthesis, and diagnostic logs stay local; the helper cleans up temporary audio.

CPU uses **FP32 Kokoro**. [AMD MIGraphX](docs/AMD_GPU.md) is opt-in; RX 7900 XT
inference remains unverified in the recorded hardware acceptance.

## Runtime paths

<details>
<summary><strong>Custom locations, model selection, and worker commands</strong></summary>

Set overrides in the environment that launches OpenCode. Set
`TUI_VOICE_PYTHON` explicitly when relocating the service: `TUI_VOICE_SERVICE`
does not change the TUI's built-in interpreter path.

| Dependency | Default location or value | Override |
| --- | --- | --- |
| Voice service | `~/repos/fire-tv-next/lulu-launcher/service` | `TUI_VOICE_SERVICE` |
| Models and worker runtimes | `voice` beside the service directory | `TUI_VOICE_VOICE_DIR` |
| TUI helper interpreter | `/home/brajam/repos/fire-tv-next/lulu-launcher/service/.venv/bin/python` | `TUI_VOICE_PYTHON` |
| Warm Whisper interpreter | `VOICE_DIR/.asr-venv/bin/python` | `TUI_VOICE_ASR_PYTHON` |
| Warm Kokoro interpreter | `VOICE_DIR/.venv/bin/python` | `TUI_VOICE_TTS_PYTHON` |
| Windows FFmpeg | Discovered from the Windows installation | `TUI_VOICE_FFMPEG` |
| Microphone | Automatic selection | `TUI_VOICE_MIC` |
| Kokoro provider | `cpu` | `TUI_VOICE_TTS_PROVIDER=cpu` or `migraphx` |
| Kokoro model | `kokoro-v1.0.onnx` (FP32) | `TUI_VOICE_TTS_MODEL` |

`VOICE_DIR` means the voice models/runtime directory above. Selected models and
voices must be pinned in its `neural-models.json`. INT8 remains available with
`TUI_VOICE_TTS_MODEL=kokoro-v1.0.int8.onnx`.

From the plugin checkout, with the quick-start exports set:

```bash
"$TUI_VOICE_PYTHON" voice_helper.py devices
"$TUI_VOICE_PYTHON" voice_helper.py warm --status
"$TUI_VOICE_PYTHON" voice_helper.py warm --start
"$TUI_VOICE_PYTHON" voice_helper.py warm --stop
```

Workers exit after 30 idle minutes by default and are started again when needed.
Restart TTS with `warm --restart --kind tts` after changing its provider, model, or
interpreter. CPU mode retains the cold local fallback; an explicit GPU request
fails clearly if the requested backend is unavailable.

</details>

## Further reading

[Documentation](docs/README.md) · [Runbook](docs/RUNBOOK.md) ·
[Architecture](docs/ARCHITECTURE.md) · [Testing](docs/TESTING.md) · [Handoff](HANDOFF.md)

Contributing? Start with [AGENTS.md](AGENTS.md) and the [product rules](rules.md).
