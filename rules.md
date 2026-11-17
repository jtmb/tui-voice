# Owner rules — tui-voice

These are the current product requirements for the `tui-voice` OpenCode TUI plugin.
Latest explicit owner instructions govern over older notes; system/developer safety
requirements remain.

## Placement

- The `🎙` microphone icon button lives in the **bottom footer** of a chat session.
- Do not move it to the composer, a floating overlay, a full-screen route, or a
  different corner.
- It must visibly change state while listening/transcribing: `○` idle, amber `●`
  listening, cyan `◐` transcribing, `✕` error. Do not leave it visually idle.
- The `🔊` / `🔇` button sits next to Voice in the same bottom footer.
  It toggles read-aloud and immediately stops current playback. Do not move it.
- Microphone and speaker share **one compact rounded panel**: status circle +
  microphone, a subtle divider, then speaker.
- **The footer bar must stay one row tall.** Do not add a border, vertical padding,
  or anything else that makes the bottom bar taller. Rounded ends use half-cell
  caps inside the existing row. Keep fixed cell widths so the prompt does not shift
  between states.
- Keep the status circle directly against the microphone. Reserve exactly one cell
  after the microphone before the divider so the glyph cannot be clipped.
- The settings screen is a **native-sized OpenCode dialog** (`large`, centered),
  not a full-screen page, and uses the active theme's dialog surface and native
  selected-row colors.
- Load the plugin globally from `~/.config/opencode/cli.json`; it must remain active
  across TUI restarts and outside this repository.

## Dictation

- A turn records the local microphone, transcribes locally, and **types the
  transcript into the OpenCode message box**. It must not cut off while the user is
  still speaking. Dictation is a manual on/off toggle: stay on through pauses
  and long turns until the user presses the microphone/shortcut again.
- Dictation must start from the home screen and after `/new` without a manually
  opened session. The first press creates and opens a session when needed.
- A second press while listening must gracefully stop the recording at that point
  and transcribe everything recorded so far. It must not drop the audio or force
  the user to wait for another silence.
- Never send the prompt automatically. The user reviews and sends it.
- Show local recognition in the message box while the user speaks, not only after
  recording stops. Revise the live phrase without duplicating it or overwriting
  manual draft edits. Keep capture independent of ASR latency and requests bounded.
- If the session is replying and the message box is hidden, transcription still
  happens immediately; hold the draft and insert it when the message box returns.
- Do not auto-finish on silence, a maximum turn duration, or a helper lifetime
  timer. Keep local ASR requests bounded independently of recording duration;
  genuine device/inference failures must be reported rather than hidden.
- A turn longer than the ASR 32 s budget must be split into safe chunks and the
  text joined; it must never fail with "Audio duration outside finite budget".
- Failures must append to the local log at `~/.local/state/tui-voice/voice.log`
  (override `TUI_VOICE_LOG_DIR`) with the helper error and traceback.

## Read-aloud

- Read-aloud is a visible setting with an explicit On/Off switch.
- It speaks completed assistant replies through the local Kokoro model and the
  local speakers. No cloud speech.
- The local voice is selectable: Heart, Velvet, English Rose, Dora.
- The footer speaker icon is the quick control; it must stop the active
  `say` process, not just hide the label.
- It must remain functional; do not silently remove it or move it to another
  surface.

## Settings

- Settings are global and persisted under `tui-voice`.
- The screen tunes: input device, speech
  threshold, input gain, microphone test, read-aloud, read-aloud voice, reset.
- Defaults: manual on/off recording, automatic microphone, −35 dB, 0 dB gain,
  read-aloud On, Heart.
- Keyboard: `↑↓` select, `←→` change, `Enter` choose, `Esc` close.
- Settings must affect the actual recorder, not just the UI.

## Privacy and scope

- Audio is local; temporary capture files are deleted after transcription.
- Warm ASR/TTS workers are local AF_UNIX helpers; keep them local and optional.
- No cloud service, TV, ADB, Control Center login, upload, or automatic prompt send.
- Use a local Whisper/Kokoro stack; the plugin provisions its own by default
  (`setup_stack.py`). Do not add a remote provider.

## Portability, dependencies and testing

- **Every change is tested for multi-OS compatibility.** The supported matrix is
  native Linux on glibc (Debian/Ubuntu first, plus Fedora/RHEL, Arch, openSUSE)
  and WSL2. Alpine is supported for the audio packages only; the speech stack
  cannot install on musl (no `onnxruntime`/`ctranslate2` wheels) and must refuse
  with an explanation. A feature is not done because it works on the development host alone;
  record which OS families were exercised and name any that were not.
- **Every required dependency is verified, not assumed.** From a clean state,
  prove that the audio packages (`ffmpeg`, PulseAudio/PipeWire or ALSA tools), a
  supported Python, and the Whisper/Kokoro models and virtualenvs are present or
  installed. Nothing may rely on one developer's home directory, checkout, shell
  profile, or preinstalled tools.
- **Dependency provisioning ships as a deterministic script in this repository.**
  `platform_deps.py` (OS packages) and `setup_stack.py` (models and virtualenvs)
  are the only supported paths: fixed package allow-list, pinned versions and
  SHA-256 for every download, argv-only commands, idempotent reruns that change
  nothing when the machine is already correct, and an actionable error naming the
  exact command when privileges or network are unavailable. Do not replace them
  with prose setup steps or an interactive installer.
- **No manual procurement step is acceptable.** A user on a supported OS with a
  network connection must reach working dictation and read-aloud without
  downloading a model by hand or building a virtualenv. Offline installs may copy
  an existing model set (`--models-from`) but stay hash-verified.
- Never fall back to a cloud provider when a local dependency is missing; fail
  with the deterministic command that fixes it.

## Verification gates

- Fresh TUI from outside the repository: `🎙` microphone icon is in the bottom footer.
- `Ctrl+Alt+V`/`F9`/`/dictate` changes it to `Listening`.
- A test turn inserts the exact transcript and does not send.
- A turn during a running reply queues and later inserts.
- Settings dialog opens with `Ctrl+Alt+S`, uses the active theme, and values stick.
- Read-aloud On produces local speech; Off produces none. Muting during speech
  and unmuting resumes the interrupted reply.
- Plugin survives a full TUI restart.
- `voice_helper.py doctor` reports audio and speech ready on a clean supported
  OS, and names the exact command when something is missing.
- `setup_stack.py --install` is idempotent: a second run keeps the existing
  files and virtualenvs and changes nothing.
- Dependency installation was exercised on every OS family claimed as supported;
  untested families are named explicitly in the handoff.
