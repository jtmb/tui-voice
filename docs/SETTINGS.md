# Settings

Open with `Ctrl+Alt+S`, or **Ctrl+P → Voice: Settings**. The dialog is centered,
native-sized (`large`), themed by the active OpenCode theme, and closes with
`Esc`.

## Rows

| Row | Default | Choices | Effect |
| --- | --- | --- | --- |
| Input | Automatic | Local capture inputs | Explicit microphone |
| Speech threshold | −35 dB | −25, −30, −35, −40, −45 | Recognition noise gate; never stops recording |
| Input gain | +6 dB | 0, 3, 6, 9, 12, 15, 18 dB | Boost a quiet input |
| Test microphone | — | action | Records 2 s and shows RMS/peak |
| Read replies aloud | On | On/Off | Local Kokoro read-back; footer speaker Replies control toggles this too |
| Read-aloud voice | Heart | Heart, Velvet, English Rose, Dora | Kokoro preset for read-back |
| Test read-aloud | — | action | Plays a sample; mute/unmute resumes it |
| Live conversation | Off | On/Off | Sends spoken turns to the selected local model, streams speech and supports interruption; not persisted across restarts |
| Pause before replying | 0.8 s | 0.5, 0.8, 1.2, 1.8, 2.5 s | Live-only end-of-turn silence, applied on the next Live start |
| Unload voice models when idle | 30 min | Never, 1, 5, 10, 30 min, 1 hour | Frees the loaded Whisper/Kokoro models after this long without dictation or read-aloud; they reload on the next use |
| Reset to defaults | — | action | Restores the defaults below |

Defaults: manual on/off recording, automatic mic, **−35 dB**, **+6 dB**, read-aloud
**On**, **Heart**, models unload after **30 min** idle.

`Ctrl+Alt+L` or `/voice-live` toggles Live conversation independently of ordinary
F9 dictation. Use a configured local OpenCode model and headphones. Turning Live
off preserves unsent recognized words in the original draft. Read-aloud mute
also mutes Live; unmuting Live begins with a fresh phrase and does not replay old
speech. The microphone/read-aloud test actions are unavailable during Live so
they cannot compete with its devices. See [LIVE_CONVERSATION.md](LIVE_CONVERSATION.md).

## Voice model loading and unloading

The recognizer and the read-aloud voice stay in memory between uses so that the
next turn starts at once. After the idle time you choose they unload (about
200 MB for recognition and 470 MB for read-aloud).

When you press the microphone and the recognizer is not loaded, the plugin says
so: *"Microphone on. Loading the voice model: keep talking, your words will
appear when it is ready."* followed by *"Voice model ready."* (about 2 s here).
Recording starts immediately and nothing you say is lost; the words appear as
soon as the model is in memory. If the fast worker cannot start, you get a
warning and dictation continues on the slower fallback.

Changing the setting applies at once to models that are already loaded. A
recognizer from an older stack that cannot change its limit in place is stopped
and comes back with the new limit the next time you dictate. Read-aloud reloads
its voice the same way, without a message, so the first reply after an unload
starts a moment late.

`TUI_VOICE_WARM_IDLE` (seconds) still sets the limit when you have never chosen
one in the dialog; a choice made in the dialog wins.

## Tuning missing words

Dictation stays on until the microphone/F9 is pressed again. Pauses and long
turns never auto-stop it. Legacy saved `seconds`/`silence` values are ignored;
their controls have been removed.

1. Run **Test microphone**. `silent`/`quiet` means gain or device selection is the
   problem, not transcription.
2. The default is now **+6 dB**. Raise **Input gain** to 9–12 dB for a very
   quiet input. Avoid clipping on loud speech.
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
