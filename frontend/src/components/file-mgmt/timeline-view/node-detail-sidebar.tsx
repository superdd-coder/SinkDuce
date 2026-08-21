import {
  useState,
  useEffect,
  useLayoutEffect,
  useCallback,
  useRef,
  useMemo,
} from "react"
import { DatePicker } from "@/components/ui/date-picker"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import {
  X,
  Edit3,
  Plus,
  Clock,
  Loader2,
  Trash2,
  Video,
} from "lucide-react"
import { toast } from "sonner"
import { useT } from "@/i18n/use-t"
import { systemFolderDisplayName } from "@/i18n/system-folder"
import { formatApiError } from "@/api/http"
import { cn } from "@/lib/utils"
import type {
  FileSummary,
  Message,
  NodeDetail,
  NodeGroup,
} from "@/types/file-mgmt"
import {
  getNodeDetail,
  updateNode,
  deleteNode,
  getNodeMessages,
  getFileMessages,
  createNodeMessage,
  updateMessage,
  detachFileFromNode,
} from "@/api/file-mgmt"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { useAppStore } from "@/stores/app-store"
import { NodeFileAttach } from "./node-file-attach"
import { MessageEditorDialog } from "../folder-view/message-editor-dialog"
import { FileMgmtDetailDialog } from "@/components/file-mgmt/file-detail"
import { FileSelectPreviewFloating } from "@/components/file-mgmt/file-select-preview-panel"

/** Short list time — same language as MessageEditorDialog node rail. */
function formatMsgListTime(iso: string | null | undefined): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ""
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

