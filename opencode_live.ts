/**
 * Narrow OpenCode v2 bridge for Live conversation. Dictation does not use this.
 *
 * API contracts verified against the upstream generated client and TUI context:
 * https://github.com/anomalyco/opencode/blob/dev/packages/client/src/promise/generated/types.ts
 * https://github.com/anomalyco/opencode/blob/dev/packages/plugin/src/tui/context.ts
 * https://github.com/anomalyco/opencode/blob/dev/packages/schema/src/session-event.ts
 *
 * The locality check describes the configured model endpoint. A local server or
 * an OpenCode request hook can itself proxy elsewhere; a TUI plugin cannot prove
 * the destination of that server's eventual network requests.
 */

export type LiveModelSelection = {
  providerID: string
  modelID: string
  variant?: string
}

export type LiveSessionSnapshot = {
  sessionID: string
  model: LiveModelSelection
  baseURL: string
  location: {directory: string}
}

export type LiveEvent = {
  sessionID: string
  eventID?: string
  created?: number
  sequence?: number
}

export type LiveStepEvent = LiveEvent & {assistantMessageID: string}
export type LiveTextEvent = LiveStepEvent & {ordinal: number}
export type LiveTextDeltaEvent = LiveTextEvent & {delta: string}
export type LiveTextEndedEvent = LiveTextEvent & {text: string}
export type LiveExecutionEndedEvent = LiveEvent & {
  outcome: "succeeded" | "interrupted" | "failed"
  reason?: string
}
export type LiveErrorEvent = LiveEvent & {assistantMessageID?: string; error: unknown; message: string}

export type LiveEventHandlers = {
  onStepStarted?: (event: LiveStepEvent) => void
  onTextStarted?: (event: LiveTextEvent) => void
  onTextDelta?: (event: LiveTextDeltaEvent) => void
  onTextEnded?: (event: LiveTextEndedEvent) => void
  onExecutionStarted?: (event: LiveEvent) => void
  onExecutionEnded?: (event: LiveExecutionEndedEvent) => void
  onError?: (event: LiveErrorEvent) => void
}

export class LiveAdapterError extends Error {
  readonly code: string
  constructor(code: string, message: string) {
    super(message)
    this.name = "LiveAdapterError"
    this.code = code
  }
}

function fail(code: string, message: string): never {
  throw new LiveAdapterError(code, message)
}

function selectedModel(context: any): LiveModelSelection {
  const selected = context.ui?.model?.current?.()
  if (!selected?.providerID || !selected?.modelID) {
    fail("model-unavailable", "Select a local model in OpenCode before starting Live conversation. This mode requires the OpenCode v2 model API.")
  }
  return {
    providerID: selected.providerID,
    modelID: selected.modelID,
    ...(selected.variant && selected.variant !== "default" ? {variant: selected.variant} : {}),
  }
}

function sameModel(a: LiveModelSelection, b: LiveModelSelection): boolean {
  return a.providerID === b.providerID && a.modelID === b.modelID && a.variant === b.variant
}

