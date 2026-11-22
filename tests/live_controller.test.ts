import { test } from "node:test"
import assert from "node:assert/strict"
import { LiveConversationController, type LiveDependencies, type LivePhase } from "../live_controller.ts"

function deferred<T = void>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

const settled = () => new Promise<void>(resolve => setImmediate(resolve))

function fixture(overrides: Partial<LiveDependencies> = {}) {
  const prompts: string[] = []
  const spoken: string[] = []
  const restored: string[] = []
  const errors: Error[] = []
  const states: LivePhase[] = []
  let interrupts = 0
  let stops = 0
  const controller = new LiveConversationController({
    prompt: async text => { prompts.push(text) },
    interrupt: async () => { interrupts++ },
    speak: async text => { spoken.push(text) },
    stopSpeech: () => { stops++ },
    onUnsent: text => restored.push(text),
    onError: error => errors.push(error),
    onState: state => states.push(state),
    ...overrides,
  })
  let sequence = 0
  return { controller, prompts, spoken, restored, errors, states,
    interrupts: () => interrupts, stops: () => stops,
    identity: () => ({sequence: ++sequence}),
    start: (id: number) => controller.capture({event: "speech_start", turn_id: id}),
    end: (id: number) => controller.capture({event: "speech_end", turn_id: id}),
    turn: (id: number, text: string) => controller.capture({event: "turn", turn_id: id, text}),
    step: (id: string) => controller.stepStarted({assistantMessageID: id, sequence: ++sequence}),
    delta: (id: string, delta: string, ordinal = 0) =>
      controller.textDelta({assistantMessageID: id, ordinal, delta, sequence: ++sequence}),
    final: (id: string, text: string, ordinal = 0) =>
      controller.textEnded({assistantMessageID: id, ordinal, text, sequence: ++sequence}),
  }
}

test("live turns submit once and speech begins before the completed response", async () => {
  const f = fixture()
  f.start(1)
  f.end(1)
  f.turn(1, "What changed?")
  await settled()
  assert.deepEqual(f.prompts, ["What changed?"])
  f.step("answer-1")
  f.delta("answer-1", "The first change is ready. ")
  await settled()
  assert.deepEqual(f.spoken, ["The first change is ready."])
  f.delta("answer-1", "And this is the last word")
  f.final("answer-1", "The first change is ready. And this is the last word")
  f.final("answer-1", "The first change is ready. And this is the last word")
  f.turn(1, "What changed?")
  f.controller.executionEnded(f.identity())
  await settled()
  assert.deepEqual(f.spoken, ["The first change is ready.", "And this is the last word"])
  assert.deepEqual(f.prompts, ["What changed?"])
  assert.equal(f.states.at(-1), "listening")
  await f.controller.stop()
})

test("delayed ASR cannot submit an earlier phrase while a newer utterance is still being spoken", async () => {
  const f = fixture()
  f.start(1); f.end(1); f.start(2)
  f.turn(1, "Use the blue")
  await settled()
  assert.deepEqual(f.prompts, [])
  f.end(2); f.turn(2, "button, please.")
  await settled()
  assert.deepEqual(f.prompts, ["Use the blue button, please."])
  await f.controller.stop()
})

test("out of order final transcripts preserve input order and empty turns do not send", async () => {
  const f = fixture()
  f.start(1); f.end(1); f.start(2); f.end(2)
  f.turn(2, "second")
  await settled()
  assert.deepEqual(f.prompts, [])
  f.turn(1, "first")
  await settled()
  assert.deepEqual(f.prompts, ["first second"])
  f.start(3); f.end(3); f.turn(3, "")
  await settled()
  assert.deepEqual(f.prompts, ["first second"])
  await f.controller.stop()
})

