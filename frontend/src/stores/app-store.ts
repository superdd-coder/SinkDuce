import { create } from "zustand"

function loadPersisted<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(`rag_${key}`)
    return v !== null ? JSON.parse(v) : fallback
  } catch {
    return fallback
  }
}

export type SidebarView = "chat" | "database" | "recall" | "meeting" | "llm_provider"

export interface Source {
  text: string
  score: number
  metadata: Record<string, unknown>
}

export interface ThinkingStep {
  label: string
  status: "active" | "done"
  details?: string[]
  children?: ThinkingStep[]
}

export interface ThinkingIteration {
  iteration: number
  label?: string
  steps: ThinkingStep[]
}

// ── Clean thinking summary (from backend metadata / SSE) ──

export interface AqSummary {
  aq_id: string
  query: string
  variants: string[]
  variant_count: number
  final_chunks: number
  current_chunks: number
  has_gaps: boolean
}

export interface TaskSummary {
  task: string
  task_query: string
  aq_count: number
  aqs: AqSummary[]
  useful_chunks: number
}

export interface ThinkingSummary {
  aq_count: number
  task_count: number
  tasks: TaskSummary[]
  status?: string
}

export interface MetaInfo {
  provider?: string
  model?: string
  search_mode?: string
  mode?: string
  max_iterations?: number
}

export interface TimelineBlock {
  type: "thinking" | "tool"
  content?: string              // thinking text (accumulated)
  summary?: ThinkingSummary     // tool call summary
  isStreaming?: boolean         // still receiving
}

export interface Message {
  id: string
  role: "user" | "assistant"
  content: string
  sources?: Source[]
  isStreaming?: boolean
  thinkingSteps?: ThinkingIteration[]
  thinkingSummary?: ThinkingSummary
  thinkingContent?: string
  hasToolCall?: boolean
  metaInfo?: MetaInfo
  /** Ordered timeline of thinking + tool calls */
  timeline?: TimelineBlock[]
}

export interface LLMProvider {
  id: string
  name: string
  provider: string
  model: string
  base_url: string
  api_key: string

  is_default: boolean
  function_call_model_ids: string[]
  selected_models?: string[]
  default_model?: string
  visual_model_ids?: string[]
  status?: "ready" | "error" | "unknown"
}

export interface CollectionItem {
  id: string
  name: string
  points_count: number
}

// Import getCollections for fetchCollections action
import { getCollections, createSession, getSession, deleteSession } from "@/api/client"

interface AppState {
  sidebarView: SidebarView
  sidebarOpen: boolean
  setSidebarView: (view: SidebarView) => void
  toggleSidebar: () => void

  activeCollection: string  // Now stores collection ID
  setActiveCollection: (id: string) => void
  collections: CollectionItem[]  // Cache of collection list
  setCollections: (collections: CollectionItem[]) => void
  fetchCollections: () => Promise<void>  // Fetch and update collections
  pendingCreateCollection: boolean
  setPendingCreateCollection: (v: boolean) => void
  pendingOpenFile: string | null
  setPendingOpenFile: (source: string | null) => void
  pendingOpenNote: string | null
  setPendingOpenNote: (noteId: string | null) => void

  // Meeting ingest progress (persists across dialog open/close)
  ingestMeetingId: string | null
  ingestProgress: Record<number, "pending" | "done" | "error">
  ingestProjectNames: string[]
  setIngestState: (meetingId: string | null, progress: Record<number, "pending" | "done" | "error">, names: string[]) => void

  selectedCollections: string[]  // Chat collection selection
  setSelectedCollections: (ids: string[]) => void
  toggleCollection: (id: string) => void
  removeDeletedCollection: (id: string) => void

  recallCollections: string[]     // Recall page collection selection
  setRecallCollections: (ids: string[]) => void
  toggleRecallCollection: (id: string) => void

  activeProvider: string | null
  setActiveProvider: (id: string | null) => void
  activeModel: string | null
  setActiveModel: (model: string | null) => void
  providers: LLMProvider[]
  setProviders: (providers: LLMProvider[] | ((prev: LLMProvider[]) => LLMProvider[])) => void

