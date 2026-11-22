import {test} from "node:test"
import assert from "node:assert/strict"
import {LiveConversationMode} from "../live_mode.ts"
import {createOpenCodeLiveAdapter} from "../opencode_live.ts"

function deferred<T = void>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return {promise, resolve, reject}
}
const settled = () => new Promise<void>(resolve => setImmediate(resolve))

function fixture() {
  let route = "chat-a"
  let running = false
  let beginCapture = () => {}
  let captureEvent: (event: any) => void = () => {}
  let admission = async (input: any) => ({id: "input-1", sessionID: input.sessionID})
  const captureDone = deferred()
  const heard: string[] = []
  const restored: [string, string][] = []
  const prompts: any[] = []
  const interrupted: string[] = []
  const errors: any[] = []
  const states: string[] = []
  const active: boolean[] = []
  let speechStopped = 0
  let captureStopped = 0
  let captureCancelled = 0
  let captures = 0
  const listeners = new Map<string, (event: any) => void>()
  const provider = {id: "local", settings: {baseURL: "http://localhost:8080/v1"}}
  const context = {
    ui: {router: {current: () => ({type: "session", sessionID: route})},
      model: {current: () => ({providerID: "local", modelID: "chat"})}},
    data: {session: {status: () => running ? "running" : "idle"},
      on: (name: string, fn: (event: any) => void) => {
        listeners.set(name, fn)
        return () => listeners.delete(name)
      }},
    client: {
      session: {
        get: async () => ({id: "chat-a", location: {directory: "/project"}, model: {providerID: "local", id: "chat"}}),
        switchModel: async () => {},
        prompt: async (input: any) => { prompts.push(input); running = true; return admission(input) },
        interrupt: async ({sessionID}: any) => { interrupted.push(sessionID); running = false; return {interrupted: true} },
      },
      provider: {get: async () => ({data: provider})},
      model: {list: async () => ({data: [{providerID: "local", id: "chat", enabled: true}]})},
    },
  }
  const mode = new LiveConversationMode(createOpenCodeLiveAdapter(context), {
    listen: emit => {
      captures++
      captureEvent = emit
      beginCapture()
      return {done: captureDone.promise, stop: () => { captureStopped++ },
        cancel: () => { captureCancelled++; captureDone.resolve() }}
    },
    warm: () => {},
    speak: async text => { heard.push(text) },
    stopSpeech: () => { speechStopped++ },
  }, {
    active: value => active.push(value), state: value => states.push(value),
    error: error => errors.push(error), restoreDraft: (id, text) => restored.push([id, text]),
  })
  return {mode, prompts, heard, restored, errors, interrupted, listeners, states, active, provider, context,
    emit: (name: string, data: any, seq: number) => listeners.get(name)?.({type: name, data: {sessionID: "chat-a", ...data}, durable: {seq}}),
    capture: (event: string, turn_id: number, text?: string) => captureEvent({event, turn_id, text}),
    endCapture: () => captureDone.resolve(),
    failCapture: (error: Error) => captureDone.reject(error),
    captureStarted: (fn: () => void) => { beginCapture = fn },
    admit: (fn: typeof admission) => { admission = fn },
    route: (id: string) => { route = id },
    counts: () => ({captures, speechStopped, captureStopped, captureCancelled}),
  }
}

test("the actual adapter, controller and mode wire capture to a local prompt and streamed speech", async () => {
  const f = fixture()
  await f.mode.start("chat-a")
  f.capture("speech_start", 1)
  f.capture("speech_end", 1)
  f.capture("turn", 1, "Explain the current function.")
  await settled()
  assert.equal(f.prompts[0].text, "Explain the current function.")
  f.emit("session.execution.started", {}, 1)
  f.emit("session.step.started", {assistantMessageID: "assistant-1"}, 2)
  f.emit("session.text.delta", {assistantMessageID: "assistant-1", ordinal: 0, delta: "It reads a file. "}, 3)
  await settled()
  assert.deepEqual(f.heard, ["It reads a file."])
  f.capture("speech_start", 2)
  await settled()
  assert.deepEqual(f.interrupted, ["chat-a"])
  f.emit("session.text.ended", {assistantMessageID: "assistant-1", ordinal: 0, text: "It reads a file. Old tail."}, 4)
  await settled()
  assert.deepEqual(f.heard, ["It reads a file."])
  const stopping = f.mode.stop()
  f.capture("speech_end", 2)
  f.capture("turn", 2, "Unsent final words")
  f.endCapture()
  await stopping
  assert.deepEqual(f.restored, [["chat-a", "Unsent final words"]])
  assert.equal(f.prompts.length, 1)
  assert.equal(f.mode.ownsMessage("assistant-1"), true,
    "ordinary read-aloud must continue suppressing a delayed final after Live is off")
  assert.equal(f.mode.busy, false)
  assert.equal(f.listeners.size, 0)
  assert.deepEqual(f.errors, [])
})

