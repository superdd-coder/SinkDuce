import { useAppStore, type Source, type ThinkingIteration, type MetaInfo } from "@/stores/app-store"

interface StreamInfo {
  type: "info"
  provider?: string
  model?: string
  search_mode?: string
  mode?: string
  max_iterations?: number
}
interface StreamMeta {
  type: "meta"
  sources: Source[]
  iterations: number
  query_used: string
  provider?: string
  model?: string
  search_mode?: string
  mode?: string
  agent_active?: boolean
}
interface StreamToken {
  type: "token"
  content: string
}
interface StreamStep {
  type: "step"
  step: string
  content: string
  iteration?: number
}
interface StreamDetail {
  type: "detail"
  iteration: number
  content: string
}
interface StreamDone {
  type: "done"
}
interface StreamError {
  type: "error"
  content: string
}
type StreamEvent = StreamInfo | StreamMeta | StreamToken | StreamStep | StreamDetail | StreamDone | StreamError

export function useStreamChat() {
  const {
    addMessage,
    appendToLastMessage,
    setLastMessageSources,
    setLastMessageMetaInfo,
    setLastMessageThinkingSteps,
    finishLastMessage,
    setStreaming,
  } = useAppStore()

  const sendMessage = async (
    question: string,
    collections: string[],
    providerId?: string | null,
    model?: string | null,
    useAgent?: boolean,
    searchMode?: string,
    params?: { top_k?: number; use_reranker?: boolean; max_iterations?: number; min_score?: number; rerank_top_k?: number; sparse_llm_tokenize?: boolean },
  ) => {
    const userId = crypto.randomUUID()
    const assistantId = crypto.randomUUID()

    addMessage({ id: userId, role: "user", content: question })
    addMessage({ id: assistantId, role: "assistant", content: "", isStreaming: true })
    setStreaming(true)

    // Local accumulator for thinking steps (rebuilt on each update)
    const thinkingSteps: ThinkingIteration[] = []
    let metaInfo: MetaInfo = {}

    try {
      const body: Record<string, unknown> = {
        question,
        collection: collections[0] || "default",
        use_agent: useAgent !== false,
      }
      if (providerId) body.provider_id = providerId
      if (model) body.model = model
      if (collections.length > 1) body.collections = collections
      if (searchMode) body.search_mode = searchMode
      if (params?.top_k) body.top_k = params.top_k
      if (params?.rerank_top_k) body.rerank_top_k = params.rerank_top_k
      if (params?.use_reranker !== undefined) body.use_reranker = params.use_reranker
      if (params?.max_iterations) body.max_iterations = params.max_iterations
      if (params?.min_score !== undefined && params.min_score > 0) body.min_score = params.min_score
      if (params?.sparse_llm_tokenize !== undefined) body.sparse_llm_tokenize = params.sparse_llm_tokenize

      const res = await fetch("/api/query/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })

      if (!res.ok) {
        const text = await res.text()
        appendToLastMessage(`Error: ${res.status} - ${text}`)
        finishLastMessage()
        return
      }

      const reader = res.body!.getReader()
      const decoder = new TextDecoder()
      let buffer = ""

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split("\n")
        buffer = lines.pop() ?? ""

        for (const line of lines) {
          const trimmed = line.trim()
          if (!trimmed.startsWith("data: ")) continue

          let event: StreamEvent
          try {
            event = JSON.parse(trimmed.slice(6))
          } catch {
            continue
          }

          switch (event.type) {
            case "info": {
              metaInfo = {
                provider: event.provider,
                model: event.model,
                search_mode: event.search_mode,
                mode: event.mode,
                max_iterations: event.max_iterations,
              }
              setLastMessageMetaInfo(metaInfo)
              break
            }
            case "meta": {
              // Legacy meta event — extract provider info if no info event was received
              if (!metaInfo.provider && (event.provider || event.model)) {
                metaInfo = {
                  provider: event.provider,
                  model: event.model,
                  search_mode: event.search_mode,
                  mode: event.mode,
                }
                setLastMessageMetaInfo(metaInfo)
              }
              setLastMessageSources(event.sources)
              break
            }
            case "step": {
              const iterNum = event.iteration ?? 0
              // Mark ALL steps across ALL iterations as done first
              for (const g of thinkingSteps) {
                for (const s of g.steps) {
                  s.status = "done"
                }
              }
              // Find or create iteration group
              let group = thinkingSteps.find(g => g.iteration === iterNum)
              if (!group) {
                group = { iteration: iterNum, steps: [] }
                thinkingSteps.push(group)
                // Sort by iteration number (0 = decompose phase, goes last)
                thinkingSteps.sort((a, b) => {
                  if (a.iteration === 0) return 1
                  if (b.iteration === 0) return -1
                  return a.iteration - b.iteration
                })
              }
              // Add new active step
              group.steps.push({ label: event.content, status: "active" })
              // Trigger store update
              setLastMessageThinkingSteps([...thinkingSteps])
              break
            }
            case "detail": {
              const iterNum = event.iteration ?? 0
              const group = thinkingSteps.find(g => g.iteration === iterNum)
              if (group && group.steps.length > 0) {
                const lastStep = group.steps[group.steps.length - 1]
                if (!lastStep.details) lastStep.details = []
                lastStep.details.push(event.content)
                setLastMessageThinkingSteps([...thinkingSteps])
              }
              break
            }
            case "token":
              // Mark all thinking steps as done when answer tokens start
              for (const g of thinkingSteps) {
                for (const s of g.steps) {
                  s.status = "done"
                }
              }
              setLastMessageThinkingSteps([...thinkingSteps])
              appendToLastMessage(event.content)
              break
            case "done":
              finishLastMessage()
              return
            case "error":
              appendToLastMessage(`\n\n**Error:** ${event.content}`)
              finishLastMessage()
              return
          }
        }
      }
    } catch (err) {
      appendToLastMessage(`\n\n**Error:** ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      finishLastMessage()
    }
  }

  return { sendMessage }
}
