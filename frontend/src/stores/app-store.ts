import type { ReportComment, CommentFocus, ReportCommentDraft, ReviewBatch } from "@/lib/report-review"
import { applyChatArtifacts, type ReportReference } from "@/lib/chat-reports"
import type { Evidence } from "@/lib/chat-citations"
import { create } from "zustand"
import {
  applyDocumentLang,
  normalizeLocale,
  notifyDesktopLocale,
  type Locale,
} from "@/i18n"
import { bindLocaleGetter } from "@/i18n/tr"
import { listSessions, updateConfig } from "@/api/client"
import { patchShellSettings } from "@/api/shell-settings"
import { getIdentityMe, type IdentityMe } from "@/api/identity"
import {
  forgetSessionConfig,
  recallSessionConfig,
  rememberSessionConfig,
  resolveSessionConfig,
  retainCollectionScope,
} from "@/api/session-config"
import { readSourceScoped, writeSourceScoped } from "@/api/source-scope"
import { DEFAULT_VIEW_RAIL_WIDTH, normalizeViewRailWidth } from "@/lib/view-rail-width"
import { DEFAULT_REPORT_SPLIT, normalizeReportSplit } from "@/lib/report-split"
import { mergeNoteChanges, type NoteChange } from "@/lib/note-changes"
import type { ChatAttachment } from "@/api/client"
import type { ChatAttachmentDraft } from "@/lib/chat-attachments"

function loadPersisted<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(`rag_${key}`)
    return v !== null ? JSON.parse(v) : fallback
  } catch {
    return fallback
  }
}

export type SidebarView = "chat" | "database" | "recall" | "meeting" | "llm_provider"

/** 来源健康状态（SourcePill）。unknown = 尚未探测到。 */
export type SourceHealthState = "online" | "offline" | "revoked" | "unknown"

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
  attachments?: ChatAttachment[]
  sources?: Source[]
  noteChanges?: NoteChange[]
  citations?: Evidence[]
  reportChanges?: ReportReference[]
  reportReviews?: ReviewBatch[]
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
  archived?: boolean
  /** 该库对当前身份的四档开关（后端算；UI 靠 config 决定设置入口画不画）。 */
  permissions?: { read: boolean; write: boolean; delete: boolean; config: boolean }
}

// Import getCollections for fetchCollections action
import { getCollections, createSession, getSession, deleteSession } from "@/api/client"

interface AppState {
  reportComments: Record<string, ReportComment[]>
  reportReviewEpoch: Record<string, number>
  clearReportReviewSession: (sid: string) => void
  setReportComments: (sid: string, comments: ReportComment[]) => void
  reportCommentFocus: CommentFocus | null
  reportHistoryEpoch: number
  bumpReportHistory: () => void
  focusReportComment: (focus: Omit<CommentFocus, "nonce">) => void
  reportCommentDrafts: Record<string, ReportCommentDraft>
  setReportCommentDraft: (key: string, draft: ReportCommentDraft | null) => void

  chatAttachmentDrafts: Record<string, ChatAttachmentDraft[]>
  consumedChatAttachments: Record<string, string[]>
  setChatAttachmentDrafts: (sid: string, update: (drafts: ChatAttachmentDraft[]) => ChatAttachmentDraft[]) => void
  consumeChatAttachments: (sid: string, ids: string[]) => void
  clearChatAttachmentDrafts: (sid: string) => void
  viewRailWidth: number
  setViewRailWidth: (width: number) => void
  reportChatRatio: number
  setReportChatRatio: (ratio: number) => void
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
  addLastMessageNoteChanges: (changes: unknown) => void
  addLastMessageArtifacts: (data: Record<string, unknown>) => void
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

  /**
   * 来源健康：key = box_id（本机固定 "local"）→ 探测状态。
   * 由 api/source-health.ts 的统一探测循环维护；缺 key = 尚未探测到（unknown）。
   */
  sourceHealth: Record<string, SourceHealthState>
  setSourceHealth: (id: string, v: SourceHealthState) => void

