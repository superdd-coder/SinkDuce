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
}

export interface ThinkingIteration {
  iteration: number
  steps: ThinkingStep[]
}

export interface MetaInfo {
  provider?: string
  model?: string
  search_mode?: string
  mode?: string
  max_iterations?: number
}

export interface Message {
  id: string
  role: "user" | "assistant"
  content: string
  sources?: Source[]
  isStreaming?: boolean
  thinkingSteps?: ThinkingIteration[]
  metaInfo?: MetaInfo
}

export interface LLMProvider {
  id: string
  name: string
  provider: string
  model: string
  base_url: string
  api_key: string

  is_default: boolean
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
import { getCollections } from "@/api/client"

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

  selectedCollections: string[]  // Now stores collection IDs
  setSelectedCollections: (ids: string[]) => void
  toggleCollection: (id: string) => void
  removeDeletedCollection: (id: string) => void

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
  finishLastMessage: () => void
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
  }, 500)
})
