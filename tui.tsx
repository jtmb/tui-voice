import { Plugin, usePlugin } from "@opencode/plugin/tui"
import { createEffect, createSignal, For } from "solid-js"
import { spawn, type ChildProcess } from "node:child_process"
import { appendFileSync, mkdirSync, readFileSync, rmSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const HERE = dirname(fileURLToPath(import.meta.url))
const HELPER = join(HERE, "voice_helper.py")
const SPEECH_PROGRESS = "/tmp/opencode/tui-voice-speech.json"
const LOG_FILE = join(process.env.XDG_STATE_HOME || join(homedir(), ".local", "state"),
  "tui-voice", "voice.log")
const PYTHON = process.env.TUI_VOICE_PYTHON
  || "/home/brajam/repos/fire-tv-next/lulu-launcher/service/.venv/bin/python"

function logError(where: string, error: any, detail?: string) {
  try {
    mkdirSync(dirname(LOG_FILE), {recursive: true})
    const record: any = {
      time: new Date().toISOString(), level: "error", source: "plugin", where,
      message: String(error?.message ?? error ?? "unknown error"),
    }
    if (detail) record.detail = detail.slice(-2000)
    appendFileSync(LOG_FILE, JSON.stringify(record) + "\n")
  } catch {}
}

type Phase = "idle" | "listening" | "transcribing" | "thinking" | "speaking" | "error"
type Settings = { mic: string; voice: string; seconds: number; silence: number; threshold: number; gain: number; readAloud: boolean }

const [active, setActive] = createSignal(false)
const [phase, setPhase] = createSignal<Phase>("idle")
const [settingsOpen, setSettingsOpen] = createSignal(false)
let settings: Settings = { mic: "", voice: "heart", seconds: 60, silence: 1.5, threshold: -35, gain: 0, readAloud: true }
let updateSettings: ((mutation: (draft: Settings) => void) => Promise<void>) | undefined
let child: ChildProcess | null = null
let pendingDraft: {sessionID: string; text: string} | null = null
let pendingFlush = false
let speechQueue: Promise<void> = Promise.resolve()
let speechChild: ChildProcess | null = null
let speechState: {text: string; voice: string; startChunk: number} | null = null
let interruptedSpeech: {text: string; voice: string; startChunk: number} | null = null
let lastReadAloud: boolean | null = null
let lastSession = ""
let onToggle: () => void = () => {}
let onMute: () => void = () => {}

function phaseLabel() {
  return phase() === "idle" ? "Voice" : phase() === "transcribing" ? "Transcribing"
    : phase() === "thinking" ? "Thinking" : phase() === "speaking" ? "Speaking"
    : phase() === "error" ? "Try again" : "Listening"
}

function phaseColor() {
  return phase() === "listening" ? "#f4b65f" : phase() === "speaking" ? "#8ee0c8"
    : phase() === "transcribing" || phase() === "thinking" ? "#99e7ff"
    : phase() === "error" ? "#fa9e95" : "#3ca9e8"
}

function toast(context: any, message: string, variant: "info" | "success" | "warning" | "error" = "info") {
  context.ui.toast.show({message, variant, duration: 3500})
}

function helperPayload(args: string[], timeoutMs: number, track?: (child: ChildProcess | null) => void): Promise<any> {
  return new Promise((resolve, reject) => {
    if (!PYTHON) { reject(new Error("TUI_VOICE_PYTHON is not configured")); return }
    const childProcess = spawn(PYTHON, [HELPER, ...args], {stdio: ["ignore", "pipe", "pipe"]})
    child = childProcess
    track?.(childProcess)
    let stdout = ""
    let stderr = ""
    const timer = setTimeout(() => {
      childProcess.kill("SIGTERM")
      logError("helper timeout", new Error("Voice helper timed out"), args.join(" "))
      reject(new Error("Voice helper timed out"))
    }, timeoutMs)
    childProcess.stdout?.on("data", data => { stdout += String(data) })
    childProcess.stderr?.on("data", data => { stderr += String(data) })
    childProcess.on("error", error => {
      clearTimeout(timer)
      if (child === childProcess) child = null
      track?.(null)
      logError("helper spawn", error, args.join(" "))
      reject(error)
    })
    childProcess.on("close", code => {
      clearTimeout(timer)
      if (child === childProcess) child = null
      track?.(null)
      let payload: any = null
      try { payload = JSON.parse(stdout.trim().split("\n").at(-1) || "{}") } catch {}
      if (payload?.ok === false) {
        logError("helper failed", payload.error || "Voice helper failed", stderr.trim())
        reject(new Error(payload.error || "Voice helper failed")); return
      }
      if (code !== 0) {
        if (code !== null) logError("helper exit", new Error(`Voice helper exited ${code}`), stderr.trim())
        reject(new Error(stderr.trim() || `Voice helper exited ${code}`))
        return
      }
      if (!payload) {
        logError("helper output", new Error("Voice helper returned unreadable output"), stdout.slice(-2000))
        reject(new Error("Voice helper returned unreadable output")); return
      }
      resolve(payload)
    })
  })
}

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

function prewarmWorkers() {
  if (!PYTHON) return
  try {
    const worker = spawn(PYTHON, [HELPER, "warm", "--start"], {detached: true, stdio: "ignore"})
    worker.on("error", () => {})
    worker.unref()
  } catch {}
}

function stopVoice() {
  setActive(false)
  setPhase("idle")
  if (child) {
    try { child.kill("SIGTERM") } catch {}
    child = null
  }
}

function stopSpeech() {
  if (speechChild) {
    try { speechChild.kill("SIGTERM") } catch {}
    speechChild = null
  }
}

function placeholderText(node: any) {
  const value = node?.placeholder
  if (typeof value === "string") return value
  if (value && typeof value.plainText === "string") return value.plainText
  if (value && typeof value.toString === "function") return String(value)
  return ""
}

function findPromptEditor(renderer: any) {
  const stack = renderer?.root ? [renderer.root] : []
  const editors: any[] = []
  while (stack.length) {
    const node = stack.pop()
    if (node?.editBuffer && typeof node.focus === "function") editors.push(node)
    const children = node?.getChildren?.() ?? []
    for (const child of children) stack.push(child)
  }
  return editors.find(editor => /ask anything|message|prompt|reply/i.test(placeholderText(editor))) ?? editors[0] ?? null
}

function insertIntoPrompt(context: any, text: string) {
  const editor = findPromptEditor(context.renderer)
  if (!editor) return false
  editor.focus()
  const stdin = context.renderer.stdin
  if (stdin && typeof stdin.emit === "function") {
    stdin.emit("data", Buffer.from(text))
    return true
  }
  if (!editor.editBuffer) return false
  const current = editor.editBuffer.getText()
  const prefix = current && !/\s$/.test(current) ? " " : ""
  editor.editBuffer.setText(current + prefix + text)
  editor.requestRender?.()
  return true
}

async function sessionRunning(context: any, sessionID: string) {
  const messages = await context.client.session.context({sessionID})
  return (Array.isArray(messages) ? messages : []).some(
    message => message?.type === "assistant" && !message?.time?.completed,
  )
}

async function flushPending(context: any) {
  if (pendingFlush) return
  pendingFlush = true
  try {
    const end = Date.now() + 300000
    while (pendingDraft && Date.now() < end) {
      const draft = pendingDraft
      if (active()) { await delay(300); continue }
      const route = context.ui.router.current()
      if (route?.type !== "session" || route.sessionID !== draft.sessionID) {
        pendingDraft = null
        break
      }
      if (await sessionRunning(context, draft.sessionID)) { await delay(500); continue }
      await delay(80)
      const text = pendingDraft?.text
      pendingDraft = null
      if (!text) break
      if (insertIntoPrompt(context, text)) toast(context, "Inserted into the message box. Edit it, then send.", "success")
      else toast(context, "Could not find the message box.", "warning")
      break
    }
  } finally {
    pendingFlush = false
  }
}

function queueDraft(context: any, sessionID: string, text: string, running: boolean) {
  if (pendingDraft?.sessionID === sessionID) pendingDraft.text += " " + text
  else pendingDraft = {sessionID, text}
  if (running) toast(context, "Transcribed. Will insert when the reply finishes.", "info")
  void flushPending(context)
}

async function ensureSession(context: any) {
  let route = context.ui.router.current()
  if (route?.type === "session") return route
  const created: any = await context.client.session.create({ location: context.location })
  const sessionID = created?.data?.id ?? created?.id
  if (!sessionID) throw new Error("Could not create a chat session.")
  context.ui.router.navigate({type: "session", sessionID})
  await delay(250)
  route = context.ui.router.current()
  if (route?.type === "session") return route
  throw new Error("Could not open the new chat session.")
}

async function toggleVoice(context: any) {
  try {
    if (active()) {
      if (phase() === "listening" && child) {
        try { child.kill("SIGUSR1") } catch {}
        toast(context, "Finishing…", "info")
      } else {
        toast(context, "Working…", "info")
      }
      return
    }
    const route = await ensureSession(context)
    setActive(true)
    setPhase("listening")
    prewarmWorkers()
    toast(context, "Listening… speak now, then pause.", "info")
    const args = [
      "record",
      "--seconds", String(settings.seconds ?? 60),
      "--silence", String(settings.silence ?? 1.5),
      "--threshold", String(settings.threshold ?? -35),
      "--gain", String(settings.gain ?? 0),
    ]
    if (settings.mic) args.push("--mic", settings.mic)
    const recording = await helperPayload(args, 120000)
    if (!active()) return
    setPhase("transcribing")
    const heard = await helperPayload(["transcribe", "--wav", recording.path], 150000)
    if (!active()) return
    const text = String(heard.text || "").trim()
    if (!text) { toast(context, "I did not catch that. Try again.", "warning"); return }
    const running = await sessionRunning(context, route.sessionID)
    queueDraft(context, route.sessionID, text, running)
  } catch (error: any) {
    logError("dictation", error)
    if (active()) {
      stopVoice()
      setPhase("error")
      toast(context, error?.message || "Voice input failed.", "error")
    }
  } finally {
    if (active()) stopVoice()
  }
}

async function toggleMute(context: any) {
  const currently = readAloudEnabled()
  await saveSetting("readAloud", !currently)
  toast(context, currently ? "Read-aloud muted." : "Read-aloud on.", currently ? "info" : "success")
}

function interruptSpeech() {
  const state = speechState
  if (state) {
    let chunk = state.startChunk
    try {
      const progress = JSON.parse(readFileSync(SPEECH_PROGRESS, "utf8"))
      if (Number.isInteger(progress?.chunk)) chunk = progress.chunk
    } catch {}
    interruptedSpeech = {...state, startChunk: chunk}
  }
  stopSpeech()
  speechState = null
}

function resumeInterruptedSpeech(context: any) {
  if (!readAloudEnabled() || !interruptedSpeech) return
  const pending = interruptedSpeech
  interruptedSpeech = null
  speakReply(context, pending.text, {startChunk: pending.startChunk, voice: pending.voice})
}

function speakReply(context: any, text: string, options?: {startChunk?: number; voice?: string}) {
  const startChunk = Math.max(0, options?.startChunk ?? 0)
  const voice = options?.voice || settings.voice || "heart"
  prewarmWorkers()
  speechQueue = speechQueue.then(async () => {
    if (!readAloudEnabled()) return
    while (active()) await delay(250)
    try { rmSync(SPEECH_PROGRESS, {force: true}) } catch {}
    const state = {text, voice, startChunk}
    speechState = state
    try {
      await helperPayload([
        "say", "--text", text, "--voice", voice,
        "--start-chunk", String(startChunk),
        "--progress-file", SPEECH_PROGRESS,
      ], process.env.TUI_VOICE_TTS_PROVIDER?.trim().toLowerCase() === "migraphx" ? 600000 : 240000,
      process => { speechChild = process })
      interruptedSpeech = null
    } catch (error: any) {
      if (speechState === state) logError("read-aloud", error)
      // A mute/interrupt keeps interruptedSpeech for resume.
      if (speechState === state && readAloudEnabled()) {
        toast(context, error?.message || "Local read-aloud failed.", "error")
      }
    } finally {
      if (speechState === state) speechState = null
    }
  }).catch(() => {})
}

async function chooseMic(context: any) {
  try {
    const data = await helperPayload(["devices"], 30000)
    const devices: string[] = Array.isArray(data.devices) ? data.devices : []
    if (!devices.length) throw new Error("No microphone was found.")
    const choice = await context.ui.dialog.select({
      title: "Microphone",
      current: settings.mic || data.selected,
      options: devices.map(name => ({title: name, value: name})),
    })
    if (choice && updateSettings) {
      await updateSettings(draft => { draft.mic = String(choice) })
      toast(context, "Microphone set to " + choice, "success")
    }
  } catch (error: any) { logError("microphone list", error); toast(context, error?.message || "Could not list microphones.", "error") }
}

type SettingsRow =
  | {kind: "section"; title: string}
  | {
      kind: "item"
      id: string
      label: string
      description: string
      value: () => string
      adjust?: (direction: number) => void
      activate?: () => void
    }

const SILENCE_CHOICES = [0.8, 1.0, 1.2, 1.5, 2.0, 2.5]
const MAX_CHOICES = [15, 20, 30, 45, 60, 90]
const THRESHOLD_CHOICES = [-25, -30, -35, -40, -45]
const GAIN_CHOICES = [0, 3, 6, 9, 12, 15, 18]
const VOICE_PRESETS = [
  {id: "heart", name: "Heart"},
  {id: "velvet", name: "Velvet"},
  {id: "english", name: "English Rose"},
  {id: "portuguese", name: "Dora"},
]

function readAloudEnabled() {
  return settings.readAloud ?? true
}

function readAloudVoice() {
  return VOICE_PRESETS.find(voice => voice.id === (settings.voice || "heart"))?.name ?? "Heart"
}

function cycleVoice(direction: number) {
  const current = settings.voice || "heart"
  let index = VOICE_PRESETS.findIndex(voice => voice.id === current)
  if (index < 0) index = 0
  const next = VOICE_PRESETS[(index + direction + VOICE_PRESETS.length) % VOICE_PRESETS.length]
  void saveSetting("voice", next.id)
}

async function saveSetting(key: keyof Settings, value: string | number) {
  if (!updateSettings) return
  await updateSettings(draft => { (draft as any)[key] = value })
}

function cycleNumber(key: keyof Settings, choices: number[], fallback: number, direction: number) {
  const current = Number((settings as any)[key] ?? fallback)
  let index = choices.findIndex(value => Math.abs(value - current) < 0.001)
  if (index < 0) index = 0
  const next = choices[(index + direction + choices.length) % choices.length]
  void saveSetting(key, next)
}

function VoiceSettings(props: {onClose: () => void}) {
  const context = usePlugin()
  const theme = context.theme.surface("dialog")
  context.ui.dialog.set({size: "large", centered: true})
  const [selected, setSelected] = createSignal(1)
  const [devices, setDevices] = createSignal<string[]>([])
  const [testStatus, setTestStatus] = createSignal("")

  const silence = () => Number(settings.silence ?? 1.5)
  const maxSeconds = () => Number(settings.seconds ?? 60)
  const threshold = () => Number(settings.threshold ?? -35)
  const gain = () => Number(settings.gain ?? 0)
  const mic = () => settings.mic || "Automatic"

  async function selectMic() {
    try {
      const data = await helperPayload(["devices"], 30000)
      const list: string[] = Array.isArray(data.devices) ? data.devices : []
      setDevices(list)
      const options = [{title: "Automatic (best available input)", value: ""},
        ...list.map(name => ({title: name, value: name}))]
      const choice = await context.ui.dialog.select({
        title: "Microphone",
        current: settings.mic || "",
        options,
      })
      if (choice !== undefined) await saveSetting("mic", String(choice))
    } catch (error: any) {
      logError("microphone select", error)
      setTestStatus(error?.message || "Could not list microphones.")
    }
  }

  function cycleMic(direction: number) {
    const options = ["", ...devices()]
    const current = settings.mic || ""
    let index = options.indexOf(current)
    if (index < 0) index = 0
    const next = options[(index + direction + options.length) % options.length]
    void saveSetting("mic", next)
  }

  async function testMicrophone() {
    setTestStatus("Recording 2 s…")
    try {
      const args = ["record", "--seconds", "2", "--silence", "0", "--gain", String(gain())]
      if (settings.mic) args.push("--mic", settings.mic)
      const result = await helperPayload(args, 30000)
      const level = Number(result.rms || 0)
      const label = level < 20 ? "silent" : level < 100 ? "quiet" : level < 1000 ? "ok" : "strong"
      setTestStatus(`Input level ${Math.round(level)} · ${label} · peak ${result.peak ?? 0}`)
      try { rmSync(result.path, {force: true}) } catch {}
    } catch (error: any) {
      logError("microphone test", error)
      setTestStatus(error?.message || "Microphone test failed.")
    }
  }

  async function resetDefaults() {
    if (!updateSettings) return
    await updateSettings(draft => {
      draft.mic = ""
      draft.seconds = 60
      draft.silence = 1.5
      draft.threshold = -35
      draft.gain = 0
      draft.readAloud = true
      draft.voice = "heart"
    })
    setTestStatus("")
    toast(context, "Defaults restored.", "success")
    setSelected(1)
  }

  const rows = () => {
    const list: SettingsRow[] = [
      {kind: "section", title: "TIMING"},
      {
        kind: "item", id: "silence", label: "Finish after a pause",
        description: "Default 1.5 s avoids cutting between words or thoughts. Lower it only if you want faster turns.",
        value: () => `${silence().toFixed(1)} s`,
        adjust: direction => cycleNumber("silence", SILENCE_CHOICES, 1.5, direction),
      },
      {
        kind: "item", id: "seconds", label: "Maximum turn",
        description: "Hard stop if silence is never detected. Normal turns end on a pause; keep this long so a long sentence is not cut off.",
        value: () => `${maxSeconds()} s`,
        adjust: direction => cycleNumber("seconds", MAX_CHOICES, 60, direction),
      },
      {kind: "section", title: "MICROPHONE"},
      {
        kind: "item", id: "mic", label: "Input",
        description: "Automatic chooses the real input before virtual devices. Enter opens the full list.",
        value: mic,
        adjust: cycleMic,
        activate: selectMic,
      },
      {
        kind: "item", id: "threshold", label: "Silence threshold",
        description: "Higher values like -25 dB treat more room noise as silence. Lower values ignore only very quiet rooms.",
        value: () => `${threshold()} dB`,
        adjust: direction => cycleNumber("threshold", THRESHOLD_CHOICES, -35, direction),
      },
      {
        kind: "item", id: "gain", label: "Input gain",
        description: "Boosts a quiet microphone. Try 6–12 dB if speech is soft; avoid clipping loud input.",
        value: () => gain() === 0 ? "Off" : `+${gain()} dB`,
        adjust: direction => cycleNumber("gain", GAIN_CHOICES, 0, direction),
      },
      {
        kind: "item", id: "test", label: "Test microphone",
        description: "Records 2 seconds and reports the level. It does not transcribe or send.",
        value: () => testStatus() || "Run test",
        activate: testMicrophone,
      },
      {kind: "section", title: "READ ALOUD"},
      {
        kind: "item", id: "readAloud", label: "Read replies aloud",
        description: "Speaks the final assistant reply through the Windows speakers. Turn it off for silent chat.",
        value: () => readAloudEnabled() ? "On" : "Off",
        adjust: () => { void saveSetting("readAloud", !readAloudEnabled()) },
        activate: () => { void saveSetting("readAloud", !readAloudEnabled()) },
      },
      {
        kind: "item", id: "voice", label: "Read-aloud voice",
        description: "Local Kokoro voice used for spoken replies. Preview is not required to change it.",
        value: readAloudVoice,
        adjust: cycleVoice,
        activate: () => cycleVoice(1),
      },
      {
        kind: "item", id: "testSpeech", label: "Test read-aloud",
        description: "Speaks a sample. Use the footer speaker button to mute and unmute; it resumes the sample.",
        value: () => "Play sample",
        activate: () => {
          if (!readAloudEnabled()) {
            setTestStatus("Enable Read replies aloud first.")
            return
          }
          speakReply(context, "This is a local voice test. Read-aloud is speaking through the Windows speakers. Mute it with the speaker button, then unmute to resume.")
        },
      },
      {kind: "section", title: "BEHAVIOR"},
      {
        kind: "item", id: "reset", label: "Reset to defaults",
        description: "1.5 s pause, 60 s maximum, -35 dB threshold, no gain, automatic microphone.",
        value: () => "Restore",
        activate: resetDefaults,
      },
    ]
    return list
  }

  const items = () => rows().filter(row => row.kind === "item") as Extract<SettingsRow, {kind: "item"}>[]
  const current = () => {
    const all = rows()
    let index = selected()
    if (index < 0 || index >= all.length || all[index].kind === "section") {
      index = all.findIndex(row => row.kind === "item")
    }
    return all[index]
  }

  function move(direction: number) {
    const all = rows()
    let index = selected()
    for (let step = 0; step < all.length; step++) {
      index = (index + direction + all.length) % all.length
      if (all[index].kind === "item") { setSelected(index); return }
    }
  }

  function adjust(direction: number) {
    const row = current()
    if (row?.kind === "item" && row.adjust) row.adjust(direction)
  }

  function activate() {
    const row = current()
    if (row?.kind !== "item") return
    if (row.activate) row.activate()
    else row.adjust?.(1)
  }

  function back() {
    props.onClose()
  }

  context.keymap.layer(() => ({
    mode: "global",
    priority: 20,
    commands: [
      {id: "tui.voice.settings.up", title: "Up", bind: "up", run: () => move(-1)},
      {id: "tui.voice.settings.down", title: "Down", bind: "down", run: () => move(1)},
      {id: "tui.voice.settings.left", title: "Decrease", bind: "left", run: () => adjust(-1)},
      {id: "tui.voice.settings.right", title: "Increase", bind: "right", run: () => adjust(1)},
      {id: "tui.voice.settings.enter", title: "Choose", bind: "return", run: () => activate()},
      {id: "tui.voice.settings.back", title: "Back", bind: "escape", run: () => back()},
    ],
    bindings: [
      "tui.voice.settings.up", "tui.voice.settings.down",
      "tui.voice.settings.left", "tui.voice.settings.right",
      "tui.voice.settings.enter", "tui.voice.settings.back",
    ],
  }))

  return (
    <box flexDirection="column" width="100%" backgroundColor={theme.background.raised.base}
      border borderStyle="rounded" borderColor={theme.border.base}
      paddingLeft={2} paddingRight={2} paddingTop={1} paddingBottom={1}>
        <text fg={theme.text.base}>Voice settings</text>
        <text fg={theme.text.muted} wrapMode="word">
          Local dictation types into the message box. Nothing is sent automatically.
        </text>
        <box height={1} />
        <For each={rows()}>{(row, index) => row.kind === "section" ? (
          <box flexDirection="column" paddingTop={index() === 0 ? 0 : 1}>
            <text fg={theme.text.faint}>{row.title}</text>
          </box>
        ) : (
          <box flexDirection="column">
            <box flexDirection="row" paddingLeft={1} paddingRight={1}
              backgroundColor={selected() === index() ? theme.background.action.primary.focused : undefined}
              onMouseDown={() => setSelected(index())}>
              <text fg={selected() === index() ? theme.text.action.primary.focused : theme.text.faint}>
                {selected() === index() ? "▸ " : "  "}
              </text>
              <text fg={selected() === index() ? theme.text.action.primary.focused : theme.text.muted}>{row.label}</text>
              <box flexGrow={1} />
              {row.adjust ? (
                <box flexDirection="row">
                  <box onMouseDown={() => row.adjust?.(-1)}><text fg={theme.text.muted}>‹ </text></box>
                  <text fg={theme.text.base}>{row.value()}</text>
                  <box onMouseDown={() => row.adjust?.(1)}><text fg={theme.text.muted}> ›</text></box>
                </box>
              ) : (
                <box onMouseDown={() => row.activate?.()}>
                  <text fg={selected() === index() ? theme.text.action.primary.focused : theme.text.base}>{row.value()}</text>
                </box>
              )}
            </box>
            {selected() === index() ? (
              <box paddingLeft={4} paddingRight={2}>
                <text fg={theme.text.faint} wrapMode="word">{row.description}</text>
              </box>
            ) : null}
          </box>
        )}</For>
        <box height={1} />
        <box flexDirection="row" justifyContent="space-between" paddingTop={1}>
          <text fg={theme.text.faint}>↑↓ select · ←→ change</text>
          <box onMouseDown={() => props.onClose()}>
            <text fg={theme.text.muted}>esc close</text>
          </box>
        </box>
    </box>
  )
}

function openSettings(context: any) {
  setSettingsOpen(true)
  context.ui.dialog.set({size: "large", centered: true})
  context.ui.dialog.show(() => (
    <VoiceSettings onClose={() => { setSettingsOpen(false); context.ui.dialog.clear() }} />
  ))
}

function VoiceButton(props: {sessionID?: string; panel: any}) {
  let lastAction = 0
  const activate = () => {
    const now = Date.now()
    if (now - lastAction < 500) return
    lastAction = now
    onToggle()
  }
  createEffect(() => {
    if (props.sessionID && props.sessionID !== lastSession) {
      if (lastSession && active()) stopVoice()
      lastSession = props.sessionID
    }
  })
  const indicator = () =>
    phase() === "listening" ? {glyph: "●", color: "#f4b65f"}
      : phase() === "transcribing" ? {glyph: "◐", color: "#99e7ff"}
      : phase() === "error" ? {glyph: "✕", color: "#fa9e95"}
      : {glyph: "○", color: "#3ca9e8"}
  return (
    <box flexDirection="row" backgroundColor={props.panel}
      onMouseDown={activate} onMouseUp={activate}>
      <text fg={indicator().color} onMouseDown={activate} onMouseUp={activate}>
        {indicator().glyph}
      </text>
      <text fg={indicator().color} onMouseDown={activate} onMouseUp={activate}>{"🎙\uFE0E"}</text>
    </box>
  )
}

function MuteButton(props: {panel: any; theme: any}) {
  let lastAction = 0
  const activate = () => {
    const now = Date.now()
    if (now - lastAction < 500) return
    lastAction = now
    onMute()
  }
  return (
    <box flexDirection="row" backgroundColor={props.panel}
      onMouseDown={activate} onMouseUp={activate}>
      <text fg={readAloudEnabled() ? props.theme.text?.base : props.theme.text?.muted}
        onMouseDown={activate} onMouseUp={activate}>
        {readAloudEnabled() ? "🔊\uFE0E" : "🔇\uFE0E"}
      </text>
    </box>
  )
}

export default Plugin.define({
  id: "tui.voice",
  async setup(context) {
    const [stored, update] = context.storage.store<Settings>("tui-voice", {
      initial: {mic: "", voice: "heart", seconds: 60, silence: 1.5, threshold: -35, gain: 0, readAloud: true},
    })
    const current = stored as Settings
    if ((Number(current.seconds) || 60) < 15 || (Number(current.silence) || 1.5) < 0.8) {
      await update(draft => { draft.seconds = 60; draft.silence = 1.5 })
    }
    settings = stored as Settings
    updateSettings = update
    onToggle = () => { void toggleVoice(context) }
    onMute = () => { void toggleMute(context) }
    prewarmWorkers()
    context.ui.toast.show({title: "tui-voice", message: "Ready · F9 or Ctrl+Alt+V", variant: "info", duration: 3000})

    createEffect(() => {
      const enabled = readAloudEnabled()
      if (lastReadAloud === null) { lastReadAloud = enabled; return }
      if (enabled === lastReadAloud) return
      lastReadAloud = enabled
      if (enabled) resumeInterruptedSpeech(context)
      else interruptSpeech()
    })

    const stopText = context.data.on("session.text.ended", (event: any) => {
      const route = context.ui.router.current()
      if (route?.type !== "session" || route.sessionID !== event?.data?.sessionID) return
      const text = String(event?.data?.text || "").trim()
      if (text) speakReply(context, text)
    })

    const offFooter = context.ui.slot({
      append: "prompt.footer",
      render: input => {
        if (settingsOpen()) return <box height={1} />
        const theme = context.theme.surface("dialog")
        const panel = theme.background?.raised?.max
        return (
          <box flexDirection="row" height={1}>
            <text fg={panel}>▐</text>
            <box flexDirection="row" height={1} backgroundColor={panel}>
              <VoiceButton sessionID={input.sessionID} panel={panel} />
              <text> </text>
              <text fg={theme.background?.raised?.base}>│</text>
              <MuteButton panel={panel} theme={theme} />
            </box>
            <text fg={panel}>▌</text>
          </box>
        )
      },
    })

    context.keymap.layer(() => ({
      mode: "global",
      commands: [
        {
          id: "tui.voice.toggle",
          title: "Voice: Dictate into message",
          group: "Voice",
          bind: "f9",
          palette: true,
          slash: {name: "dictate", aliases: ["voice"]},
          run: () => toggleVoice(context),
        },
        {
          id: "tui.voice.toggle.alt",
          title: "Voice: Dictate into message (Ctrl+Alt+V)",
          group: "Voice",
          bind: "ctrl+alt+v",
          palette: false,
          run: () => toggleVoice(context),
        },
        {
          id: "tui.voice.mic",
          title: "Voice: Choose microphone",
          group: "Voice",
          palette: true,
          run: () => chooseMic(context),
        },
        {
          id: "tui.voice.mute",
          title: "Voice: Toggle reply sound",
          group: "Voice",
          bind: "ctrl+alt+m",
          palette: true,
          run: () => toggleMute(context),
        },
        {
          id: "tui.voice.mute.alt",
          title: "Voice: Toggle reply sound (F10)",
          group: "Voice",
          bind: "f10",
          palette: false,
          run: () => toggleMute(context),
        },
        {
          id: "tui.voice.settings",
          title: "Voice: Settings",
          group: "Voice",
          bind: "ctrl+alt+s",
          palette: true,
          run: () => openSettings(context),
        },
      ],
    }))

    return () => {
      stopText()
      offFooter()
      stopSpeech()
      stopVoice()
    }
  },
})
