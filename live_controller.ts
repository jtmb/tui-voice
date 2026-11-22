/** Conversation scheduling; audio capture, model transport and playback stay outside. */
export type LivePhase = "listening" | "transcribing" | "thinking" | "speaking" | "error" | "stopped"

export type LiveIdentity = { eventID?: string; sequence?: number; created?: string | number }
export type LiveCapture = {
  event: "speech_start" | "speech_end" | "turn"
  turn_id: number
  text?: string
}
export type LiveStep = LiveIdentity & { assistantMessageID: string }
export type LiveText = LiveStep & { ordinal: number; delta?: string; text?: string }

export type LiveDependencies = {
  /** Resolves when the prompt is admitted, not when the answer finishes. */
  prompt(text: string): Promise<unknown>
  interrupt(): Promise<unknown>
  /** Resolves only after this phrase has actually finished playing. */
  speak(text: string): Promise<void>
  stopSpeech(): void
  onState?(state: LivePhase): void
  onError?(error: Error): void
  /** Recover words which were never admitted, including a late final after OFF. */
  onUnsent?(text: string): void
}

type CaptureTurn = { ended: boolean; text?: string }
type Segment = { raw: string; consumed: number; ended: boolean; skipBoundary: boolean; lastSequence: number }
type Response = {
  floor: number
  cancelled: boolean
  executing: boolean
  started: boolean
  finished: boolean
  chars: number
  lastSequence: number
  admitted: boolean
  admission: Promise<unknown>
  messages: Set<string>
  segments: Map<string, Segment>
  events: Set<string>
}

const MAX_PENDING_TURNS = 32
const MAX_RESPONSE_CHARS = 64000
const MAX_QUEUED_CHARS = 6000
const MAX_PHRASE_CHARS = 180

