/** Line-based SSE parser for POST /api/sessions/{id}/messages.

Matches the existing Chat / Quick Chat protocol: emit as soon as a
``data:`` line arrives (do not wait for a blank line). Incomplete
trailing lines stay in the buffer across chunks.
*/

export type SessionSseMessage = {
  event: string
  data: Record<string, unknown>
}

export function createSessionSseParser() {
  let buffer = ""
  let eventType = ""

  function push(chunk: string): SessionSseMessage[] {
    const out: SessionSseMessage[] = []
    buffer += chunk
    const lines = buffer.split("\n")
    buffer = lines.pop() ?? ""

    for (const raw of lines) {
      const line = raw.trimEnd()
      if (line.startsWith("event: ")) {
        eventType = line.slice(7).trim()
      } else if (line.startsWith("data: ") && eventType) {
        try {
          const data = JSON.parse(line.slice(6)) as Record<string, unknown>
          out.push({ event: eventType, data })
        } catch {
          // skip malformed JSON; keep reading
        }
        eventType = ""
      }
    }
    return out
  }

  return { push }
}

export async function* iterateSessionSse(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<SessionSseMessage> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  const parser = createSessionSseParser()
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      for (const msg of parser.push(decoder.decode(value, { stream: true }))) {
        yield msg
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export type SessionMessagePayload = {
  content: string
  thinking?: boolean
  collections?: string[]
  provider_id?: string
  model?: string
  web_search_enabled?: boolean
  mode?: string
}

export function postSessionMessage(
  sessionId: string,
  payload: SessionMessagePayload,
  signal?: AbortSignal,
): Promise<Response> {
  return fetch(`/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  })
}