  messages: Message[]
  isStreaming: boolean
  addMessage: (msg: Message) => void
  appendToLastMessage: (token: string) => void
  setLastMessageSources: (sources: Source[]) => void
  setLastMessageMetaInfo: (info: MetaInfo) => void
  setLastMessageThinkingSteps: (steps: ThinkingIteration[]) => void
  setLastMessageThinkingSummary: (summary: ThinkingSummary | undefined) => void
  setLastMessageThinkingContent: (token: string) => void
  appendTimelineThinking: (token: string) => void
  setTimelineToolSummary: (summary: ThinkingSummary | undefined) => void
  startTimelineTool: () => void
  setLastMessageHasToolCall: () => void
  finishLastMessage: () => void
  flushLastMessageToThinking: () => void
  setStreaming: (v: boolean) => void

  isOnline: boolean
  setOnline: (v: boolean) => void

  logPanelOpen: boolean
  toggleLogPanel: () => void

  activeMeeting: string | null
  setActiveMeeting: (id: string | null) => void

  // Navigation guard — return false to block navigation
  navigationGuard: (() => boolean) | null
  setNavigationGuard: (guard: (() => boolean) | null) => void

  // ── Session ──
  sessionId: string | null
  sessions: import("@/api/client").SessionItem[]
  setSessionId: (id: string | null) => void
  setSessions: (s: import("@/api/client").SessionItem[]) => void
  initSession: (collections?: string[]) => Promise<string>
  loadSessionMessages: (sessionId: string) => Promise<void>
  deleteCurrentSession: () => Promise<void>
}

// Module-level per-session state
const _streamAborts = new Map<string, AbortController>()
const _sessionCache = new Map<string, Message[]>()

/** Register an abort controller for a session. Returns the controller. */
export function _registerStream(sessionId: string, ctrl: AbortController) {
  _streamAborts.get(sessionId)?.abort()
  _streamAborts.set(sessionId, ctrl)
}
/** Abort a specific session's stream. */
export function _abortStream(sessionId: string) {
  _streamAborts.get(sessionId)?.abort()
  _streamAborts.delete(sessionId)
}
/** Unregister without aborting (stream ended normally). */
export function _unregisterStream(sessionId: string) {
  _streamAborts.delete(sessionId)
}
/** Get or create cached messages for a session. */
export function _getCachedMessages(sessionId: string): Message[] | undefined {
  return _sessionCache.get(sessionId)
}
/** Set cached messages for a session. */
export function _setCachedMessages(sessionId: string, msgs: Message[]) {
  _sessionCache.set(sessionId, msgs)
}
/** Save active session messages from store to cache. */
function _saveActiveToCache() {
  const { sessionId, messages } = useAppStore.getState()
  if (sessionId && messages.length > 0) {
    _sessionCache.set(sessionId, [...messages])
  }
}