  /**
   * 远程模式（瘦壳）：本机 /health 的 remote_shell 标志，启动时写一次。
   * origin.ts 里的 isRemoteShellActive() 是同一事实的模块级镜像（给不能
   * 引 store 的模块用）；两处由 main.tsx 一并置位。
   */
  remoteShellMode: boolean
  setRemoteShellMode: (v: boolean) => void
  /**
   * 容器部署（Docker）：本机 /health 的 in_container 标志。远程模式在容器里
   * 由 .env 的 SINKDUCE_REMOTE_MODE 决定，UI 一律只读（显示模式 + 指路 .env）。
   */
  containerDeployment: boolean
  setContainerDeployment: (v: boolean) => void
  /** 远程模式下活动盒子失联的原因（handleRemoteConnectionLost 写入；恢复在线自动清除）。 */
  remoteGateReason: "unreachable" | "revoked" | null
  setRemoteGateReason: (r: "unreachable" | "revoked" | null) => void

  /** RBAC 身份画像（UI 化妆的唯一真相源；服务端才是墙）。null = 尚未拉到。 */
  identity: IdentityMe | null
  setIdentity: (v: IdentityMe | null) => void
  /** 重新拉取身份画像（切换来源/权限变更后）。 */
  refreshIdentity: () => Promise<void>

  logPanelOpen: boolean
  toggleLogPanel: () => void

  developerMode: boolean
  toggleDeveloperMode: () => void

  locale: Locale
  setLocale: (locale: Locale) => void
  hydrateLocale: (raw: unknown) => void

  pendingMeetingCitation?: { meetingId: string; sentenceId?: string; startTime?: number }
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
  sessionListStatus: "idle" | "loading" | "ready" | "error"
  setSessionId: (id: string | null) => void
  setSessions: (s: import("@/api/client").SessionItem[]) => void
  /** Refetch the session list from the backend (used by the app-wide event stream). */
  refreshSessionList: () => Promise<void>
  /** Drop a session's cached history so the next visit refetches it. */
  invalidateSessionCache: (sessionId: string) => void
  initSession: (collections?: string[]) => Promise<string>
  loadSessionMessages: (sessionId: string) => Promise<void>
  /** 把会话自己存的配置灌回 store（逐项校验，失效项不动）。 */
  applySessionConfig: (config: import("@/api/session-config").SessionConfig | undefined) => Promise<void>
  /**
   * 输入框的两个开关：思考 / Web 搜索。
   *
   * 与 provider/model/collections 一样住在 store（不再散在组件本地 state），
   * 并随消息存进会话 config —— 重新打开该会话时恢复。
   */
  thinking: boolean
  webSearch: boolean
  setThinking: (v: boolean) => void
  setWebSearch: (v: boolean) => void
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
  const controller = _streamAborts.get(sessionId)
  controller?.abort()
  if (_streamAborts.get(sessionId) === controller) _streamAborts.delete(sessionId)
}
/** Unregister without aborting (stream ended normally). */
export function _unregisterStream(sessionId: string, controller?: AbortController) {
  if (!controller || _streamAborts.get(sessionId) === controller) _streamAborts.delete(sessionId)
}
/** A superseded generation must not write into or finish its replacement. */
export function _ownsStream(sessionId: string, controller: AbortController) {
  return _streamAborts.get(sessionId) === controller
}
/** Get or create cached messages for a session. */
export function _getCachedMessages(sessionId: string): Message[] | undefined {
  return _sessionCache.get(sessionId)
}
/** Set cached messages for a session. */
export function _setCachedMessages(sessionId: string, msgs: Message[]) {
  _sessionCache.set(sessionId, msgs)
}

/**
 * Drop a session's cached history so the next visit refetches from the server.
 *
 * Used by the app-wide event stream: another device changed this session, and
 * the cache has no TTL, so without eviction the stale copy would be served
 * forever (development_doc/2026-09-16-multi-device-state-sync-design.md §10).
 */
