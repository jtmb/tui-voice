// Follow opencode-ghost's footer-anchored composer lookup, never a global
// "first editor" search: dialogs and other sessions can also contain editors.
export function locateComposer(marker: any): any {
  if (!marker || marker.isDestroyed) return null
  const editors = (node: any): any[] => {
    if (node.isDestroyed) return []
    if (typeof node.getClipboardText === "function" &&
        typeof node.insertText === "function" && typeof node.plainText === "string") return [node]
    return (node.getChildren?.() ?? []).flatMap(editors)
  }
  for (let node = marker.parent; node?.parent; node = node.parent) {
    if (node.isDestroyed) return null
    const found = editors(node)
    if (found.length > 1) return null
    if (found.length === 1) return found[0]
  }
  return null
}

export function cleanTranscript(text: string) {
  return text.replace(/[\u0000-\u001f\u007f-\u009f]+/g, " ").trim()
}

const wordCount = (text: string) => text.split(/\s+/).filter(Boolean).length

// Writes a growing, revisable transcript into the native composer without ever
// overwriting what the user typed. The live region is the text this class
// inserted; if the user types around or inside it, that text becomes theirs and
// dictation carries on after it with only the words not yet shown. It never
// gives up for the rest of the turn.
export class LiveDraft {
  private editor: any
  private prefix = ""      // everything before the live region
  private inserted = ""    // text currently in the live region
  private placed = false   // whether the live region exists in the buffer
  private skip = 0         // transcript words already handed over to the user
  private lastText = ""    // last transcript applied, to count words at a hand-over

  apply(editor: any, transcript: string): "written" | "blocked" {
    if (!editor || editor.isDestroyed || editor.hasSelection?.()) return "blocked"
    const text = cleanTranscript(transcript)
    if (!this.editor) {
      if (!text) return "written"
      this.editor = editor
      this.beginRegion(editor, true)
    } else {
      if (editor !== this.editor) {
        // A busy session/tab can remount its composer. Two live editors would be
        // ambiguous, so only follow a replacement for a destroyed one.
        if (!this.editor.isDestroyed) return "blocked"
        this.editor = editor
      }
      this.reconcile(editor)
    }
    this.write(editor, this.visible(text))
    this.lastText = text
    return "written"
  }

  // Words of the transcript the user has not already received as their own text.
  private visible(text: string) {
    if (!this.skip) return text
    return text.split(/\s+/).filter(Boolean).slice(this.skip).join(" ")
  }

  // Start a fresh live region at the end of whatever the buffer holds now. Words
  // already shown in the previous region now belong to the user and are skipped.
  private beginRegion(editor: any, initial = false) {
    if (!initial && this.placed) this.skip = wordCount(this.lastText)
    const current: string = editor.plainText
    this.prefix = current && !/\s$/.test(current) ? current + " " : current
    this.inserted = ""
    this.placed = false
  }

  // Compare the buffer with what was inserted and adapt to the user's edits.
  private reconcile(editor: any) {
    const current: string = editor.plainText
    const owned = this.prefix + this.inserted
    if (current === owned) return
    if (!this.placed) return this.beginRegion(editor)
    const at = current.indexOf(owned)
    if (at < 0) return this.beginRegion(editor) // edited inside it, or cleared/sent
    // Region intact: typing before it just moves the anchor.
    this.prefix = current.slice(0, at + this.prefix.length)
    // Typing after it: carry on after the user's text rather than before it.
    if (current.length > at + owned.length) this.beginRegion(editor)
  }

  private write(editor: any, shown: string) {
    if (shown === this.inserted && (this.placed || !shown)) return
    const offset = (value: string) => editor.editBuffer.measureText(value)
    const cursor = editor.cursorOffset
    if (!this.placed) {
      if (!shown) return
      const current: string = editor.plainText
      const before = offset(current)
      editor.gotoBufferEnd()
      editor.insertText(this.prefix.slice(current.length) + shown)
      // Only the very first insertion moves the caret to the end; after a
      // hand-over the user is still typing wherever they were.
      if (this.skip && typeof cursor === "number" && cursor < before) editor.cursorOffset = cursor
      this.inserted = shown
      this.placed = true
      return
    }
    const owned = this.prefix + this.inserted
    const start = offset(this.prefix)
    const end = offset(owned)
    editor.setSelection(start, end)
    editor.insertText(shown)
    if (typeof cursor === "number") {
      const delta = offset(shown) - offset(this.inserted)
      editor.cursorOffset = cursor >= end ? cursor + delta : Math.min(cursor, end + delta)
    }
    this.inserted = shown
  }
}

// Streaming stdout can split a UTF-8 code point or JSON line at any boundary.
export function jsonLines(onEvent: (event: any) => void) {
  let pending = ""
  return (chunk: string) => {
    pending += chunk
    let end: number
    while ((end = pending.indexOf("\n")) >= 0) {
      const line = pending.slice(0, end)
      pending = pending.slice(end + 1)
      if (line.trim()) onEvent(JSON.parse(line))
    }
    if (pending.length > 1_000_000) throw new Error("Voice event exceeds its finite budget")
  }
}
