// Load only in a disposable TUI via OPENCODE_CLI_CONFIG_CONTENT. This exercises
// the host's own native editor, not a second OpenTUI install or a model session.
import { Plugin } from "@opencode/plugin/tui"
import { BoxRenderable, TextareaRenderable } from "@opentui/core"
import { writeFileSync } from "node:fs"
import assert from "node:assert/strict"
import { LiveDraft, locateComposer } from "../live_draft"

export default Plugin.define({
  id: "tui.voice.native-probe",
  setup(context) {
    const result: any = {ok: false}
    const parent = new BoxRenderable(context.renderer, {id: "voice-probe", height: 5})
    try {
      if (process.env.TUI_VOICE_EXPECT_PLUGIN === "1") {
        const commands = context.keymap.commands()
        for (const id of ["tui.voice.toggle", "tui.voice.recover", "tui.voice.read-last"]) {
          assert.ok(commands.some((command: any) => command.id === id), `Missing command ${id}`)
        }
      }
      const editor: any = new TextareaRenderable(context.renderer, {
        id: "voice-draft", initialValue: "Existing 🌙 draft", height: 3,
      })
      editor.getClipboardText = () => editor.plainText
      const marker = new BoxRenderable(context.renderer, {id: "voice-marker", height: 1})
      context.renderer.root.add(parent)
      parent.add(editor)
      parent.add(marker)
      assert.equal(locateComposer(marker), editor)
      let submitted = 0
      editor.onSubmit = () => submitted++
      const draft = new LiveDraft()
      assert.equal(draft.apply(editor, "turn on"), "written")
      draft.apply(editor, "turn off the light.")
      editor.gotoBufferEnd()
      editor.insertText(" please")
      draft.apply(editor, "turn off the lights.")
      assert.equal(editor.plainText, "Existing 🌙 draft turn off the lights. please")
      assert.equal(editor.cursorOffset, editor.editBuffer.measureText(editor.plainText))
      assert.equal(submitted, 0)
      result.ok = true
      result.checks = ["native textarea", "Unicode prefix", "typed suffix", "footer lookup", "no submit"]
    } catch (error: any) {
      result.error = String(error?.stack || error)
    } finally {
      parent.destroy()
      writeFileSync(process.env.TUI_VOICE_NATIVE_RECEIPT!, JSON.stringify(result, null, 2))
    }
  },
})
