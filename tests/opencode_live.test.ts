import {test} from "node:test"
import assert from "node:assert/strict"
import {createOpenCodeLiveAdapter, localModelURL} from "../opencode_live.ts"

function fixture() {
  let route: any = {type: "session", sessionID: "chat-a"}
  let selection: any = {providerID: "local", modelID: "chat-model"}
  let running = false
  let getSession = async () => session
  const session: any = {id: "chat-a", location: {directory: "/project"}, model: {id: "old", providerID: "local"}}
  const provider: any = {id: "local", settings: {baseURL: "http://127.0.0.1:8080/v1"}}
  const definition: any = {
    id: "chat-model", modelID: "upstream-model-name", providerID: "local", enabled: true,
    variants: [{id: "fast", settings: {temperature: 0.2}}],
  }
  const calls: any[] = []
  const listeners = new Map<string, (event: any) => void>()
  const context = {
    ui: {router: {current: () => route}, model: {current: () => selection}},
    data: {
      session: {status: () => running ? "running" : "idle"},
      on: (type: string, handler: (event: any) => void) => {
        listeners.set(type, handler)
        return () => listeners.delete(type)
      },
    },
    client: {
      session: {
        get: async ({sessionID}: any) => { assert.equal(sessionID, "chat-a"); return getSession() },
        switchModel: async (input: any) => { calls.push(["model", input]); session.model = input.model },
        prompt: async (input: any) => { calls.push(["prompt", input]); return {id: "user-1", sessionID: input.sessionID} },
        interrupt: async (input: any) => { calls.push(["interrupt", input]); return {interrupted: running} },
      },
      provider: {get: async (input: any) => {
        assert.deepEqual(input, {providerID: "local", location: {directory: "/project"}})
        return {location: {directory: "/project"}, data: provider}
      }},
      model: {list: async (input: any) => {
        assert.deepEqual(input, {location: {directory: "/project"}})
        return {location: {directory: "/project"}, data: [definition]}
      }},
    },
  }
  return {
    context, session, provider, definition, calls, listeners,
    adapter: createOpenCodeLiveAdapter(context),
    route: (value: any) => { route = value },
    select: (value: any) => { selection = value },
    running: (value: boolean) => { running = value },
    get: (value: typeof getSession) => { getSession = value },
    emit: (type: string, data: any, extra: any = {}) => listeners.get(type)?.({type, data, ...extra}),
  }
}

test("live turn uses the selected catalog model in the existing OpenCode chat", async () => {
  const f = fixture()
  f.select({providerID: "local", modelID: "chat-model", variant: "fast"})
  const snapshot = await f.adapter.snapshot()
  assert.deepEqual(snapshot, {
    sessionID: "chat-a", model: {providerID: "local", modelID: "chat-model", variant: "fast"},
    location: {directory: "/project"}, baseURL: "http://127.0.0.1:8080/v1",
  })
  assert.deepEqual(f.calls, [])
  assert.deepEqual(await f.adapter.prompt(snapshot, "  Explain this function.  "), {id: "user-1", sessionID: "chat-a"})
  assert.deepEqual(f.calls, [
    ["model", {sessionID: "chat-a", model: {id: "chat-model", providerID: "local", variant: "fast"}}],
    ["prompt", {sessionID: "chat-a", text: "Explain this function.", delivery: "steer", resume: true}],
  ])
})

test("locality uses effective model and variant settings, never a friendly provider name", async () => {
  for (const apply of [
    (f: ReturnType<typeof fixture>) => { f.provider.settings = {} },
    (f: ReturnType<typeof fixture>) => { f.definition.settings = {baseURL: "https://api.example.com/v1"} },
    (f: ReturnType<typeof fixture>) => {
      f.select({providerID: "local", modelID: "chat-model", variant: "fast"})
      f.definition.variants[0].settings.baseURL = "https://api.example.com/v1"
    },
  ]) {
    const f = fixture()
    apply(f)
    await assert.rejects(f.adapter.snapshot(), {code: "local-model-required"})
    assert.deepEqual(f.calls, [])
  }
  const f = fixture()
  f.provider.settings.baseURL = "https://api.example.com/v1"
  f.definition.settings = {baseURL: "http://localhost:1234/v1"}
  assert.equal((await f.adapter.snapshot()).baseURL, "http://localhost:1234/v1")
})

