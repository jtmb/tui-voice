// OpenCode V2 messages contain typed content, not user/tool text parts.
export function lastAssistantText(messages: any[]) {
  let latest: any = null
  let text = ""
  for (const message of messages) {
    if (message?.type !== "assistant" || message.time?.completed == null) continue
    const value = (message.content ?? [])
      .filter((part: any) => part.type === "text" && typeof part.text === "string")
      .map((part: any) => part.text).join("\n\n").trim()
    if (!value || (latest && message.time.created < latest.time.created)) continue
    latest = message
    text = value
  }
  return text
}

export async function readLastReply(context: any, playback: {
  enabled: () => boolean
  generation: () => number
  replay: (text: string, sessionID: string) => void
  notify: (message: string) => void
}) {
  const route = context.ui.router.current()
  if (route?.type !== "session") {
    playback.notify("Open a chat to read its last reply.")
    return
  }
  if (!playback.enabled()) {
    playback.notify("Reply sound is muted. Turn it on with F10, then use /read-last.")
    return
  }
  const generation = playback.generation()
  await context.data.session.message.sync(route.sessionID)
  const current = context.ui.router.current()
  if (current?.type !== "session" || current.sessionID !== route.sessionID ||
      !playback.enabled() || generation !== playback.generation()) return
  const text = lastAssistantText(context.data.session.message.list(route.sessionID) ?? [])
  if (!text) {
    playback.notify("No completed assistant reply to read yet.")
    return
  }
  playback.replay(text, route.sessionID)
}
