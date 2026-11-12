# Testing

There is no automated test framework yet. Verification is a small helper check
plus a fresh-TUI gate. Reuse these checks; do not build recursive test
infrastructure for this plugin.

## Helper checks

```bash
cd ~/repos/tui-voice

python3 -m py_compile voice_helper.py tts_worker.py

SERVICE=~/repos/fire-tv-next/lulu-launcher/service
$SERVICE/.venv/bin/python voice_helper.py devices

$SERVICE/.venv/bin/python voice_helper.py warm --start
$SERVICE/.venv/bin/python voice_helper.py warm --status

$SERVICE/.venv/bin/python voice_helper.py transcribe \
  --wav ~/repos/fire-tv-next/lulu-launcher/voice/fixtures/short-2s.wav
```

Expected:

- `devices` lists the real Windows input first (Focusrite Analogue 1 + 2) and the
  virtual Steam microphone second.
- `warm --status` reports an `asr` worker on CPU and a `tts` worker with the four
  local voices.
- `transcribe` returns “Turn on the living room lights.” with confidence around
  0.93. With the warm worker it completes in about half a second.

## Warm-worker checks

```bash
cd ~/repos/tui-voice
SERVICE=~/repos/fire-tv-next/lulu-launcher/service

# Status must answer even while speech is synthesizing.
$SERVICE/.venv/bin/python voice_helper.py say \
  --text "This is a warm local speech test." --no-play
$SERVICE/.venv/bin/python voice_helper.py warm --status

# Stop and confirm the cold fallback still works, then start again.
$SERVICE/.venv/bin/python voice_helper.py warm --stop
$SERVICE/.venv/bin/python voice_helper.py say --text "Cold fallback." --no-play
$SERVICE/.venv/bin/python voice_helper.py warm --start
```

Expected: `say` reports `"warm_chunks": 1` with a worker running and `0` after
`warm --stop`; both paths return usable audio. The worker sockets are `0600` under
`$XDG_RUNTIME_DIR`.

## Fresh-TUI gate

Close the current TUI completely and start a new one from outside the repository
(`cd ~`). `/reload` is not enough after a plugin path change.

1. `opencode plugin list` includes `/home/brajam/repos/tui-voice`.
2. Open a chat session. The bottom footer shows `🎙` microphone icon.
3. Press `Ctrl+Alt+V`. It changes to `Listening`; a helper `record` process starts.
4. Wait for silence / the maximum. It changes to `Transcribing`, then a transcript
   appears in the message box. Nothing is sent.
5. Press `Ctrl+Alt+S`. A centered native-sized settings dialog opens; arrows change
   values; `Esc` closes.
6. With read-aloud On, send a short prompt in a disposable session and confirm a
   `voice_helper.py say` process starts with the completed assistant text.
7. Open settings, run **Test read-aloud**, then toggle footer `🔊` → `🔇`; the
   current `say` process stops. Toggle back to `🔊`; a new `say` process starts
   with `--start-chunk` and the interrupted sample continues.
8. Set read-aloud Off in settings, send another prompt, and confirm no `say`
   process starts.
9. Stop dictation and confirm no `voice_helper.py` process remains.

## Verified evidence

- Fixture transcript inserted into the message box without sending.
- Warm ASR returned the same fixture transcript in ~0.6 s (cold ~1.5 s); the TTS
  worker kept one ONNX session and served repeated chunks (warm ~1.1 s vs cold
  ~3.2 s for the same short sentence, 8 threads).
- Stopping a worker waits for the socket to disappear; two concurrent starts
  produced one worker.
- Settings dialog opened, values changed, read-aloud toggled, `Test microphone`
  returned `Input level 87 · quiet · peak 1724`.
- Read-aloud spawned:
  `voice_helper.py say --text "read aloud final check." --voice heart`.
- Read-aloud mute stopped the helper; unmute started a new helper with
  `--start-chunk` and incremented the warm worker request count.
- Silence auto-stop stopped a synthetic recording after the speech segment rather
  than using the full maximum.
- Global load verified from `/home/brajam`, not from this repository.

## Not verified

- Mouse-click activation of the footer button (keyboard paths are verified).
- Physical speech accuracy, latency, echo, and the practical gain/threshold
  settings for the owner’s room.
- Dictation queued behind a real model turn was not physically repeated.
