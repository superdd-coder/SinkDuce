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

export type TimelineToolStatus = "running" | "done" | "error" | "declined" | "awaiting_confirm"

export interface TimelineBlock {
  type: "thinking" | "tool"
  content?: string              // thinking text (accumulated)
  summary?: ThinkingSummary     // optional agentic detail (not shown by default UI)
  isStreaming?: boolean         // still receiving
  /** Tool function name */
  tool?: string
  /** Short query / argument preview */
  toolQuery?: string
  /** running | done | error | declined | awaiting_confirm */
  toolStatus?: TimelineToolStatus
  sourceType?: string
  sourcesCount?: number
  /** Tool return text (collapsible body) */
  toolResult?: string
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
  setSidebarOpen: (open: boolean) => void
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
  setTimelineToolStatus: (status: string) => void
  startTimelineTool: (info?: {
    tool?: string
    raw_query?: string
    source_type?: string
  }) => void
  finishTimelineTool: (info?: {
    status?: TimelineToolStatus
    sources_count?: number
    source_type?: string
    content?: string
    tool?: string
  }) => void
  /** Force any non-terminal tool rows to done (when answer tokens start). */
  closeOpenTimelineTools: () => void
  setLastMessageHasToolCall: () => void
  finishLastMessage: () => void
  flushLastMessageToThinking: () => void
  setStreaming: (v: boolean) => void

  isOnline: boolean
  setOnline: (v: boolean) => void

  logPanelOpen: boolean
  toggleLogPanel: () => void

  developerMode: boolean
  toggleDeveloperMode: () => void

  activeMeeting: string | null
  setActiveMeeting: (id: string | null) => void

  // Navigation guard — return false to block navigation
  navigationGuard: (() => boolean) | null
  setNavigationGuard: (guard: (() => boolean) | null) => void

  // ── Session ──
  sessionId: string | null
  /** Which sessionId currently has its history loaded into `messages`. */
  sessionHydratedId: string | null
  /** True while fetching session messages from the backend. */
  sessionLoading: boolean
  sessions: import("@/api/client").SessionItem[]
  setSessionId: (id: string | null) => void
  setSessions: (s: import("@/api/client").SessionItem[]) => void
  initSession: (collections?: string[]) => Promise<string>
  loadSessionMessages: (sessionId: string) => Promise<void>
  /** Ensure active session history is in `messages` before sending. */
  ensureSessionHydrated: () => Promise<string | null>
  deleteCurrentSession: () => Promise<void>
}

