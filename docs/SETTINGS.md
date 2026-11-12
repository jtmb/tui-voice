# Settings

Open with `Ctrl+Alt+S`, or **Ctrl+P → Voice: Settings**. The dialog is centered,
native-sized (`large`), themed by the active OpenCode theme, and closes with
`Esc`.

## Rows

| Row | Default | Choices | Effect |
| --- | --- | --- | --- |
| Finish after a pause | 1.5 s | 0.8, 1.0, 1.2, 1.5, 2.0, 2.5 | Silence duration before the recorder stops |
| Maximum turn | 60 s | 15, 20, 30, 45, 60, 90 | Hard stop only when silence is never detected |
| Input | Automatic | Windows dshow inputs | Explicit microphone |
| Silence threshold | −35 dB | −25, −30, −35, −40, −45 | Noise floor for silence detection |
| Input gain | Off | 0, 3, 6, 9, 12, 15, 18 dB | Boost a quiet input |
| Test microphone | — | action | Records 2 s and shows RMS/peak |
| Read replies aloud | On | On/Off | Local Kokoro read-back; footer speaker Replies control toggles this too |
| Read-aloud voice | Heart | Heart, Velvet, English Rose, Dora | Kokoro preset for read-back |
| Test read-aloud | — | action | Plays a sample; mute/unmute resumes it |
| Reset to defaults | — | action | Restores the defaults below |

Defaults: **1.5 s**, **60 s**, automatic mic, **−35 dB**, **0 dB**, read-aloud
**On**, **Heart**.

## Tuning missing words

Missing words usually mean the turn ended during a pause or the input is quiet.

1. Increase **Finish after a pause** to 0.6–0.8 s. This is the first fix for
   clipped sentence endings.
2. Run **Test microphone**. `silent`/`quiet` means gain or device selection is the
   problem, not transcription.
3. Raise **Input gain** to 6–12 dB for a quiet Focusrite input. Avoid clipping on
   loud speech.
4. If room noise prevents silence detection, raise the threshold toward −25 dB;
   if the recorder stops during quiet speech, lower it toward −40/−45 dB.
5. Keep **Maximum turn** long. Normal turns end on a pause; the maximum is only a
   safety stop and must not cut off a long sentence.

The virtual Steam Streaming Microphone is silent on this machine. Automatic
selection prefers the Focusrite Analogue input; use **Input** if that is not the
microphone you speak into.

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