test("URL parsing rejects remote destinations and only opts into literal private network addresses", () => {
  for (const url of ["http://localhost:8080/v1", "http://127.0.0.2:8000/v1", "http://[::1]:8080/v1"]) {
    assert.equal(localModelURL(url), new URL(url).href)
  }
  for (const url of ["https://localhost.evil.example/v1", "https://localhost@api.example.com/v1", "file:///tmp/model", "http://0.0.0.0:8080/v1", "http://192.168.0.4:8080/v1", "http://local-model:8080/v1", "${MODEL_URL}"]) {
    assert.throws(() => localModelURL(url), {code: "local-model-required"})
  }
  assert.equal(localModelURL("http://192.168.0.4:8080/v1", true), "http://192.168.0.4:8080/v1")
  assert.equal(localModelURL("http://[fd00::1]:8080/v1", true), "http://[fd00::1]:8080/v1")
  assert.throws(() => localModelURL("https://api.example.com/v1", true), {code: "local-model-required"})
})

test("start refuses active manual work and send does not switch a running session model", async () => {
  const f = fixture()
  f.running(true)
  await assert.rejects(f.adapter.snapshot(), {code: "session-busy"})
  f.running(false)
  const snapshot = await f.adapter.snapshot()
  f.running(true)
  await assert.rejects(f.adapter.prompt(snapshot, "New turn"), {code: "session-busy"})
  assert.deepEqual(f.calls, [])
})

test("changed chat, selected model or endpoint prevents stale transcript submission", async () => {
  for (const apply of [
    (f: ReturnType<typeof fixture>) => f.route({type: "session", sessionID: "chat-b"}),
    (f: ReturnType<typeof fixture>) => f.select({providerID: "cloud", modelID: "chat-model"}),
    (f: ReturnType<typeof fixture>) => { f.definition.settings = {baseURL: "http://localhost:9999/v1"} },
  ]) {
    const f = fixture()
    const snapshot = await f.adapter.snapshot()
    apply(f)
    await assert.rejects(f.adapter.prompt(snapshot, "This must not send"), /changed/)
    assert.deepEqual(f.calls, [])
  }
  const f = fixture()
  const snapshot = await f.adapter.snapshot()
  f.get(async () => { f.route({type: "home"}); return f.session })
  await assert.rejects(f.adapter.prompt(snapshot, "This must not send"), {code: "session-changed"})
  assert.deepEqual(f.calls, [])
})

test("stopping during preflight prevents admission even when the host ignores the abort signal", async () => {
  const f = fixture()
  const snapshot = await f.adapter.snapshot()
  const controller = new AbortController()
  let entered!: () => void
  let release!: () => void
  const waiting = new Promise<void>(resolve => { entered = resolve })
  const barrier = new Promise<void>(resolve => { release = resolve })
  f.get(async () => { entered(); await barrier; return f.session })
  const pending = f.adapter.prompt(snapshot, "This must not send", {signal: controller.signal})
  await waiting
  controller.abort()
  release()
  await assert.rejects(pending, {code: "cancelled"})
  assert.deepEqual(f.calls, [])
})

test("interruption targets the owned chat after navigation and reports idle as a no-op", async () => {
  const f = fixture()
  f.running(true)
  f.route({type: "session", sessionID: "chat-b"})
  assert.equal(await f.adapter.interrupt("chat-a"), true)
  f.running(false)
  assert.equal(await f.adapter.interrupt("chat-a"), false)
  assert.deepEqual(f.calls, [
    ["interrupt", {sessionID: "chat-a", resume: false}],
    ["interrupt", {sessionID: "chat-a", resume: false}],
  ])
})

