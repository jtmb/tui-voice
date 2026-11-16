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

export class LiveDraft {
  private editor: any
  private prefix = ""
  private inserted = ""
  edited = false

  apply(editor: any, transcript: string): "written" | "blocked" | "edited" {
    if (this.edited) return "edited"
    if (!editor || editor.isDestroyed || editor.hasSelection?.()) return "blocked"
    const text = cleanTranscript(transcript)
    if (!this.editor) {
      if (!text) return "written"
      this.editor = editor
      this.prefix = editor.plainText
      if (this.prefix && !/\s$/.test(this.prefix)) this.prefix += " "
      editor.gotoBufferEnd()
      const addition = this.prefix.slice(editor.plainText.length) + text
      editor.insertText(addition)
      this.inserted = text
      return "written"
    }
    if (editor !== this.editor) {
      // A busy session/tab can remount its composer with the same saved draft.
      if (!this.editor.isDestroyed || !editor.plainText.startsWith(this.prefix + this.inserted)) return "blocked"
      this.editor = editor
    }
    const current = editor.plainText
    const owned = this.prefix + this.inserted
    // Preserve manual typing after the live region; never replace a user's
    // edits inside/before it, nor resurrect text cleared by a manual send.
    if (!current.startsWith(owned)) {
      this.edited = true
      return "edited"
    }
    if (text === this.inserted) return "written"
    const offset = (value: string) => editor.editBuffer.measureText(value)
    const cursor = editor.cursorOffset
    const start = offset(this.prefix)
    const end = offset(owned)
    editor.setSelection(start, end)
    editor.insertText(text)
    if (typeof cursor === "number") {
      const delta = offset(text) - offset(this.inserted)
      editor.cursorOffset = cursor >= end ? cursor + delta : Math.min(cursor, end + delta)
    }
    this.inserted = text
    return "written"
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
