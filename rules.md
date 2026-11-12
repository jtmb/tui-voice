# Owner rules — tui-voice

These are the current product requirements for the `tui-voice` OpenCode TUI plugin.
Latest explicit owner instructions govern over older notes; system/developer safety
requirements remain.

## Placement

- The `🎙` microphone icon button lives in the **bottom footer** of a chat session.
- Do not move it to the composer, a floating overlay, a full-screen route, or a
  different corner.
- The `🔊` / `🔇` button sits next to Voice in the same bottom footer.
  It toggles read-aloud and immediately stops current playback. Do not move it.
- The settings screen is a **native-sized OpenCode dialog** (`large`, centered),
  not a full-screen page, and uses the active theme's dialog surface and native
  selected-row colors.
- Load the plugin globally from `~/.config/opencode/cli.json`; it must remain active
  across TUI restarts and outside this repository.

## Dictation

- A turn records the Windows microphone, transcribes locally, and **types the
  transcript into the OpenCode message box**.
- Never send the prompt automatically. The user reviews and sends it.
- If the session is replying and the message box is hidden, transcription still
  happens immediately; hold the draft and insert it when the message box returns.
- A second press while listening must not destroy the recording; the turn finishes
  on silence or the configured maximum.

## Read-aloud

- Read-aloud is a visible setting with an explicit On/Off switch.
- It speaks completed assistant replies through the local Kokoro model and Windows
  speakers. No cloud speech.
- The local voice is selectable: Heart, Velvet, English Rose, Dora.
- The footer speaker icon is the quick control; it must stop the active
  `say` process, not just hide the label.
- It must remain functional; do not silently remove it or move it to another
  surface.

## Settings

- Settings are global and persisted under `tui-voice`.
- The screen tunes: pause before finishing, maximum turn, input device, silence
  threshold, input gain, microphone test, read-aloud, read-aloud voice, reset.
- Defaults: 0.4 s pause, 4 s maximum, automatic microphone, −35 dB, 0 dB gain,
  read-aloud On, Heart.
- Keyboard: `↑↓` select, `←→` change, `Enter` choose, `Esc` close.
- Settings must affect the actual recorder, not just the UI.

## Privacy and scope

- Audio is local; temporary capture files are deleted after transcription.
- No cloud service, TV, ADB, Control Center login, upload, or automatic prompt send.
- Use the existing local Whisper/Kokoro stack; do not add a remote provider.

## Verification gates

- Fresh TUI from outside the repository: `🎙` microphone icon is in the bottom footer.
- `Ctrl+Alt+V`/`F9`/`/dictate` changes it to `Listening`.
- A test turn inserts the exact transcript and does not send.
- A turn during a running reply queues and later inserts.
- Settings dialog opens with `Ctrl+Alt+S`, uses the active theme, and values stick.
- Read-aloud On produces local speech; Off produces none.
- Plugin survives a full TUI restart.
