# Settings

Open with `Ctrl+Alt+S`, or **Ctrl+P → Voice: Settings**. The dialog is centered,
native-sized (`large`), themed by the active OpenCode theme, and closes with
`Esc`.

## Rows

| Row | Default | Choices | Effect |
| --- | --- | --- | --- |
| Input | Automatic | Local capture inputs | Explicit microphone |
| Speech threshold | −35 dB | −25, −30, −35, −40, −45 | Recognition noise gate; never stops recording |
| Input gain | Off | 0, 3, 6, 9, 12, 15, 18 dB | Boost a quiet input |
| Test microphone | — | action | Records 2 s and shows RMS/peak |
| Read replies aloud | On | On/Off | Local Kokoro read-back; footer speaker Replies control toggles this too |
| Read-aloud voice | Heart | Heart, Velvet, English Rose, Dora | Kokoro preset for read-back |
| Test read-aloud | — | action | Plays a sample; mute/unmute resumes it |
| Reset to defaults | — | action | Restores the defaults below |

Defaults: manual on/off recording, automatic mic, **−35 dB**, **0 dB**, read-aloud
**On**, **Heart**.

## Tuning missing words

Dictation stays on until the microphone/F9 is pressed again. Pauses and long
turns never auto-stop it. Legacy saved `seconds`/`silence` values are ignored;
their controls have been removed.

1. Run **Test microphone**. `silent`/`quiet` means gain or device selection is the
   problem, not transcription.
2. Raise **Input gain** to 6–12 dB for a quiet Focusrite input. Avoid clipping on
   loud speech.
3. Raise **Speech threshold** toward −25 dB to ignore noise, or lower it toward
   −40/−45 dB for quiet speech. This only affects recognition, not recording state.

The virtual Steam Streaming Microphone is silent on this WSL2 machine. Automatic
selection prefers a real hardware microphone (the Focusrite Analogue input there,
a hardware source on native Linux); use **Input** if that is not the microphone you
speak into.

## Keyboard

| Key | Action |
| --- | --- |
| `↑` / `↓` | Move between settings |
| `←` / `→` | Change the selected value |
| `Enter` | Choose / cycle / run the selected action |
| `Esc` | Close the dialog |

Changes apply to the next dictation turn. There is no separate Save button. The
footer `🔊` / `🔇` button toggles read-aloud and stops current playback
without opening settings.