test("barge in clears current and queued audio, blocks stale text, and waits for model interruption", async () => {
  const interrupted = deferred()
  const played: string[] = []
  // Leave the first phrase playing so the second must be queued.
  const hold = deferred()
  let release!: () => void
  const g = fixture({
    speak: async text => { played.push(text); if (text === "First.") { release = () => hold.resolve(); await hold.promise } },
    stopSpeech: () => release?.(),
    interrupt: () => interrupted.promise,
  })
  g.start(1); g.end(1); g.turn(1, "Start")
  await settled()
  g.step("old")
  g.delta("old", "First. Second queued.")
  assert.deepEqual(played, ["First."])
  g.start(2)
  g.delta("old", " Stale after interrupt.")
  g.final("old", "First. Second queued. Stale after interrupt.")
  g.end(2); g.turn(2, "Actually, stop and explain that")
  await settled()
  assert.deepEqual(g.prompts, ["Start"])
  assert.deepEqual(played, ["First."])
  interrupted.resolve()
  await settled()
  assert.deepEqual(g.prompts, ["Start", "Actually, stop and explain that"])
  g.step("old")
  g.delta("old", "Still stale.")
  g.step("new")
  g.delta("new", "New response.")
  await settled()
  assert.deepEqual(played, ["First.", "New response."])
  await g.controller.stop()
})

test("OFF while admission is in flight interrupts only after admission and never sends late ASR", async () => {
  const admission = deferred()
  const actions: string[] = []
  const f = fixture({
    prompt: async text => { actions.push(`prompt:${text}`); await admission.promise; actions.push("admitted") },
    interrupt: async () => { actions.push("interrupt") },
  })
  f.start(1); f.end(1); f.turn(1, "Already submitting")
  await settled()
  f.start(2)
  const stopped = f.controller.stop()
  f.end(2); f.turn(2, "Keep these words in my draft")
  assert.deepEqual(actions, ["prompt:Already submitting"])
  assert.deepEqual(f.restored, ["Keep these words in my draft"])
  admission.resolve()
  await stopped
  f.step("late")
  f.delta("late", "Should not play.")
  assert.deepEqual(actions, ["prompt:Already submitting", "admitted", "interrupt"])
  assert.deepEqual(f.spoken, [])
})

test("switching OFF before the scheduled submission recovers finalized words without sending", async () => {
  const f = fixture()
  f.start(1); f.end(1); f.turn(1, "Do not send this")
  await f.controller.stop()
  assert.deepEqual(f.prompts, [])
  assert.deepEqual(f.restored, ["Do not send this"])
  f.turn(1, "Do not send this")
  assert.equal(f.restored.length, 1)
})

test("mute discards audio and unmute does not replay a stale speech backlog", async () => {
  const f = fixture()
  f.start(1); f.end(1); f.turn(1, "Explain")
  await settled()
  f.step("reply")
  f.controller.setMuted(true)
  f.delta("reply", "Do not speak this. A muted partial")
  f.controller.setMuted(false)
  f.delta("reply", " sentence ends. This is fresh.")
  f.final("reply", "Do not speak this. A muted partial sentence ends. This is fresh.")
  await settled()
  assert.deepEqual(f.spoken, ["This is fresh."])
  assert.equal(f.prompts.length, 1)
  await f.controller.stop()
})

test("Markdown fences and URL targets are not spoken, including fences split over deltas", async () => {
  const f = fixture()
  f.start(1); f.end(1); f.turn(1, "Show code")
  await settled()
  f.step("reply")
  f.delta("reply", "Before.\n``")
  f.delta("reply", "`python\nprint('secret code.')\n``")
  f.delta("reply", "`\nSee [the docs](https://example.test/path). After")
  f.final("reply", "Before.\n```python\nprint('secret code.')\n```\nSee [the docs](https://example.test/path). After")
  await settled()
  assert.deepEqual(f.spoken, ["Before.", "See the docs.", "After"])
  await f.controller.stop()
})

test("long punctuation-free responses stream bounded phrases and preserve the final word", async () => {
  const f = fixture()
  f.start(1); f.end(1); f.turn(1, "Explain")
  await settled()
  f.step("reply")
  const text = Array.from({length: 85}, (_, i) => `word${i}`).join(" ")
  f.delta("reply", text)
  await settled()
  assert.ok(f.spoken.length >= 2)
  f.final("reply", text)
  await settled()
  assert.equal(f.spoken.join(" "), text)
  assert.ok(f.spoken.every(phrase => phrase.length <= 180))
  await f.controller.stop()
})

