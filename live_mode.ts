/** Owns one microphone/session lifecycle; normal dictation never enters this path. */
import { LiveConversationController } from "./live_controller.ts"
import { createOpenCodeLiveAdapter } from "./opencode_live.ts"

export type LiveCapture = {
  done: Promise<unknown>
  stop(): void
  cancel(): void
}

export type LiveAudio = {
  listen(emit: (event: any) => void): LiveCapture
  speak(text: string): Promise<void>
  stopSpeech(): void
  warm(): void
}

export type LiveModeHooks = {
  active(value: boolean): void
  state(value: "idle" | "listening" | "transcribing" | "thinking" | "speaking" | "error"): void
  error(error: unknown): void
  restoreDraft(sessionID: string, text: string): void
  captureEvent?(event: any): void
}

type Adapter = ReturnType<typeof createOpenCodeLiveAdapter>
type Run = {
  sessionID: string
  abort: AbortController
  controller?: LiveConversationController
  capture?: LiveCapture
  unsubscribe?: () => void
  stopping: boolean
  failed: boolean
  hardCancelled?: boolean
  stopTask?: Promise<void>
}

export class LiveConversationMode {
  private run: Run | null = null
  private muted = false
  private ownedMessages = new Set<string>()
  private adapter: Adapter
  private audio: LiveAudio
  private hooks: LiveModeHooks

  constructor(adapter: Adapter, audio: LiveAudio, hooks: LiveModeHooks) {
    this.adapter = adapter
    this.audio = audio
    this.hooks = hooks
  }

  get active() { return Boolean(this.run && !this.run.stopping) }
  get busy() { return this.run !== null }
  get sessionID() { return this.run?.sessionID ?? null }
  ownsMessage(messageID: string | undefined) { return Boolean(messageID && this.ownedMessages.has(messageID)) }

  async start(sessionID: string): Promise<void> {
    if (this.run) throw new Error("The previous voice conversation is still finishing.")
    const run: Run = {sessionID, abort: new AbortController(), stopping: false, failed: false}
    this.run = run
    this.hooks.active(true)
    this.hooks.state("thinking")
    try {
      const snapshot = await this.adapter.snapshot(sessionID, {signal: run.abort.signal})
      if (this.run !== run || run.stopping) return
      const controller = new LiveConversationController({
        prompt: text => this.adapter.prompt(snapshot, text, {signal: run.abort.signal}),
        interrupt: () => this.adapter.interrupt(sessionID),
        speak: text => this.audio.speak(text),
        stopSpeech: () => this.audio.stopSpeech(),
        onState: state => {
          if (this.run === run && !run.stopping && state !== "stopped") this.hooks.state(state)
        },
        onError: error => this.fail(run, error),
        onUnsent: text => this.hooks.restoreDraft(sessionID, text),
      })
      run.controller = controller
      controller.setMuted(this.muted)
      run.unsubscribe = this.adapter.subscribe(sessionID, {
        onStepStarted: event => {
          if (controller.stepStarted(event)) {
            this.ownedMessages.add(event.assistantMessageID)
            if (this.ownedMessages.size > 256) this.ownedMessages.delete(this.ownedMessages.values().next().value!)
          }
        },
        onTextDelta: event => controller.textDelta(event),
        onTextEnded: event => controller.textEnded(event),
        onExecutionStarted: event => controller.executionStarted(event),
        onExecutionEnded: event => controller.executionEnded(event),
        onError: event => this.fail(run, new Error(event.message)),
      })
      this.audio.warm()
      run.capture = this.audio.listen(event => {
        // A graceful OFF can still deliver the last recognized words. The
        // stopped controller restores them to the draft and cannot submit them.
        if (this.run !== run) return
        controller.capture(event)
        if (!run.stopping) {
          if (event.event === "listening") this.hooks.state("listening")
          this.hooks.captureEvent?.(event)
        }
      })
      void run.capture.done.then(() => {
        if (!run.stopping) this.fail(run, new Error("The live microphone stopped. Restart Live conversation to reconnect."))
      }, error => {
        if (!run.stopping) this.fail(run, error)
      })
    } catch (error) {
      if (this.run === run && !run.stopping) {
        this.fail(run, error)
        await run.stopTask
      }
    }
  }

  private fail(run: Run, error: unknown) {
    if (this.run !== run || run.failed) return
    run.failed = true
    this.hooks.error(error)
    if (!run.stopping) void this.stop()
  }

  setMuted(muted: boolean) {
    this.muted = muted
    this.run?.controller?.setMuted(muted)
  }

  checkRoute() {
    if (this.run && this.adapter.currentSession() !== this.run.sessionID) void this.stop()
  }

  stop(cancelCapture = false): Promise<void> {
    const run = this.run
    if (!run) return Promise.resolve()
    if (run.stopTask) {
      if (cancelCapture) { run.hardCancelled = true; run.capture?.cancel() }
      return run.stopTask
    }
    run.stopping = true
    run.hardCancelled = cancelCapture
    run.abort.abort()
    this.hooks.active(false)
    run.unsubscribe?.()
    run.unsubscribe = undefined
    // Silence is immediate; network acknowledgement and final ASR drain are
    // asynchronous, and none of them may restart speech or submit another turn.
    this.audio.stopSpeech()
    const stopped = run.controller?.stop() ?? Promise.resolve()
    if (run.capture) {
      this.hooks.state(run.failed ? "error" : "transcribing")
      if (cancelCapture) run.capture.cancel()
      else run.capture.stop()
    }
    run.stopTask = Promise.allSettled([stopped, run.capture?.done ?? Promise.resolve()]).then(results => {
      const interruption = results[0]
      if (interruption.status === "rejected" && !run.failed) {
        run.failed = true
        this.hooks.error(interruption.reason)
      }
      const capture = results[1]
      if (capture.status === "rejected" && !run.hardCancelled && !run.failed) {
        run.failed = true
        this.hooks.error(capture.reason)
      }
      if (this.run === run) {
        this.run = null
        this.hooks.state(run.failed ? "error" : "idle")
      }
    })
    return run.stopTask
  }
}
