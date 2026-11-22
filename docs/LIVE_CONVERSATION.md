# Local live conversation

Live conversation adds automatic spoken turns and interruptible, streamed
responses to the existing OpenCode chat. Dictation remains the default and
still writes only into an editable draft. This feature implements the owner's
2026-11-22 request for working code and a PR, superseding the earlier
planning-only request for this feature.

## Start and stop

1. Install the normal speech stack using [INSTALL.md](../INSTALL.md). Live reuses
   its pinned Whisper and Kokoro models and warm workers; it adds no model or
   Python package download.
2. Select the local language model you want to use in OpenCode. Its effective
   provider/model/variant `baseURL` must explicitly point to loopback, such as
   `http://127.0.0.1:8080/v1` or `http://localhost:1234/v1`. Configure the actual
   provider in OpenCode using that server's supported model name. The plugin
   does not start, download, or silently replace the language model.
3. Use headphones, or an input device that already provides effective echo
   cancellation. The microphone stays active during playback.
4. In an idle chat, press **Ctrl+Alt+L**, run **/voice-live**, or switch **Voice
   settings → Live conversation** On. The same command switches it Off. If
   dictation is running, it finishes into the draft before Live opens the mic.
5. Speak, then pause. The default **0.8-second** silence ends the spoken turn.
   Local recognition may need additional time. Responses begin playing as
   phrases arrive, before the whole assistant reply finishes.
6. Speak again to interrupt. The player stops, queued audio is discarded, and
   the current OpenCode execution is interrupted before the next prompt is sent.

The mic/F9 command always starts ordinary Dictation. If Live is active, it
first stops Live and drains its unsent words into the draft. Turning Live Off
does **not** automatically start dictation or submit those remaining words.
Switching chats also ends Live; unfinished text stays with its original chat.

Live always starts **Off** after a TUI restart. Its listening/speaking glyphs are
`◎` and `◉` within the existing footer space. The footer remains one row tall.
The speaker control mutes Live while it continues listening and responding in
text. Unmute resumes with a fresh phrase, without replaying the muted backlog.
Ordinary read-aloud retains its existing mute/resume behavior.

## Local model contract

The adapter uses the selected **OpenCode v2** model and the existing session,
so normal conversation history, agent behavior, and tool permission flows stay
with OpenCode. It never creates a separate hidden chat or accepts tool approvals
on the user's behalf.

It reads the effective endpoint after provider, model, and variant settings are
merged. A remote or missing endpoint fails before opening the mic. A model,
endpoint, location, or chat change during preflight prevents that transcript
from being submitted; recognized unsent text returns to the draft. Missing v2
APIs produce an actionable error rather than guessed v1 calls.

For an explicitly configured model on a trusted private LAN, set
`TUI_VOICE_LIVE_ALLOW_PRIVATE=1` before starting the TUI. This additionally allows
literal RFC1918 IPv4 and IPv6 ULA addresses; public endpoints and arbitrary
hostnames are still rejected. This opt-in does not change the speech workers.

This validates the **configured inference endpoint**, not the behavior inside a
local server. A local proxy or OpenCode request hook can forward elsewhere.
Use a server that actually runs your local model. OpenCode tools and other
plugins retain their own configured network behavior.

API contracts were checked against the current official v2 sources:

