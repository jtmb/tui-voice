import { test } from "node:test"
import assert from "node:assert/strict"
import { lastAssistantText, readLastReply } from "../read_last"

const reply = (created: number, text: string, completed: number | undefined = 10) => ({
  type: "assistant", time: {created, completed}, content: [{type: "text", text}],
})

test("selects newest completed assistant text, excluding tools/reasoning and unfinished replies", () => {
  const latest = reply(3, "First paragraph")
  latest.content.push({type: "reasoning", text: "private reasoning"},
    {type: "tool", text: "tool output"}, {type: "text", text: "Second paragraph"})
  assert.equal(lastAssistantText([
    latest, reply(1, "older"), {...reply(4, "user"), type: "user"},
    {...reply(5, "partial"), time: {created: 5}}, reply(6, "   "),
  ]), "First paragraph\n\nSecond paragraph")
  assert.equal(lastAssistantText([]), "")
})

function fixture() {
  let route: any = {type: "session", sessionID: "chat-a"}
  let enabled = true
  let generation = 0
  let messages: any[] = [reply(1, "Replay me")]
  let sync = async (_id: string) => {}
  const spoken: any[] = []
  const notices: string[] = []
  const context = {
    ui: {router: {current: () => route}},
    data: {session: {message: {
      sync: (id: string) => sync(id),
      list: (id: string) => { assert.equal(id, "chat-a"); return messages },
    }}},
  }
  const playback = {
    enabled: () => enabled, generation: () => generation,
    replay: (text: string, id: string) => spoken.push([text, id]),
    notify: (text: string) => notices.push(text),
  }
  return {context, playback, spoken, notices,
    route: (value: any) => { route = value },
    mute: () => { enabled = false },
    cancel: () => { generation++ },
    messages: (value: any[]) => { messages = value },
    sync: (value: typeof sync) => { sync = value },
  }
}

test("refreshes history and replays the latest completed reply in this chat", async () => {
  const f = fixture()
  f.sync(async id => { assert.equal(id, "chat-a"); f.messages([reply(2, "Fresh reply")]) })
  await readLastReply(f.context, f.playback)
  assert.deepEqual(f.spoken, [["Fresh reply", "chat-a"]])
})

test("empty history, no session and mute give feedback without speech", async () => {
  for (const setup of [f => f.messages([]), f => f.route({type: "home"}), f => f.mute()] as ((f: ReturnType<typeof fixture>) => void)[]) {
    const f = fixture()
    setup(f)
    await readLastReply(f.context, f.playback)
    assert.equal(f.spoken.length, 0)
    assert.equal(f.notices.length, 1)
  }
})

test("session switch, mute or cancellation during refresh prevents stale speech", async () => {
  for (const change of [f => f.route({type: "session", sessionID: "chat-b"}), f => f.mute(), f => f.cancel()] as ((f: ReturnType<typeof fixture>) => void)[]) {
    const f = fixture()
    f.sync(async () => change(f))
    await readLastReply(f.context, f.playback)
    assert.deepEqual(f.spoken, [])
  }
})

test("history failures propagate to the command's error handler without playback", async () => {
  const f = fixture()
  f.sync(async () => { throw new Error("offline") })
  await assert.rejects(readLastReply(f.context, f.playback), /offline/)
  assert.deepEqual(f.spoken, [])
})
