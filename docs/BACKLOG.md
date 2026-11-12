# Backlog

## Known limits

- Footer button mouse activation is not automatically verified. The keyboard
  shortcuts (`Ctrl+Alt+V`, `F9`, `/dictate`) are the reliable path.
- Physical speech accuracy, latency, echo, and the best gain/threshold values for
  the owner’s room are not measured.
- Dictation queued behind a running model reply is implemented but not physically
  repeated end to end.
- Read-aloud and dictation can overlap if the user speaks during playback.
- The settings dialog is custom JSX and depends on host dialog sizing/theming.

## Possible follow-up

- Add a small automated harness for `voice_helper.py` VAD/args and settings
  persistence.
- Add a one-key “stop read-aloud” action.
- Add a preview button for the selected read-aloud voice.
- Add a compact level meter while listening using the helper’s RMS data.
- Document and test non-Focusrite microphones.