test("admission failure recovers user words and prevents automatic retries", async () => {
  let attempts = 0
  const f = fixture({prompt: async () => { attempts++; throw new Error("Local model unavailable") }})
  f.start(1); f.end(1); f.turn(1, "Keep my question")
  await settled()
  assert.equal(attempts, 1)
  assert.deepEqual(f.restored, ["Keep my question"])
  assert.match(f.errors[0].message, /Local model unavailable/)
  assert.equal(f.states.at(-1), "error")
  f.start(2); f.end(2); f.turn(2, "Also keep this")
  await settled()
  assert.equal(attempts, 1)
  assert.deepEqual(f.restored, ["Keep my question", "Also keep this"])
})

test("interruption failure never admits a new turn into an uncertain running session", async () => {
  const f = fixture({interrupt: async () => { throw new Error("Could not interrupt local model") }})
  f.start(1); f.end(1); f.turn(1, "First question")
  await settled()
  f.start(2); f.end(2); f.turn(2, "Second question")
  await settled()
  assert.deepEqual(f.prompts, ["First question"])
  assert.deepEqual(f.restored, ["Second question"])
  assert.match(f.errors[0].message, /Could not interrupt/)
})

test("an uncertain dispatched admission is interrupted before OFF completes", async () => {
  const admission = deferred()
  const actions: string[] = []
  const f = fixture({
    prompt: () => admission.promise,
    interrupt: async () => { actions.push("interrupt") },
  })
  f.start(1); f.end(1); f.turn(1, "Possibly admitted")
  await settled()
  const stopped = f.controller.stop()
  admission.reject(Object.assign(new Error("Acknowledgement lost"), {code: "admission-uncertain"}))
  await stopped
  assert.deepEqual(actions, ["interrupt"])
  assert.deepEqual(f.restored, ["Possibly admitted"])
})

test("synchronous dispatch rejection recovers text without leaving OFF waiting on admission", async () => {
  const f = fixture({prompt: () => { throw new Error("Local configuration invalid") }})
  f.start(1); f.end(1); f.turn(1, "Recover me")
  await settled()
  await f.controller.stop()
  assert.deepEqual(f.restored, ["Recover me"])
  assert.equal(f.interrupts(), 0)
})

test("text from unknown steps and replayed events cannot leak into live speech", async () => {
  const f = fixture()
  f.start(1); f.end(1); f.turn(1, "Hello")
  await settled()
  f.delta("unrelated", "Do not speak.")
  f.step("reply")
  const event = {assistantMessageID: "reply", ordinal: 0, delta: "Speak once.", eventID: "same-event", sequence: 100}
  f.controller.textDelta(event)
  f.controller.textDelta(event)
  f.controller.textDelta({...event, sequence: 99, eventID: "older-event"})
  await settled()
  assert.deepEqual(f.spoken, ["Speak once."])
  f.controller.executionEnded({sequence: 90})
  f.controller.textEnded({assistantMessageID: "reply", ordinal: 0, text: "Speak once. Final word", sequence: 101})
  f.controller.executionEnded({sequence: 102})
  f.controller.textDelta({assistantMessageID: "reply", ordinal: 1, delta: "Late text.", sequence: 103})
  await settled()
  assert.deepEqual(f.spoken, ["Speak once.", "Final word"])
  await f.controller.stop()
})

test("a stalled player cannot accumulate an unbounded spoken backlog", async () => {
  const hold = deferred()
  const f = fixture({speak: () => hold.promise, stopSpeech: () => {}})
  f.start(1); f.end(1); f.turn(1, "Long answer")
  await settled()
  f.step("reply")
  f.delta("reply", "A fairly long phrase to read while synthesis runs. ".repeat(150))
  assert.match(f.errors[0].message, /speech fell behind/)
  assert.equal(f.states.at(-1), "error")
  hold.resolve()
  await f.controller.stop()
})

test("a host interruption cancels queued speech without flushing unfinished assistant text", async () => {
  const hold = deferred()
  let started = false
  const spoken: string[] = []
  const f = fixture({
    speak: async text => { spoken.push(text); started = true; await hold.promise },
    stopSpeech: () => { if (started) hold.resolve() },
  })
  f.start(1); f.end(1); f.turn(1, "Explain")
  await settled()
  f.step("reply")
  f.delta("reply", "First sentence. Queued sentence. Unfinished")
  f.controller.executionEnded({...f.identity(), outcome: "interrupted"})
  await settled()
  assert.deepEqual(spoken, ["First sentence."])
  assert.equal(f.states.at(-1), "listening")
  await f.controller.stop()
})