// Module-level per-session state
const _streamAborts = new Map<string, AbortController>()
const _sessionCache = new Map<string, Message[]>()
/** In-flight history loads — dedupe concurrent hydrate for the same session. */
const _hydratePromises = new Map<string, Promise<void>>()

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
  sidebarOpen: false,
  setSidebarView: (view) => {
    const state = useAppStore.getState()
    // Only guard if navigating away from meeting view
    if (state.sidebarView === "meeting" && view !== "meeting" && state.navigationGuard) {
      if (!state.navigationGuard()) return
    }
    try {
      localStorage.setItem("rag_sidebarView", JSON.stringify(view))
    } catch { /* ignore */ }
    set({ sidebarView: view })
  },
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),

  activeCollection: loadPersisted<string>("activeCollection", ""),
  setActiveCollection: (id) => {
    try {
      localStorage.setItem("rag_activeCollection", JSON.stringify(id))
    } catch { /* ignore */ }
    set({ activeCollection: id })
  },
  collections: [],
  setCollections: (collections) => set({ collections }),
  fetchCollections: async () => {
    try {
      const items = await getCollections()
      // Library list: alphabetical by name (API sorts; keep client stable)
      const sorted = [...items].sort((a: any, b: any) =>
        String(a.name || "").localeCompare(String(b.name || ""), undefined, {
          sensitivity: "base",
          numeric: true,
        }),
      )
      // Clean up stale selectedCollections (e.g. deleted collections)
      const validIds = new Set(sorted.map((c: any) => c.id))
      const current = loadPersisted<string[]>("selectedCollections", [])
      const cleaned = current.filter((id) => validIds.has(id))
      if (cleaned.length !== current.length) {
        localStorage.setItem("rag_selectedCollections", JSON.stringify(cleaned))
      }
      // Drop activeCollection if it no longer exists
      const active = useAppStore.getState().activeCollection
      if (active && !validIds.has(active)) {
        try {
          localStorage.setItem("rag_activeCollection", JSON.stringify(""))
        } catch { /* ignore */ }
        set({ collections: sorted, selectedCollections: cleaned, activeCollection: "" })
        return
      }
      set({ collections: sorted, selectedCollections: cleaned })
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
    set((s) => {
      const nextActive = s.activeCollection === id ? "" : s.activeCollection
      const nextSelected = s.selectedCollections.filter((c) => c !== id)
      try {
        localStorage.setItem("rag_activeCollection", JSON.stringify(nextActive))
        localStorage.setItem("rag_selectedCollections", JSON.stringify(nextSelected))
      } catch { /* ignore */ }
      return {
        selectedCollections: nextSelected,
        activeCollection: nextActive,
      }
    }),

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
      // Keep answer tokens at normal priority so UI streams like reasoning.
      // (startTransition deferred paints until the SSE loop idled → one big dump.)
      const msgs = s.messages
      if (msgs.length === 0) return s
      const last = msgs[msgs.length - 1]
      const next = msgs.slice(0, -1)
      next.push({ ...last, content: last.content + token })
      return { messages: next }
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
  startTimelineTool: (info) =>
    set((s) => {
      const msgs = [...s.messages]
      if (msgs.length > 0) {
        const last = msgs[msgs.length - 1]
        const tl = [...(last.timeline || [])]
        // Close any open thinking block
        if (tl.length > 0 && tl[tl.length - 1].type === "thinking") {
          tl[tl.length - 1] = { ...tl[tl.length - 1], isStreaming: false }
        }
        const toolName = (info?.tool || "").trim()
        const rawQ = (info?.raw_query || "").trim()
        const sourceType = (info?.source_type || "").trim() || undefined
        tl.push({
          type: "tool",
          summary: undefined,
          isStreaming: true,
          tool: toolName || undefined,
          toolQuery: rawQ || undefined,
          // Web confirm is applied later via setTimelineToolStatus / web_search_confirm.
          // Do not stick on awaiting_confirm forever when search runs without a dialog
          // (e.g. already Allowed this turn / Always-allow).
          toolStatus: "running",
          sourceType,
        })
        msgs[msgs.length - 1] = {
          ...last,
          timeline: tl,
          hasToolCall: true,
        }
      }
      return { messages: msgs }
    }),
  finishTimelineTool: (info) =>
    set((s) => {
      const msgs = [...s.messages]
      if (msgs.length > 0) {
        const last = msgs[msgs.length - 1]
        const tl = [...(last.timeline || [])]
        for (let i = tl.length - 1; i >= 0; i--) {
          if (tl[i].type === "tool") {
            const st = info?.status || "done"
            tl[i] = {
              ...tl[i],
              isStreaming: false,
              toolStatus: st,
              tool: info?.tool || tl[i].tool,
              sourcesCount:
                info?.sources_count !== undefined
                  ? info.sources_count
                  : tl[i].sourcesCount,
              sourceType: info?.source_type || tl[i].sourceType,
              toolResult:
                info?.content !== undefined ? info.content : tl[i].toolResult,
            }
            break
          }
        }
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
  setTimelineToolStatus: (status) =>
    set((s) => {
      const msgs = [...s.messages]
      if (msgs.length > 0) {
        const last = msgs[msgs.length - 1]
        const tl = [...(last.timeline || [])]
        for (let i = tl.length - 1; i >= 0; i--) {
          if (tl[i].type === "tool") {
            // Never reopen a finished tool when later status text arrives
            const prev = tl[i].toolStatus
            if (prev === "done" || prev === "error" || prev === "declined") {
              const cur = tl[i].summary || { aq_count: 0, task_count: 0, tasks: [] }
              tl[i] = { ...tl[i], summary: { ...cur, status } }
              break
            }
            const cur = tl[i].summary || { aq_count: 0, task_count: 0, tasks: [] }
            const st = String(status || "")
            const lower = st.toLowerCase()
            // HITL waiting only while confirm dialog is up; Searching/[WEB] → running
            const waitingConfirm = lower.includes("waiting for web search confirmation")
            const nextStatus = waitingConfirm ? "awaiting_confirm" : "running"
            tl[i] = {
              ...tl[i],
              summary: { ...cur, status: st },
              toolStatus: nextStatus,
            }
            break
          }
        }
        msgs[msgs.length - 1] = { ...last, timeline: tl }
      }
      return { messages: msgs }
    }),
  /** Mark any still-open tool rows done (e.g. answer tokens started). */
  closeOpenTimelineTools: () =>
    set((s) => {
      const msgs = [...s.messages]
      if (msgs.length === 0) return s
      const last = msgs[msgs.length - 1]
      const tl = last.timeline
      if (!tl?.length) return s
      let changed = false
      const next = tl.map((b) => {
        if (b.type !== "tool") return b
        const st = b.toolStatus
        if (st === "done" || st === "error" || st === "declined") return b
        changed = true
        return { ...b, isStreaming: false, toolStatus: "done" as const }
      })
      if (!changed) return s
      const out = msgs.slice(0, -1)
      out.push({ ...last, timeline: next })
      return { messages: out }
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
        const last = msgs[msgs.length - 1]
        // Force every tool step to a terminal state when the turn ends
        const tl = (last.timeline || []).map((b) => {
          if (b.type === "thinking" && b.isStreaming) {
            return { ...b, isStreaming: false }
          }
          if (b.type !== "tool") return b
          const st = b.toolStatus
          // Keep terminal statuses; coerce running / awaiting_confirm / undefined → done
          const terminal =
            st === "done" || st === "error" || st === "declined"
          return {
            ...b,
            isStreaming: false,
            toolStatus: terminal ? st : "done",
          }
        })
        msgs[msgs.length - 1] = {
          ...last,
          isStreaming: false,
          timeline: tl.length ? tl : last.timeline,
        }
      }
      return { messages: msgs, isStreaming: false }
    }),
  setStreaming: (v) => set({ isStreaming: v }),

  isOnline: false,
  setOnline: (v) => set({ isOnline: v }),

  logPanelOpen: false,
  toggleLogPanel: () => set((s) => ({ logPanelOpen: !s.logPanelOpen })),

  developerMode: true,
  toggleDeveloperMode: () => set((s) => ({ developerMode: !s.developerMode })),

  activeMeeting: null,
  setActiveMeeting: (id) => set({ activeMeeting: id }),

  navigationGuard: null,
  setNavigationGuard: (guard) => set({ navigationGuard: guard }),

  // ── Session ──
  // sessionId is restored from localStorage; messages are not — must hydrate on Chat enter.
  sessionId: loadPersisted<string | null>("sessionId", null),
  sessionHydratedId: null as string | null,
  sessionLoading: false,
  sessions: [] as import("@/api/client").SessionItem[],
  setSessionId: (id) =>
    set(
      id
        ? { sessionId: id }
        : { sessionId: null, sessionHydratedId: null, sessionLoading: false, messages: [] },
    ),
  setSessions: (sessions) => set({ sessions }),
  initSession: async (collections) => {
    const state = useAppStore.getState()
    _saveActiveToCache()
    const s = await createSession("", collections ?? state.selectedCollections)
    // Brand-new empty session is already "hydrated"
    set({
      sessionId: s.id,
      sessionHydratedId: s.id,
      sessionLoading: false,
      messages: [],
      isStreaming: false,
    })
    return s.id
  },
  loadSessionMessages: async (sessionId) => {
    const state = useAppStore.getState()
    // Already showing this session's history
    if (state.sessionHydratedId === sessionId && state.sessionId === sessionId) {
      return
    }
    // Join in-flight hydrate for the same id (send during Loading…)
    const inflight = _hydratePromises.get(sessionId)
    if (inflight) {
      await inflight
      return
    }

    const run = (async () => {
      // Save current session to cache (keep its stream alive in background)
      _saveActiveToCache()
      // Restore target from cache if available
      const cached = _sessionCache.get(sessionId)
      if (cached) {
        set({
          messages: [...cached],
          sessionId,
          sessionHydratedId: sessionId,
          sessionLoading: false,
          isStreaming: useAppStore.getState().isStreaming,
        })
        return
      }
      set({
        sessionId,
        sessionLoading: true,
        sessionHydratedId: null,
        isStreaming: false,
        // Clear immediately so we never show another session's messages under this id
        messages: [],
      })
      try {
        const detail = await getSession(sessionId)
        // Drop stale response if user switched sessions mid-fetch
        if (useAppStore.getState().sessionId !== sessionId) return
        set({
          messages: detail.messages
            .filter((m) => m.role === "user" || m.role === "assistant")
            .map((m) => {
              const meta = (m.metadata ?? {}) as Record<string, any>
              const summary = meta.thinking_summary as ThinkingSummary | undefined
              // Skip assistant messages that are just tool-call placeholders
              // (no visible content, only function call metadata)
              if (m.role === "assistant" && !m.content && meta.tool_calls) {
                return null
              }
              const timeline = buildTimelineFromMeta(meta)
              return {
                id: m.id,
                role: m.role as "user" | "assistant",
                content: m.content,
                sources: m.sources ?? undefined,
                metaInfo: meta as MetaInfo,
                thinkingSummary: summary,
                hasToolCall: !!timeline?.length || !!summary,
                timeline,
              }
            })
            .filter((m): m is NonNullable<typeof m> => m != null),
          sessionId,
          sessionHydratedId: sessionId,
          sessionLoading: false,
        })
      } catch {
        // Invalid / deleted session — clear selection so next send creates a new one
        if (useAppStore.getState().sessionId === sessionId) {
          set({
            sessionId: null,
            sessionHydratedId: null,
            sessionLoading: false,
            messages: [],
          })
        }
      }
    })()

    _hydratePromises.set(sessionId, run)
    try {
      await run
    } finally {
      _hydratePromises.delete(sessionId)
    }
  },
  ensureSessionHydrated: async (): Promise<string | null> => {
    const state = useAppStore.getState()
    if (!state.sessionId) {
      return state.initSession()
    }
    if (state.sessionHydratedId === state.sessionId) {
      return state.sessionId
    }
    await state.loadSessionMessages(state.sessionId)
    const after = useAppStore.getState()
    // load failed → session cleared; start a fresh one for this message
    if (!after.sessionId || after.sessionHydratedId !== after.sessionId) {
      return after.initSession()
    }
    return after.sessionId
  },
  deleteCurrentSession: async () => {
    const { sessionId } = useAppStore.getState()
    if (!sessionId) return
    _abortStream(sessionId)
    _sessionCache.delete(sessionId)
    await deleteSession(sessionId)
    set({
      sessionId: null,
      sessionHydratedId: null,
      sessionLoading: false,
      messages: [],
    })
  },
}))

/** Rebuild UI timeline from assistant message metadata (after page reload). */
export function buildTimelineFromMeta(
  meta: Record<string, any> | null | undefined,
): TimelineBlock[] | undefined {
  if (!meta) return undefined

  const raw = meta.tool_trace
  if (Array.isArray(raw) && raw.length > 0) {
    return raw.map((t: Record<string, any>) => ({
      type: "tool" as const,
      tool: String(t.tool || t.name || ""),
      toolQuery: String(t.toolQuery ?? t.tool_query ?? ""),
      toolResult: String(t.toolResult ?? t.tool_result ?? ""),
      toolStatus: (t.toolStatus || t.tool_status || "done") as TimelineToolStatus,
      sourceType: t.sourceType || t.source_type || undefined,
      sourcesCount:
        typeof t.sourcesCount === "number"
          ? t.sourcesCount
          : typeof t.sources_count === "number"
            ? t.sources_count
            : undefined,
      summary: (t.summary as ThinkingSummary | undefined) || undefined,
      isStreaming: false,
    }))
  }

  // Backward compat: only agentic thinking_summary was saved
  const summary = meta.thinking_summary as ThinkingSummary | undefined
  if (
    summary &&
    ((summary.aq_count ?? 0) > 0 || (summary.tasks?.length ?? 0) > 0)
  ) {
    return [
      {
        type: "tool",
        tool: "search_knowledge_base",
        summary,
        toolStatus: "done",
        isStreaming: false,
      },
    ]
  }
  return undefined
}

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
    localStorage.setItem("rag_sessionId", JSON.stringify(state.sessionId))
  }, 500)
})