export function _invalidateSessionCache(sessionId: string) {
  _sessionCache.delete(sessionId)
}
/** Save active session messages from store to cache. */
function _saveActiveToCache() {
  const { sessionId, messages } = useAppStore.getState()
  if (sessionId && messages.length > 0) {
    _sessionCache.set(sessionId, [...messages])
  }
}

export const useAppStore = create<AppState>((set) => ({
  reportComments: {},
  reportReviewEpoch: {},
  clearReportReviewSession: (sid) => set((s) => {
    const comments={...s.reportComments}; delete comments[sid]
    const drafts=Object.fromEntries(Object.entries(s.reportCommentDrafts).filter(([key])=>!key.startsWith(sid+"/")))
    return {reportComments:comments,reportCommentDrafts:drafts,
      reportReviewEpoch:{...s.reportReviewEpoch,[sid]:(s.reportReviewEpoch[sid]||0)+1},
      reportCommentFocus:s.reportCommentFocus?.report.session_id===sid?null:s.reportCommentFocus}
  }),
  setReportComments: (sid, comments) => set((s) => ({reportComments:{...s.reportComments,[sid]:comments}})),
  reportCommentFocus: null,
  reportHistoryEpoch: 0,
  bumpReportHistory: () => set(s=>({reportHistoryEpoch:s.reportHistoryEpoch+1})),
  focusReportComment: (focus) => set({reportCommentFocus:{...focus,nonce:Date.now()+Math.random()}}),
  reportCommentDrafts: {},
  setReportCommentDraft: (key, draft) => set((s) => {const drafts={...s.reportCommentDrafts}; if (draft) drafts[key]=draft; else delete drafts[key]; return {reportCommentDrafts:drafts}}),
  chatAttachmentDrafts: {},
  consumedChatAttachments: {},
  setChatAttachmentDrafts: (sid, update) => set((s) => ({
    chatAttachmentDrafts: { ...s.chatAttachmentDrafts, [sid]: update(s.chatAttachmentDrafts[sid] ?? []) },
  })),
  consumeChatAttachments: (sid, ids) => set((s) => ({
    consumedChatAttachments: { ...s.consumedChatAttachments, [sid]: [...(s.consumedChatAttachments[sid] ?? []), ...ids] },
    chatAttachmentDrafts: { ...s.chatAttachmentDrafts, [sid]: (s.chatAttachmentDrafts[sid] ?? []).filter((d) => !d.attachment || !ids.includes(d.attachment.id)) },
  })),
  clearChatAttachmentDrafts: (sid) => set((s) => ({
    chatAttachmentDrafts: { ...s.chatAttachmentDrafts, [sid]: [] },
    consumedChatAttachments: { ...s.consumedChatAttachments, [sid]: [] },
  })),
  // Shared reading rails follow this browser, independently of the backend source.
  reportChatRatio: normalizeReportSplit(loadPersisted<unknown>("reportChatRatio", DEFAULT_REPORT_SPLIT)),
  setReportChatRatio: (ratio) => {
    const normalized = normalizeReportSplit(ratio)
    set({ reportChatRatio: normalized })
    try { localStorage.setItem("rag_reportChatRatio", JSON.stringify(normalized)) } catch { /* Keep resizing usable without storage. */ }
  },
  viewRailWidth: normalizeViewRailWidth(loadPersisted<unknown>("viewRailWidth", DEFAULT_VIEW_RAIL_WIDTH)),
  setViewRailWidth: (width) => {
    const normalized = normalizeViewRailWidth(width)
    set({ viewRailWidth: normalized })
    try {
      localStorage.setItem("rag_viewRailWidth", JSON.stringify(normalized))
    } catch {
      // Resizing remains usable when browser storage is unavailable.
    }
  },
  // 界面偏好也按来源：每台盒子记住自己的习惯，而不是互相覆盖
  sidebarView: readSourceScoped<SidebarView>("rag_sidebarView", "chat"),
  sidebarOpen: false,
  setSidebarView: (view) => {
    const state = useAppStore.getState()
    // Only guard if navigating away from meeting view
    if (state.sidebarView === "meeting" && view !== "meeting" && state.navigationGuard) {
      if (!state.navigationGuard()) return
    }
    writeSourceScoped("rag_sidebarView", view)
    set({ sidebarView: view })
  },
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),

  // 按来源隔离：切到别的盒子后不该拿上一台的 collection 去请求（会 404 弹错）
  activeCollection: readSourceScoped<string>("rag_activeCollection", ""),
  setActiveCollection: (id) => {
    writeSourceScoped("rag_activeCollection", id)
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
      const current = readSourceScoped<string[]>("rag_selectedCollections", [])
      const cleaned = retainCollectionScope(current, validIds)
      if (cleaned.length !== current.length) {
        writeSourceScoped("rag_selectedCollections", cleaned)
      }
      // Drop activeCollection if it no longer exists
      const active = useAppStore.getState().activeCollection
      if (active && !validIds.has(active)) {
        writeSourceScoped("rag_activeCollection", "")
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

  // 同样是 collection ID 列表 → 按来源隔离，否则切换后勾选的是另一台盒子的库
  selectedCollections: readSourceScoped<string[]>("rag_selectedCollections", []),
  setSelectedCollections: (ids) => {
    writeSourceScoped("rag_selectedCollections", ids)
    set({ selectedCollections: ids })
  },
  toggleCollection: (id) =>
    set((s) => {
      const exists = s.selectedCollections.includes(id)
      const next = exists
        ? s.selectedCollections.filter((c) => c !== id)
        : [...s.selectedCollections, id]
      writeSourceScoped("rag_selectedCollections", next)
      return { selectedCollections: next }
    }),
  removeDeletedCollection: (id) =>
    set((s) => {
      const nextActive = s.activeCollection === id ? "" : s.activeCollection
      const nextSelected = retainCollectionScope(
        s.selectedCollections, new Set(s.selectedCollections.filter((c) => c !== id)),
      )
      try {
        writeSourceScoped("rag_activeCollection", nextActive)
        writeSourceScoped("rag_selectedCollections", nextSelected)
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

  // 按来源隔离：这两个值会直接进请求体（use-stream → provider_id/model），
  // 残留对方的 id 会被后端静默降级成默认模型（不报错，只让用户疑惑）
  activeProvider: readSourceScoped<string | null>("rag_activeProvider", null),
  setActiveProvider: (id) => {
    writeSourceScoped("rag_activeProvider", id)
    set({ activeProvider: id })
  },
  activeModel: readSourceScoped<string | null>("rag_activeModel", null),
  setActiveModel: (model) => {
    writeSourceScoped("rag_activeModel", model)
    set({ activeModel: model })
  },
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
  addLastMessageArtifacts: (data) => set((state) => ({ messages: state.messages.map((m, index) => index === state.messages.length - 1 ? applyChatArtifacts(m, data) : m) })),
  addLastMessageNoteChanges: (changes) =>
    set((s) => {
      const msgs = [...s.messages]
      if (msgs.length > 0) {
        const last = msgs[msgs.length - 1]
        msgs[msgs.length - 1] = { ...last, noteChanges: mergeNoteChanges(last.noteChanges, changes) }
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
        const want = (info?.tool || "").trim()
        let target = -1
        for (let i = 0; i < tl.length; i++) {
          if (tl[i].type !== "tool") continue
          const st = tl[i].toolStatus
          const running =
            tl[i].isStreaming === true ||
            st === "running" ||
            st === "awaiting_confirm"
          if (!running) continue
          if (want && tl[i].tool && tl[i].tool !== want) continue
          target = i
          break
        }
        if (target < 0) {
          for (let i = tl.length - 1; i >= 0; i--) {
            if (tl[i].type === "tool") {
              target = i
              break
            }
          }
        }
        if (target >= 0) {
          const st = info?.status || "done"
          tl[target] = {
            ...tl[target],
            isStreaming: false,
            toolStatus: st,
            tool: info?.tool || tl[target].tool,
            sourcesCount:
              info?.sources_count !== undefined
                ? info.sources_count
                : tl[target].sourcesCount,
            sourceType: info?.source_type || tl[target].sourceType,
            toolResult:
              info?.content !== undefined ? info.content : tl[target].toolResult,
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

  sourceHealth: {},
  identity: null,
  setIdentity: (v) => set({ identity: v }),
  refreshIdentity: async () => {
    try {
      const me = await getIdentityMe()
      set({ identity: me })
    } catch {
      // 拿不到就保持 null（UI 全显示，服务端仍会拦）
    }
  },
  setSourceHealth: (id, v) =>
    set((s) => ({ sourceHealth: { ...s.sourceHealth, [id]: v } })),

  remoteShellMode: false,
  setRemoteShellMode: (v) => set({ remoteShellMode: v }),
  containerDeployment: false,
  setContainerDeployment: (v) => set({ containerDeployment: v }),
  remoteGateReason: null,
  setRemoteGateReason: (r) => set({ remoteGateReason: r }),

  logPanelOpen: false,
  toggleLogPanel: () => set((s) => ({ logPanelOpen: !s.logPanelOpen })),

  developerMode: loadPersisted<boolean>("developerMode", false),
  toggleDeveloperMode: () => set((s) => ({ developerMode: !s.developerMode })),

  locale: (() => {
    const locale = normalizeLocale(loadPersisted<unknown>("locale", "en"))
    applyDocumentLang(locale)
    return locale
  })(),
  setLocale: (locale) => {
    const next = normalizeLocale(locale)
    const cur = useAppStore.getState().locale
    if (next === cur) return
    try {
      localStorage.setItem("rag_locale", JSON.stringify(next))
    } catch { /* ignore */ }
    applyDocumentLang(next)
    set({ locale: next })
    if (useAppStore.getState().remoteShellMode) {
      // 远程模式：语言是壳自己的设置（写瘦壳 config.yaml），绝不写远端
      // 盒子的 config——那是别人的盒子配置，不该被入口壳改写。
      patchShellSettings({ locale: next }).catch(() => {})
    } else {
      updateConfig("locale", { locale: next }).catch(() => {})
    }
    notifyDesktopLocale(next)
  },
  hydrateLocale: (raw) => {
    // 远程模式：语言只存壳本地（localStorage 是唯一事实），不取远端盒子的
    // config 来覆盖——那台盒子有它自己的 locale。
    if (useAppStore.getState().remoteShellMode) return
    if (raw !== "en" && raw !== "zh-CN") return
    const next = raw
    const cur = useAppStore.getState().locale
    if (next === cur) return
    try {
      localStorage.setItem("rag_locale", JSON.stringify(next))
    } catch { /* ignore */ }
    applyDocumentLang(next)
    set({ locale: next })
    notifyDesktopLocale(next)
  },

  activeMeeting: null,
  setActiveMeeting: (id) => set({ activeMeeting: id }),

  navigationGuard: null,
  setNavigationGuard: (guard) => set({ navigationGuard: guard }),

  // ── Session ──
  // sessionId is restored from localStorage; messages are not — must hydrate on Chat enter.
  // 会话 id 属后端实体：按来源隔离，否则切来源会拿对方 id 去拉（404 后被静默清空，
  // 连带把对方那台的上次会话位置也弄丢了）
  sessionId: readSourceScoped<string | null>("rag_sessionId", null),
  sessionHydratedId: null as string | null,
  sessionLoading: false,
  // 默认与今天一致：思考开、Web 搜索关。新会话沿用当前值（"粘手"）。
  thinking: true,
  webSearch: false,
  setThinking: (v) => set({ thinking: v }),
  setWebSearch: (v) => set({ webSearch: v }),
  sessions: [] as import("@/api/client").SessionItem[],
  sessionListStatus: "idle",
  setSessionId: (id) =>
    set(
      id
        ? { sessionId: id }
        : { sessionId: null, sessionHydratedId: null, sessionLoading: false, messages: [] },
    ),
  setSessions: (sessions) => set({ sessions }),
  refreshSessionList: async () => {
    const state = useAppStore.getState()
    if (state.sessions.length === 0 && state.sessionListStatus !== "ready") {
      set({ sessionListStatus: "loading" })
    }
    try {
      set({ sessions: await listSessions(), sessionListStatus: "ready" })
    } catch {
      // A transient failure must not disturb the current list.
      set({ sessionListStatus: "error" })
    }
  },
  invalidateSessionCache: (sessionId) => {
    // Never drop the session on screen: its in-flight stream owns `messages`.
    if (sessionId === useAppStore.getState().sessionId) return
    _invalidateSessionCache(sessionId)
  },
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
        const controller = _streamAborts.get(sessionId)
        const isStreaming = !!controller && !controller.signal.aborted
        set({
          messages: [...cached],
          sessionId,
          sessionHydratedId: sessionId,
          sessionLoading: false,
          isStreaming,
        })
        if (!isStreaming) useAppStore.getState().finishLastMessage()
        // 走内存缓存的快路径不会请求服务端，配置得从本地缓存取。
        // 漏了这一步就会沿用上一个会话的配置 → 一发送就把错配置写进本会话（级联污染）。
        await useAppStore.getState().applySessionConfig(recallSessionConfig(sessionId))
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
              if (m.role === "assistant" && !m.content && meta.tool_calls && !meta.note_changes?.length && !meta.report_changes?.length) {
                return null
              }
              const timeline = buildTimelineFromMeta(meta)
              return {
                id: m.id,
                role: m.role as "user" | "assistant",
                content: m.role === "user" && typeof meta.display_content === "string" ? meta.display_content : m.content,
                attachments: meta.attachments as ChatAttachment[] | undefined,
                sources: m.sources ?? undefined,
                noteChanges: mergeNoteChanges([], meta.note_changes),
                ...applyChatArtifacts({}, meta),
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
        // 记住原始配置（快路径要用），并按当前有效性校验后恢复。
        rememberSessionConfig(sessionId, detail.config)
        await useAppStore.getState().applySessionConfig(detail.config)
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
  applySessionConfig: async (config) => {
    // undefined = 这个会话的配置未知（缓存未命中）→ 保持现状，无从恢复
    if (config === undefined) return
    const st = useAppStore.getState()
    const resolved = resolveSessionConfig(config, {
      providers: st.providers,
      collectionIds: new Set(st.collections.map((c) => c.id)),
    })
    // 五项**全部写入**（缺失项已由 resolveSessionConfig 补成默认）。
    // 曾经是「有值才写」，导致打开已有会话时 store 仍留着上一个会话的配置
    // ——一旦发送就把错配置写进本会话，级联污染（2026-09-16 用户报的 bug）。
    st.setActiveProvider(resolved.provider_id)
    st.setActiveModel(resolved.model)
    st.setSelectedCollections(resolved.collections)
    st.setThinking(resolved.thinking)
    st.setWebSearch(resolved.web_search)
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
    forgetSessionConfig(sessionId)
    await deleteSession(sessionId)
    useAppStore.getState().clearReportReviewSession(sessionId)
    useAppStore.getState().clearChatAttachmentDrafts(sessionId)
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
    writeSourceScoped("rag_activeProvider", state.activeProvider)
    writeSourceScoped("rag_activeModel", state.activeModel)
    writeSourceScoped("rag_selectedCollections", state.selectedCollections)
    writeSourceScoped("rag_sessionId", state.sessionId)
    localStorage.setItem("rag_developerMode", JSON.stringify(state.developerMode))
  }, 500)
})

bindLocaleGetter(() => useAppStore.getState().locale)
