export type QaDraftStage = "preparing" | "intent" | "planning" | "retrieving" | "reranking" | "grading" | "drafting" | "retrying" | "finalizing"
export type QaDraftOperation = "draft" | "polish" | "revise"
export interface QaDraftProgress { stage: QaDraftStage; operation?: QaDraftOperation; count?: number }

const stages = new Set<QaDraftStage>(["preparing", "intent", "planning", "retrieving", "reranking", "grading", "drafting", "retrying", "finalizing"])
const operations = new Set<QaDraftOperation>(["draft", "polish", "revise"])

/** Only public pipeline stages reach the UI; arbitrary provider text stays out. */
function progressValue(value: unknown): QaDraftProgress | null {
  if (!value || typeof value !== "object" || !("stage" in value) || !stages.has(value.stage as QaDraftStage)) return null
  const progress: QaDraftProgress = { stage: value.stage as QaDraftStage }
  if ("operation" in value && operations.has(value.operation as QaDraftOperation)) progress.operation = value.operation as QaDraftOperation
  if ("count" in value && typeof value.count === "number" && Number.isInteger(value.count) && value.count >= 0) progress.count = value.count
  return progress
}

/** Frame-based SSE: handles network boundaries, UTF-8 and server keepalives. */
export async function readQaDraftStream<T>(
  stream: ReadableStream<Uint8Array>, onProgress: (progress: QaDraftProgress) => void,
  parseResult: (value: unknown) => T, signal?: AbortSignal,
): Promise<T> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = "", event = "", lines: string[] = []
  const abort = () => { void reader.cancel().catch(() => {}) }
  signal?.addEventListener("abort", abort, { once: true })
  function dispatch(): { result: T } | undefined {
    const name = event, data = lines.join("\n")
    event = ""; lines = []
    if (!["progress", "result", "error"].includes(name) || !data) return
    let value: unknown
    try { value = JSON.parse(data) } catch { throw new Error("qa_invalid_stream") }
    if (name === "error") throw new Error(value && typeof value === "object" && "code" in value && typeof value.code === "string" ? value.code : "qa_generation_failed")
    if (name === "result") return { result: parseResult(value) }
    const progress = progressValue(value)
    if (progress) onProgress(progress)
  }
  function line(raw: string) {
    if (!raw) return dispatch()
    if (raw.startsWith(":")) return
    const colon = raw.indexOf(":")
    const field = colon < 0 ? raw : raw.slice(0, colon)
    const value = colon < 0 ? "" : raw.slice(colon + 1).replace(/^ /, "")
    if (field === "event") event = value
    else if (field === "data") lines.push(value)
  }
  try {
    signal?.throwIfAborted()
    while (true) {
      const { done, value } = await reader.read()
      signal?.throwIfAborted()
      buffer += decoder.decode(value, { stream: !done })
      let newline: number
      while ((newline = buffer.indexOf("\n")) >= 0) {
        signal?.throwIfAborted()
        const raw = buffer.slice(0, newline).replace(/\r$/, "")
        buffer = buffer.slice(newline + 1)
        const complete = line(raw)
        if (complete) return complete.result
      }
      if (done) {
        if (buffer) line(buffer.replace(/\r$/, ""))
        const complete = dispatch()
        if (complete) return complete.result
        throw new Error("qa_incomplete_stream")
      }
    }
  } finally {
    signal?.removeEventListener("abort", abort)
    void reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}

/** Coalesce true events; an idle pipeline never invents a new stage. */
export function createQaProgressSampler(publish: (progress: QaDraftProgress) => void) {
  let latest: QaDraftProgress | null = null
  let stopped = false
  const timer = setInterval(() => {
    if (!latest) return
    const value = latest
    latest = null
    publish(value)
  }, 500)
  return {
    update(progress: QaDraftProgress) { if (!stopped) latest = progress },
    stop() { stopped = true; latest = null; clearInterval(timer) },
  }
}
