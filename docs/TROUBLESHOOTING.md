# Troubleshooting

## Plugin is missing or looks stale

- Confirm `~/.config/opencode/cli.json` contains
  `/home/brajam/repos/tui-voice`.
- Run `opencode plugin list`.
- A TUI-only plugin added or moved on disk is not picked up by `/reload` alone.
  **Close the TUI completely and reopen it.**
- If a TUI was already running when the path changed, it may still hold the old
  generation. Reopening is the reliable fix.

## Button is in the wrong place

The owner requirement is fixed: `🎙` microphone icon must stay in the **bottom prompt footer**.
Do not move it to the composer, a dialog, or an overlay.

## Button click does nothing

- Use `Ctrl+Alt+V`, `F9`, or `/dictate`; these are the verified paths.
- The footer button now handles mouse-down and mouse-up with a debounce and is
  focusable, but mouse delivery depends on the terminal/tmux mouse mode.
- If the footer does not change to `Listening` on the keyboard shortcut, the plugin
  has not loaded in that TUI; reopen it.

## No transcript

1. Check the footer changes to `Listening` and then `Transcribing`.
2. Run **Voice: Settings → Test microphone**. `silent` means the wrong device or no
   input; `quiet` means raise **Input gain**.
3. `voice_helper.py devices` lists the inputs. The Steam Streaming Microphone is
   silent here; use the Focusrite input.
4. Increase **Finish after a pause** if sentence endings are being cut off.
5. Confirm the local service still has the Whisper model and Python environment:
   `~/repos/fire-tv-next/lulu-launcher/service`.

## It waits too long

- Keep **Maximum turn** long. Normal turns end on a pause; the maximum is a safety stop.
- Lower **Finish after a pause** only if you want faster turns. The default is
  1.5 s so a brief pause or breath does not cut you off.
- If it always runs to the maximum, room noise is above the silence threshold.
  Raise the threshold toward −25 dB, or test the mic level first.

## Read-aloud does not work

- Enable **Read replies aloud** in settings and select a voice.
- It speaks only completed assistant replies in the currently open session.
- Verify the helper directly:
  `voice_helper.py say --text "test" --voice heart`.
- Check the warm workers:
  `voice_helper.py warm --status`; use `warm --start` if either is absent or
  `warm --restart` after changing models.
- Read-aloud uses Windows speakers; check the default output device.
- If dictation is active, speech waits for the turn to finish.

## Settings theme looks wrong

- The dialog uses `theme.surface("dialog")` and native selected-row tokens.
- Reopen the dialog after changing the TUI theme.
- If it still looks like a full-screen custom page, the TUI is running an older
  plugin generation; reopen it.

## Audio or privacy

- Temporary WAVs are in the Windows temp directory and are deleted after
  transcription. If a run was cancelled, a temp file may remain; remove stale
  `tui-voice-*.wav` files.
- No cloud request is made. Keep it that way.