/** Plain excerpt for list rows (matches message-editor-dialog). */
function messagePlainExcerpt(body: string): string {
  return (body || "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`[^`]*`/g, " ")
    .replace(/!\[.*?\]\(.*?\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/[*_~>|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
}

/** Normalize stored event_time to yyyy-mm-dd for DatePicker. */
function toDateInputValue(raw: string | null | undefined): string {
  if (!raw) return ""
  const m = raw.match(/^(\d{4}-\d{2}-\d{2})/)
  if (m) return m[1]
  const d = new Date(raw)
  if (Number.isNaN(d.getTime())) return ""
  const y = d.getFullYear()
  const mo = String(d.getMonth() + 1).padStart(2, "0")
  const day = String(d.getDate()).padStart(2, "0")
  return `${y}-${mo}-${day}`
}

/** Meeting-ingest anchors use external_ref = `meeting:{meetingId}`. */
function meetingIdFromExternalRef(
  ref: string | null | undefined,
): string | null {
  const raw = (ref || "").trim()
  if (!raw.startsWith("meeting:")) return null
  const id = raw.slice("meeting:".length).split(":")[0]?.trim()
  return id || null
}

interface NodeDetailSidebarProps {
  collectionId: string
  nodeId: string | null
  onClose: () => void
  /** Hide header close control (deselect node closes the panel). */
  hideCloseButton?: boolean
  onNodeUpdated: () => void
  getGroupName: (groupId: string | null) => string
  groups: NodeGroup[]
}

export function NodeDetailSidebar({
  collectionId,
  nodeId,
  onClose,
  hideCloseButton = false,
  onNodeUpdated,
  groups,
}: NodeDetailSidebarProps) {
  const t = useT()
  const [detail, setDetail] = useState<NodeDetail | null>(null)
  /** Only true for cold open (no shell yet) — never flash cards on refresh. */
  const [loading, setLoading] = useState(false)
  /**
   * Content crossfade when switching nodes (shell stays; body fades).
   * out → swap data → in
   */
  const [bodyPhase, setBodyPhase] = useState<"in" | "out">("in")
  const [editingTitle, setEditingTitle] = useState(false)
  const [editTitle, setEditTitle] = useState("")
  const titleInputRef = useRef<HTMLTextAreaElement>(null)
  /** Escape cancel must not race with blur/outside-click save */
  const skipTitleSaveRef = useRef(false)
  /** Latest draft for outside-click / blur (avoid stale closures) */
  const editTitleRef = useRef(editTitle)
  editTitleRef.current = editTitle
  /** Sync flag so pointerdown + blur don't double-save */
  const editingTitleRef = useRef(false)
  editingTitleRef.current = editingTitle

  const [eventTime, setEventTime] = useState("")
  const [messages, setMessages] = useState<Message[]>([])
  const [msgTab, setMsgTab] = useState<"all" | "node">("all")
  const [attachOpen, setAttachOpen] = useState(false)
  /** Select-existing tree open inside attach zone */
  const [selectTreeOpen, setSelectTreeOpen] = useState(false)
  /**
   * Accordion: fixed rail height, only one expanded card at a time
   * (Overview Notes/Meetings + File detail side-card language).
   */
  const [expandedCard, setExpandedCard] = useState<"node" | "messages">(
    "messages"
  )
  const [msgDialogOpen, setMsgDialogOpen] = useState(false)
  const [editingMsg, setEditingMsg] = useState<Message | null>(null)
  const [msgDialogReadonly, setMsgDialogReadonly] = useState(false)
  const [detailFileId, setDetailFileId] = useState<string | null>(null)
  const [selectPreviewFile, setSelectPreviewFile] =
    useState<FileSummary | null>(null)
  const sidebarPanelRef = useRef<HTMLDivElement>(null)
  const detailRef = useRef<NodeDetail | null>(null)
  detailRef.current = detail
  const switchGenRef = useRef(0)
  /** Sequential silk: out → commit → in (match preview panel language) */
  const BODY_OUT_MS = 160

  /**
   * Pixel-height accordion (Node ↔ Messages).
   * flex-grow is not silk when the two cards have very different collapsed
   * heights; we lock current px heights → set React state → tween to targets.
   */
  const ACC_MS = 280
  const ACC_EASE = "cubic-bezier(0.45, 0.05, 0.55, 0.95)"
  const accStackRef = useRef<HTMLDivElement>(null)
  const nodeCardRef = useRef<HTMLElement | null>(null)
  const msgsCardRef = useRef<HTMLElement | null>(null)
  const accAnimGenRef = useRef(0)
  const accClearTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingAccRef = useRef<{
    fromNode: number
    fromMsgs: number
    next: "node" | "messages"
  } | null>(null)

  const clearAccInlineStyles = useCallback(() => {
    for (const el of [nodeCardRef.current, msgsCardRef.current]) {
      if (!el) continue
      el.style.height = ""
      el.style.flex = ""
      el.style.flexGrow = ""
      el.style.flexShrink = ""
      el.style.flexBasis = ""
      el.style.transition = ""
      el.style.minHeight = ""
      /* Don’t leave overflow:hidden from the tween — it clips soft card shadows */
      el.style.overflow = ""
    }
  }, [])

  /** Collapsed Node = 1/3 of the card stack (gap excluded). */
  const NODE_COLLAPSED_RATIO = 1 / 3

  const measureMsgsHeaderH = (msgs: HTMLElement) => {
    const header = msgs.querySelector(".pm-ws-side-h") as HTMLElement | null
    return Math.ceil(header?.offsetHeight ?? 44)
  }

  /** Start a silk height hand-off to `next` (call before/with setExpandedCard). */
  const beginAccordionHandoff = useCallback((next: "node" | "messages") => {
    const node = nodeCardRef.current
    const msgs = msgsCardRef.current
    if (!node || !msgs) return
    const fromNode = node.getBoundingClientRect().height
    const fromMsgs = msgs.getBoundingClientRect().height
    pendingAccRef.current = { fromNode, fromMsgs, next }
    // Freeze layout immediately so React reflow can't snap
    node.style.flex = "0 0 auto"
    msgs.style.flex = "0 0 auto"
    node.style.height = `${fromNode}px`
    msgs.style.height = `${fromMsgs}px`
    node.style.transition = "none"
    msgs.style.transition = "none"
  }, [])

  // After React commits expandedCard, tween frozen heights → targets
  useLayoutEffect(() => {
    const pending = pendingAccRef.current
    if (!pending || pending.next !== expandedCard) return
    pendingAccRef.current = null

    const stack = accStackRef.current
    const node = nodeCardRef.current
    const msgs = msgsCardRef.current
    if (!stack || !node || !msgs) return

    const gen = ++accAnimGenRef.current
    if (accClearTimerRef.current) {
      clearTimeout(accClearTimerRef.current)
      accClearTimerRef.current = null
    }

    const gap = 10
    const available = Math.max(0, stack.clientHeight - gap)

    // Re-assert freeze at pre-commit heights
    node.style.flex = "0 0 auto"
    msgs.style.flex = "0 0 auto"
    node.style.height = `${pending.fromNode}px`
    msgs.style.height = `${pending.fromMsgs}px`
    node.style.transition = "none"
    msgs.style.transition = "none"
    node.style.overflow = "hidden"
    msgs.style.overflow = "hidden"

    const msgsHeader = measureMsgsHeaderH(msgs)

    let toNode: number
    let toMsgs: number
    if (pending.next === "messages") {
      // Node collapsed: fixed 1/3 of card area; Messages takes the rest
      toNode = Math.round(available * NODE_COLLAPSED_RATIO)
      toMsgs = Math.max(msgsHeader, available - toNode)
      toNode = available - toMsgs
    } else {
      // Node expanded: Messages header only; Node fills remainder
      toMsgs = Math.min(msgsHeader, available)
      toNode = Math.max(0, available - toMsgs)
    }

    // Reflow, then animate both heights on the same clock
    void node.offsetHeight
    node.style.transition = `height ${ACC_MS}ms ${ACC_EASE}`
    msgs.style.transition = `height ${ACC_MS}ms ${ACC_EASE}`

    requestAnimationFrame(() => {
      if (gen !== accAnimGenRef.current) return
      node.style.height = `${Math.max(0, toNode)}px`
      msgs.style.height = `${Math.max(0, toMsgs)}px`
    })

    accClearTimerRef.current = setTimeout(() => {
      if (gen !== accAnimGenRef.current) return
      clearAccInlineStyles()
      accClearTimerRef.current = null
    }, ACC_MS + 40)

    return () => {
      /* gen guard handles stale */
    }
  }, [expandedCard, clearAccInlineStyles])

  useEffect(() => {
    return () => {
      if (accClearTimerRef.current) clearTimeout(accClearTimerRef.current)
      accAnimGenRef.current += 1
    }
  }, [])

  /** Two-step delete (× → DELETE) — same as Files MessageCard */
  const [deleteArmed, setDeleteArmed] = useState(false)
  const deleteArmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const deleteBtnRef = useRef<HTMLButtonElement>(null)

  const setActiveMeeting = useAppStore((s) => s.setActiveMeeting)
  const setSidebarView = useAppStore((s) => s.setSidebarView)

  const linkedMeetingId = useMemo(
    () => meetingIdFromExternalRef(detail?.external_ref),
    [detail?.external_ref],
  )

  const goToLinkedMeeting = useCallback(() => {
    if (!linkedMeetingId) return
    setActiveMeeting(linkedMeetingId)
    setSidebarView("meeting")
  }, [linkedMeetingId, setActiveMeeting, setSidebarView])

  const disarmDelete = useCallback(() => {
    setDeleteArmed(false)
    if (deleteArmTimerRef.current) {
      clearTimeout(deleteArmTimerRef.current)
      deleteArmTimerRef.current = null
    }
  }, [])

  const armDelete = useCallback(() => {
    setDeleteArmed(true)
    if (deleteArmTimerRef.current) clearTimeout(deleteArmTimerRef.current)
    deleteArmTimerRef.current = setTimeout(() => disarmDelete(), 4000)
  }, [disarmDelete])

  useEffect(() => {
    if (!deleteArmed) return
    const onPointerDown = (ev: Event) => {
      const t = ev.target as Node | null
      if (t && deleteBtnRef.current?.contains(t)) return
      disarmDelete()
    }
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") disarmDelete()
    }
    const t = window.setTimeout(() => {
      document.addEventListener("pointerdown", onPointerDown, true)
      document.addEventListener("keydown", onKey, true)
    }, 0)
    return () => {
      window.clearTimeout(t)
      document.removeEventListener("pointerdown", onPointerDown, true)
      document.removeEventListener("keydown", onKey, true)
    }
  }, [deleteArmed, disarmDelete])

  type LoadedNode = {
    detail: NodeDetail
    messages: Message[]
    eventTime: string
  }

  /** Latest collection / node — ignore stale loads after library switch. */
  const collectionIdRef = useRef(collectionId)
  collectionIdRef.current = collectionId
  const nodeIdRef = useRef(nodeId)
  nodeIdRef.current = nodeId

  /** Fetch only — does not write React state (commit happens after fade-out). */
  const loadNodeBundle = useCallback(
    async (id: string): Promise<LoadedNode | null> => {
      const col = collectionId
      try {
        const d = await getNodeDetail(col, id)
        // Drop if user switched library / selection mid-flight
        if (collectionIdRef.current !== col || nodeIdRef.current !== id) {
          return null
        }
        const nodeMsgs = await getNodeMessages(col, id)
        if (collectionIdRef.current !== col || nodeIdRef.current !== id) {
          return null
        }
        const fileMsgLists = await Promise.all(
          (d.attachments ?? []).map((a) =>
            getFileMessages(col, a.file_id).catch(() => [] as Message[])
          )
        )
        if (collectionIdRef.current !== col || nodeIdRef.current !== id) {
          return null
        }
        const fileMsgs = fileMsgLists.flat()
        const merged = [...nodeMsgs, ...fileMsgs].sort((a, b) =>
          (b.created_at || "").localeCompare(a.created_at || "")
        )
        return {
          detail: d,
          messages: merged,
          eventTime: toDateInputValue(d.event_time),
        }
      } catch (err) {
        // Library switch / deselection: never toast a ghost 404
        if (collectionIdRef.current !== col || nodeIdRef.current !== id) {
          return null
        }
        const msg = err instanceof Error ? err.message : String(err)
        // Soft-fail missing node (stale selection) without scaring the user
        if (/not found/i.test(msg)) {
          return null
        }
        toast.error(t("fileMgmt.failedLoadNode", { error: msg }))
        return null
      }
    },
    [collectionId]
  )

  const commitBundle = useCallback((bundle: LoadedNode) => {
    setDetail(bundle.detail)
    setMessages(bundle.messages)
    setEventTime(bundle.eventTime)
  }, [])

  /**
   * Silent refresh of the *current* node (attach / edit / detach).
   * Never triggers switch fade or Loading shell.
   * Skips commit if user already switched away.
   */
  const fetchDetail = useCallback(
    async (opts?: { silent?: boolean; forNodeId?: string | null }) => {
      const id = opts?.forNodeId !== undefined ? opts.forNodeId : nodeId
      if (!id) {
        setDetail(null)
        setMessages([])
        setEventTime("")
        return null
      }
      const silent =
        opts?.silent !== undefined ? opts.silent : detailRef.current != null
      if (!silent) setLoading(true)
      try {
        const bundle = await loadNodeBundle(id)
        if (!bundle) {
          if (!silent) {
            setDetail(null)
            setEventTime("")
          }
          return null
        }
        // Drop stale refresh if selection moved
        if (nodeId && id !== nodeId) return bundle.detail
        commitBundle(bundle)
        return bundle.detail
      } finally {
        if (!silent) setLoading(false)
      }
    },
    [nodeId, loadNodeBundle, commitBundle]
  )

  // Cold open / node switch — hold previous content until new data is ready,
  // then sequential out → commit → in (no mid-fade data swap / empty flash).
  useEffect(() => {
    if (!nodeId) {
      setDetail(null)
      setMessages([])
      setEventTime("")
      setLoading(false)
      setBodyPhase("in")
      return
    }

    const gen = ++switchGenRef.current
    const prevId = detailRef.current?.node_id ?? null
    const hadShell = detailRef.current != null
    const switchingNode = hadShell && prevId !== nodeId

    disarmDelete()
    setEditingTitle(false)
    setSelectPreviewFile(null)
    // Soft-close attach/picker (height silk via CSS); keep accordion expansion

    let outTimer: ReturnType<typeof setTimeout> | null = null
    let inRaf1 = 0
    let inRaf2 = 0

    const sleep = (ms: number) =>
      new Promise<void>((resolve) => {
        outTimer = setTimeout(resolve, ms)
      })

    const run = async () => {
      if (!hadShell) setLoading(true)

      // 1) Load while still showing previous node (stale-while-revalidate)
      const bundle = await loadNodeBundle(nodeId)
      if (gen !== switchGenRef.current) return

      if (!bundle) {
        if (!hadShell) {
          setDetail(null)
          setLoading(false)
        }
        return
      }

      if (switchingNode) {
        // Soft close attach before content fade so height eases first
        setAttachOpen(false)
        setSelectTreeOpen(false)

        // 2) Fade out previous content (cards stay mounted)
        setBodyPhase("out")
        await sleep(BODY_OUT_MS)
        if (gen !== switchGenRef.current) return

        // 3) Commit only after fully out — no mid-transition hard swap
        commitBundle(bundle)
        setLoading(false)

        // 4) Double rAF so browser paints new content at opacity 0, then fade in
        inRaf1 = requestAnimationFrame(() => {
          inRaf2 = requestAnimationFrame(() => {
            if (gen !== switchGenRef.current) return
            setBodyPhase("in")
          })
        })
      } else {
        // Cold open or same-node re-entry
        commitBundle(bundle)
        setLoading(false)
        setBodyPhase("in")
        setAttachOpen(false)
        setSelectTreeOpen(false)
      }
    }

    void run()
    return () => {
      if (outTimer) clearTimeout(outTimer)
      if (inRaf1) cancelAnimationFrame(inRaf1)
      if (inRaf2) cancelAnimationFrame(inRaf2)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collectionId, nodeId])

  // File tree / attach open → expand Node card (with height hand-off)
  useEffect(() => {
    if (!(attachOpen || selectTreeOpen)) return
    if (expandedCard === "node") return
    beginAccordionHandoff("node")
    setExpandedCard("node")
  }, [attachOpen, selectTreeOpen, expandedCard, beginAccordionHandoff])

  /** Messages open — pixel height hand-off (works both from Attach close & header). */
  const expandMessages = useCallback(() => {
    if (expandedCard === "messages" && !attachOpen) return
    setSelectTreeOpen(false)
    setSelectPreviewFile(null)
    beginAccordionHandoff("messages")
    setAttachOpen(false)
    setExpandedCard("messages")
  }, [expandedCard, attachOpen, beginAccordionHandoff])

  /** Node takes free space (optional attach open). */
  const expandNodeCard = useCallback(
    (opts?: { attach?: boolean }) => {
      if (opts?.attach) setAttachOpen(true)
      if (expandedCard !== "node") {
        beginAccordionHandoff("node")
        setExpandedCard("node")
      }
    },
    [expandedCard, beginAccordionHandoff]
  )

  /** Fit title field height to content so edit matches multi-line display. */
  const syncTitleInputHeight = useCallback(() => {
    const el = titleInputRef.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = `${el.scrollHeight}px`
  }, [])

  useLayoutEffect(() => {
    if (!editingTitle) return
    syncTitleInputHeight()
    const el = titleInputRef.current
    if (!el) return
    el.focus()
    /* Caret at end — feels like continuing the displayed title, not a form field */
    const len = el.value.length
    el.setSelectionRange(len, len)
  }, [editingTitle, syncTitleInputHeight])

  const cancelTitleEdit = useCallback(() => {
    skipTitleSaveRef.current = true
    setEditingTitle(false)
    setEditTitle(detail?.title ?? "")
  }, [detail?.title])

  const handleSaveTitle = useCallback(async () => {
    if (skipTitleSaveRef.current) {
      skipTitleSaveRef.current = false
      return
    }
    /* pointerdown outside + blur both fire — only the first commits */
    if (!editingTitleRef.current) return
    editingTitleRef.current = false
    setEditingTitle(false)

    const d = detailRef.current
    if (!d) return
    const next = editTitleRef.current.trim()
    const prev = (d.title ?? "").trim()
    /* Empty or unchanged → already left edit mode */
    if (!next || next === prev) {
      setEditTitle(d.title ?? "")
      return
    }
    try {
      await updateNode(collectionId, d.node_id, {
        title: next,
        version: d.version,
      })
      void fetchDetail({ silent: true })
      onNodeUpdated()
    } catch (err) {
      /* Re-open edit so the user can retry */
      editingTitleRef.current = true
      setEditingTitle(true)
      toast.error(t("fileMgmt.failed", { error: formatApiError(err, t) }))
    }
  }, [collectionId, fetchDetail, onNodeUpdated])

  /**
   * Click anywhere outside the title field → save (blank rail, tags, messages…).
   * pointerdown capture runs before focus changes / preventDefault blur quirks.
   */
  useEffect(() => {
    if (!editingTitle) return
    const onPointerDown = (e: PointerEvent) => {
      const el = titleInputRef.current
      const t = e.target
      if (!el || !(t instanceof Node)) return
      if (el.contains(t)) return
      void handleSaveTitle()
    }
    document.addEventListener("pointerdown", onPointerDown, true)
    return () =>
      document.removeEventListener("pointerdown", onPointerDown, true)
  }, [editingTitle, handleSaveTitle])

  const startTitleEdit = useCallback(() => {
    if (!detail) return
    setEditTitle(detail.title ?? "")
    setEditingTitle(true)
  }, [detail])

  /** Click group option → save immediately (no confirm step). */
  const handleSelectGroup = async (groupId: string) => {
    if (!detail) return
    const next = groupId || null
    if (next === (detail.group_id ?? null)) return
    try {
      await updateNode(collectionId, detail.node_id, {
        group_id: next,
        version: detail.version,
      })
      void fetchDetail({ silent: true })
      onNodeUpdated()
    } catch (err) {
      toast.error(t("fileMgmt.failed", { error: formatApiError(err, t) }))
    }
  }

  const handleSaveEventTime = async (value: string) => {
    if (!detail) return
    const next = value || null
    const prev = toDateInputValue(detail.event_time) || null
    if (next === prev) return
    try {
      await updateNode(collectionId, detail.node_id, {
        event_time: next,
        version: detail.version,
      })
      setEventTime(value)
      void fetchDetail({ silent: true })
      onNodeUpdated()
      toast.success(t("fileMgmt.eventTimeUpdated"))
    } catch (err) {
      toast.error(t("fileMgmt.failed", { error: formatApiError(err, t) }))
      setEventTime(toDateInputValue(detail.event_time))
    }
  }

  const handleDeleteNode = async () => {
    if (!detail) return
    try {
      await deleteNode(collectionId, detail.node_id)
      onClose()
      onNodeUpdated()
      toast.success(t("fileMgmt.nodeDeleted"))
    } catch (err) {
      toast.error(t("fileMgmt.deleteFailed", { error: formatApiError(err, t) }))
    }
  }

  const handleToggleDefinitive = async (
    fileId: string,
    currentDefinitive: boolean,
    version: number
  ) => {
    await useFileMgmtStore
      .getState()
      .toggleDefinitive(collectionId, fileId, !currentDefinitive, version)
    void fetchDetail({ silent: true })
    onNodeUpdated()
  }

  const handleDetachFile = async (fileId: string) => {
    if (!detail) return
    // Optimistic remove so list doesn’t wait for network + no shell flash
    const prev = detail
    setDetail({
      ...detail,
      attachments: (detail.attachments ?? []).filter((a) => a.file_id !== fileId),
    })
    try {
      await detachFileFromNode(collectionId, detail.node_id, fileId)
      void fetchDetail({ silent: true })
      onNodeUpdated()
      toast.success(t("fileMgmt.fileDetached"))
    } catch (err) {
      setDetail(prev)
      toast.error(t("fileMgmt.failed", { error: formatApiError(err, t) }))
    }
  }

  const refreshMessages = useCallback(async () => {
    if (!detail) return
    try {
      // Full re-merge (node + file msgs) so list stays consistent with fetchDetail
      void fetchDetail({ silent: true })
    } catch {
      /* ignore */
    }
  }, [detail, fetchDetail])

  const handleAddMessage = useCallback(
    async (content: string) => {
      if (!detail || !content.trim()) return
      try {
        await createNodeMessage(collectionId, detail.node_id, {
          owner_type: "node",
          owner_id: detail.node_id,
          body: content.trim(),
          author_type: "user",
        })
        await refreshMessages()
        toast.success(t("fileMgmt.messageAdded"))
      } catch (err) {
        toast.error(t("fileMgmt.failed", { error: formatApiError(err, t) }))
      }
    },
    [collectionId, detail, refreshMessages]
  )

  const handleEditMessage = useCallback(
    async (content: string) => {
      if (!editingMsg || !content.trim()) return
      try {
        await updateMessage(collectionId, editingMsg.message_id, {
          body: content.trim(),
          version: editingMsg.version,
        })
        setEditingMsg(null)
        await refreshMessages()
        toast.success(t("fileMgmt.messageUpdated"))
      } catch (err) {
        toast.error(t("fileMgmt.failed", { error: formatApiError(err, t) }))
      }
    },
    [collectionId, editingMsg, refreshMessages]
  )

  const msgDialogCloseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null
  )
  const MSG_DIALOG_CLOSE_MS = 320

  const handleCloseMsgDialog = useCallback((next: boolean) => {
    if (msgDialogCloseTimerRef.current) {
      clearTimeout(msgDialogCloseTimerRef.current)
      msgDialogCloseTimerRef.current = null
    }
    setMsgDialogOpen(next)
    if (!next) {
      msgDialogCloseTimerRef.current = setTimeout(() => {
        setEditingMsg(null)
        setMsgDialogReadonly(false)
        msgDialogCloseTimerRef.current = null
      }, MSG_DIALOG_CLOSE_MS)
    }
  }, [])

  const openMsgDialog = useCallback(
    (opts: { msg?: Message | null; readonly: boolean }) => {
      setEditingMsg(opts.msg ?? null)
      setMsgDialogReadonly(opts.readonly)
      setMsgDialogOpen(false)
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          setMsgDialogOpen(true)
        })
      })
    },
    []
  )

  const handleOpenForAdd = useCallback(() => {
    openMsgDialog({ msg: null, readonly: false })
  }, [openMsgDialog])

  const handleOpenForView = useCallback(
    (msg: Message) => {
      openMsgDialog({ msg, readonly: true })
    },
    [openMsgDialog]
  )

  const filteredMessages = useMemo(() => {
    if (msgTab === "node") {
      return messages.filter((m) => m.owner_type === "node")
    }
    return messages
  }, [messages, msgTab])

  if (!nodeId) return null

  return (
    <div className="relative h-full w-full min-h-0">
      <FileSelectPreviewFloating
        collectionId={collectionId}
        file={selectPreviewFile}
        open={!!selectPreviewFile}
        anchorRef={sidebarPanelRef}
        onClose={() => setSelectPreviewFile(null)}
      />

      {/*
        Layout (unchanged intent):
        - Node always shows title / tags / Attachments+date (never header-only)
        - Attach zone only when attachOpen
        - Messages list body opens when messages expanded
        Motion: pure flex-grow hand-off only (no full-card collapse).
      */}
      <div
        ref={sidebarPanelRef}
        data-node-detail-sidebar
        className={cn(
          "pm-timeline-node-rail pm-ws-side pm-node-rail h-full w-full min-h-0",
          detail && (bodyPhase === "out" ? "is-body-out" : "is-body-in")
        )}
      >
        {loading && !detail ? (
          <section className="pm-ws-side-card flex-1 flex items-center justify-center gap-2 py-10">
            <Loader2 className="h-4 w-4 animate-spin text-[var(--pm-faint)]" />
            <span className="pm-meta">{t("common.loading")}</span>
          </section>
        ) : !detail ? (
          <section className="pm-ws-side-card flex-1 flex items-center justify-center px-5 py-10">
            <p className="pm-meta">{t("fileMgmt.nodeNotFound")}</p>
          </section>
        ) : (
          <div ref={accStackRef} className="pm-timeline-acc-stack">
            {/* ── Node card — chrome always visible ── */}
            <section
              ref={(el) => {
                nodeCardRef.current = el
              }}
              className={cn(
                "pm-ws-side-card pm-timeline-node-card",
                expandedCard === "node" && "is-expanded"
              )}
            >
              {/* Title bar is chrome only — does not expand/collapse the card */}
              <div className="pm-ws-side-h">
                <span className="pm-timeline-panel-title">{t("fileMgmt.node")}</span>
                <div className="ml-auto flex items-center gap-0.5 shrink-0">
                  {!hideCloseButton && (
                    <button
                      type="button"
                      className="p-1 text-[var(--pm-faint)] hover:text-[var(--pm-ink)] transition-colors rounded-[var(--pm-r-sm)]"
                      onClick={onClose}
                      title={t("common.close")}
                    >
                      <X className="h-4 w-4" />
                    </button>
                  )}
                  {linkedMeetingId && (
                    <button
                      type="button"
                      className="p-1 text-[var(--pm-faint)] hover:text-[var(--pm-green)] transition-colors rounded-[var(--pm-r-sm)]"
                      onClick={(e) => {
                        e.preventDefault()
                        e.stopPropagation()
                        goToLinkedMeeting()
                      }}
                      title={t("fileMgmt.openMeeting")}
                      aria-label={t("fileMgmt.openLinkedMeeting")}
                    >
                      <Video
                        className="h-3.5 w-3.5"
                        strokeWidth={1.75}
                        aria-hidden
                      />
                    </button>
                  )}
                  <button
                    ref={deleteBtnRef}
                    type="button"
                    className={cn("pm-msg-delete", deleteArmed && "is-confirm")}
                    title={
                      deleteArmed
                        ? "Click again to delete node"
                        : "Delete node"
                    }
                    aria-label={
                      deleteArmed ? "Confirm delete node" : "Delete node"
                    }
                    aria-expanded={deleteArmed}
                    onClick={(e) => {
                      e.preventDefault()
                      e.stopPropagation()
                      if (!deleteArmed) {
                        armDelete()
                        return
                      }
                      disarmDelete()
                      void handleDeleteNode()
                    }}
                  >
                    {!deleteArmed ? (
                      <Trash2
                        className="pm-msg-delete-x h-3.5 w-3.5"
                        strokeWidth={1.75}
                        aria-hidden
                      />
                    ) : (
                      <span className="pm-msg-delete-label is-solo">{t("common.delete")}</span>
                    )}
                  </button>
                </div>
              </div>

              {/* Always flex-1 so free height lives in the pad (never hard-cut on expand toggle) */}
              <div className="pm-ws-side-pad pt-0 pb-3 min-h-0 flex flex-col flex-1">
                <div className="pm-node-id-row shrink-0">
                  <div className="pm-node-id-main min-w-0 flex-1 group/title">
                    <div className="flex items-start gap-1.5 min-w-0">
                      {editingTitle ? (
                        <textarea
                          ref={titleInputRef}
                          className="pm-node-id-title pm-node-id-title-input flex-1 min-w-0"
                          value={editTitle}
                          rows={1}
                          spellCheck={false}
                          aria-label={t("common.title")}
                          onChange={(e) => {
                            setEditTitle(e.target.value)
                            /* Resize after React paints next value */
                            requestAnimationFrame(syncTitleInputHeight)
                          }}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.preventDefault()
                              void handleSaveTitle()
                            } else if (e.key === "Escape") {
                              e.preventDefault()
                              cancelTitleEdit()
                            }
                          }}
                          /* Blur still saves (e.g. Tab); outside click uses pointerdown */
                          onBlur={() => void handleSaveTitle()}
                        />
                      ) : (
                        <p
                          className="pm-node-id-title flex-1 min-w-0 cursor-text"
                          onDoubleClick={startTitleEdit}
                          title={t("fileMgmt.doubleClickEdit")}
                        >
                          {(detail.title || "").trim() || "Untitled"}
                        </p>
                      )}
                      {!editingTitle && (
                        <button
                          type="button"
                          className="opacity-0 group-hover/title:opacity-100 transition-opacity text-[var(--pm-faint)] hover:text-[var(--pm-ink)] shrink-0 mt-1"
                          onClick={startTitleEdit}
                          title={t("meeting.editTitle")}
                        >
                          <Edit3 className="h-3 w-3" />
                        </button>
                      )}
                    </div>

                    {(detail.attachments?.length ?? 0) > 0 && (
                      <p className="pm-meta mt-1">
                        {detail.attachments.length} file
                        {detail.attachments.length === 1 ? "" : "s"}
                      </p>
                    )}
                  </div>

                  <div className="pm-node-tag-col shrink-0">
                    {/* One click opens menu; pick option saves; outside click closes */}
                    <DropdownSelect
                      size="tag"
                      className="w-full min-w-0"
                      value={detail.group_id ?? ""}
                      onChange={(v) => void handleSelectGroup(v)}
                      placeholder={t("fileMgmt.noGroup")}
                      options={[
                        { value: "", label: t("fileMgmt.noGroup") },
                        ...groups.map((g) => ({
                          value: g.group_id,
                          label: systemFolderDisplayName(g.name, t),
                        })),
                      ]}
                    />
                    <span
                      className="pm-node-tag is-branch capitalize"
                      title={`Type · ${detail.node_type}`}
                    >
                      {detail.node_type}
                    </span>
                  </div>
                </div>

                {/* Attach zone — only grows when open; Node chrome stays */}
                <div
                  className={cn(
                    "pm-timeline-attach-expand",
                    attachOpen && "is-open"
                  )}
                >
                  <div className="pm-timeline-attach-expand-inner">
                    <div className="pt-2 min-h-0 flex flex-col h-full">
                      <NodeFileAttach
                        collectionId={collectionId}
                        nodeId={detail.node_id}
                        active={attachOpen}
                        attachments={detail.attachments ?? []}
                        onAttached={() => {
                          void fetchDetail({ silent: true })
                          onNodeUpdated()
                        }}
                        onPreviewFile={setSelectPreviewFile}
                        onSelectOpenChange={(open) => {
                          setSelectTreeOpen(open)
                          if (!open) setSelectPreviewFile(null)
                          if (open) expandNodeCard({ attach: true })
                        }}
                        onOpenFile={setDetailFileId}
                        onToggleDefinitive={(fileId, cur, ver) => {
                          void handleToggleDefinitive(fileId, cur, ver)
                        }}
                        onDetach={(fileId) => {
                          void handleDetachFile(fileId)
                        }}
                      />
                    </div>
                  </div>
                </div>

                <div
                  data-node-foot
                  className="mt-2.5 flex items-center gap-2 min-w-0 shrink-0"
                >
                  <button
                    type="button"
                    className={cn(
                      "pm-timeline-attach-btn",
                      attachOpen && "is-on"
                    )}
                    onClick={() => {
                      if (attachOpen) {
                        expandMessages()
                      } else {
                        expandNodeCard({ attach: true })
                      }
                    }}
                  >
                    Attachments
                  </button>
                  <div className="ml-auto flex items-center gap-1 shrink-0 min-w-0">
                    <DatePicker
                      size="sm"
                      className="pm-timeline-date-picker"
                      value={eventTime}
                      onChange={(v) => {
                        setEventTime(v)
                        void handleSaveEventTime(v)
                      }}
                      placeholder={t("fileMgmt.setDate")}
                      allowClear
                    />
                  </div>
                </div>
              </div>
            </section>

            {/* ── Messages card — header always; list clipped by card height ── */}
            <section
              ref={(el) => {
                msgsCardRef.current = el
              }}
              className={cn(
                "pm-ws-side-card pm-node-msgs-card pm-timeline-msgs-card",
                expandedCard === "messages" && "is-expanded"
              )}
            >
              <div
                className="pm-ws-side-h cursor-pointer"
                onClick={expandMessages}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault()
                    expandMessages()
                  }
                }}
              >
                <span className="pm-timeline-panel-title">{t("common.messages")}</span>
                <span className="pm-count-pill">{filteredMessages.length}</span>
                <button
                  type="button"
                  className="pm-timeline-scope-add ml-auto"
                  title={t("fileMgmt.addNodeMessage")}
                  onClick={(e) => {
                    e.stopPropagation()
                    expandMessages()
                    handleOpenForAdd()
                  }}
                >
                  <Plus className="h-3.5 w-3.5" />
                </button>
              </div>

              <div
                className={cn(
                  "pm-timeline-msgs-body",
                  expandedCard === "messages" && "is-open"
                )}
              >
                <div className="pm-timeline-msgs-body-inner">
                  <div className="pm-timeline-scope-row !px-3.5 !pb-1.5 shrink-0">
                    {(["all", "node"] as const).map((tab) => (
                      <button
                        key={tab}
                        type="button"
                        className={cn(
                          "pm-timeline-scope-btn capitalize",
                          msgTab === tab && "is-on"
                        )}
                        onClick={() => setMsgTab(tab)}
                      >
                        {tab === "all" ? t("common.all") : t("fileMgmt.node")}
                      </button>
                    ))}
                  </div>

                  {filteredMessages.length === 0 ? (
                    <div className="pm-ws-side-pad pt-0">
                      <p className="pm-meta">
                        {t("fileMgmt.noMessagesClick")}
                      </p>
                    </div>
                  ) : (
                    <div className="pm-node-msg-list flex-1 min-h-0 overflow-y-auto">
                      {filteredMessages.map((m) => {
                        const excerpt = messagePlainExcerpt(m.body || "")
                        const time = formatMsgListTime(m.created_at)
                        return (
                          <div
                            key={m.message_id}
                            role="button"
                            tabIndex={0}
                            onClick={() => handleOpenForView(m)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter" || e.key === " ") {
                                e.preventDefault()
                                handleOpenForView(m)
                              }
                            }}
                            className="pm-msg-card pm-node-msg-row"
                          >
                            <div className="flex items-center gap-1.5 mb-0.5 min-w-0">
                              <Clock
                                className="h-3 w-3 shrink-0 text-[var(--pm-faint)]"
                                strokeWidth={1.75}
                              />
                              {time ? (
                                <span className="pm-meta shrink-0 tabular-nums">
                                  {time}
                                </span>
                              ) : null}
                              {m.edited_at ? (
                                <span className="pm-meta italic shrink-0">
                                  edited
                                </span>
                              ) : null}
                              {m.owner_type && m.owner_type !== "node" ? (
                                <span className="pm-meta shrink-0 capitalize">
                                  · {m.owner_type}
                                </span>
                              ) : null}
                            </div>
                            <p className="pm-node-msg-excerpt">
                              {excerpt || "Empty message"}
                            </p>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              </div>
            </section>
          </div>
        )}
      </div>

      <MessageEditorDialog
        key="node-message-editor"
        open={msgDialogOpen}
        onOpenChange={handleCloseMsgDialog}
        title={
          msgDialogReadonly
            ? t("common.message")
            : editingMsg
              ? t("fileMgmt.editMessage")
              : t("fileMgmt.addMessage")
        }
        kicker={
          msgDialogReadonly || editingMsg
            ? undefined
            : t("fileMgmt.node")
        }
        description={
          msgDialogReadonly || editingMsg
            ? undefined
            : t("fileMgmt.newMessageOnThisNode")
        }
        initialContent={editingMsg?.body || ""}
        onSave={
          !editingMsg
            ? handleAddMessage
            : !msgDialogReadonly
              ? handleEditMessage
              : () => {}
        }
        readonly={msgDialogReadonly}
        message={editingMsg}
        collectionId={collectionId}
        onSelectNodeMessage={(msg) => {
          setEditingMsg(msg)
          setMsgDialogReadonly(true)
        }}
      />

      <FileMgmtDetailDialog
        collectionId={collectionId}
        fileId={detailFileId}
        open={!!detailFileId}
        onOpenChange={(v) => {
          if (!v) setDetailFileId(null)
        }}
        onDeleted={() => {
          setDetailFileId(null)
          fetchDetail()
          onNodeUpdated()
        }}
        contextNodeId={nodeId}
      />
    </div>
  )
}
