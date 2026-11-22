import { Plugin, usePlugin } from "@opencode/plugin/tui"
import { createEffect, createSignal, For, onCleanup } from "solid-js"
import { spawn, execFileSync, type ChildProcess } from "node:child_process"
import { appendFileSync, closeSync, mkdirSync, openSync, readFileSync, rmSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { readLastReply } from "./read_last"
import { jsonLines, LiveDraft, locateComposer } from "./live_draft"
import { createOpenCodeLiveAdapter } from "./opencode_live"
import { LiveConversationMode, type LiveCapture } from "./live_mode"

const HERE = dirname(fileURLToPath(import.meta.url))
const HELPER = join(HERE, "voice_helper.py")
const SPEECH_DIR = join(process.env.XDG_RUNTIME_DIR || "/tmp", "tui-voice")
const LOG_DIR = process.env.TUI_VOICE_LOG_DIR ||
  join(process.env.XDG_STATE_HOME || join(homedir(), ".local", "state"), "tui-voice")
const LOG_FILE = join(LOG_DIR, "voice.log")
const SETUP_LOG = join(LOG_DIR, "setup.log")
// The helper must run in the interpreter that owns the local speech stack.
// Resolution lives in voice_config.py so the TUI, helper and workers agree:
// TUI_VOICE_PYTHON, then the configured stack's venv, then python3.
function helperPython(): string {
  if (process.env.TUI_VOICE_PYTHON) return process.env.TUI_VOICE_PYTHON
  try {
    const resolved = execFileSync("python3", [join(HERE, "voice_config.py"), "--python"], {
      encoding: "utf8", timeout: 2000, stdio: ["ignore", "pipe", "ignore"],
    }).trim()
    if (resolved) return resolved
  } catch {}
  return "python3"
}
// The stack's interpreter can appear while the TUI is open (first-run install),
// so it is resolved per use, not once at startup. A short cache keeps rapid
// spawns cheap.
let cachedPython = ""
let cachedPythonAt = 0
function pythonPath(): string {
  if (process.env.TUI_VOICE_PYTHON) return process.env.TUI_VOICE_PYTHON
  const now = Date.now()
  if (!cachedPython || now - cachedPythonAt > 1500) {
    cachedPython = helperPython()
    cachedPythonAt = now
  }
  return cachedPython
}

// True while this TUI's own background installer is provisioning the stack.
let speechInstalling = false
const NEEDS_STACK = new Set(["dictate", "listen", "say", "transcribe", "warm"])

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
type Settings = { mic: string; voice: string; threshold: number; gain: number; readAloud: boolean; idleMinutes?: number; liveSilence?: number }

// How long the loaded voice models may sit unused before they are unloaded to
// free memory. 0 = keep them loaded. They reload on the next use, and dictation
// says so while that happens.
const IDLE_CHOICES = [0, 1, 5, 10, 30, 60]
const DEFAULT_IDLE_MINUTES = 30
const idleMinutes = () => Number(settings.idleMinutes ?? DEFAULT_IDLE_MINUTES)
const idleLabel = (minutes: number) =>
  minutes === 0 ? "Never" : minutes === 60 ? "1 hour" : `${minutes} min`

class VoiceCancelled extends Error {
  constructor() {
    super("Voice cancelled")
    this.name = "VoiceCancelled"
  }
}

const [active, setActive] = createSignal(false)
const [phase, setPhase] = createSignal<Phase>("idle")
const [settingsOpen, setSettingsOpen] = createSignal(false)
const [liveEnabled, setLiveEnabled] = createSignal(false)
const [livePreparing, setLivePreparing] = createSignal(false)
let liveMode: LiveConversationMode | null = null
let liveAttempt = 0
let liveSpeechChild: ChildProcess | null = null
let settings: Settings = { mic: "", voice: "heart", threshold: -35, gain: 6, readAloud: true }
let updateSettings: ((mutation: (draft: Settings) => void) => Promise<void>) | undefined
type DictationTurn = {
  id: number
  sessionID: string
  child: ChildProcess | null
  ready: boolean
  listening: boolean
  loading: boolean
  cancelled: boolean
  stopRequested: boolean
  finished: Promise<void>
  finish(): void
}
let dictationTurn: DictationTurn | null = null
let dictationCounter = 0
type Draft = {sessionID: string; text: string; writer: LiveDraft; final: boolean}
let pendingDrafts: Draft[] = []
const composers = new Map<string, {marker: any; normal: () => boolean}>()
let lastMicrophone = ""
let lastDictation: {sessionID: string; text: string} | null = null
let pendingFlush = false
let speechQueue: Promise<void> = Promise.resolve()
let speechChild: ChildProcess | null = null
let speechGeneration = 0
let speechJobCounter = 0
let speechState: {text: string; voice: string; startChunk: number; progressFile: string} | null = null
let interruptedSpeech: {text: string; voice: string; startChunk: number} | null = null
// A stop or timeout is an expected exit, not a helper failure to log.
const expectedExit = new WeakSet<ChildProcess>()
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

function toast(context: any, message: string, variant: "info" | "success" | "warning" | "error" = "info",
  duration = 3500) {
  context.ui.toast.show({message, variant, duration})
}

// Helpers inherit the user's idle-unload choice. Without an explicit choice the
// environment (TUI_VOICE_WARM_IDLE) or the built-in default applies.
function helperEnv() {
  if (settings.idleMinutes === undefined) return process.env
  return {...process.env, TUI_VOICE_WARM_IDLE: String(idleMinutes() * 60)}
}

function helperPayload(args: string[], timeoutMs: number | null, track?: (child: ChildProcess | null) => void,
  onEvent?: (event: any) => void): Promise<any> {
  if (speechInstalling && NEEDS_STACK.has(args[0])) {
    return Promise.reject(new Error("Speech models are still installing. Try again when “Local speech is ready” appears."))
  }
  return new Promise((resolve, reject) => {
    const childProcess = spawn(pythonPath(), [HELPER, ...args], {stdio: ["ignore", "pipe", "pipe"], env: helperEnv()})
    track?.(childProcess)
    let stdout = ""
    let stderr = ""
    const timer = timeoutMs === null ? undefined : setTimeout(() => {
      expectedExit.add(childProcess)
      childProcess.kill("SIGTERM")
      logError("helper timeout", new Error("Voice helper timed out"), args.join(" "))
      reject(new Error("Voice helper timed out"))
    }, timeoutMs)
    const events = jsonLines(event => { if (event.event) onEvent?.(event) })
    childProcess.stdout?.setEncoding("utf8")
    childProcess.stdout?.on("data", data => {
      stdout += data
      if (onEvent) {
        // Streaming helpers can stay open for hours. Retain only the last
        // complete JSON line and the unfinished line, not the whole session.
        const previousLine = stdout.lastIndexOf("\n", stdout.length - 2)
        if (previousLine >= 0) stdout = stdout.slice(previousLine + 1)
        try { events(data) } catch (error) {
          expectedExit.add(childProcess)
          childProcess.kill("SIGTERM")
          reject(error)
        }
      }
    })
    childProcess.stderr?.on("data", data => { stderr = (stderr + String(data)).slice(-8192) })
    childProcess.on("error", error => {
      clearTimeout(timer)
      track?.(null)
      logError("helper spawn", error, args.join(" "))
      reject(error)
    })
    childProcess.on("close", code => {
      clearTimeout(timer)
      track?.(null)
      if (expectedExit.has(childProcess)) {
        reject(new VoiceCancelled()); return
      }
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

// Start (or restore after an idle unload) the warm workers that are needed.
// Dictation loads its own recognizer and reports it, so it does not call this.
function prewarmWorkers(kind: "asr" | "tts" | "both" = "both") {
  if (speechInstalling) return
  try {
    const worker = spawn(pythonPath(), [HELPER, "warm", "--start", "--kind", kind],
      {detached: true, stdio: "ignore", env: helperEnv()})
    worker.on("error", () => {})
    worker.unref()
  } catch {}
}

// Run a long installer detached, with its output in setup.log instead of nowhere.
function background(command: string, args: string[], onExit?: (code: number | null) => void) {
  try {
    mkdirSync(LOG_DIR, {recursive: true})
    const fd = openSync(SETUP_LOG, "a")
    const child = spawn(command, args, {detached: true, stdio: ["ignore", fd, fd]})
    closeSync(fd)
    child.on("error", error => {
      logError("background spawn", error, args.join(" "))
      onExit?.(null)
    })
    child.on("exit", code => onExit?.(code))
    child.unref()
  } catch (error) {
    logError("background spawn", error)
    onExit?.(null)
  }
}

const shellWords = (words?: string[]) => (words ?? []).join(" ")

// Detect the host OS once per session, install the allow-listed audio packages
// through its own package manager, and provision the local speech models when
// they are absent. Disabled with TUI_VOICE_AUTO_INSTALL=0; the helper reports
// the exact command when it cannot install without privileges.
async function prepareDependencies(context: any) {
  try {
    const report = await helperPayload(["doctor"], 15000)
    const autoInstall = report?.auto_install !== false
    const missing: string[] = report?.required_missing ?? []
    const audioFix = shellWords(report?.fix) || `${pythonPath()} ${HELPER} doctor --install`
    if (missing.length) {
      if (!autoInstall) {
        toast(context, `Missing ${missing.join(", ")}: run ${audioFix}`, "warning")
      } else {
        toast(context, `Preparing local audio dependencies (${missing.join(", ")})…`, "info")
        background(pythonPath(), [HELPER, "doctor", "--install"], code => {
          if (code === 0) return
          logError("audio install", new Error(`doctor --install exited ${code}`))
          toast(context, `Could not install ${missing.join(", ")}: run ${audioFix} (see ${SETUP_LOG})`, "warning")
        })
      }
    }
    const speech = report?.speech
    if (!speech || speech.ready !== false) return
    const speechFix = shellWords(speech.fix) || `${pythonPath()} ${join(HERE, "setup_stack.py")} --install`
    if (speech.supported === false) {
      toast(context, `Speech models cannot be installed here: ${speech.reason}`, "warning")
    } else if (!autoInstall) {
      toast(context, `Speech models are not installed: run ${speechFix}`, "warning")
    } else {
      const megabytes = Math.round((speech.download_bytes ?? 0) / 1e6)
      const size = megabytes > 0 ? `about ${megabytes} MB of models plus Python packages` : "Python packages"
      toast(context, `Installing local speech models (one time, ${size})…`, "info")
      speechInstalling = true
      background(pythonPath(), [join(HERE, "setup_stack.py"), "--install", "--json"], async code => {
        speechInstalling = false
        cachedPython = "" // the stack's own interpreter exists now
        let ready = false
        try { ready = (await helperPayload(["doctor"], 15000))?.speech?.ready === true } catch {}
        if (ready) {
          prewarmWorkers()
          toast(context, "Local speech is ready", "success")
          return
        }
        if (code === 0) return // another installer holds the lock and will finish
        logError("speech install", new Error(`setup_stack exited ${code}`))
        toast(context, `Speech install failed (see ${SETUP_LOG}); retry with ${speechFix}`, "warning")
      })
    }
  } catch (error) {
    logError("dependency check", error)
  }
}

function ownsDictation(turn: DictationTurn) {
  return dictationTurn === turn && !turn.cancelled
}

function beginDictation(): DictationTurn {
  let finish = () => {}
  const finished = new Promise<void>(resolve => { finish = resolve })
  const turn: DictationTurn = {
    id: ++dictationCounter, sessionID: "", child: null, ready: false, listening: false, loading: false,
    cancelled: false, stopRequested: false, finished, finish,
  }
  dictationTurn = turn
  setActive(true)
  setPhase("thinking")
  return turn
}

function requestDictationStop(turn: DictationTurn) {
  turn.stopRequested = true
  const child = turn.child
  // The helper explicitly acknowledges that its signal handlers are installed.
  if (!child || !turn.ready || !ownsDictation(turn)) return
  try { child.kill("SIGUSR1") } catch {}
}

function cancelDictation() {
  const turn = dictationTurn
  if (!turn) return
  turn.cancelled = true
  if (turn.child) {
    expectedExit.add(turn.child)
    try { turn.child.kill("SIGTERM") } catch {}
    turn.child = null
  }
  dictationTurn = null
  setActive(false)
  setPhase("idle")
}

function finishDictation(turn: DictationTurn) {
  turn.finish()
  if (dictationTurn !== turn) return
  dictationTurn = null
  setActive(false)
  setPhase("idle")
}

function stopVoice() {
  cancelDictation()
}

function stopSpeech() {
  speechGeneration += 1
  if (speechChild) {
    expectedExit.add(speechChild)
    try { speechChild.kill("SIGTERM") } catch {}
    speechChild = null
  }
}

function speechProgressFile() {
  mkdirSync(SPEECH_DIR, {recursive: true, mode: 0o700})
  return join(SPEECH_DIR, `speech-${process.pid}-${++speechJobCounter}.json`)
}

function writeDraft(context: any, draft: Draft) {
  const route = context.ui.router.current()
  if (route?.type !== "session" || route.sessionID !== draft.sessionID || settingsOpen()) return "blocked"
  const entry = composers.get(draft.sessionID)
  if (!entry?.normal() || context.keymap.mode.current() !== "base") return "blocked"
  return draft.writer.apply(locateComposer(entry.marker), draft.text)
}

async function flushPending(context: any) {
  if (pendingFlush) return
  pendingFlush = true
  try {
    const end = Date.now() + 300000
    while (pendingDrafts.length && Date.now() < end) {
      for (const draft of [...pendingDrafts]) {
        const result = writeDraft(context, draft)
        if (result === "written" && draft.final) {
          pendingDrafts = pendingDrafts.filter(item => item !== draft)
        }
      }
      await delay(150)
    }
  } finally {
    pendingFlush = false
  }
}

function queueDraft(context: any, draft: Draft) {
  if (!pendingDrafts.includes(draft)) pendingDrafts.push(draft)
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
  // F9/the original microphone always means Dictation. Switching away from
  // Live drains its unfinished words into a draft before opening another mic.
  if (livePreparing() || liveMode?.busy) {
    liveAttempt += 1
    setLivePreparing(false)
    await liveMode?.stop()
  }
  if (active()) {
    const turn = dictationTurn
    if (turn && !turn.stopRequested) {
      requestDictationStop(turn)
      toast(context, "Finishing…", "info")
    } else {
      toast(context, "Working…", "info")
    }
    return
  }
  const turn = beginDictation()
  let draft: Draft | null = null
  try {
    const route = await ensureSession(context)
    if (!ownsDictation(turn)) return
    turn.sessionID = route.sessionID
    draft = {sessionID: turn.sessionID, text: "", writer: new LiveDraft(), final: false}
    toast(context, "Opening microphone…", "info")
    const args = [
      "dictate",
      "--threshold", String(settings.threshold ?? -35),
      "--gain", String(settings.gain ?? 0),
    ]
    const mic = settings.mic || lastMicrophone
    if (mic) args.push("--mic", mic)
    const capture = () => helperPayload(args, null, process => {
      if (!ownsDictation(turn)) return
      turn.child = process
      turn.ready = false
    }, event => {
      if (!ownsDictation(turn) || !draft) return
      if (event.event === "starting") {
        turn.ready = true
        if (turn.stopRequested) requestDictationStop(turn)
      } else if (event.event === "listening") {
        lastMicrophone = event.device || ""
        turn.listening = true
        setPhase("listening")
        // One message: a second toast would replace this one before it is read.
        toast(context, turn.loading
          ? "Microphone on. Loading the voice model: keep talking, your words will appear when it is ready."
          : "Microphone on. Press F9 or the mic again to stop.", "info", turn.loading ? 6000 : 3500)
      } else if (event.event === "model") {
        if (event.state === "loading") {
          turn.loading = true
          if (turn.listening) {
            toast(context, "Loading the voice model: keep talking, your words will appear when it is ready.", "info", 6000)
          }
        } else if (event.state === "ready") {
          turn.loading = false
          toast(context, "Voice model ready.", "success")
        } else if (event.state === "failed") {
          turn.loading = false
          toast(context, `Fast voice model unavailable (${event.error || "unknown error"}). Using the slower fallback.`,
            "warning", 6000)
        }
      } else if (event.event === "transcribing") {
        setPhase("transcribing")
      } else if (event.event === "partial" && typeof event.text === "string") {
        draft.text = event.text
        lastDictation = {sessionID: turn.sessionID, text: event.text}
        queueDraft(context, draft)
      }
    })
    let heard: any
    try {
      heard = await capture()
    } catch (error) {
      // A device can disappear between turns. Only retry an automatic cached
      // choice, before any transcript, never an explicitly selected device.
      if (!mic || settings.mic || draft.text || !ownsDictation(turn) || turn.stopRequested) throw error
      lastMicrophone = ""
      args.splice(args.indexOf("--mic"), 2)
      heard = await capture()
    }
    if (!ownsDictation(turn)) return
    const text = String(heard.text || "").trim()
    if (!text) { toast(context, "I did not catch that. Try again.", "warning"); return }
    draft.text = text
    lastDictation = {sessionID: turn.sessionID, text}
    queueDraft(context, draft)
    if (writeDraft(context, draft) === "blocked") {
      toast(context, "Transcribed. Draft will update when its message box returns.", "info")
    }
  } catch (error: any) {
    if (error?.name === "VoiceCancelled" || turn.cancelled || dictationTurn !== turn) return
    // A stop that arrives before the helper installs SIGUSR1 can terminate it
    // with a signal; the user asked to cancel this turn, not to see an error.
    if (turn.stopRequested && /exited null|returned no audio/.test(String(error?.message || ""))) return
    logError("dictation", error)
    lastMicrophone = ""
    cancelDictation()
    setPhase("error")
    toast(context, error?.message || "Voice input failed.", "error")
  } finally {
    if (draft) {
      draft.final = true
      if (draft.text && !turn.cancelled) queueDraft(context, draft)
    }
    finishDictation(turn)
  }
}

function stopLiveSpeech() {
  const child = liveSpeechChild
  if (!child) return
  liveSpeechChild = null
  expectedExit.add(child)
  try { child.kill("SIGTERM") } catch {}
}

function liveCapture(deliver: (event: any) => void): LiveCapture {
  let child: ChildProcess | null = null
  let ready = false
  let stopRequested = false
  let cancelled = false
  const requestStop = () => {
    stopRequested = true
    if (child && ready) { try { child.kill("SIGUSR1") } catch {} }
  }
  const result: LiveCapture = {
    done: Promise.resolve(),
    stop: requestStop,
    cancel: () => {
      cancelled = true
      if (child) {
        expectedExit.add(child)
        try { child.kill("SIGTERM") } catch {}
      }
    },
  }
  const args = ["listen", "--threshold", String(settings.threshold ?? -35),
    "--gain", String(settings.gain ?? 0), "--silence", String(settings.liveSilence ?? 0.8)]
  if (settings.mic) args.push("--mic", settings.mic)
  result.done = helperPayload(args, null, process => {
    child = process
    if (child && cancelled) {
      expectedExit.add(child)
      try { child.kill("SIGTERM") } catch {}
    }
  }, event => {
    if (event.event === "starting") {
      ready = true
      if (stopRequested) requestStop()
    }
    deliver(event)
  })
  return result
}

function configureLiveMode(context: any) {
  let attached = true
  const adapter = createOpenCodeLiveAdapter(context, {
    allowPrivateNetwork: process.env.TUI_VOICE_LIVE_ALLOW_PRIVATE === "1",
  })
  liveMode = new LiveConversationMode(adapter, {
    warm: () => prewarmWorkers("tts"),
    listen: liveCapture,
    speak: async text => {
      let ownChild: ChildProcess | null = null
      try {
        await helperPayload(["say", "--text", text, "--voice", settings.voice || "heart"],
          process.env.TUI_VOICE_TTS_PROVIDER?.trim().toLowerCase() === "migraphx" ? 600000 : 120000,
          child => {
            if (child) { ownChild = child; liveSpeechChild = child }
            else if (liveSpeechChild === ownChild) liveSpeechChild = null
          })
      } finally {
        if (liveSpeechChild === ownChild) liveSpeechChild = null
      }
    },
    stopSpeech: stopLiveSpeech,
  }, {
    active: value => { if (attached) setLiveEnabled(value) },
    state: value => { if (attached) setPhase(value) },
    error: error => {
      if (!attached) return
      logError("live conversation", error)
      toast(context, String((error as any)?.message || error || "Live conversation failed."), "error", 7000)
    },
    restoreDraft: (sessionID, text) => {
      if (!attached || !text.trim()) return
      queueDraft(context, {sessionID, text, writer: new LiveDraft(), final: true})
      lastDictation = {sessionID, text}
    },
    captureEvent: event => {
      if (!attached) return
      if (event.event === "listening") {
        lastMicrophone = event.device || ""
        toast(context, "Live conversation on. Speak naturally; Ctrl+Alt+L stops it. Use headphones to avoid echo.", "success", 6000)
      } else if (event.event === "model" && event.state === "loading") {
        toast(context, "Loading the local recognizer. The microphone is buffering your words.", "info", 6000)
      } else if (event.event === "model" && event.state === "failed") {
        toast(context, "The local recognizer could not load. Live conversation needs the speech stack to be ready.", "warning", 6000)
      }
    },
  })
  liveMode.setMuted(!readAloudEnabled())
  const ownedMode = liveMode
  return () => {
    attached = false
    setLiveEnabled(false)
    void ownedMode.stop(true)
  }
}

async function toggleLive(context: any) {
  if (livePreparing() || liveMode?.busy) {
    liveAttempt += 1
    setLivePreparing(false)
    const stopping = liveMode?.stop()
    toast(context, "Live conversation off. Finishing any unsent words into the draft.", "info")
    await stopping
    return
  }
  const attempt = ++liveAttempt
  setLivePreparing(true)
  try {
    const dictation = dictationTurn
    if (dictation) {
      requestDictationStop(dictation)
      toast(context, "Finishing dictation into the draft before starting Live conversation…", "info")
      await dictation.finished
    }
    if (attempt !== liveAttempt) return
    const route = await ensureSession(context)
    if (attempt !== liveAttempt) return
    stopSpeech()
    speechState = null
    interruptedSpeech = null
    await liveMode?.start(route.sessionID)
  } catch (error) {
    logError("start live conversation", error)
    toast(context, String((error as any)?.message || error), "error")
  } finally {
    if (attempt === liveAttempt) setLivePreparing(false)
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
      const progress = JSON.parse(readFileSync(state.progressFile, "utf8"))
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

function speakReply(context: any, text: string, options?: {startChunk?: number; voice?: string; sessionID?: string}) {
  const startChunk = Math.max(0, options?.startChunk ?? 0)
  const voice = options?.voice || settings.voice || "heart"
  const generation = speechGeneration
  const progressFile = speechProgressFile()
  const remaining = Math.max(1, Math.ceil(text.length / 420) - startChunk)
  const base = process.env.TUI_VOICE_TTS_PROVIDER?.trim().toLowerCase() === "migraphx"
    ? 600000 : 240000
  const timeout = Math.min(1800000, Math.max(base, remaining * 60000))
  prewarmWorkers("tts")
  speechQueue = speechQueue.then(async () => {
    const sameSession = () => !options?.sessionID || (
      context.ui.router.current()?.type === "session" &&
      context.ui.router.current()?.sessionID === options.sessionID)
    if (!readAloudEnabled() || generation !== speechGeneration) return
    while (active()) {
      if (generation !== speechGeneration || !sameSession()) return
      await delay(250)
    }
    // Mute or teardown may have happened while dictation was finishing.
    if (!readAloudEnabled() || generation !== speechGeneration || !sameSession()) return
    try { rmSync(progressFile, {force: true}) } catch {}
    const state = {text, voice, startChunk, progressFile}
    speechState = state
    try {
      await helperPayload([
        "say", "--text", text, "--voice", voice,
        "--start-chunk", String(startChunk),
        "--progress-file", progressFile,
      ], timeout, process => { if (speechState === state) speechChild = process })
      interruptedSpeech = null
    } catch (error: any) {
      if (error?.name !== "VoiceCancelled" && speechState === state) {
        logError("read-aloud", error)
        let chunk = startChunk
        try {
          const progress = JSON.parse(readFileSync(progressFile, "utf8"))
          if (Number.isInteger(progress?.chunk)) chunk = progress.chunk
        } catch {}
        // Keep a resume point instead of losing a long reply to the deadline.
        interruptedSpeech = {text, voice, startChunk: chunk}
        if (readAloudEnabled()) {
          toast(context, `${error?.message || "Local read-aloud failed."} Mute and unmute to resume.`, "error")
        }
      }
    } finally {
      if (speechState === state) speechState = null
      try { rmSync(progressFile, {force: true}) } catch {}
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

async function saveSetting(key: keyof Settings, value: string | number | boolean) {
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

let idleApplyTimer: ReturnType<typeof setTimeout> | undefined

// Save the choice, then tell any running workers (debounced while cycling).
function chooseIdle(context: any, direction: number) {
  const current = idleMinutes()
  let index = IDLE_CHOICES.indexOf(current)
  if (index < 0) index = IDLE_CHOICES.indexOf(DEFAULT_IDLE_MINUTES)
  const next = IDLE_CHOICES[(index + direction + IDLE_CHOICES.length) % IDLE_CHOICES.length]
  void saveSetting("idleMinutes", next).then(() => {
    if (idleApplyTimer) clearTimeout(idleApplyTimer)
    idleApplyTimer = setTimeout(async () => {
      try {
        await helperPayload(["warm", "--set-idle", String(idleMinutes() * 60)], 30000)
        toast(context, idleMinutes() === 0
          ? "Voice models stay loaded."
          : `Voice models unload after ${idleLabel(idleMinutes())} without use.`, "success")
      } catch (error: any) {
        logError("idle setting", error)
        toast(context, `Could not update running voice models: ${error?.message || error}`, "warning", 5000)
      }
    }, 500)
  })
}

function VoiceSettings(props: {onClose: () => void}) {
  const context = usePlugin()
  const theme = context.theme.surface("dialog")
  context.ui.dialog.set({size: "large", centered: true})
  const [selected, setSelected] = createSignal(1)
  const [devices, setDevices] = createSignal<string[]>([])
  const [testStatus, setTestStatus] = createSignal("")

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

  async function cycleMic(direction: number) {
    let list = devices()
    if (!list.length) {
      try {
        const data = await helperPayload(["devices"], 30000)
        list = Array.isArray(data.devices) ? data.devices : []
        setDevices(list)
      } catch (error: any) {
        logError("microphone list", error)
        setTestStatus(error?.message || "Could not list microphones.")
        return
      }
    }
    const options = ["", ...list]
    const current = settings.mic || ""
    let index = options.indexOf(current)
    if (index < 0) index = 0
    const next = options[(index + direction + options.length) % options.length]
    await saveSetting("mic", next)
  }

  async function testMicrophone() {
    if (active() || liveMode?.busy) {
      setTestStatus("Stop dictation or Live conversation before testing the microphone.")
      return
    }
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
      draft.threshold = -35
      draft.gain = 6
      draft.readAloud = true
      draft.voice = "heart"
      draft.idleMinutes = DEFAULT_IDLE_MINUTES
      draft.liveSilence = 0.8
    })
    chooseIdle(context, 0)
    setTestStatus("")
    toast(context, "Defaults restored.", "success")
    setSelected(1)
  }

  const rows = () => {
    const list: SettingsRow[] = [
      {kind: "section", title: "MICROPHONE"},
      {
        kind: "item", id: "mic", label: "Input",
        description: "Automatic chooses the real input before virtual devices. Enter opens the full list.",
        value: mic,
        adjust: cycleMic,
        activate: selectMic,
      },
      {
        kind: "item", id: "threshold", label: "Speech threshold",
        description: "Controls which audio is sent to recognition, not when recording stops. Lower it for quieter speech.",
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
          if (liveMode?.busy) {
            setTestStatus("Stop Live conversation before playing a read-aloud sample.")
            return
          }
          if (!readAloudEnabled()) {
            setTestStatus("Enable Read replies aloud first.")
            return
          }
          speakReply(context, "This is a local voice test. Read-aloud is speaking through the Windows speakers. Mute it with the speaker button, then unmute to resume.")
        },
      },
      {kind: "section", title: "LIVE CONVERSATION"},
      {
        kind: "item", id: "live", label: "Live conversation",
        description: "Optional hands-free chat with your selected local model. Replies stream aloud and stop when you speak. Ctrl+Alt+L toggles it; use headphones.",
        value: () => livePreparing() ? "Starting…" : liveEnabled() ? "On" : "Off",
        adjust: () => { void toggleLive(context) },
        activate: () => { void toggleLive(context) },
      },
      {
        kind: "item", id: "liveSilence", label: "Pause before replying",
        description: "Live mode only: silence that ends a spoken turn. Increase this if it answers during your pauses. Applies the next time Live starts.",
        value: () => `${settings.liveSilence ?? 0.8} s`,
        adjust: direction => cycleNumber("liveSilence", [0.5, 0.8, 1.2, 1.8, 2.5], 0.8, direction),
      },
      {kind: "section", title: "MEMORY"},
      {
        kind: "item", id: "idle", label: "Unload voice models when idle",
        description: "Frees memory after this long without dictation or read-aloud. Models reload on the next use and dictation tells you while it loads. Never keeps them loaded.",
        value: () => idleLabel(idleMinutes()),
        adjust: direction => chooseIdle(context, direction),
        activate: () => chooseIdle(context, 1),
      },
      {kind: "section", title: "BEHAVIOR"},
      {
        kind: "item", id: "reset", label: "Reset to defaults",
        description: "Manual on/off recording, -35 dB speech threshold, +6 dB gain, automatic microphone, models unload after 30 min idle.",
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
          Dictation keeps an editable draft. Optional Live conversation sends spoken turns automatically to your selected local model.
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
  let downAt = 0
  let lastUp = 0
  const activate = () => {
    const now = Date.now()
    if (now - lastAction < 500) return
    lastAction = now
    onToggle()
  }
  const down = () => {
    downAt = Date.now()
    activate()
  }
  const up = () => {
    const now = Date.now()
    if (downAt) { downAt = 0; lastUp = now; return }
    if (now - lastUp < 50) return
    lastUp = now
    activate()
  }
  createEffect(() => {
    if (props.sessionID && props.sessionID !== lastSession) {
      // Thinking/listening belong to the old session and are canceled. A turn
      // already transcribing is allowed to finish and queue into that session.
      if (lastSession && active() && phase() !== "transcribing") cancelDictation()
      lastSession = props.sessionID
    }
  })
  const indicator = () =>
    liveEnabled() && phase() === "listening" ? {glyph: "◎", color: "#8ee0c8"}
      : liveEnabled() && phase() === "speaking" ? {glyph: "◉", color: "#8ee0c8"}
      : phase() === "listening" ? {glyph: "●", color: "#f4b65f"}
      : phase() === "transcribing" || phase() === "thinking" ? {glyph: "◐", color: "#99e7ff"}
      : phase() === "error" ? {glyph: "✕", color: "#fa9e95"}
      : {glyph: "○", color: "#3ca9e8"}
  return (
    <box flexDirection="row" backgroundColor={props.panel}
      onMouseDown={down} onMouseUp={up}>
      <text fg={indicator().color} onMouseDown={down} onMouseUp={up}>
        {indicator().glyph}
      </text>
      <text fg={indicator().color} onMouseDown={down} onMouseUp={up}>{"🎙\uFE0E"}</text>
    </box>
  )
}

function MuteButton(props: {panel: any; theme: any}) {
  let lastAction = 0
  let downAt = 0
  let lastUp = 0
  const activate = () => {
    const now = Date.now()
    if (now - lastAction < 500) return
    lastAction = now
    onMute()
  }
  const down = () => {
    downAt = Date.now()
    activate()
  }
  const up = () => {
    const now = Date.now()
    if (downAt) { downAt = 0; lastUp = now; return }
    if (now - lastUp < 50) return
    lastUp = now
    activate()
  }
  return (
    <box flexDirection="row" backgroundColor={props.panel}
      onMouseDown={down} onMouseUp={up}>
      <text fg={readAloudEnabled() ? props.theme.text?.base : props.theme.text?.muted}
        onMouseDown={down} onMouseUp={up}>
        {readAloudEnabled() ? "🔊\uFE0E" : "🔇\uFE0E"}
      </text>
    </box>
  )
}

export default Plugin.define({
  id: "tui.voice",
  async setup(context) {
    const [stored, update] = context.storage.store<Settings>("tui-voice", {
      initial: {mic: "", voice: "heart", threshold: -35, gain: 6, readAloud: true},
    })
    settings = stored as Settings
    updateSettings = update
    onToggle = () => { void toggleVoice(context) }
    onMute = () => { void toggleMute(context) }
    const disposeLive = configureLiveMode(context)
    prewarmWorkers()
    void prepareDependencies(context)
    context.ui.toast.show({title: "tui-voice", message: "Ready · F9 or Ctrl+Alt+V", variant: "info", duration: 3000})

    createEffect(() => {
      const enabled = readAloudEnabled()
      if (lastReadAloud === null) { lastReadAloud = enabled; return }
      if (enabled === lastReadAloud) return
      lastReadAloud = enabled
      liveMode?.setMuted(!enabled)
      if (liveMode?.busy) return
      if (enabled) resumeInterruptedSpeech(context)
      else interruptSpeech()
    })

    createEffect(() => {
      const route = context.ui.router.current()
      liveMode?.checkRoute()
      if (route?.type === "session" &&
          pendingDrafts.some(item => item.sessionID === route.sessionID)) {
        void flushPending(context)
      }
    })

    const stopText = context.data.on("session.text.ended", (event: any) => {
      const route = context.ui.router.current()
      if (route?.type !== "session" || route.sessionID !== event?.data?.sessionID) return
      const text = String(event?.data?.text || "").trim()
      if (text && liveMode?.sessionID !== event?.data?.sessionID
          && !liveMode?.ownsMessage(event?.data?.assistantMessageID)) speakReply(context, text)
      if (pendingDrafts.some(item => item.sessionID === event?.data?.sessionID)) {
        void flushPending(context)
      }
    })

    const offFooter = context.ui.slot({
      append: "prompt.footer",
      render: input => {
        const [marker, setMarker] = createSignal<any>()
        createEffect(() => {
          const node = marker()
          const sessionID = input.sessionID
          if (!node || !sessionID) return
          composers.set(sessionID, {marker: node, normal: () => input.mode === "normal"})
          if (pendingDrafts.length) void flushPending(context)
          onCleanup(() => {
            if (composers.get(sessionID)?.marker === node) composers.delete(sessionID)
          })
        })
        if (settingsOpen()) return <box height={1} />
        const theme = context.theme.surface("dialog")
        const panel = theme.background?.raised?.max
        return (
          <box flexDirection="row" height={1} ref={setMarker}>
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
          id: "tui.voice.live",
          title: "Voice: Toggle Live conversation",
          group: "Voice",
          bind: "ctrl+alt+l",
          palette: true,
          slash: {name: "voice-live"},
          run: () => toggleLive(context),
        },
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
          id: "tui.voice.recover",
          title: "Voice: Insert last dictation text",
          group: "Voice",
          palette: true,
          slash: {name: "voice-text"},
          run: () => {
            const route = context.ui.router.current()
            if (route?.type !== "session" || lastDictation?.sessionID !== route.sessionID || !lastDictation?.text) {
              toast(context, "No dictation to recover in this chat.", "info")
              return
            }
            queueDraft(context, {...lastDictation, writer: new LiveDraft(), final: true})
          },
        },
        {
          id: "tui.voice.read-last",
          title: "Voice: Read last reply aloud",
          group: "Voice",
          palette: true,
          slash: {name: "read-last"},
          run: async () => {
            try {
              await readLastReply(context, {
                enabled: () => readAloudEnabled() && !liveMode?.busy,
                generation: () => speechGeneration,
                notify: message => toast(context, message, "info"),
                replay: (text, sessionID) => {
                  stopSpeech()
                  speechState = null
                  interruptedSpeech = null
                  speakReply(context, text, {sessionID})
                },
              })
            } catch (error: any) {
              logError("read-last", error)
              toast(context, "Could not load the last reply. Try /read-last again.", "error")
            }
          },
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
      liveAttempt += 1
      setLivePreparing(false)
      disposeLive()
      stopText()
      offFooter()
      stopSpeech()
      stopVoice()
      pendingDrafts = []
      composers.clear()
    }
  },
})
