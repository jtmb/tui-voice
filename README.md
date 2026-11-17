<p align="center">
  <img src="docs/media/tui-voice-logo.png" width="150" alt="tui-voice logo: a cyan terminal prompt and waveform with an amber cursor">
</p>

<h1 align="center">tui-voice</h1>

<p align="center">
  <strong>Speak your prompts. Hear your replies.</strong><br>
  Local voice dictation and read-aloud for the OpenCode TUI.
</p>

<p align="center">
  <a href="INSTALL.md"><strong>Install</strong></a> ·
  <a href="docs/SETTINGS.md">Settings</a> ·
  <a href="docs/TROUBLESHOOTING.md">Troubleshooting</a> ·
  <a href="https://github.com/jtmb/tui-voice/releases">Releases</a>
</p>

> **Agents:** read **[INSTALL.md](INSTALL.md)** before installing. It guides you
> through adding the plugin and asks the owner whether tui-voice should load
> **globally** (every project) or **locally** (this project only). Enable one
> scope, never both.

<p align="center">
  <a href="https://github.com/jtmb/tui-voice/releases"><img alt="Latest release" src="https://img.shields.io/github/v/release/jtmb/tui-voice?style=flat-square&label=release&color=6E56CF"></a>
  <img alt="Speech stays local" src="https://img.shields.io/badge/speech-100%25%20local-2EA043?style=flat-square">
  <img alt="No cloud, no account" src="https://img.shields.io/badge/cloud-none-2EA043?style=flat-square">
  <img alt="Platform WSL2" src="https://img.shields.io/badge/platform-WSL2-0078D4?style=flat-square">
</p>

---

<h3 align="center">Your voice, in your terminal.</h3>

<p align="center">
  Dictate straight into the OpenCode message box and hear replies spoken back in
  a local voice.<br>
  No cloud. No account. No upload. Just you and your machine.
</p>

| 🎙 **Dictate** | 🔊 **Listen** |
| :--- | :--- |
| Talk and watch the words appear in the draft **while you speak** — not after you stop. Fix a phrase, then send when you're ready. | Completed replies are read aloud in a natural local voice. Pause it with one key and it picks up where it left off. |

| ⚡ **Live, not laggy** | 🔒 **Private by design** |
| :--- | :--- |
| Speech streams to a single local recognizer, so the newest phrase simply refines itself. No waiting for a recording to finish. | Everything runs on your machine: local Whisper for hearing, local Kokoro for speaking. Nothing is sent anywhere. |

```text
You speak.          ──▶   words appear in the draft, live
The reply ends.     ──▶   a local voice reads it back
You decide.         ──▶   edit, send, or just listen
```

## Built for real sessions

- **Hands off the keyboard.** Start dictation, keep talking through pauses, and
  stop with the same key.
- **Never auto-sends.** The transcript is a draft you can edit; your prompt leaves
  only when you send it.
- **Works during a reply.** Keep talking while the assistant is thinking — the
  transcript is held and placed when the message box returns.
- **One tiny panel.** Mic and speaker live together in the footer, out of the way
  until you need them.
- **Yours, in every project.** Install once for your whole machine, or just for
  one project.

## Private by default

Your microphone audio is processed in memory and never uploaded. The transcript
goes only into your local OpenCode draft. Read-aloud is synthesized by a local
Kokoro model and played through your own speakers.

**No cloud speech services. No accounts. No telemetry.**

## Ready to talk?

<h3 align="center"><a href="INSTALL.md">Install tui-voice →</a></h3>

<p align="center">
  About a minute, one restart, and a choice: <strong>global</strong> for every
  project or <strong>local</strong> for just one.<br>
  Requires WSL2, the OpenCode TUI, Windows ffmpeg, and a local Whisper/Kokoro stack.
</p>

---

<p align="center">
  <sub>Built for the OpenCode TUI · Speech stays on your machine ·
  <a href="docs/README.md">Documentation</a> · <a href="HANDOFF.md">Project status</a></sub>
</p>