/** Only configured literal local destinations are accepted; never resolve a guessed hostname. */
export function localModelURL(value: unknown, allowPrivateNetwork = false): string {
  if (typeof value !== "string" || !value.trim() || /\$\{|\{env:/.test(value)) {
    fail("local-model-required", "Live conversation needs an explicitly configured local model baseURL, such as http://127.0.0.1:8080/v1. Cloud models are not used.")
  }
  let endpoint: URL
  try { endpoint = new URL(value) } catch {
    return fail("local-model-required", "The selected model baseURL is not a valid local HTTP URL.")
  }
  const host = endpoint.hostname.toLowerCase()
  const ipv4 = host.split(".").map(Number)
  const dotted = /^\d+\.\d+\.\d+\.\d+$/.test(host) && ipv4.every(n => n >= 0 && n <= 255)
  const loopback = host === "localhost" || host === "[::1]" || (dotted && ipv4[0] === 127)
  const privateAddress = allowPrivateNetwork && (
    (dotted && (ipv4[0] === 10 || (ipv4[0] === 172 && ipv4[1] >= 16 && ipv4[1] <= 31)
      || (ipv4[0] === 192 && ipv4[1] === 168)))
    || /^\[f[cd][0-9a-f]{2}:/.test(host)
  )
  if (!["http:", "https:"].includes(endpoint.protocol) || (!loopback && !privateAddress)) {
    fail("local-model-required", "Live conversation only sends to a configured local model. Select a model with a localhost or loopback baseURL.")
  }
  return endpoint.href
}

function abortIfNeeded(signal?: AbortSignal) {
  if (signal?.aborted) {
    throw new LiveAdapterError("cancelled", "Live conversation was stopped before the message was sent.")
  }
}

function messageFor(error: unknown): string {
  if (typeof error === "string") return error
  const value = error as any
  return typeof value?.message === "string" ? value.message
    : typeof value?.data?.message === "string" ? value.data.message
      : "OpenCode could not complete the live response."
}

export function createOpenCodeLiveAdapter(context: any, options: {allowPrivateNetwork?: boolean} = {}) {
  function currentSession(): string | null {
    const route = context.ui?.router?.current?.()
    return route?.type === "session" && typeof route.sessionID === "string" ? route.sessionID : null
  }

  function assertCurrent(sessionID: string, model?: LiveModelSelection) {
    if (currentSession() !== sessionID) {
      fail("session-changed", "The active chat changed. Start Live conversation in the chat you want to use.")
    }
    if (model && !sameModel(selectedModel(context), model)) {
      fail("model-changed", "The selected model changed. Restart Live conversation to use the new model.")
    }
  }

  function status(sessionID: string): "idle" | "running" {
    const value = context.data?.session?.status?.(sessionID)
    if (value !== "idle" && value !== "running") {
      fail("status-unavailable", "OpenCode cannot report this chat's execution state. Live conversation was not started.")
    }
    return value
  }

  function assertIdle(sessionID: string) {
    if (status(sessionID) === "running") {
      fail("session-busy", "Wait for the current OpenCode response to finish before starting Live conversation.")
    }
  }

  async function inspect(sessionID: string, signal?: AbortSignal) {
    abortIfNeeded(signal)
    assertCurrent(sessionID)
    const model = selectedModel(context)
    if (!context.client?.session?.prompt || !context.client?.session?.interrupt
        || !context.client?.session?.switchModel || !context.client?.session?.get
        || !context.client?.provider?.get || !context.client?.model?.list) {
      fail("unsupported-host", "Live conversation requires OpenCode v2 prompt, interrupt, model and provider APIs.")
    }
    const result = await context.client.session.get({sessionID}, {signal})
    abortIfNeeded(signal)
    assertCurrent(sessionID, model)
    // Current clients return SessionInfo directly. Some earlier v2 builds wrap it.
    const session = result?.data ?? result
    if (session?.id !== sessionID || typeof session?.location?.directory !== "string") {
      fail("session-unavailable", "OpenCode could not resolve the current chat and its model configuration.")
    }
    const location = {directory: session.location.directory}
    const [providerResult, modelsResult] = await Promise.all([
      context.client.provider.get({providerID: model.providerID, location}, {signal}),
      context.client.model.list({location}, {signal}),
    ])
    abortIfNeeded(signal)
    assertCurrent(sessionID, model)
    const provider = providerResult?.data
    const catalog = Array.isArray(modelsResult?.data) ? modelsResult.data : []
    // The UI's modelID is the catalog id, which can differ from the provider API modelID.
    const definition = catalog.find((item: any) => item.providerID === model.providerID && item.id === model.modelID)
    if (provider?.id !== model.providerID || !definition || definition.enabled === false) {
      fail("model-unavailable", "The selected local model is not available in this chat's location.")
    }
    const variant = model.variant ? definition.variants?.find((item: any) => item.id === model.variant) : undefined
    if (model.variant && !variant) {
      fail("variant-unavailable", "The selected model variant is no longer available. Choose a model variant before starting Live conversation.")
    }
    // This is the upstream ModelResolver precedence for endpoint settings.
    const settings = {...provider.settings, ...definition.settings, ...variant?.settings}
    const baseURL = localModelURL(settings.baseURL, options.allowPrivateNetwork)
    return {session, snapshot: {sessionID, model, location, baseURL} satisfies LiveSessionSnapshot}
  }

  async function snapshot(sessionID = currentSession() ?? "", request: {signal?: AbortSignal} = {}): Promise<LiveSessionSnapshot> {
    abortIfNeeded(request.signal)
    assertCurrent(sessionID)
    assertIdle(sessionID)
    const checked = await inspect(sessionID, request.signal)
    assertIdle(sessionID)
    return checked.snapshot
  }

  async function prompt(expected: LiveSessionSnapshot, text: string, request: {signal?: AbortSignal} = {}) {
    const value = text.trim()
    if (!value) fail("empty-turn", "There is no recognized speech to send.")
    abortIfNeeded(request.signal)
    assertCurrent(expected.sessionID, expected.model)
    const checked = await inspect(expected.sessionID, request.signal)
    if (!sameModel(expected.model, checked.snapshot.model) || expected.baseURL !== checked.snapshot.baseURL
        || expected.location.directory !== checked.snapshot.location.directory) {
      fail("model-changed", "The local model configuration changed. Restart Live conversation before sending another turn.")
    }
    const committed = checked.session.model
    const needsSelection = committed?.providerID !== expected.model.providerID
      || committed?.id !== expected.model.modelID
      || (committed?.variant === "default" ? undefined : committed?.variant) !== expected.model.variant
    if (needsSelection) {
      // A running manual response must never be redirected to another model.
      assertIdle(expected.sessionID)
      await context.client.session.switchModel({
        sessionID: expected.sessionID,
        model: {
          id: expected.model.modelID,
          providerID: expected.model.providerID,
          ...(expected.model.variant ? {variant: expected.model.variant} : {}),
        },
      }, {signal: request.signal})
    }
    abortIfNeeded(request.signal)
    assertCurrent(expected.sessionID, expected.model)
    // Once dispatched, admission must settle even if OFF aborts preflight work.
    // Aborting this request could hide a successfully admitted server execution
    // from the controller, which must then interrupt that exact execution.
    let admitted: any
    try {
      admitted = await context.client.session.prompt({
        sessionID: expected.sessionID,
        text: value,
        delivery: "steer",
        resume: true,
      })
    } catch (error) {
      // The server may have admitted the request before the connection failed.
      // This distinct code tells the controller to interrupt its scoped response;
      // preflight failures above must never cancel unrelated manual execution.
      fail("admission-uncertain", `OpenCode did not confirm the live message: ${messageFor(error)} Check this chat before repeating it.`)
    }
    const item = admitted?.data ?? admitted
    if (typeof item?.id !== "string" || item?.sessionID !== expected.sessionID) {
      // Do not retry an uncertain admission: it could submit the same speech twice.
      fail("admission-uncertain", "OpenCode did not confirm the live message. Check this chat before repeating it.")
    }
    return {id: item.id as string, sessionID: item.sessionID as string}
  }

  async function interrupt(sessionID: string): Promise<boolean> {
    if (!sessionID || typeof context.client?.session?.interrupt !== "function") {
      fail("unsupported-host", "OpenCode cannot interrupt this live response.")
    }
    // The caller owns this response. A route change must not redirect cancellation
    // to a newly selected chat, nor prevent cancellation of the old response.
    const result = await context.client.session.interrupt({sessionID, resume: false})
    if (typeof result?.interrupted !== "boolean") {
      fail("interruption-uncertain", "OpenCode did not confirm that the live response stopped.")
    }
    return result.interrupted
  }

  function subscribe(sessionID: string, handlers: LiveEventHandlers): () => void {
    if (typeof context.data?.on !== "function") {
      fail("unsupported-host", "OpenCode cannot stream live response events.")
    }
    const stops: (() => void)[] = []
    const seen = new Set<string>()
    let closed = false
    function on(type: string, handler: (data: any, event: LiveEvent) => void) {
      stops.push(context.data.on(type, (incoming: any) => {
        if (closed || incoming?.data?.sessionID !== sessionID) return
        const id = typeof incoming.id === "string" ? incoming.id : undefined
        if (id && seen.has(id)) return
        if (id) {
          seen.add(id)
          if (seen.size > 1024) seen.delete(seen.values().next().value!)
        }
        handler(incoming.data, {
          sessionID,
          ...(id ? {eventID: id} : {}),
          ...(Number.isFinite(incoming.created) ? {created: incoming.created} : {}),
          ...(Number.isFinite(incoming.durable?.seq) ? {sequence: incoming.durable.seq} : {}),
        })
      }))
    }
    function identity(data: any, event: LiveEvent): LiveTextEvent | undefined {
      if (typeof data.assistantMessageID !== "string" || !Number.isInteger(data.ordinal) || data.ordinal < 0) return
      return {...event, assistantMessageID: data.assistantMessageID, ordinal: data.ordinal}
    }
    const close = () => {
      if (closed) return
      closed = true
      for (const stop of stops) stop()
      seen.clear()
    }
    try {
      // data.on is the live event stream; never subscribe by replaying message history.
      on("session.step.started", (data, event) => {
        if (typeof data.assistantMessageID === "string") handlers.onStepStarted?.({...event, assistantMessageID: data.assistantMessageID})
      })
      on("session.text.started", (data, event) => {
        const value = identity(data, event)
        if (value) handlers.onTextStarted?.(value)
      })
      on("session.text.delta", (data, event) => {
        const value = identity(data, event)
        if (value && typeof data.delta === "string") handlers.onTextDelta?.({...value, delta: data.delta})
      })
      on("session.text.ended", (data, event) => {
        const value = identity(data, event)
        if (value && typeof data.text === "string") handlers.onTextEnded?.({...value, text: data.text})
      })
      on("session.execution.started", (_data, event) => handlers.onExecutionStarted?.(event))
      on("session.execution.succeeded", (_data, event) => handlers.onExecutionEnded?.({...event, outcome: "succeeded"}))
      on("session.execution.interrupted", (data, event) => handlers.onExecutionEnded?.({...event, outcome: "interrupted", reason: data.reason}))
      on("session.execution.failed", (data, event) => {
        handlers.onError?.({...event, error: data.error, message: messageFor(data.error)})
        handlers.onExecutionEnded?.({...event, outcome: "failed"})
      })
      on("session.step.failed", (data, event) => handlers.onError?.({
        ...event, assistantMessageID: data.assistantMessageID, error: data.error, message: messageFor(data.error),
      }))
    } catch (error) {
      close()
      throw error
    }
    return close
  }

  return {currentSession, snapshot, prompt, interrupt, subscribe}
}
