import { test } from "node:test"
import assert from "node:assert/strict"
import { LiveDraft, locateComposer, jsonLines } from "../live_draft.ts"

function editor(initial = "") {
  let value = initial
  let selection: [number, number] = [value.length, value.length]
  return {
    get plainText() { return value },
    isDestroyed: false,
    hasSelection: (): boolean => false,
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

test("live revisions replace only dictated text and preserve an existing draft", () => {
  const input = editor("Already typed.")
  const draft = new LiveDraft()
  assert.equal(draft.apply(input, "Turn on"), "written")
  draft.apply(input, "Turn off the lights.")
  draft.apply(input, "Turn off the lights.")
  assert.equal(input.plainText, "Already typed. Turn off the lights.")
})

test("typing after the dictated text keeps it, and speech continues after what was typed", () => {
  const input = editor("Already typed.")
  const draft = new LiveDraft()
  draft.apply(input, "Turn on")
  input.type(" please")
  draft.apply(input, "Turn on the lights")
  assert.equal(input.plainText, "Already typed. Turn on please the lights")
  draft.apply(input, "Turn on the lights and the fan")
  assert.equal(input.plainText, "Already typed. Turn on please the lights and the fan")
})

test("typing before the dictated text keeps revising it in place", () => {
  const input = editor()
  const draft = new LiveDraft()
  draft.apply(input, "I want to describe")
  input.edit("FIX I want to describe")
  draft.apply(input, "I want to describe the page")
  draft.apply(input, "I want to describe the page today")
  assert.equal(input.plainText, "FIX I want to describe the page today")
})

test("fixing a word inside dictated text never stops dictation", () => {
  const input = editor()
  const draft = new LiveDraft()
  draft.apply(input, "I want to describe")
  input.edit("I wanted to describe")
  assert.equal(draft.apply(input, "I want to describe the new page"), "written")
  assert.equal(input.plainText, "I wanted to describe the new page")
  draft.apply(input, "I want to describe the new page today")
  assert.equal(input.plainText, "I wanted to describe the new page today")
})

test("repeated edits keep handing over without repeating words", () => {
  const input = editor()
  const draft = new LiveDraft()
  draft.apply(input, "one two")
  input.edit("1 two")
  draft.apply(input, "one two three four")
  input.edit("1 two three 4")
  draft.apply(input, "one two three four five six")
  assert.equal(input.plainText, "1 two three 4 five six")
})

test("a sent or cleared draft is not resurrected, but later speech still arrives", () => {
  const input = editor()
  const draft = new LiveDraft()
  draft.apply(input, "live")
  input.edit("")
  draft.apply(input, "live")
  assert.equal(input.plainText, "")
  draft.apply(input, "live more words")
  assert.equal(input.plainText, "more words")
})

test("a hidden composer or an open selection is not written", () => {
  const input = editor()
  const draft = new LiveDraft()
  assert.equal(draft.apply(null, "queued"), "blocked")
  draft.apply(input, "live")
  input.hasSelection = () => true
  assert.equal(draft.apply(input, "live text"), "blocked")
  assert.equal(input.plainText, "live")
  input.hasSelection = () => false
  draft.apply(input, "live text")
  assert.equal(input.plainText, "live text")
  assert.equal(draft.apply(editor(), "other composer"), "blocked")
})

test("a remounted composer resumes with the preserved draft or continues after a replaced one", () => {
  const old = editor("prefix")
  const draft = new LiveDraft()
  draft.apply(old, "live")
  old.isDestroyed = true
  const restored = editor(old.plainText)
  assert.equal(draft.apply(restored, "live final"), "written")
  assert.equal(restored.plainText, "prefix live final")
  restored.isDestroyed = true
  const replaced = editor("unrelated")
  draft.apply(replaced, "live final words")
  assert.equal(replaced.plainText, "unrelated words")
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