- [TUI context](https://github.com/anomalyco/opencode/blob/dev/packages/plugin/src/tui/context.ts)
- [Generated client types](https://github.com/anomalyco/opencode/blob/dev/packages/client/src/promise/generated/types.ts)
- [Session events](https://github.com/anomalyco/opencode/blob/dev/packages/schema/src/session-event.ts)
- [Native prompt submission](https://github.com/anomalyco/opencode/blob/dev/packages/tui/src/component/prompt/index.tsx)
- [Model settings resolution](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/model-resolver.ts)

`session.prompt` receives text and uses native steering/admission. Selection is
committed through `session.switchModel`; cancellation is `session.interrupt`.
Only assistant text events enter speech; reasoning and tool output do not.

## Implementation

| File | Responsibility |
| --- | --- |
| `live_conversation.py` | Immediate speech activity events, silence endpoints, ordered local ASR results |
| `voice_helper.py listen` | Existing platform capture and warm recognizer transport, manual lifetime |
| `live_controller.ts` | Turn scheduling, response ownership, phrase buffering, cancellation and mute |
| `opencode_live.ts` | Current v2 APIs, selected model/local endpoint verification, typed event mapping |
| `live_mode.ts` | Start/stop/session lifecycle, microphone ownership, recovery of unsent words |
| `tui.tsx` | Existing footer, new command/settings toggle, player and helper wiring |

The listener checks 20 ms PCM blocks and emits `speech_start` after 100 ms of
sustained amplitude above the configured threshold. This does not wait for
Whisper. Final `turn` events can arrive later than a newer `speech_start`; the
controller preserves their order and waits while the user is still speaking.

ASR uses one request in flight, at most eight seconds of audio per request, a
short overlap at hard boundaries, and a 32-second total outstanding PCM budget.
Capture remains independent of inference. Queues fail clearly when a worker
cannot keep up. Audio is not retained as recording files.

The output path buffers short phrases, strips code fences/URL targets, and
flushes final words even without ending punctuation. Barge-in cancels current
playback and pending phrases immediately, then awaits scoped model interruption.
Late text from old assistant IDs cannot restart speech. OFF during prompt
admission waits for its acknowledgement before interrupting; an uncertain
admission is reported and cleaned up rather than retried automatically.

Normal OFF gracefully closes capture and transcribes the final buffered audio
into the draft. Teardown may cancel capture immediately. ASR drain failures are
reported; the UI does not claim to have recovered words it could not recognize.

## Timing and acoustic limits

This first implementation uses an **amplitude gate plus configurable silence**.
It does not infer whether an unfinished thought is complete, distinguish every
backchannel from an interruption, or deliberately interject midway through the
user's utterance. Increase **Pause before replying** if ordinary thinking pauses
cause early replies. Increase the speech threshold if noise causes interruptions.

No acoustic echo canceller is added. Speakers feeding into the mic can create
self-interruptions; headphones are the baseline. No RX 7900 XT timing is claimed.
Existing CPU speech is supported; optional MIGraphX TTS keeps its existing
explicit setup and validation in [AMD_GPU.md](AMD_GPU.md).

Suggested acceptance targets on the owner's system are first audible response
around **1–2 seconds after the user stops**, and playback stopping within
**250 ms of real speech onset**. These are targets, not benchmark results. The
100 ms speech detector budget is only one part of the interruption path.

## Verification and rollout

With Node 24, run `npm run test:ts`. The TypeScript tests use the built-in runner
and need no downloaded test framework. Run the existing Python suite with
`python3 -B -m unittest discover -s tests -v`, following the local development
output rules in [STORAGE.md](STORAGE.md). No test suite result substitutes for
physical audio or GPU measurements.

Before calling this hardware-qualified, exercise this sequence on WSL2 and a
native Linux audio host:

1. Fresh TUI: ordinary F9 dictation, editable draft, manual Send, read-aloud and
   mute/resume still work. Live starts Off and the footer height is unchanged.
2. Select a real local model; start Live, speak two turns, and confirm both appear
   once in the existing chat. Speech starts before the assistant text completes.
3. Interrupt during playback. Old audio stops, stale text never resumes, and the
   next request contains the interruption exactly once.
4. Toggle Off while speaking and while a model request is being admitted. No new
   automatic prompt is sent; remaining recognized speech appears in the draft.
5. Switch chats, toggle mute/unmute, and switch from Live to F9. No response or
   draft crosses sessions, and only one microphone capture runs.
6. Select a cloud model. Live refuses before capture/submission, with no fallback.
7. Record first-audio and interruption times on the actual local model/GPU; check
   that speech can keep up across a sustained conversation.

Turning Live Off is the feature rollback; normal Dictation remains available.
The PR does not install a language model, change AMD drivers, migrate the speech
stack, alter OpenCode's tool approvals, or merge itself into the default branch.
Current verification evidence and any blocked gates are in [HANDOFF.md](../HANDOFF.md).