export const useAppStore = create<AppState>((set) => ({
  sidebarView: loadPersisted<SidebarView>("sidebarView", "chat"),
  sidebarOpen: true,
  setSidebarView: (view) => {
    const state = useAppStore.getState()
    // Only guard if navigating away from meeting view
    if (state.sidebarView === "meeting" && view !== "meeting" && state.navigationGuard) {
      if (!state.navigationGuard()) return
    }
    set({ sidebarView: view })
  },
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),

  activeCollection: "",
  setActiveCollection: (id) => set({ activeCollection: id }),
  collections: [],
  setCollections: (collections) => set({ collections }),
  fetchCollections: async () => {
    try {
      const items = await getCollections()
      set({ collections: items })
    } catch {
      // ignore
    }
  },
  pendingCreateCollection: false,
  setPendingCreateCollection: (v) => set({ pendingCreateCollection: v }),
  pendingOpenFile: null,
  setPendingOpenFile: (source) => set({ pendingOpenFile: source }),
  pendingOpenNote: null,
  setPendingOpenNote: (noteId) => set({ pendingOpenNote: noteId }),

  ingestMeetingId: null,
  ingestProgress: {},
  ingestProjectNames: [],
  setIngestState: (meetingId, progress, names) => set({
    ingestMeetingId: meetingId,
    ingestProgress: progress,
    ingestProjectNames: names,
  }),

  selectedCollections: loadPersisted<string[]>("selectedCollections", []),
  setSelectedCollections: (ids) => {
    // Persist to localStorage
    localStorage.setItem("rag_selectedCollections", JSON.stringify(ids))
    set({ selectedCollections: ids })
  },
  toggleCollection: (id) =>
    set((s) => {
      const exists = s.selectedCollections.includes(id)
      const next = exists
        ? s.selectedCollections.filter((c) => c !== id)
        : [...s.selectedCollections, id]
      // Persist to localStorage
      localStorage.setItem("rag_selectedCollections", JSON.stringify(next))
      return { selectedCollections: next }
    }),
  removeDeletedCollection: (id) =>
    set((s) => ({
      selectedCollections: s.selectedCollections.filter((c) => c !== id),
      activeCollection: s.activeCollection === id ? "" : s.activeCollection,
    })),

  recallCollections: [],
  setRecallCollections: (ids) => set({ recallCollections: ids }),
  toggleRecallCollection: (id) =>
    set((s) => {
      const exists = s.recallCollections.includes(id)
      return {
        recallCollections: exists
          ? s.recallCollections.filter((c) => c !== id)
          : [...s.recallCollections, id],
      }
    }),

  activeProvider: loadPersisted<string | null>("activeProvider", null),
  setActiveProvider: (id) => set({ activeProvider: id }),
  activeModel: loadPersisted<string | null>("activeModel", null),
  setActiveModel: (model) => set({ activeModel: model }),
  providers: [],
  setProviders: (providers) =>
    set((s) => ({
      providers: typeof providers === "function" ? providers(s.providers) : providers,
    })),

  messages: [],
  isStreaming: false,
  addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),
  appendToLastMessage: (token) =>
    set((s) => {
      const msgs = [...s.messages]
      if (msgs.length > 0) {
        const last = msgs[msgs.length - 1]
        msgs[msgs.length - 1] = { ...last, content: last.content + token }
      }
      return { messages: msgs }
    }),
  flushLastMessageToThinking: () =>
    set((s) => {
      const msgs = [...s.messages]
      if (msgs.length > 0) {
        const last = msgs[msgs.length - 1]
        if (last.content) {
          const tl = [...(last.timeline || []), { type: "thinking" as const, content: last.content, isStreaming: false }]
          msgs[msgs.length - 1] = { ...last, content: "", timeline: tl }
        }
      }
      return { messages: msgs }
    }),
  setLastMessageSources: (sources) =>
    set((s) => {
      const msgs = [...s.messages]
      if (msgs.length > 0) {
        msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], sources }
      }
      return { messages: msgs }
    }),
  setLastMessageMetaInfo: (info) =>
    set((s) => {
      const msgs = [...s.messages]
      if (msgs.length > 0) {
        msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], metaInfo: info }
      }
      return { messages: msgs }
    }),
  setLastMessageThinkingSteps: (steps) =>
    set((s) => {
      const msgs = [...s.messages]
      if (msgs.length > 0) {
        msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], thinkingSteps: steps }
      }
      return { messages: msgs }
    }),
  setLastMessageThinkingSummary: (summary: ThinkingSummary | undefined) =>
    set((s) => {
      const msgs = [...s.messages]
      if (msgs.length > 0) {
        msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], thinkingSummary: summary }
      }
      return { messages: msgs }
    }),
  setLastMessageThinkingContent: (token) =>
    set((s) => {
      const msgs = [...s.messages]
      if (msgs.length > 0) {
        const last = msgs[msgs.length - 1]
        msgs[msgs.length - 1] = { ...last, thinkingContent: (last.thinkingContent || "") + token }
      }
      return { messages: msgs }
    }),
  appendTimelineThinking: (token) =>
    set((s) => {
      const msgs = [...s.messages]
      if (msgs.length > 0) {
        const last = msgs[msgs.length - 1]
        const tl = [...(last.timeline || [])]
        const lastBlock = tl[tl.length - 1]
        if (lastBlock && lastBlock.type === "thinking" && lastBlock.isStreaming) {
          tl[tl.length - 1] = { ...lastBlock, content: (lastBlock.content || "") + token }
        } else {
          tl.push({ type: "thinking", content: token, isStreaming: true })
        }
        msgs[msgs.length - 1] = { ...last, timeline: tl }
      }
      return { messages: msgs }
    }),
  startTimelineTool: () =>
    set((s) => {
      const msgs = [...s.messages]
      if (msgs.length > 0) {
        const last = msgs[msgs.length - 1]
        const tl = [...(last.timeline || [])]
        // Close any open thinking block
        if (tl.length > 0 && tl[tl.length - 1].type === "thinking") {
          tl[tl.length - 1] = { ...tl[tl.length - 1], isStreaming: false }
        }
        tl.push({ type: "tool", summary: undefined })
        msgs[msgs.length - 1] = { ...last, timeline: tl }
      }
      return { messages: msgs }
    }),
  setTimelineToolSummary: (summary) =>
    set((s) => {
      const msgs = [...s.messages]
      if (msgs.length > 0) {
        const last = msgs[msgs.length - 1]
        const tl = [...(last.timeline || [])]
        // Update last tool block
        for (let i = tl.length - 1; i >= 0; i--) {
          if (tl[i].type === "tool") {
            tl[i] = { ...tl[i], summary: summary || tl[i].summary }
            break
          }
        }
        msgs[msgs.length - 1] = { ...last, timeline: tl }
      }
      return { messages: msgs }
    }),
  setLastMessageHasToolCall: () =>
    set((s) => {
      const msgs = [...s.messages]
      if (msgs.length > 0) {
        msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], hasToolCall: true }
      }
      return { messages: msgs }
    }),
  finishLastMessage: () =>
    set((s) => {
      const msgs = [...s.messages]
      if (msgs.length > 0) {
        msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], isStreaming: false }
      }
      return { messages: msgs, isStreaming: false }
    }),
  setStreaming: (v) => set({ isStreaming: v }),

  isOnline: false,
  setOnline: (v) => set({ isOnline: v }),

  logPanelOpen: false,
  toggleLogPanel: () => set((s) => ({ logPanelOpen: !s.logPanelOpen })),

  activeMeeting: null,
  setActiveMeeting: (id) => set({ activeMeeting: id }),

  navigationGuard: null,
  setNavigationGuard: (guard) => set({ navigationGuard: guard }),

  // ── Session ──
  sessionId: loadPersisted<string | null>("sessionId", null),
  sessions: [] as import("@/api/client").SessionItem[],
  setSessionId: (id) => set({ sessionId: id }),
  setSessions: (sessions) => set({ sessions }),
  initSession: async (collections) => {
    const state = useAppStore.getState()
    const s = await createSession("", collections ?? state.selectedCollections)
    set({ sessionId: s.id, messages: [] })
    return s.id
  },
  loadSessionMessages: async (sessionId) => {
    // Save current session to cache (keep its stream alive in background)
    _saveActiveToCache()
    // Restore target from cache if available
    const cached = _sessionCache.get(sessionId)
    if (cached) {
      set({ messages: [...cached], sessionId, isStreaming: useAppStore.getState().isStreaming })
      return
    }
    set({ isStreaming: false })
    try {
      const detail = await getSession(sessionId)
      set({
        messages: detail.messages.map((m) => {
          const meta = (m.metadata ?? {}) as Record<string, any>
          const summary = meta.thinking_summary as ThinkingSummary | undefined
          return {
            id: m.id,
            role: m.role as "user" | "assistant",
            content: m.content,
            sources: m.sources ?? undefined,
            metaInfo: meta as MetaInfo,
            thinkingSummary: summary,
          }
        }),
        sessionId,
      })
    } catch {
      set({ sessionId: null, messages: [] })
    }
  },
  deleteCurrentSession: async () => {
    const { sessionId } = useAppStore.getState()
    if (!sessionId) return
    _abortStream(sessionId)
    _sessionCache.delete(sessionId)
    await deleteSession(sessionId)
    set({ sessionId: null, messages: [] })
  },
}))

// Helper functions to get collection by id or name
export function getCollectionById(id: string): CollectionItem | undefined {
  return useAppStore.getState().collections.find(c => c.id === id)
}

export function getCollectionByName(name: string): CollectionItem | undefined {
  return useAppStore.getState().collections.find(c => c.name === name)
}

// Persist selected chat params to localStorage (debounced to avoid writing on every streaming token)
let _persistTimer: ReturnType<typeof setTimeout> | null = null
useAppStore.subscribe((state) => {
  if (_persistTimer) clearTimeout(_persistTimer)
  _persistTimer = setTimeout(() => {
    localStorage.setItem("rag_activeProvider", JSON.stringify(state.activeProvider))
    localStorage.setItem("rag_activeModel", JSON.stringify(state.activeModel))
    localStorage.setItem("rag_selectedCollections", JSON.stringify(state.selectedCollections))
    localStorage.setItem("rag_sidebarView", JSON.stringify(state.sidebarView))
    localStorage.setItem("rag_sessionId", JSON.stringify(state.sessionId))
  }, 500)
})
