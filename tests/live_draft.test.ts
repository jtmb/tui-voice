import { test } from "node:test"
import assert from "node:assert/strict"
import { LiveDraft, locateComposer, jsonLines } from "../live_draft"

function editor(initial = "") {
  let value = initial
  let selection: [number, number] = [value.length, value.length]
  return {
    get plainText() { return value },
    isDestroyed: false,
    hasSelection: () => false,
    getClipboardText() { return value },
    editBuffer: {measureText: (text: string) => text.length},
    setSelection(start: number, end: number) { selection = [start, end] },
    gotoBufferEnd() { selection = [value.length, value.length] },
    insertText(text: string) {
      value = value.slice(0, selection[0]) + text + value.slice(selection[1])
      selection = [selection[0] + text.length, selection[0] + text.length]
    },
    type(text: string) { value += text },
    edit(text: string) { value = text },
  }
}

test("live revisions replace only dictated text and preserve an existing draft and typed suffix", () => {
  const input = editor("Already typed.")
  const draft = new LiveDraft()
  assert.equal(draft.apply(input, "Turn on"), "written")
  input.type(" please")
  draft.apply(input, "Turn off the lights.")
  draft.apply(input, "Turn off the lights.")
  assert.equal(input.plainText, "Already typed. Turn off the lights. please")
})

test("hidden/recreated composer is not written, manual edits/sends are never overwritten", () => {
  const input = editor()
  const draft = new LiveDraft()
  assert.equal(draft.apply(null, "queued"), "blocked")
  draft.apply(input, "live")
  assert.equal(draft.apply(editor(), "not this chat"), "blocked")
  input.edit("I edited this")
  assert.equal(draft.apply(input, "final transcription"), "edited")
  assert.equal(input.plainText, "I edited this")
  input.edit("")
  assert.equal(draft.apply(input, "must not resurrect a sent draft"), "edited")
  assert.equal(input.plainText, "")
})

test("a remounted composer resumes only when the owned draft was preserved", () => {
  const old = editor("prefix")
  const draft = new LiveDraft()
  draft.apply(old, "live")
  old.isDestroyed = true
  assert.equal(draft.apply(editor("unrelated"), "final"), "blocked")
  const restored = editor(old.plainText)
  assert.equal(draft.apply(restored, "final"), "written")
  assert.equal(restored.plainText, "prefix final")
})

test("footer-anchored lookup excludes unrelated dialog editors and fails closed on ambiguity", () => {
  const input = editor()
  const dialog = editor()
  const root: any = {getChildren: () => [composer, dialog]}
  const marker: any = {getChildren: () => []}
  const composer: any = {parent: root, getChildren: () => [marker, input]}
  marker.parent = composer
  assert.equal(locateComposer(marker), input)
  composer.getChildren = () => [marker, input, editor()]
  assert.equal(locateComposer(marker), null)
  marker.isDestroyed = true
  assert.equal(locateComposer(marker), null)
})

test("partial JSON lines survive transport boundaries and sanitize control text before native insertion", () => {
  const events: any[] = []
  const consume = jsonLines(event => events.push(event))
  consume('{"event":"partial","text":"hel')
  consume('lo"}\n{"ok":true,"text":"final"}\n')
  assert.deepEqual(events, [{event: "partial", text: "hello"}, {ok: true, text: "final"}])
  const input = editor()
  new LiveDraft().apply(input, "\r\nhello\u001b[0m\nworld")
  assert.equal(input.plainText, "hello [0m world")
})