test("OFF during dispatched admission waits for its ack then interrupts the owned session", async () => {
  const f = fixture()
  const entered = deferred()
  const release = deferred<{id: string; sessionID: string}>()
  f.admit(async () => { entered.resolve(); return release.promise })
  await f.mode.start("chat-a")
  f.capture("speech_start", 1)
  f.capture("speech_end", 1)
  f.capture("turn", 1, "A prompt in transit")
  await entered.promise
  const stopping = f.mode.stop()
  assert.equal(f.mode.active, false)
  release.resolve({id: "input-1", sessionID: "chat-a"})
  f.endCapture()
  await stopping
  assert.deepEqual(f.interrupted, ["chat-a"])
  assert.deepEqual(f.restored, [])
})

test("navigation stops live mode and restores unfinished words only to their original chat", async () => {
  const f = fixture()
  await f.mode.start("chat-a")
  f.capture("speech_start", 1)
  f.route("chat-b")
  f.mode.checkRoute()
  assert.equal(f.mode.active, false)
  f.capture("speech_end", 1)
  f.capture("turn", 1, "Keep these words")
  f.endCapture()
  await f.mode.stop()
  assert.deepEqual(f.restored, [["chat-a", "Keep these words"]])
  assert.deepEqual(f.prompts, [])
})

test("a cloud model fails before any microphone capture or prompt", async () => {
  const f = fixture()
  f.provider.settings.baseURL = "https://api.example.com/v1"
  await f.mode.start("chat-a")
  assert.equal(f.mode.busy, false)
  assert.equal(f.counts().captures, 0)
  assert.equal(f.errors.length, 1)
  assert.deepEqual(f.prompts, [])
})

test("OFF during startup cannot open a late microphone", async () => {
  const f = fixture()
  const entered = deferred()
  const release = deferred()
  const original = f.context.client.session.get
  f.context.client.session.get = async () => { entered.resolve(); await release.promise; return original() }
  const starting = f.mode.start("chat-a")
  await entered.promise
  await f.mode.stop()
  release.resolve()
  await starting
  assert.equal(f.counts().captures, 0)
  assert.equal(f.mode.busy, false)
  assert.deepEqual(f.errors, [])
})

test("a final ASR failure during graceful OFF is reported instead of claiming draft recovery", async () => {
  const f = fixture()
  await f.mode.start("chat-a")
  f.capture("speech_start", 1)
  const stopped = f.mode.stop()
  f.failCapture(new Error("The final ASR request timed out"))
  await stopped
  assert.equal(f.errors.length, 1)
  assert.match(f.errors[0].message, /final ASR/)
  assert.equal(f.states.at(-1), "error")
  assert.equal(f.mode.busy, false)
  assert.deepEqual(f.prompts, [])
})

test("a model interruption failure during OFF remains visible", async () => {
  const f = fixture()
  f.context.client.session.interrupt = async () => { throw new Error("Could not stop the local model") }
  await f.mode.start("chat-a")
  f.capture("speech_start", 1)
  f.capture("speech_end", 1)
  f.capture("turn", 1, "Start a response")
  await settled()
  const stopped = f.mode.stop()
  f.endCapture()
  await stopped
  assert.equal(f.errors.length, 1)
  assert.match(f.errors[0].message, /Could not stop/)
  assert.equal(f.states.at(-1), "error")
  assert.equal(f.mode.busy, false)
})