test("OFF after prompt dispatch preserves the admission acknowledgement for scoped interruption", async () => {
  const f = fixture()
  const snapshot = await f.adapter.snapshot()
  const controller = new AbortController()
  let entered!: () => void
  let release!: () => void
  const dispatched = new Promise<void>(resolve => { entered = resolve })
  const reply = new Promise<void>(resolve => { release = resolve })
  f.context.client.session.prompt = async (input: any, options?: {signal?: AbortSignal}) => {
    f.calls.push(["prompt", input])
    entered()
    await new Promise<void>((resolve, reject) => {
      options?.signal?.addEventListener("abort", () => reject(new Error("HTTP request aborted")), {once: true})
      void reply.then(resolve)
    })
    return {id: "admitted-while-stopping", sessionID: input.sessionID}
  }
  const pending = f.adapter.prompt(snapshot, "An admitted turn", {signal: controller.signal})
  await dispatched
  controller.abort()
  release()
  assert.deepEqual(await pending, {id: "admitted-while-stopping", sessionID: "chat-a"})
})

test("only a dispatched prompt failure is marked as uncertain admission", async () => {
  const f = fixture()
  const snapshot = await f.adapter.snapshot()
  f.context.client.session.prompt = async (input: any) => {
    f.calls.push(["prompt", input])
    throw new Error("Connection lost")
  }
  await assert.rejects(f.adapter.prompt(snapshot, "Potentially admitted"), {code: "admission-uncertain"})
  assert.equal(f.calls.filter(([kind]) => kind === "prompt").length, 1)

  const before = fixture()
  const initial = await before.adapter.snapshot()
  before.context.client.provider.get = async () => { throw new Error("Configuration unavailable") }
  await assert.rejects(before.adapter.prompt(initial, "Never dispatched"), error => {
    assert.equal((error as any).code, undefined)
    assert.match((error as Error).message, /Configuration unavailable/)
    return true
  })
  assert.deepEqual(before.calls, [])
})

test("subscriptions forward live identities, isolate chats, deduplicate and dispose", () => {
  const f = fixture()
  const events: any[] = []
  const close = f.adapter.subscribe("chat-a", {
    onStepStarted: e => events.push(["step", e]),
    onTextDelta: e => events.push(["delta", e]),
    onTextEnded: e => events.push(["ended", e]),
    onExecutionEnded: e => events.push(["execution", e]),
    onError: e => events.push(["error", e.message]),
  })
  const part = {sessionID: "chat-a", assistantMessageID: "answer-1", ordinal: 0}
  f.emit("session.step.started", part, {id: "step-1", created: 50, durable: {seq: 12}})
  f.emit("session.text.delta", {...part, delta: "Hello"}, {id: "delta-1"})
  f.emit("session.text.delta", {...part, delta: "Hello"}, {id: "delta-1"})
  f.emit("session.text.delta", {...part, sessionID: "chat-b", delta: "Other chat"}, {id: "other"})
  f.emit("session.text.ended", {...part, text: "Hello."}, {id: "ended-1", durable: {seq: 13}})
  f.emit("session.execution.failed", {sessionID: "chat-a", error: {message: "Local model stopped"}}, {id: "failure"})
  assert.deepEqual(events, [
    ["step", {sessionID: "chat-a", eventID: "step-1", created: 50, sequence: 12, assistantMessageID: "answer-1"}],
    ["delta", {sessionID: "chat-a", eventID: "delta-1", assistantMessageID: "answer-1", ordinal: 0, delta: "Hello"}],
    ["ended", {sessionID: "chat-a", eventID: "ended-1", sequence: 13, assistantMessageID: "answer-1", ordinal: 0, text: "Hello."}],
    ["error", "Local model stopped"],
    ["execution", {sessionID: "chat-a", eventID: "failure", outcome: "failed"}],
  ])
  close()
  close()
  assert.equal(f.listeners.size, 0)
  f.emit("session.text.delta", {...part, delta: "Too late"})
  assert.equal(events.length, 5)
})