/** Hold incomplete Markdown constructs so later deltas cannot revise spoken text. */
function speechText(value: string, final: boolean) {
  let text = value.replace(/```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)/g, " ")
  if (!final) {
    // A closing link or image can arrive in a later token. Hold its label until
    // the whole construct is known, instead of reading out its target URL.
    const incompleteLink = /!?\[[^\]]*(?:\][ \t]*\([^)]*)?$/.exec(text)
    if (incompleteLink) text = text.slice(0, incompleteLink.index)
    text = text.replace(/`{1,2}$/, "")
  }
  return text
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/https?:\/\/\S+/g, " ")
    .replace(/[*_`#>]/g, " ")
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ").trim()
}

function phraseEnd(text: string, final: boolean) {
  const sentence = /[.!?]["')\]]*(?:\s+|$)/g
  let match: RegExpExecArray | null
  while ((match = sentence.exec(text))) {
    // Don't turn an unfinished decimal token ("3.") into a spoken sentence.
    if (!final && match[0] === "." && /\d/.test(text[match.index - 1] || "")) continue
    return match.index + match[0].length
  }
  if (text.length > MAX_PHRASE_CHARS) {
    const cut = text.lastIndexOf(" ", MAX_PHRASE_CHARS)
    if (cut > MAX_PHRASE_CHARS / 2) return cut + 1
    const next = text.indexOf(" ", MAX_PHRASE_CHARS)
    if (next >= 0) return next + 1
    // A very long identifier should not grow the buffer without bound.
    if (text.length > 2 * MAX_PHRASE_CHARS) return 2 * MAX_PHRASE_CHARS
  }
  return final ? text.length : 0
}

const asError = (value: unknown) => value instanceof Error ? value : new Error(String(value))
const uncertainAdmission = (value: unknown) =>
  typeof value === "object" && value !== null && "code" in value && value.code === "admission-uncertain"

export class LiveConversationController {
  private deps: LiveDependencies
  private stopped = false
  private faulted = false
  private muted = false
  private audioEpoch = 0
  private latestSequence = -1
  private turns = new Map<number, CaptureTurn>()
  private activeTurns = new Set<number>()
  private handledTurn = 0
  private response: Response | null = null
  private blockedMessages = new Set<string>()
  private barrier: Promise<void> = Promise.resolve()
  private draining = false
  private speech: { text: string; epoch: number }[] = []
  private speechChars = 0
  private speaking = false
  private playingEpoch = -1
  private lastState: LivePhase | undefined

  constructor(deps: LiveDependencies, options: { muted?: boolean } = {}) {
    this.deps = deps
    this.muted = options.muted ?? false
    this.state()
  }

  capture(event: LiveCapture) {
    const id = event.turn_id
    if (!Number.isSafeInteger(id) || id < 1 || id <= this.handledTurn) return
    let turn = this.turns.get(id)
    if (!turn) {
      if (this.turns.size >= MAX_PENDING_TURNS) {
        this.fail(new Error("Live recognition fell behind. Stop and restart live conversation."))
        if (event.event === "turn" && event.text?.trim()) this.deps.onUnsent?.(event.text.trim())
        return
      }
      turn = { ended: false }
      this.turns.set(id, turn)
    }
    if (event.event === "speech_start") {
      if (this.stopped || turn.ended || this.activeTurns.has(id)) return
      this.activeTurns.add(id)
      this.cancelResponse()
    } else if (event.event === "speech_end") {
      turn.ended = true
      this.activeTurns.delete(id)
    } else {
      if (turn.text !== undefined) return
      turn.ended = true
      turn.text = String(event.text || "").replace(/[\u0000-\u001f\u007f]/g, " ").trim()
      this.activeTurns.delete(id)
    }
    if (this.stopped) this.restoreReady()
    else void this.drain()
    this.state()
  }

  executionStarted(identity: LiveIdentity = {}) {
    const response = this.accept(identity)
    if (!response || response.finished) return
    response.started = true
    response.executing = true
    this.observeResponse(response, identity)
    this.state()
  }

  stepStarted(event: LiveStep) {
    const response = this.accept(event)
    if (!response || response.finished || !event.assistantMessageID || this.blockedMessages.has(event.assistantMessageID)) return false
    response.messages.add(event.assistantMessageID)
    response.started = true
    this.observeResponse(response, event)
    this.state()
    return true
  }

  textDelta(event: LiveText) {
    this.text(event, false)
  }

  textEnded(event: LiveText) {
    this.text(event, true)
  }

  executionEnded(identity: LiveIdentity & { outcome?: "succeeded" | "interrupted" | "failed"; reason?: string } = {}) {
    const response = this.accept(identity)
    // An old execution-ended notification can precede the new step. It must
    // not close a response whose execution we have not observed starting.
    if (!response || !response.started || response.finished
      || (Number.isFinite(identity.sequence) && identity.sequence! < response.lastSequence)) return
    response.executing = false
    response.finished = true
    if (identity.outcome === "failed") {
      this.fail(new Error(identity.reason || "The local model could not finish its live response."))
      return
    }
    if (identity.outcome === "interrupted") {
      this.cancelResponse()
      this.state()
      return
    }
    for (const segment of response.segments.values()) { segment.ended = true; this.flush(segment, true) }
    this.state()
  }

  setMuted(muted: boolean) {
    if (muted === this.muted) return
    this.muted = muted
    this.clearSpeech()
    for (const segment of this.response?.segments.values() || []) {
      segment.consumed = speechText(segment.raw, segment.ended).length
      // Resume at a fresh phrase, not midway through a word which began muted.
      segment.skipBoundary = !muted && !segment.ended
    }
    this.state()
  }

  async stop() {
    if (!this.stopped) {
      this.stopped = true
      this.cancelResponse()
      this.restoreReady()
      this.state()
    }
    await this.barrier
  }

  /** Surface a capture/transport failure and leave pending words recoverable. */
  error(error: unknown) {
    this.fail(asError(error))
  }

  private accept(identity: LiveIdentity) {
    const sequence = identity.sequence
    if (Number.isFinite(sequence)) this.latestSequence = Math.max(this.latestSequence, sequence!)
    const response = this.response
    if (this.stopped || !response || response.cancelled) return null
    if (Number.isFinite(sequence) && sequence! <= response.floor) return null
    if (identity.eventID) {
      if (response.events.has(identity.eventID)) return null
      response.events.add(identity.eventID)
      if (response.events.size > 1024) response.events.delete(response.events.values().next().value!)
    }
    return response
  }

  private text(event: LiveText, final: boolean) {
    const response = this.accept(event)
    if (!response || response.finished || !response.messages.has(event.assistantMessageID)
      || this.blockedMessages.has(event.assistantMessageID) || !Number.isInteger(event.ordinal)) return
    const key = `${event.assistantMessageID}:${event.ordinal}`
    let segment = response.segments.get(key)
    if (!segment) {
      segment = { raw: "", consumed: 0, ended: false, skipBoundary: false, lastSequence: -1 }
      response.segments.set(key, segment)
    }
    if (segment.ended || (Number.isFinite(event.sequence) && event.sequence! <= segment.lastSequence)) return
    if (Number.isFinite(event.sequence)) segment.lastSequence = event.sequence!
    this.observeResponse(response, event)
    const previousLength = segment.raw.length
    if (final) {
      const complete = String(event.text || "")
      if (complete.startsWith(segment.raw)) segment.raw = complete
      // A final-only event is normal; a revised non-prefix final is safe to
      // replace only before any of the earlier content has been spoken.
      else if (!segment.consumed) segment.raw = complete
      segment.ended = true
    } else segment.raw += String(event.delta || "")
    response.chars += segment.raw.length - previousLength
    if (response.chars > MAX_RESPONSE_CHARS) {
      this.fail(new Error("The live reply is too long. Use ordinary chat for this response."))
      return
    }
    this.flush(segment, final)
  }

  private flush(segment: Segment, final: boolean) {
    const text = speechText(segment.raw, final)
    while (segment.consumed < text.length) {
      const remaining = text.slice(segment.consumed)
      const end = phraseEnd(remaining, final)
      if (!end) break
      segment.consumed += end
      const phrase = remaining.slice(0, end).trim()
      if (segment.skipBoundary) { segment.skipBoundary = false; continue }
      if (!phrase || this.muted || this.stopped) continue
      if (this.speechChars + phrase.length > MAX_QUEUED_CHARS) {
        this.fail(new Error("Live speech fell behind. Try shorter replies or a faster local voice."))
        return
      }
      this.speech.push({ text: phrase, epoch: this.audioEpoch })
      this.speechChars += phrase.length
      void this.play()
    }
  }

  private async play() {
    if (this.speaking || this.stopped || this.muted) return
    this.speaking = true
    try {
      while (this.speech.length && !this.stopped && !this.muted) {
        const phrase = this.speech.shift()!
        this.speechChars -= phrase.text.length
        if (phrase.epoch !== this.audioEpoch) continue
        this.playingEpoch = phrase.epoch
        this.state()
        try { await this.deps.speak(phrase.text) }
        catch (error) {
          if (phrase.epoch === this.audioEpoch && !this.stopped && !this.muted) this.fail(asError(error))
        }
      }
    } finally {
      this.speaking = false
      this.playingEpoch = -1
      this.state()
      if (this.speech.length && !this.stopped && !this.muted) void this.play()
    }
  }

  private clearSpeech() {
    this.audioEpoch++
    this.speech = []
    this.speechChars = 0
    try { this.deps.stopSpeech() } catch (error) { this.deps.onError?.(asError(error)) }
  }

  private cancelResponse() {
    this.clearSpeech()
    const response = this.response
    if (!response || response.cancelled) return
    response.cancelled = true
    for (const message of response.messages) this.blockedMessages.add(message)
    while (this.blockedMessages.size > 128) this.blockedMessages.delete(this.blockedMessages.values().next().value!)
    this.response = null
    if (!response.executing && response.admitted) return
    this.barrier = this.barrier.then(async () => {
      // Interrupting before admission can miss a request which is still in
      // flight. Await admission first while cancelling audible speech now.
      try { await response.admission }
      catch (error) { if (!uncertainAdmission(error)) return }
      if (response.executing) await this.deps.interrupt()
    }).catch(error => { this.fail(asError(error)) })
  }

  private readyTurns() {
    const entries = [...this.turns].sort((a, b) => a[0] - b[0])
    if (this.activeTurns.size || entries.some(([, turn]) => !turn.ended || turn.text === undefined)) return []
    return entries
  }

  private async drain() {
    if (this.draining || this.stopped) return
    this.draining = true
    try {
      await this.barrier
      if (this.stopped) return
      const entries = this.readyTurns()
      if (!entries.length) return
      const text = entries.map(([, turn]) => turn.text).filter(Boolean).join(" ")
      for (const [id] of entries) { this.turns.delete(id); this.handledTurn = Math.max(this.handledTurn, id) }
      if (!text) return
      // No await between the final capture-state check and admission: OFF or a
      // new speech-start cannot sneak into that boundary.
      let resolveAdmission!: (value: unknown) => void
      let rejectAdmission!: (reason: unknown) => void
      const admission = new Promise<unknown>((resolve, reject) => {
        resolveAdmission = resolve; rejectAdmission = reject
      })
      const response: Response = {
        floor: this.latestSequence, cancelled: false,
        executing: true, started: false, finished: false, chars: 0, lastSequence: -1,
        admitted: false, admission,
        messages: new Set(), segments: new Map(), events: new Set(),
      }
      this.response = response
      this.state()
      try {
        // Install the gate before dispatch: even a synchronously delivered
        // speech-start / OFF callback must await this exact admission.
        try {
          Promise.resolve(this.deps.prompt(text)).then(value => {
            response.admitted = true
            resolveAdmission(value)
          }, rejectAdmission)
        } catch (error) { rejectAdmission(error) }
        await response.admission
      } catch (error) {
        response.executing = uncertainAdmission(error)
        this.deps.onUnsent?.(text)
        if (!this.stopped) this.fail(asError(error))
      }
    } finally {
      this.draining = false
      this.state()
      if (!this.stopped && this.readyTurns().length) void this.drain()
    }
  }

  private restoreReady() {
    const restored: string[] = []
    for (const [id, turn] of [...this.turns].sort((a, b) => a[0] - b[0])) {
      if (turn.text === undefined) break
      if (turn.text) restored.push(turn.text)
      this.turns.delete(id)
      this.handledTurn = Math.max(this.handledTurn, id)
    }
    if (restored.length) this.deps.onUnsent?.(restored.join(" "))
  }

  private observeResponse(response: Response, identity: LiveIdentity) {
    if (Number.isFinite(identity.sequence)) response.lastSequence = Math.max(response.lastSequence, identity.sequence!)
  }

  private fail(error: Error) {
    if (this.faulted) return
    this.faulted = true
    this.stopped = true
    this.cancelResponse()
    this.restoreReady()
    this.state()
    this.deps.onError?.(error)
  }

  private state() {
    const state: LivePhase = this.faulted ? "error" : this.stopped ? "stopped"
      : this.activeTurns.size ? "listening"
      : this.turns.size ? "transcribing"
      : this.speaking && this.playingEpoch === this.audioEpoch && !this.muted ? "speaking"
      : this.response && !this.response.cancelled && this.response.executing ? "thinking"
      : "listening"
    if (state !== this.lastState) { this.lastState = state; this.deps.onState?.(state) }
  }
}
