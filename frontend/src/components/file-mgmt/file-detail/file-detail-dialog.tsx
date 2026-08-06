/**
 * Unified file detail dialog (folder / timeline / All Files / Quick Chat).
 * Left: Preview / Parse / Summary / Chunks.
 * Right: paths, nodes, version + message timeline (when file-mgmt file_id exists).
 * Bottom: update / promote / archive / permanent delete (managed files only).
 *
 * Open via `fileId` and/or document `source` (`__file__:{id}` extracts fileId).
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  TabsIndicator,
} from "@/components/ui/tabs"
import {
  Loader2,
  ChevronRight,
  ChevronDown,
  RefreshCw,
  Crosshair,
  Upload,
  Archive,
  ArchiveRestore,
  SearchX,
  Trash2,
  ArrowUpRight,
  PinOff,
  FolderOpen,
  GitBranch,
  Star,
  X,
} from "lucide-react"
import { cn, transformImageBlocks } from "@/lib/utils"
import {
  SoftMenu,
  MenuItem,
  MenuItemDescription,
  MenuItemTitle,
} from "@/components/ui/menu"
import { TiptapEditor } from "@/components/ui/tiptap-editor"
import type { Editor } from "@tiptap/core"
import { useAppStore } from "@/stores/app-store"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  getFilePreviewUrl,
  getDocSummary,
  generateDocSummary,
  getExtractedText,
  getFileChunks,
  getFiles,
  type ChunkDetail,
  type DocSummary,
} from "@/api/client"
import {
  getFileDetail,
  updateFile,
  promoteFilePath,
  demoteFilePath,
  removeFilePath,
  detachFileFromNode,
  toggleFileArchive,
  deleteFile,
  deleteMessage,
  createFileMessage,
} from "@/api/file-mgmt"
import type {
  FileDetail,
  FileNodeRef,
  FilePath,
  FileVersion,
  Message,
} from "@/types/file-mgmt"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { NodePreviewSheet } from "@/components/file-mgmt/node-preview-sheet"
import { UpdateFileDialog } from "@/components/file-mgmt/file-detail/update-file-dialog"
import { LogMessageDialog } from "@/components/file-mgmt/file-detail/log-message-dialog"
import { MessageEditorDialog } from "@/components/file-mgmt/folder-view/message-editor-dialog"
import { MessageBody } from "@/components/file-mgmt/message-card"
import {
  RawFileViewer,
  resolveRawFilename,
} from "@/components/file-mgmt/raw-file-viewer"
import { toast } from "sonner"

// ── summary generation markers (module-level, same pattern as legacy dialog) ──

const _generating = new Map<string, number>()

function _genKey(collection: string, source: string) {
  return `${collection}::${source}`
}

function _markGenerating(key: string) {
  const now = Date.now()
  _generating.set(key, now)
  try {
    localStorage.setItem(`wk:gen:${key}`, String(now))
  } catch {
    /* ignore */
  }
}

function _unmarkGenerating(key: string) {
  _generating.delete(key)
  try {
    localStorage.removeItem(`wk:gen:${key}`)
  } catch {
    /* ignore */
  }
}

function _isMarked(key: string): boolean {
  if (_generating.has(key)) return true
  try {
    const raw = localStorage.getItem(`wk:gen:${key}`)
    if (raw) {
      const ts = Number(raw)
      if (Date.now() - ts < 300_000) {
        _generating.set(key, ts)
        return true
      }
      localStorage.removeItem(`wk:gen:${key}`)
    }
  } catch {
    /* ignore */
  }
  return false
}

function fileSource(fileId: string) {
  return `__file__:${fileId}`
}

/** Extract file_id from document source when it is a managed file. */
export function parseFileIdFromSource(
  source: string | null | undefined
): string | null {
  if (!source) return null
  const s = source.trim()
  if (!s) return null
  if (s.startsWith("__file__:")) {
    const id = s.slice("__file__:".length).trim()
    return id || null
  }
  // Bare 32-char hex UUID (Info panel sometimes passes file_id without prefix)
  if (/^[a-f0-9]{32}$/i.test(s)) return s.toLowerCase()
  return null
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

// ── timeline merge ──

function isVersionUpdateMessage(m: Message): boolean {
  return (m.owner_type || "").toLowerCase() === "system_version"
}

type TimelineItem =
  | {
      /** Legacy version row with no linked system_version message */
      kind: "version"
      id: string
      created_at: string
      version: FileVersion
    }
  | {
      kind: "message"
      id: string
      created_at: string
      message: Message
      /** True for file version notes (owner_type=system_version) */
      isVersionUpdate: boolean
      version?: FileVersion
    }

function buildTimeline(
  versions: FileVersion[],
  messages: Message[],
  filter: "all" | "versions"
): TimelineItem[] {
  const versionMsgs = messages.filter(isVersionUpdateMessage)
  const userMsgs = messages.filter((m) => !isVersionUpdateMessage(m))

  // Pair system_version messages with file_versions:
  // 1) same created_at (upload writes both with one timestamp)
  // 2) same commit_message / body
  // 3) chronological index fallback
  const versAsc = [...versions].sort((a, b) => a.version_no - b.version_no)
  const msgsAsc = [...versionMsgs].sort((a, b) => {
    const ta = new Date(a.created_at).getTime() || 0
    const tb = new Date(b.created_at).getTime() || 0
    return ta - tb
  })
  const usedVersionIds = new Set<string>()

  const pickVersionForMessage = (m: Message, index: number): FileVersion | undefined => {
    const byTime = versAsc.find(
      (v) =>
        !usedVersionIds.has(v.version_id) &&
        v.created_at &&
        m.created_at &&
        v.created_at === m.created_at
    )
    if (byTime) return byTime
    const body = (m.body || "").trim()
    if (body) {
      const byMsg = versAsc.find(
        (v) =>
          !usedVersionIds.has(v.version_id) &&
          (v.commit_message || "").trim() === body
      )
      if (byMsg) return byMsg
    }
    const byIndex = versAsc[index]
    if (byIndex && !usedVersionIds.has(byIndex.version_id)) return byIndex
    return versAsc.find((v) => !usedVersionIds.has(v.version_id))
  }

  const versionUpdateItems: TimelineItem[] = msgsAsc.map((m, i) => {
    const v = pickVersionForMessage(m, i)
    if (v) usedVersionIds.add(v.version_id)
    return {
      kind: "message" as const,
      id: `msg-${m.message_id}`,
      created_at: m.created_at,
      message: m,
      isVersionUpdate: true,
      version: v,
    }
  })

  // Versions without a system_version message (older data) — display-only
  const orphanVersions: TimelineItem[] = versAsc
    .filter((v) => !usedVersionIds.has(v.version_id))
    .map((v) => ({
      kind: "version" as const,
      id: `ver-${v.version_id}`,
      created_at: v.created_at,
      version: v,
    }))

  const userItems: TimelineItem[] = userMsgs.map((m) => ({
    kind: "message" as const,
    id: `msg-${m.message_id}`,
    created_at: m.created_at,
    message: m,
    isVersionUpdate: false,
  }))

  const all =
    filter === "versions"
      ? [...versionUpdateItems, ...orphanVersions]
      : [...versionUpdateItems, ...orphanVersions, ...userItems]

  return all.sort((a, b) => {
    const ta = new Date(a.created_at).getTime() || 0
    const tb = new Date(b.created_at).getTime() || 0
    return tb - ta
  })
}

function versionUpdateBody(body: string | null | undefined): string {
  const t = (body || "").trim()
  return t || "version update"
}

// ── props ──

export interface FileMgmtDetailDialogProps {
  collectionId: string
  /**
   * Managed file id. Prefer this when opening from folder/timeline.
   * May be omitted if `source` is `__file__:{id}` or a legacy document source.
   */
  fileId?: string | null
  /**
   * Document source string (e.g. `__file__:{id}`, `__note__:…`, `__meeting__:…`).
   * Used for chunks / preview / summary APIs. When `__file__:`, also resolves fileId.
   */
  source?: string | null
  /**
   * When set, focus this historical version (All Files → Old versions).
   * Preview/Parse/Chunks bind to that version's blob, not the current latest.
   */
  versionId?: string | null
  /** storage_file_id for the focused version (fallback if version list not yet loaded). */
  storageFileId?: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
  /** After permanent delete — parent should close + refresh folder. */
  onDeleted?: () => void
  /** Navigate folder view to a path's folder (persistent/derived). */
  onNavigateToFolder?: (folderId: string) => void
  /**
   * When opened from a timeline node, archive/remove target paths whose
   * ``source_node_id`` is this node (group + branch mounts together).
   * Without this, actions fall back to folder-view ``currentFolderId`` and
   * can hit a native upload path instead of the node-related mounts.
   */
  contextNodeId?: string | null
}

export function FileMgmtDetailDialog({
  collectionId,
  fileId: fileIdProp = null,
  source: sourceProp = null,
  versionId: versionIdProp = null,
  storageFileId: storageFileIdProp = null,
  open,
  onOpenChange,
  onDeleted,
  onNavigateToFolder,
  contextNodeId = null,
}: FileMgmtDetailDialogProps) {
  const refreshFiles = useFileMgmtStore((s) => s.refreshFiles)
  const ingestingFiles = useFileMgmtStore((s) => s.ingestingFiles)
  const currentFolderId = useFileMgmtStore((s) => s.currentFolderId)
  const requestTimelineFocus = useFileMgmtStore((s) => s.requestTimelineFocus)
  const { setActiveMeeting, setSidebarView, setPendingOpenNote } = useAppStore()

  const parsedPropId = fileIdProp || parseFileIdFromSource(sourceProp) || null
  /** When opened via __meeting__: / __note__: source, resolve index file_id async. */
  const [resolvedFromSource, setResolvedFromSource] = useState<string | null>(
    null
  )
  const fileId = parsedPropId || resolvedFromSource
  /** Has file-mgmt metadata (paths/versions/actions). */
  const isManagedFile = !!fileId
  const [detail, setDetail] = useState<FileDetail | null>(null)
  const [loading, setLoading] = useState(false)
  /** Default Preview (raw file viewer). */
  const [activeTab, setActiveTab] = useState("raw")

  /**
   * While async ingest runs, only Preview is usable (parse/summary/chunks
   * are not ready yet). Driven by store task polling after upload.
   */
  const ingestProgress = fileId ? ingestingFiles[fileId] : undefined
  const isIngesting = !!ingestProgress

  // Force Preview tab while ingesting; leave other tabs disabled
  useEffect(() => {
    if (isIngesting && activeTab !== "raw") setActiveTab("raw")
  }, [isIngesting, activeTab])

  /**
   * Source used for chunks / extracted text / doc summary.
   * Meeting & note payloads are keyed by __meeting__: / __note__:, NOT __file__:{id}.
   * Prefer: explicit prop → detail.source (from files.json) → synthetic __file__:{id}.
   */
  const docSource = useMemo(() => {
    const prop = (sourceProp || "").trim()
    if (
      prop.startsWith("__meeting__:") ||
      prop.startsWith("__note__:") ||
      prop.startsWith("__url__:") ||
      prop.startsWith("__youtube__:")
    ) {
      return prop
    }
    // Authoritative document source after file detail loads (fixes folder-view open)
    if (detail?.source) return detail.source
    if (prop.startsWith("__file__:")) return prop
    if (fileId) return fileSource(fileId)
    return prop || null
  }, [sourceProp, detail?.source, fileId])

  // Alias used in a few places for open-state / display
  const source = docSource

  useEffect(() => {
    if (!open) {
      setResolvedFromSource(null)
      return
    }
    if (parsedPropId || !sourceProp || !collectionId) return
    // Already a managed prefix or bare uuid handled by parse
    if (
      sourceProp.startsWith("__file__:") ||
      /^[a-f0-9]{32}$/i.test(sourceProp.trim())
    ) {
      return
    }
    let cancelled = false
    getFiles(collectionId)
      .then((res) => {
        if (cancelled) return
        const hit = res.files.find((f) => f.source === sourceProp)
        if (hit?.file_id) setResolvedFromSource(hit.file_id)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [open, parsedPropId, sourceProp, collectionId])

  // Source / chunks
  const [previewContent, setPreviewContent] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [chunks, setChunks] = useState<ChunkDetail[]>([])
  const [chunksTotal, setChunksTotal] = useState(0)
  const [chunksLoading, setChunksLoading] = useState(false)
  const [expandedParents, setExpandedParents] = useState<Set<string>>(new Set())
  const [expandedChunks, setExpandedChunks] = useState<Set<string>>(new Set())
  const [highlightOffset, setHighlightOffset] = useState<number | undefined>()
  const [highlightedIdx, setHighlightedIdx] = useState<number | undefined>()
  const sourceEditorRef = useRef<Editor | null>(null)

  // Summary
  const [docSummary, setDocSummary] = useState<DocSummary | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [, setRenderTick] = useState(0)

  // Timeline
  const [timelineFilter, setTimelineFilter] = useState<"all" | "versions">(
    "all"
  )
  /**
   * Log All|Versions content swap — sequential fade (out → swap → in).
   * Avoid dual-opacity crossfade (phantom list) and hard cut.
   */
  const [logListPhase, setLogListPhase] = useState<"idle" | "out" | "in">(
    "idle"
  )
  const logSwapTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const logScopeRef = useRef<HTMLDivElement>(null)
  const logScopeAllRef = useRef<HTMLButtonElement>(null)
  const logScopeVerRef = useRef<HTMLButtonElement>(null)
  const [logScopeInd, setLogScopeInd] = useState({ left: 2, width: 0 })
  /**
   * Paths | Nodes | Log accordion (Overview Notes/Meetings language).
   * At most one open; default Log. Click open panel again to collapse all.
   * Lower stack keeps fixed height; open card flex-grows when expanded.
   */
  type SideRailPanel = "paths" | "nodes" | "log"
  const [openSide, setOpenSide] = useState<SideRailPanel | null>("log")
  const toggleSide = useCallback((panel: SideRailPanel) => {
    setOpenSide((prev) => (prev === panel ? null : panel))
  }, [])
  const [msgBusy, setMsgBusy] = useState(false)
  /** Open MessageEditorDialog to add a file-level message (no inline textarea). */
  const [addMsgDialogOpen, setAddMsgDialogOpen] = useState(false)
  /** Log card → message detail (normal or version dual-pane) */
  const [logMsgOpen, setLogMsgOpen] = useState<{
    message: Message
    version?: FileVersion | null
  } | null>(null)

  // Actions
  const [updateDialogOpen, setUpdateDialogOpen] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState(false)
  const [actionBusy, setActionBusy] = useState(false)
  /** Bottom action dropdowns — same pattern as folder toolbar (Archive / Delete). */
  const [actionMenu, setActionMenu] = useState<"archive" | "delete" | null>(
    null
  )
  const actionMenuRef = useRef<HTMLDivElement>(null)

  // Close Archive / Delete dropdowns on outside click or Esc (capture phase).
  useEffect(() => {
    if (!actionMenu) return
    const onPointerDown = (e: PointerEvent) => {
      const t = e.target
      if (!(t instanceof Node)) return
      if (actionMenuRef.current?.contains(t)) return
      setActionMenu(null)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setActionMenu(null)
    }
    document.addEventListener("pointerdown", onPointerDown, true)
    document.addEventListener("keydown", onKey, true)
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true)
      document.removeEventListener("keydown", onKey, true)
    }
  }, [actionMenu])

  // Node preview sheet (+ nested file detail from its attachments)
  const [previewNodeId, setPreviewNodeId] = useState<string | null>(null)
  const [nestedDetail, setNestedDetail] = useState<{
    fileId: string
    contextNodeId: string | null
  } | null>(null)

  const genKey =
    collectionId && source ? _genKey(collectionId, source) : null
  const isGenerating = !!(genKey && _isMarked(genKey))

  // GO TO Note / Meeting
  const firstChunk = chunks[0]
  const isNoteFile =
    firstChunk?.file_type === "note" ||
    !!docSource?.startsWith("__note__:")
  const isRecordingFile =
    !!firstChunk?.meeting_id || !!docSource?.startsWith("__meeting__:")
  const noteId = isNoteFile
    ? firstChunk?.note_id ||
      (docSource?.startsWith("__note__:")
        ? docSource.slice("__note__:".length)
        : null)
    : null
  const meetingId = isRecordingFile
    ? firstChunk?.meeting_id ||
      (docSource?.startsWith("__meeting__:")
        ? docSource.split(":")[1] || null
        : null)
    : null
  const goToLabel = isNoteFile
    ? "GO TO THE NOTE"
    : isRecordingFile
      ? "GO TO THE MEETING"
      : null

  const handleGoToSource = () => {
    if (isNoteFile && noteId) {
      setPendingOpenNote(noteId)
      setSidebarView("database")
      onOpenChange(false)
    } else if (isRecordingFile && meetingId) {
      setActiveMeeting(meetingId)
      setSidebarView("meeting")
      onOpenChange(false)
    }
  }

  const loadDetail = useCallback(async () => {
    if (!fileId || !collectionId) {
      setDetail(null)
      return
    }
    setLoading(true)
    try {
      const d = await getFileDetail(collectionId, fileId)
      setDetail(d)
    } catch (err) {
      toast.error(
        `Failed to load file: ${err instanceof Error ? err.message : String(err)}`
      )
      setDetail(null)
    } finally {
      setLoading(false)
    }
  }, [collectionId, fileId])

  // Reset + load when dialog opens / target changes
  useEffect(() => {
    if (!open || (!fileId && !sourceProp && !docSource)) {
      setDetail(null)
      setPreviewContent(null)
      setChunks([])
      setDocSummary(null)
      setActiveTab("raw")
      setUpdateDialogOpen(false)
      setDeleteConfirm(false)
      setAddMsgDialogOpen(false)
      setLogMsgOpen(null)
      setPreviewNodeId(null)
      setNestedDetail(null)
      setActionMenu(null)
      return
    }
    if (fileId) void loadDetail()
    else setDetail(null)
  }, [open, fileId, sourceProp, loadDetail])

  // Version focus: historical (All Files old versions) vs current latest
  const focusVersion = useMemo(() => {
    if (!detail?.versions?.length) {
      // Props alone still allow historical open before detail arrives
      return null
    }
    if (versionIdProp) {
      return (
        detail.versions.find((v) => v.version_id === versionIdProp) || null
      )
    }
    if (storageFileIdProp) {
      // Prefer exact version_id match first; storage names can collide across versions
      const matches = detail.versions.filter(
        (v) => v.storage_file_id === storageFileIdProp
      )
      if (matches.length === 1) return matches[0]
      // Prefer non-current when opening from Old versions list
      const nonCurrent = matches.find(
        (v) => v.version_id !== detail.current_version_id
      )
      return nonCurrent || matches[0] || null
    }
    return null
  }, [detail?.versions, detail?.current_version_id, versionIdProp, storageFileIdProp])

  // True when opened on a non-current version (All Files → Old versions / Log)
  const isHistoricalFocus = !!(
    versionIdProp
      ? !detail?.current_version_id ||
        versionIdProp !== detail.current_version_id
      : focusVersion &&
        detail?.current_version_id &&
        focusVersion.version_id !== detail.current_version_id
  )

  // Storage blob for Preview/Parse.
  // Historical open (All Files → Old versions): NEVER fall back to current
  // filename — missing history blobs must 404, not silently show latest.
  const extractStorageFile = isHistoricalFocus
    ? focusVersion?.storage_file_id ||
      storageFileIdProp ||
      undefined
    : focusVersion?.storage_file_id ||
      storageFileIdProp ||
      detail?.filename ||
      detail?.versions?.find((v) => v.version_id === detail?.current_version_id)
        ?.storage_file_id ||
      undefined

  const focusVersionId =
    focusVersion?.version_id ||
    (isHistoricalFocus || versionIdProp
      ? versionIdProp || undefined
      : undefined) ||
    undefined

  /** Ext of the *viewed* blob (historical storage or current filename). */
  const viewedExt = (
    (
      (isHistoricalFocus
        ? focusVersion?.storage_file_id || storageFileIdProp
        : detail?.filename ||
          focusVersion?.storage_file_id ||
          storageFileIdProp) || ""
    )
      .split(".")
      .pop() || ""
  ).toLowerCase()

  /** Types we never show as Source extract (no text pipeline). */
  const UNSUPPORTED_SOURCE_EXTS = new Set([
    "png",
    "jpg",
    "jpeg",
    "gif",
    "webp",
    "bmp",
    "ico",
    "heic",
    "heif",
    "svg",
    "mp3",
    "mp4",
    "wav",
    "webm",
    "zip",
    "rar",
    "7z",
  ])

  /**
   * Unsupported applies to the *viewed* version:
   * - current: files.unsupported flag
   * - historical: by blob extension (png/jpg/…) even when current is a PDF
   * Opening an old docx of a file whose current is png stays previewable.
   */
  const isUnsupported = isHistoricalFocus
    ? !!viewedExt && UNSUPPORTED_SOURCE_EXTS.has(viewedExt)
    : !!detail?.unsupported ||
      (!!viewedExt && UNSUPPORTED_SOURCE_EXTS.has(viewedExt))

  // Chunks — current version (non-archived) or focused historical version_id
  useEffect(() => {
    if (!open || !collectionId || !docSource) return
    // Wait for detail when managed so we know unsupported before flashing old chunks
    // Historical open can start once we have versionId from props
    if (fileId && !detail && !versionIdProp) return
    // Ingest in progress: don't fetch (empty/partial) chunks; tabs are Preview-only
    if (isIngesting) {
      setChunks([])
      setChunksTotal(0)
      setChunksLoading(false)
      return
    }
    if (isUnsupported) {
      setChunks([])
      setChunksTotal(0)
      setChunksLoading(false)
      return
    }
    let cancelled = false
    setChunksLoading(true)
    getFileChunks(collectionId, docSource, 10000, {
      versionId: focusVersionId || undefined,
    })
      .then((res) => {
        if (!cancelled) {
          setChunks(res.chunks)
          setChunksTotal(res.total)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setChunks([])
          setChunksTotal(0)
        }
      })
      .finally(() => {
        if (!cancelled) setChunksLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [
    open,
    collectionId,
    docSource,
    fileId,
    detail,
    detail?.unsupported,
    isUnsupported,
    isHistoricalFocus,
    focusVersionId,
    versionIdProp,
    isIngesting,
  ])

  // Extracted text for Source — pin to focused version blob (or current)
  useEffect(() => {
    if (!open || !docSource || !collectionId) {
      setPreviewContent(null)
      setPreviewLoading(false)
      return
    }
    // Historical: can fetch as soon as we know the blob name (prop or detail)
    if (fileId && !detail && !storageFileIdProp) return
    // Unsupported type (current flag or viewed ext) — never show another version's text
    if (isUnsupported) {
      setPreviewContent(null)
      setPreviewLoading(false)
      return
    }
    const storageForExtract =
      extractStorageFile || storageFileIdProp || undefined
    if (!storageForExtract && fileId && !detail) return
    let cancelled = false
    setPreviewLoading(true)
    getExtractedText(docSource, collectionId, {
      storageFile: storageForExtract,
      // Always pin version when known — same basename across versions
      versionId:
        focusVersionId ||
        detail?.current_version_id ||
        undefined,
    })
      .then((res) => {
        if (!cancelled) setPreviewContent(res.text || null)
      })
      .catch(() => {
        if (!cancelled) setPreviewContent(null)
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [
    open,
    docSource,
    collectionId,
    fileId,
    detail,
    detail?.unsupported,
    isUnsupported,
    extractStorageFile,
    storageFileIdProp,
    isHistoricalFocus,
    focusVersionId,
  ])

  // Summary: load by focus version_id (current or historical read-only).
  // Re-summarize is UI-blocked for historical; generate API only accepts current.
  useEffect(() => {
    if (!open || !docSource || !collectionId) {
      setDocSummary(null)
      setSummaryLoading(false)
      return
    }
    if (fileId && !detail) return
    // Ingest in progress: Summary not ready — keep Preview-only
    if (isIngesting) {
      setDocSummary(null)
      setSummaryLoading(false)
      return
    }
    if (isUnsupported) {
      setDocSummary(null)
      setSummaryLoading(false)
      return
    }
    let cancelled = false
    setSummaryLoading(true)
    getDocSummary(collectionId, docSource, {
      versionId: focusVersionId || undefined,
    })
      .then((res) => {
        if (!cancelled) setDocSummary(res)
      })
      .catch(() => {
        if (!cancelled) setDocSummary(null)
      })
      .finally(() => {
        if (!cancelled) setSummaryLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [
    open,
    docSource,
    collectionId,
    fileId,
    detail,
    detail?.unsupported,
    isUnsupported,
    isHistoricalFocus,
    focusVersionId,
    isIngesting,
  ])
  // Poll while generating summary
  useEffect(() => {
    if (!isGenerating || !collectionId || !source || !genKey) return
    const startedAt = _generating.get(genKey) || Date.now()
    const poll = setInterval(async () => {
      try {
        const current = await getDocSummary(collectionId, source)
        if (current) {
          clearInterval(poll)
          _unmarkGenerating(genKey)
          setDocSummary(current)
          setRenderTick((k) => k + 1)
        } else if (Date.now() - startedAt > 300_000) {
          clearInterval(poll)
          _unmarkGenerating(genKey)
          toast.error("Summary generation timed out")
          setRenderTick((k) => k + 1)
        }
      } catch {
        /* ignore */
      }
    }, 2000)
    return () => clearInterval(poll)
  }, [isGenerating, collectionId, source, genKey])

  // Scroll to highlighted chunk
  useEffect(() => {
    if (highlightedIdx == null || chunksLoading || chunks.length === 0) return
    const timer = setTimeout(() => {
      const el = document.querySelector(`[data-chunk-index="${highlightedIdx}"]`)
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" })
        setTimeout(() => setHighlightedIdx(undefined), 3000)
      }
    }, 200)
    return () => clearTimeout(timer)
  }, [highlightedIdx, chunksLoading, chunks.length])

  // Map offset → editor scroll
  useEffect(() => {
    if (highlightOffset === undefined || !previewContent) return
    const editor = sourceEditorRef.current
    if (!editor || (editor as { isDestroyed?: boolean }).isDestroyed) return
    const rawLen = previewContent.length
    const textLen = editor.state.doc.textContent.length
    if (rawLen <= 1 || textLen <= 1) return
    const textTarget = Math.round(highlightOffset * (textLen / rawLen))
    let lo = 1
    let hi = editor.state.doc.content.size
    while (lo < hi) {
      const mid = Math.floor((lo + hi) / 2)
      if (editor.state.doc.textBetween(0, mid).length < textTarget) lo = mid + 1
      else hi = mid
    }
    const resolved = editor.state.doc.resolve(lo)
    const domPos = editor.view.domAtPos(resolved.pos)
    const node = domPos.node
    const el =
      node.nodeType === 3 /* TEXT_NODE */
        ? node.parentElement
        : (node as HTMLElement)
    el?.scrollIntoView({ behavior: "smooth", block: "start" })
  }, [previewContent, highlightOffset])

  const isParentChild = chunks.some((c) => c.chunk_type === "parent")
  const groupedChunks = useMemo(() => {
    if (!isParentChild) return null
    const groups: Array<{ parent: ChunkDetail; children: ChunkDetail[] }> = []
    let currentParent: ChunkDetail | null = null
    let currentChildren: ChunkDetail[] = []
    for (const c of chunks) {
      if (c.chunk_type === "parent") {
        if (currentParent)
          groups.push({ parent: currentParent, children: currentChildren })
        currentParent = c
        currentChildren = []
      } else if (c.chunk_type === "child") {
        currentChildren.push(c)
      }
    }
    if (currentParent)
      groups.push({ parent: currentParent, children: currentChildren })
    return groups
  }, [chunks, isParentChild])

  const timeline = useMemo(
    () =>
      detail
        ? buildTimeline(detail.versions, detail.messages, timelineFilter)
        : [],
    [detail, timelineFilter]
  )

  /** Sliding pill under All | Versions — measure active button. */
  const measureLogScopeInd = useCallback(() => {
    const host = logScopeRef.current
    const btn =
      timelineFilter === "all"
        ? logScopeAllRef.current
        : logScopeVerRef.current
    if (!host || !btn) return
    const hr = host.getBoundingClientRect()
    const br = btn.getBoundingClientRect()
    setLogScopeInd({
      left: Math.round(br.left - hr.left),
      width: Math.round(br.width),
    })
  }, [timelineFilter])

  useLayoutEffect(() => {
    measureLogScopeInd()
  }, [measureLogScopeInd, openSide, detail?.messages?.length])

  useEffect(() => {
    const onResize = () => measureLogScopeInd()
    window.addEventListener("resize", onResize)
    return () => window.removeEventListener("resize", onResize)
  }, [measureLogScopeInd])

  useEffect(() => {
    return () => {
      if (logSwapTimerRef.current) clearTimeout(logSwapTimerRef.current)
    }
  }, [])

  /**
   * All ↔ Versions: fade out (~140ms) → swap list → fade in (~180ms).
   * Symmetric soft material; reduced-motion snaps.
   */
  const handleTimelineFilter = useCallback(
    (next: "all" | "versions") => {
      if (next === timelineFilter) return
      if (logSwapTimerRef.current) {
        clearTimeout(logSwapTimerRef.current)
        logSwapTimerRef.current = null
      }
      const reduce =
        typeof window !== "undefined" &&
        window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
      if (reduce) {
        setTimelineFilter(next)
        setLogListPhase("idle")
        return
      }
      setLogListPhase("out")
      logSwapTimerRef.current = setTimeout(() => {
        setTimelineFilter(next)
        setLogListPhase("in")
        logSwapTimerRef.current = setTimeout(() => {
          setLogListPhase("idle")
          logSwapTimerRef.current = null
        }, 200)
      }, 140)
    },
    [timelineFilter]
  )

  const handleLocate = (chunk: ChunkDetail) => {
    if (isIngesting) return
    setHighlightOffset(chunk.char_offset)
    setActiveTab("source")
  }

  const handleTabChange = (tab: string) => {
    if (isIngesting && tab !== "raw") {
      toast.info("Still ingesting — only Preview is available for now.")
      setActiveTab("raw")
      return
    }
    setActiveTab(tab)
  }

  const toggleParent = (id: string) => {
    setExpandedParents((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleChunkExpand = (id: string) => {
    setExpandedChunks((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // ── actions ──

  const handlePromote = async (path: FilePath) => {
    if (!fileId) return
    setActionBusy(true)
    try {
      await promoteFilePath(collectionId, fileId, path.path_id)
      toast.success("Pinned to folder")
      await loadDetail()
      await refreshFiles(collectionId)
    } catch (err) {
      toast.error(
        `Failed: ${err instanceof Error ? err.message : String(err)}`
      )
    } finally {
      setActionBusy(false)
    }
  }

  /**
   * Unpin = demote persistent path back to derived mode, or drop the pin row
   * when a node-derived path for the same folder already exists / no node can
   * re-claim the folder.
   */
  const handleUnpin = async (path: FilePath) => {
    if (!fileId) return
    if (path.source_node_id) {
      toast.error("This path is from a timeline node — use node detach if needed")
      return
    }
    setActionBusy(true)
    try {
      await demoteFilePath(collectionId, fileId, path.path_id)
      toast.success("Unpinned from folder")
      await loadDetail()
      await refreshFiles(collectionId)
    } catch (err) {
      toast.error(
        `Unpin failed: ${err instanceof Error ? err.message : String(err)}`
      )
    } finally {
      setActionBusy(false)
    }
  }

  /**
   * Paths for the dialog's current context:
   * - From a timeline node → all mounts with that source_node_id (group + branch)
   * - From folder view → all mounts in currentFolderId
   * Never fall back across contexts (avoids archiving a native upload path
   * when the dialog was opened from a node).
   */
  const contextPaths = useMemo(() => {
    if (!detail) return [] as FilePath[]
    if (contextNodeId) {
      return detail.paths.filter((p) => p.source_node_id === contextNodeId)
    }
    if (!currentFolderId || currentFolderId === "__archived__") {
      return [] as FilePath[]
    }
    return detail.paths.filter((p) => p.folder_id === currentFolderId)
  }, [detail, contextNodeId, currentFolderId])

  const activeContextPaths = useMemo(
    () => contextPaths.filter((p) => !p.archived),
    [contextPaths]
  )
  const archivedContextPaths = useMemo(
    () => contextPaths.filter((p) => !!p.archived),
    [contextPaths]
  )

  const fileArchived = !!detail?.archived
  const canArchiveCurrentPath =
    activeContextPaths.length > 0 && !fileArchived
  const canArchiveGlobally = !!detail && !fileArchived
  const canRestore =
    !!detail && (fileArchived || archivedContextPaths.length > 0)
  // Node context: allow remove whenever attached (even if paths not yet synced)
  const canRemoveCurrentPath = contextNodeId
    ? !!(detail?.nodes?.some((n) => n.node_id === contextNodeId) ||
        contextPaths.length > 0)
    : contextPaths.length > 0

  /**
   * Unlink context path(s).
   * - Folder view: delete path rows for the open folder.
   * - Node view: detach from the node (drops file_nodes + all derived paths).
   *   Only deleting path rows is not enough — `_sync_node_derived_paths`
   *   recreates them while the attachment still exists.
   */
  const handleRemoveCurrentPath = async () => {
    if (!fileId) return
    if (!contextNodeId && contextPaths.length === 0) return
    setActionBusy(true)
    try {
      if (contextNodeId) {
        await detachFileFromNode(collectionId, contextNodeId, fileId)
        toast.success("Removed from this node (paths cleared)")
      } else {
        for (const p of contextPaths) {
          await removeFilePath(collectionId, fileId, p.path_id)
        }
        toast.success("Removed current path")
      }
      await loadDetail()
      await refreshFiles(collectionId)
    } catch (err) {
      toast.error(
        `Remove path failed: ${err instanceof Error ? err.message : String(err)}`
      )
    } finally {
      setActionBusy(false)
    }
  }

  /** Path-level archive for context mounts only (not native paths outside context). */
  const handleArchiveCurrentPath = async () => {
    if (!detail || !fileId || activeContextPaths.length === 0) return
    setActionBusy(true)
    try {
      await toggleFileArchive(collectionId, fileId, true, detail.version, {
        pathIds: activeContextPaths.map((p) => p.path_id),
        scope: "path",
      })
      toast.success("Archived current path")
      await loadDetail()
      await refreshFiles(collectionId)
    } catch (err) {
      toast.error(
        `Archive failed: ${err instanceof Error ? err.message : String(err)}`
      )
    } finally {
      setActionBusy(false)
    }
  }

  /** File-level archive — exclude from search everywhere. */
  const handleArchiveGlobally = async () => {
    if (!detail || !fileId) return
    setActionBusy(true)
    try {
      await toggleFileArchive(collectionId, fileId, true, detail.version, {
        scope: "file",
      })
      toast.success("Archived globally (excluded from search)")
      await loadDetail()
      await refreshFiles(collectionId)
    } catch (err) {
      toast.error(
        `Archive failed: ${err instanceof Error ? err.message : String(err)}`
      )
    } finally {
      setActionBusy(false)
    }
  }

  /** Restore file-level + path archive for current context mounts. */
  const handleRestore = async () => {
    if (!detail || !fileId) return
    setActionBusy(true)
    try {
      const pathIds = archivedContextPaths.map((p) => p.path_id)
      const folderId =
        !contextNodeId &&
        currentFolderId &&
        currentFolderId !== "__archived__"
          ? currentFolderId
          : null
      await toggleFileArchive(collectionId, fileId, false, detail.version, {
        ...(pathIds.length > 0 ? { pathIds } : {}),
        ...(pathIds.length === 0 && folderId ? { folderId } : {}),
      })
      toast.success("Restored")
      await loadDetail()
      await refreshFiles(collectionId)
    } catch (err) {
      toast.error(
        `Restore failed: ${err instanceof Error ? err.message : String(err)}`
      )
    } finally {
      setActionBusy(false)
    }
  }

  /**
   * Sole switch for Collection Summary participation.
   * Backend: sets is_definitive; if on + no summary → generate then consolidate;
   * if on/off + has summary → debounce consolidate (summary kept).
   */
  const handleToggleDefinitive = async () => {
    if (!detail || !fileId) return
    const next = !detail.is_definitive
    setActionBusy(true)
    try {
      await updateFile(collectionId, fileId, {
        is_definitive: next,
        version: detail.version,
      })
      toast.success(
        next
          ? "Marked definitive — will feed Collection Summary"
          : "Cleared definitive — excluded from Collection Summary"
      )
      await loadDetail()
      await refreshFiles(collectionId)
      const { triggerInfoRefresh } = await import("@/lib/info-refresh")
      triggerInfoRefresh({ collectionId, reason: "definitive" })
      // Summary may be generating; soft-refresh after a short wait if missing
      if (next && source && !docSummary) {
        setTimeout(() => {
          getDocSummary(collectionId, source)
            .then((res) => setDocSummary(res))
            .catch(() => {})
        }, 3000)
      }
    } catch (err) {
      toast.error(
        `Failed: ${err instanceof Error ? err.message : String(err)}`
      )
    } finally {
      setActionBusy(false)
    }
  }

  const handleDelete = async () => {
    if (!fileId) return
    setActionBusy(true)
    try {
      await deleteFile(collectionId, fileId)
      toast.success("File permanently deleted")
      setDeleteConfirm(false)
      onOpenChange(false)
      await refreshFiles(collectionId)
      onDeleted?.()
    } catch (err) {
      toast.error(
        `Delete failed: ${err instanceof Error ? err.message : String(err)}`
      )
    } finally {
      setActionBusy(false)
    }
  }

  const handleDeleteMessage = async (msg: Message) => {
    if (msg.author_type === "system" || isVersionUpdateMessage(msg)) {
      toast.error("Version update notes cannot be deleted")
      return
    }
    setMsgBusy(true)
    try {
      await deleteMessage(collectionId, msg.message_id)
      await loadDetail()
    } catch (err) {
      toast.error(
        `Delete failed: ${err instanceof Error ? err.message : String(err)}`
      )
    } finally {
      setMsgBusy(false)
    }
  }

  const handleAddMessage = async (body: string) => {
    if (!fileId || !body.trim()) return
    setMsgBusy(true)
    try {
      await createFileMessage(collectionId, fileId, {
        owner_type: "file",
        owner_id: fileId,
        body: body.trim(),
        author_type: "user",
      })
      toast.success("Message added")
      setAddMsgDialogOpen(false)
      await loadDetail()
    } catch (err) {
      toast.error(
        `Failed: ${err instanceof Error ? err.message : String(err)}`
      )
    } finally {
      setMsgBusy(false)
    }
  }

  // Blob for Raw/Download — always pin version_id when known (same storage
  // basename across versions would 404 if only storage_file is sent).
  const viewStorageFile = extractStorageFile
  const viewVersionId =
    focusVersionId ||
    focusVersion?.version_id ||
    versionIdProp ||
    detail?.current_version_id ||
    undefined
  const downloadUrl = source
    ? getFilePreviewUrl(source, {
        collection: collectionId || undefined,
        storageFile: viewStorageFile || undefined,
        versionId: viewVersionId,
      })
    : null
  const currentRawUrl = source
    ? getFilePreviewUrl(source, {
        collection: collectionId || undefined,
        storageFile: viewStorageFile || undefined,
        versionId: viewVersionId,
      })
    : null

  const titleName = isHistoricalFocus
    ? focusVersion?.storage_file_id ||
      storageFileIdProp ||
      detail?.display_name ||
      "File"
    : detail?.display_name || detail?.filename || source || fileId || "File"

  return (
    <>
      <Dialog
        open={open && !!(fileId || source)}
        onOpenChange={onOpenChange}
      >
        <DialogContent
          showCloseButton
          overlayClassName="pm-dialog-overlay--silk"
          className={cn(
            "pm-dialog pm-dialog--silk pm-workspace pm-ws-dialog",
            "!max-w-[94vw] !w-[94vw] h-[88vh] flex flex-col p-0 !gap-0 overflow-hidden",
            /* Strip default keyframe utilities — silk CSS owns opacity/scale + overlay fade */
            "!animate-none data-open:!animate-none data-closed:!animate-none"
          )}
        >
          <div className="pm-ws-chrome">
            <DialogHeader className="shrink-0 flex-1 min-w-0 !p-0">
              <DialogTitle className="flex items-center gap-2 min-w-0 text-left">
                <span className="pm-ws-title truncate" title={titleName}>
                  {titleName}
                </span>
                {isIngesting && (
                  <Badge
                    variant="secondary"
                    className="pm-ws-badge is-live"
                    title={ingestProgress?.message || "Ingesting…"}
                  >
                    <Loader2 className="h-3 w-3 animate-spin mr-1 inline" />
                    Ingesting
                    {typeof ingestProgress?.progress === "number"
                      ? ` ${Math.round(ingestProgress.progress)}%`
                      : ""}
                  </Badge>
                )}
                {isHistoricalFocus && (
                  <Badge variant="secondary" className="pm-ws-badge">
                    {focusVersion
                      ? `v${focusVersion.version_no} · old version`
                      : "old version"}
                  </Badge>
                )}
                {chunksTotal > 0 && !isIngesting && (
                  <Badge variant="secondary" className="ml-1 pm-ws-badge">
                    {chunksTotal} chunks
                  </Badge>
                )}
                {detail?.archived && (
                  <Badge variant="secondary" className="pm-ws-badge">
                    archived
                  </Badge>
                )}
                {detail?.unsupported && !isHistoricalFocus && (
                  <Badge variant="outline" className="pm-ws-badge">
                    unsupported
                  </Badge>
                )}
                {detail?.is_definitive && (
                  <Star className="h-3.5 w-3.5 shrink-0 text-[var(--pm-green)] fill-[var(--pm-green)]" />
                )}
              </DialogTitle>
            </DialogHeader>
            {/* room for dialog close button */}
            <div className="w-8 shrink-0" />
          </div>

          {loading && isManagedFile && !detail ? (
            <div className="pm-ws-loading flex-1">
              <Loader2 className="h-5 w-5 animate-spin" />
              Loading…
            </div>
          ) : (
            <div className="pm-ws-body">
              {/* ── Left: large nested white content card ── */}
              <div className="pm-ws-main pm-ws-card pm-ws-card--main">
                <Tabs
                  value={isIngesting ? "raw" : activeTab}
                  onValueChange={handleTabChange}
                  className="flex flex-col h-full min-h-0"
                >
                  <div className="pm-ws-main-head flex items-center justify-between gap-2 shrink-0">
                    <TabsList
                      className={cn(
                        "pm-tabs !h-auto w-fit bg-transparent p-0 gap-1 border-0 rounded-none",
                        "relative shrink-0 items-center isolate"
                      )}
                    >
                      <TabsIndicator
                        renderBeforeHydration
                        className="pm-tabs-indicator"
                      />
                      <TabsTrigger
                        value="raw"
                        className={cn(
                          "pm-vtab relative z-[1]",
                          "!h-auto min-h-0",
                          "data-[state=active]:shadow-none data-active:bg-transparent",
                          "after:!opacity-0 after:!content-none"
                        )}
                      >
                        Preview
                      </TabsTrigger>
                      <TabsTrigger
                        value="source"
                        disabled={isIngesting}
                        title={
                          isIngesting
                            ? "Available after ingest finishes"
                            : undefined
                        }
                        className={cn(
                          "pm-vtab relative z-[1]",
                          "!h-auto min-h-0",
                          "data-[state=active]:shadow-none data-active:bg-transparent",
                          "after:!opacity-0 after:!content-none",
                          "disabled:opacity-40"
                        )}
                      >
                        Parse
                      </TabsTrigger>
                      <TabsTrigger
                        value="summary"
                        disabled={isIngesting}
                        title={
                          isIngesting
                            ? "Available after ingest finishes"
                            : undefined
                        }
                        className={cn(
                          "pm-vtab relative z-[1]",
                          "!h-auto min-h-0",
                          "data-[state=active]:shadow-none data-active:bg-transparent",
                          "after:!opacity-0 after:!content-none",
                          "disabled:opacity-40"
                        )}
                      >
                        Summary
                      </TabsTrigger>
                      <TabsTrigger
                        value="chunks"
                        disabled={isIngesting}
                        title={
                          isIngesting
                            ? "Available after ingest finishes"
                            : undefined
                        }
                        className={cn(
                          "pm-vtab relative z-[1]",
                          "!h-auto min-h-0",
                          "data-[state=active]:shadow-none data-active:bg-transparent",
                          "after:!opacity-0 after:!content-none",
                          "disabled:opacity-40"
                        )}
                      >
                        Chunks
                        {chunksTotal > 0 && !isIngesting && (
                          <span className="ml-1.5 tabular-nums pm-meta normal-case tracking-normal">
                            {chunksTotal}
                          </span>
                        )}
                      </TabsTrigger>
                    </TabsList>
                    {goToLabel && (
                      <button
                        type="button"
                        className="pm-ws-link shrink-0"
                        onClick={handleGoToSource}
                      >
                        {goToLabel}
                      </button>
                    )}
                  </div>

                  {/* Preview — original file; white stage inside main card */}
                  <TabsContent
                    value="raw"
                    className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
                  >
                    <div className="pm-ws-doc-stage">
                      <RawFileViewer
                        key={`raw:${focusVersionId || "current"}:${viewStorageFile || ""}`}
                        url={currentRawUrl}
                        filename={resolveRawFilename(
                          viewStorageFile,
                          focusVersion?.storage_file_id,
                          storageFileIdProp,
                          detail?.filename,
                          detail?.original_ext
                            ? `file.${detail.original_ext}`
                            : null,
                          // Note/meeting ingest → .md even when storage_file_id is a label
                          detail?.doc_kind === "note" ||
                            detail?.doc_kind === "meeting" ||
                            source?.startsWith("__note__:") ||
                            source?.startsWith("__meeting__:")
                            ? source || "document.md"
                            : null,
                          isHistoricalFocus ? undefined : detail?.display_name,
                          source
                        )}
                        downloadUrl={downloadUrl}
                        className="h-full !rounded-none !border-0 !bg-white"
                      />
                    </div>
                  </TabsContent>

                  {/* Parse — extracted / parsed text */}
                  <TabsContent
                    value="source"
                    className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
                  >
                    <div className="pm-ws-doc-stage">
                      {previewLoading ||
                      (!isUnsupported && chunksLoading && !previewContent) ? (
                        <div className="pm-ws-loading h-full">
                          <Loader2 className="h-5 w-5 animate-spin" />
                          Loading…
                        </div>
                      ) : isUnsupported ? (
                        <div className="pm-ws-empty h-full flex flex-col items-center justify-center gap-2 px-6">
                          <p>No parse text for this version (unsupported type).</p>
                        </div>
                      ) : previewContent ? (
                        <ScrollArea className="h-full">
                          <div className="p-4">
                            <TiptapEditor
                              value={transformImageBlocks(
                                previewContent,
                                collectionId,
                                // Empty file_id: in extract blocks → use managed id
                                fileId || undefined
                              )}
                              readonly
                              showToolbar={false}
                              onEditorReady={(e) => {
                                sourceEditorRef.current = e
                              }}
                            />
                          </div>
                        </ScrollArea>
                      ) : chunks.length > 0 ? (
                        /* Fallback: chunks for *this* focusVersionId / current only */
                        <ScrollArea className="h-full">
                          <div className="p-4 space-y-2">
                            {isHistoricalFocus ? (
                              <p className="pm-meta mb-2">
                                Parse text reconstructed from this version’s chunks
                              </p>
                            ) : null}
                            {chunks.map((chunk, i) => (
                              <p
                                key={chunk.id || i}
                                className="pm-ws-prose-item"
                              >
                                {chunk.text}
                              </p>
                            ))}
                          </div>
                        </ScrollArea>
                      ) : (
                        <div className="pm-ws-empty h-full flex flex-col items-center justify-center gap-2 px-4">
                          <p>No extracted text for this version.</p>
                        </div>
                      )}
                    </div>
                  </TabsContent>

                  {/* Summary */}
                  <TabsContent
                    value="summary"
                    className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
                  >
                    <ScrollArea className="pm-ws-doc-stage">
                      <div className="p-4">
                        {isGenerating ? (
                          <div className="pm-ws-loading flex-col py-8">
                            <Loader2 className="h-5 w-5 animate-spin" />
                            <p className="pm-meta">Generating summary…</p>
                          </div>
                        ) : isUnsupported ? (
                          <div className="pm-ws-empty flex flex-col items-center justify-center py-8 gap-2 px-4">
                            <p className="pm-meta">
                              No summary for this unsupported version.
                            </p>
                          </div>
                        ) : summaryLoading ? (
                          <div className="pm-ws-loading py-8">
                            <Loader2 className="h-5 w-5 animate-spin" />
                            Loading summary…
                          </div>
                        ) : docSummary ? (
                          <div className="space-y-4">
                            {isHistoricalFocus && (
                              <p className="pm-meta px-0.5">
                                Summary for this version (read-only). Re-summarize
                                is only available on the current version.
                              </p>
                            )}
                            {detail?.is_definitive && !isHistoricalFocus && (
                              <p className="pm-meta flex items-center gap-1.5 px-0.5">
                                <Star className="h-3 w-3 text-[var(--pm-green)] fill-[var(--pm-green)]" />
                                Definitive — included in Collection Summary
                              </p>
                            )}
                            {!isHistoricalFocus && (
                              <div className="flex justify-end">
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="pm-ws-action !text-[var(--pm-green)]"
                                  disabled={isGenerating}
                                  onClick={async () => {
                                    if (!source || !collectionId) return
                                    const key = _genKey(collectionId, source)
                                    _markGenerating(key)
                                    setRenderTick((k) => k + 1)
                                    setActiveTab("summary")
                                    try {
                                      await generateDocSummary(
                                        collectionId,
                                        source,
                                        {
                                          versionId:
                                            focusVersionId || undefined,
                                        }
                                      )
                                    } catch (err) {
                                      _unmarkGenerating(key)
                                      setRenderTick((k) => k + 1)
                                      toast.error(
                                        `Failed: ${err instanceof Error ? err.message : String(err)}`
                                      )
                                    }
                                  }}
                                >
                                  {isGenerating ? (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                                  ) : (
                                    <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
                                  )}
                                  Re-summarize
                                </Button>
                              </div>
                            )}
                            {docSummary.data.length > 0 && (
                              <SummarySection
                                title="Data Points"
                                items={docSummary.data}
                              />
                            )}
                            {docSummary.facts.length > 0 && (
                              <SummarySection
                                title="Facts"
                                items={docSummary.facts}
                              />
                            )}
                            {docSummary.insights.length > 0 && (
                              <SummarySection
                                title="Insights"
                                items={docSummary.insights}
                              />
                            )}
                            {docSummary.data.length === 0 &&
                              docSummary.facts.length === 0 &&
                              docSummary.insights.length === 0 && (
                                <p className="pm-meta">
                                  No summary available for this document.
                                </p>
                              )}
                          </div>
                        ) : isHistoricalFocus ? (
                          <div className="pm-ws-empty flex flex-col items-center justify-center py-8 gap-2 px-4">
                            <p className="pm-meta">
                              No summary stored for this version.
                            </p>
                            <p className="pm-meta max-w-sm">
                              Summarize / Re-summarize is only available for the
                              current version.
                            </p>
                          </div>
                        ) : (
                          <div className="pm-ws-empty flex flex-col items-center justify-center py-8 gap-3">
                            <p className="pm-meta">
                              No summary available for this document.
                            </p>
                            <Button
                              variant="outline"
                              size="sm"
                              className="!text-[var(--pm-green)]"
                              disabled={
                                !source ||
                                !collectionId ||
                                isGenerating ||
                                isUnsupported
                              }
                              onClick={async () => {
                                if (!source || !collectionId) return
                                const key = _genKey(collectionId, source)
                                _markGenerating(key)
                                setRenderTick((k) => k + 1)
                                setDocSummary(null)
                                setActiveTab("summary")
                                try {
                                  await generateDocSummary(
                                    collectionId,
                                    source,
                                    {
                                      versionId: focusVersionId || undefined,
                                    }
                                  )
                                } catch (err) {
                                  _unmarkGenerating(key)
                                  setRenderTick((k) => k + 1)
                                  toast.error(
                                    `Failed: ${err instanceof Error ? err.message : String(err)}`
                                  )
                                }
                              }}
                            >
                              <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
                              Summarize
                            </Button>
                          </div>
                        )}
                      </div>
                    </ScrollArea>
                  </TabsContent>

                  {/* Chunks — full left pane */}
                  <TabsContent
                    value="chunks"
                    className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
                  >
                    <div className="pm-ws-doc-stage flex flex-col">
                      <ScrollArea className="flex-1 min-h-0">
                        <div className="p-3 space-y-2">
                          {chunksLoading ? (
                            <div className="pm-ws-loading py-12">
                              <Loader2 className="h-5 w-5 animate-spin" />
                              Loading chunks…
                            </div>
                          ) : chunks.length === 0 ? (
                            <div className="flex flex-col items-center justify-center gap-2 p-6 text-center">
                              <p className="pm-meta">
                                {isHistoricalFocus
                                  ? "No chunks for this old version."
                                  : isUnsupported
                                    ? "No chunks — current version is not supported for ingest."
                                    : "No chunks for this version."}
                              </p>
                              <p className="pm-meta max-w-sm leading-relaxed">
                                {isHistoricalFocus
                                  ? "Chunks are stored per version_id. Older uploads may predate version tracking, or this blob was never ingested. Preview/Parse still show the original file when available."
                                  : isUnsupported
                                    ? "Unsupported types skip RAG ingest. Previous version chunks are kept in history (All Files → Old versions / Log) and are not mixed into this view."
                                    : "Upload or re-ingest a supported file to create chunks for search and this tab."}
                              </p>
                            </div>
                          ) : groupedChunks ? (
                            groupedChunks.map((group) => {
                              const isExpanded = expandedParents.has(
                                group.parent.id
                              )
                              return (
                                <div
                                  key={group.parent.id}
                                  className="pm-ws-tile !p-0 overflow-hidden"
                                >
                                  <button
                                    type="button"
                                    className="w-full text-left p-3 hover:bg-[var(--pm-green-wash)] transition-colors flex items-start gap-2 bg-transparent border-0 cursor-pointer"
                                    onClick={() =>
                                      toggleParent(group.parent.id)
                                    }
                                  >
                                    {isExpanded ? (
                                      <ChevronDown className="h-4 w-4 mt-0.5 shrink-0" />
                                    ) : (
                                      <ChevronRight className="h-4 w-4 mt-0.5 shrink-0" />
                                    )}
                                    <div className="flex-1 min-w-0">
                                      <div className="flex items-center gap-2 mb-1">
                                        <Badge
                                          variant="default"
                                          className="pm-meta"
                                        >
                                          Parent #{group.parent.chunk_index}
                                        </Badge>
                                        <Badge
                                          variant="outline"
                                          className="pm-meta"
                                        >
                                          {group.children.length} children
                                        </Badge>
                                        <div
                                          role="button"
                                          tabIndex={0}
                                          title="Locate in Source"
                                          className="ml-auto p-0.5 rounded hover:bg-[var(--pm-green-wash)] text-[var(--pm-faint)] cursor-pointer"
                                          onClick={(e) => {
                                            e.stopPropagation()
                                            handleLocate(group.parent)
                                          }}
                                          onKeyDown={(e) => {
                                            if (
                                              e.key === "Enter" ||
                                              e.key === " "
                                            ) {
                                              e.preventDefault()
                                              e.stopPropagation()
                                              handleLocate(group.parent)
                                            }
                                          }}
                                        >
                                          <Crosshair className="h-3.5 w-3.5" />
                                        </div>
                                      </div>
                                      <p className="pm-ws-prose-item !text-[var(--pm-muted)] line-clamp-3">
                                        {group.parent.text}
                                      </p>
                                    </div>
                                  </button>
                                  {isExpanded && (
                                    <div className="border-t border-[color-mix(in_srgb,var(--pm-ink)_7%,transparent)] bg-[color-mix(in_srgb,var(--pm-ink)_2%,transparent)] p-3 space-y-2 pl-8">
                                      <p className="pm-ws-prose-item">
                                        {group.parent.text}
                                      </p>
                                      {group.children.map((child) => (
                                        <div
                                          key={child.id}
                                          className="pm-ws-tile cursor-pointer"
                                          onClick={() => handleLocate(child)}
                                          role="button"
                                          tabIndex={0}
                                          onKeyDown={(e) => {
                                            if (
                                              e.key === "Enter" ||
                                              e.key === " "
                                            )
                                              handleLocate(child)
                                          }}
                                        >
                                          <div className="flex items-center gap-2 mb-1">
                                            <Badge
                                              variant="secondary"
                                              className="pm-meta"
                                            >
                                              Child #{child.chunk_index}
                                            </Badge>
                                            <Crosshair className="h-3 w-3 ml-auto text-[var(--pm-faint)]" />
                                          </div>
                                          <p className="pm-ws-prose-item">
                                            {child.text}
                                          </p>
                                        </div>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              )
                            })
                          ) : (
                            chunks.map((chunk) => {
                              const expanded = expandedChunks.has(chunk.id)
                              return (
                                <div
                                  key={chunk.id}
                                  data-chunk-index={chunk.chunk_index}
                                  className={cn(
                                    "pm-ws-tile transition-all",
                                    highlightedIdx === chunk.chunk_index
                                      ? "is-on"
                                      : ""
                                  )}
                                >
                                  <div className="flex items-center gap-2 mb-2">
                                    <Badge
                                      variant="outline"
                                      className="pm-meta"
                                    >
                                      Chunk #{chunk.chunk_index}
                                    </Badge>
                                    {chunk.heading_path && (
                                      <span className="pm-meta truncate">
                                        {chunk.heading_path}
                                      </span>
                                    )}
                                    <button
                                      type="button"
                                      title="Locate in Source"
                                      className="ml-auto p-0.5 rounded hover:bg-[var(--pm-green-wash)] text-[var(--pm-faint)]"
                                      onClick={() => handleLocate(chunk)}
                                    >
                                      <Crosshair className="h-3.5 w-3.5" />
                                    </button>
                                  </div>
                                  <button
                                    type="button"
                                    className="w-full text-left"
                                    onClick={() => toggleChunkExpand(chunk.id)}
                                  >
                                    <p
                                      className={cn(
                                        "pm-ws-prose-item",
                                        !expanded && "line-clamp-4"
                                      )}
                                    >
                                      {chunk.text}
                                    </p>
                                    {!expanded &&
                                      (chunk.text?.length ?? 0) > 200 && (
                                        <span className="pm-ws-link mt-1 inline-block">
                                          Show more
                                        </span>
                                      )}
                                  </button>
                                </div>
                              )
                            })
                          )}
                        </div>
                      </ScrollArea>
                    </div>
                  </TabsContent>
                </Tabs>
              </div>

              {/* ── Right: Metadata / Paths / Nodes / Log as float cards ── */}
              <div className="pm-ws-side">
                {!detail ? (
                  <div className="pm-ws-side-card flex-1 flex items-center justify-center p-6 text-center border-dashed">
                    <p className="pm-meta leading-relaxed max-w-[220px]">
                      {isManagedFile
                        ? "Could not load file management metadata."
                        : "This document is not a managed file. Paths, versions, and archive actions are unavailable. You can still read Source, Summary, and Chunks."}
                    </p>
                  </div>
                ) : (
                <>
                {/* Metadata — compact always-open card; definitive in title row */}
                <section className="pm-ws-side-card pm-ws-side-card--meta shrink-0">
                  <div className="pm-ws-side-h">
                    <span
                      className="pm-label"
                      style={{ textTransform: "none", letterSpacing: "0.02em" }}
                    >
                      Metadata
                    </span>
                    {isHistoricalFocus ? (
                      <span className="pm-meta ml-1.5">this version</span>
                    ) : null}
                    <div className="ml-auto shrink-0">
                      <TooltipProvider delay={300}>
                        <Tooltip>
                          <TooltipTrigger
                            render={
                              <button
                                type="button"
                                className={cn(
                                  "pm-ws-definitive pm-ws-definitive--header",
                                  detail?.is_definitive && "is-on"
                                )}
                                disabled={actionBusy || !detail}
                                onClick={() => void handleToggleDefinitive()}
                                aria-pressed={!!detail?.is_definitive}
                              >
                                <Star
                                  className={cn(
                                    "pm-ws-definitive-star h-3.5 w-3.5",
                                    detail?.is_definitive && "is-filled"
                                  )}
                                />
                                <span className="pm-ws-definitive-label">
                                  {detail?.is_definitive
                                    ? "Definitive"
                                    : "Mark definitive"}
                                </span>
                              </button>
                            }
                          />
                          <TooltipContent
                            side="bottom"
                            className="max-w-[240px]"
                          >
                            Definitive files feed Collection Summary (and show a
                            star). Summary is kept if you clear the flag.
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </div>
                  </div>
                  <div className="pm-ws-side-pad pm-ws-side-pad--meta">
                    <dl className="pm-ws-meta-grid">
                      {isHistoricalFocus &&
                      (focusVersionId || focusVersion || storageFileIdProp) ? (
                        <>
                          <dt>Filename</dt>
                          <dd
                            className="truncate"
                            title={viewStorageFile || storageFileIdProp || ""}
                          >
                            {viewStorageFile || storageFileIdProp || "—"}
                          </dd>
                          <dt>File ID</dt>
                          <dd
                            className="font-mono truncate pm-meta"
                            title={detail.file_id}
                          >
                            {detail.file_id}
                          </dd>
                          <dt>Version</dt>
                          <dd>
                            v{focusVersion?.version_no ?? "—"}
                            {focusVersion?.archived ? " · archived" : ""}
                            {" · old"}
                          </dd>
                          <dt>Created</dt>
                          <dd>
                            {formatTime(
                              focusVersion?.created_at || detail?.created_at
                            )}
                          </dd>
                          <dt>Versions</dt>
                          <dd>{detail?.versions?.length ?? 0}</dd>
                          {focusVersion?.commit_message ? (
                            <>
                              <dt>Note</dt>
                              <dd
                                className="truncate"
                                title={focusVersion.commit_message}
                              >
                                {focusVersion.commit_message}
                              </dd>
                            </>
                          ) : null}
                        </>
                      ) : (
                        <>
                          <dt>Filename</dt>
                          <dd
                            className="truncate"
                            title={
                              detail?.filename || detail?.display_name || ""
                            }
                          >
                            {detail?.filename || detail?.display_name || "—"}
                          </dd>
                          <dt>File ID</dt>
                          <dd
                            className="font-mono truncate pm-meta"
                            title={detail.file_id}
                          >
                            {detail.file_id}
                          </dd>
                          <dt>Created</dt>
                          <dd>{formatTime(detail?.created_at)}</dd>
                          <dt>Versions</dt>
                          <dd>{detail?.versions?.length ?? 0}</dd>
                        </>
                      )}
                    </dl>
                  </div>
                </section>

                {/*
                  Paths / Nodes / Log — fixed-height accordion stack
                  (Overview .pm-rail-lower language). Exactly one expanded;
                  default Log. Expanded card flex-grows to fill remaining height.
                */}
                <div className="pm-ws-side-lower">
                  {/* Paths */}
                  <section
                    className={cn(
                      "pm-ws-side-card",
                      openSide === "paths" && "is-expanded"
                    )}
                  >
                    <div className="pm-collapse-h shrink-0">
                      <button
                        type="button"
                        className="pm-collapse-h-main"
                        aria-expanded={openSide === "paths"}
                        aria-label="Toggle Paths"
                        onClick={() => toggleSide("paths")}
                      >
                        <span
                          className={cn(
                            "pm-rail-chev",
                            openSide === "paths" && "is-open"
                          )}
                          aria-hidden
                        >
                          <ChevronRight className="size-3.5" strokeWidth={2} />
                        </span>
                        <span
                          className="pm-label"
                          style={{
                            textTransform: "none",
                            letterSpacing: "0.02em",
                          }}
                        >
                          Paths
                        </span>
                        <span className="pm-count-pill">
                          {detail?.paths?.length ?? 0}
                        </span>
                      </button>
                    </div>
                    <div
                      className={cn(
                        "pm-ws-side-collapse",
                        openSide === "paths" && "is-open"
                      )}
                    >
                      <div className="pm-ws-side-collapse-inner">
                        <div className="pm-ws-side-pad pt-0">
                          {(detail?.paths?.length ?? 0) === 0 ? (
                            <p className="pm-meta">
                              No folder paths (orphan / root file)
                            </p>
                          ) : (
                            <ul className="pm-ws-list">
                              {detail!.paths.map((p) => {
                                const srcNode = p.source_node_id
                                  ? detail!.nodes.find(
                                      (n) => n.node_id === p.source_node_id
                                    )
                                  : undefined
                                const folderHasPinned =
                                  !!p.folder_id &&
                                  detail!.paths.some(
                                    (q) =>
                                      q.folder_id === p.folder_id &&
                                      !q.source_node_id
                                  )
                                const canUnpin =
                                  !p.source_node_id &&
                                  !!p.folder_id &&
                                  (detail!.paths.some(
                                    (q) =>
                                      q.folder_id === p.folder_id &&
                                      !!q.source_node_id
                                  ) ||
                                    detail!.nodes.length > 0)
                                return (
                                  <PathRow
                                    key={p.path_id}
                                    path={p}
                                    sourceNodeTitle={
                                      srcNode?.title?.trim() ||
                                      (p.source_node_id
                                        ? "Untitled node"
                                        : null)
                                    }
                                    folderHasPinned={folderHasPinned}
                                    canUnpin={canUnpin}
                                    busy={actionBusy}
                                    onNavigate={() => {
                                      if (p.folder_id && onNavigateToFolder) {
                                        onNavigateToFolder(p.folder_id)
                                        onOpenChange(false)
                                      }
                                    }}
                                    onPromote={() => void handlePromote(p)}
                                    onUnpin={() => void handleUnpin(p)}
                                  />
                                )
                              })}
                            </ul>
                          )}
                        </div>
                      </div>
                    </div>
                  </section>

                  {/* Nodes */}
                  <section
                    className={cn(
                      "pm-ws-side-card",
                      openSide === "nodes" && "is-expanded"
                    )}
                  >
                    <div className="pm-collapse-h shrink-0">
                      <button
                        type="button"
                        className="pm-collapse-h-main"
                        aria-expanded={openSide === "nodes"}
                        aria-label="Toggle Nodes"
                        onClick={() => toggleSide("nodes")}
                      >
                        <span
                          className={cn(
                            "pm-rail-chev",
                            openSide === "nodes" && "is-open"
                          )}
                          aria-hidden
                        >
                          <ChevronRight className="size-3.5" strokeWidth={2} />
                        </span>
                        <span
                          className="pm-label"
                          style={{
                            textTransform: "none",
                            letterSpacing: "0.02em",
                          }}
                        >
                          Nodes
                        </span>
                        <span className="pm-count-pill">
                          {detail?.nodes?.length ?? 0}
                        </span>
                      </button>
                    </div>
                    <div
                      className={cn(
                        "pm-ws-side-collapse",
                        openSide === "nodes" && "is-open"
                      )}
                    >
                      <div className="pm-ws-side-collapse-inner">
                        <div className="pm-ws-side-pad pt-0">
                          {(detail?.nodes?.length ?? 0) === 0 ? (
                            <p className="pm-meta">Not attached to any node</p>
                          ) : (
                            <ul className="pm-ws-list">
                              {detail!.nodes.map((n) => (
                                <NodeRow
                                  key={n.node_id}
                                  node={n}
                                  onClick={() => setPreviewNodeId(n.node_id)}
                                />
                              ))}
                            </ul>
                          )}
                        </div>
                      </div>
                    </div>
                  </section>

                  {/* Log — default open */}
                  <section
                    className={cn(
                      "pm-ws-side-card pm-ws-side-card--log",
                      openSide === "log" && "is-expanded"
                    )}
                  >
                    <div className="pm-collapse-h shrink-0">
                      <button
                        type="button"
                        className="pm-collapse-h-main"
                        aria-expanded={openSide === "log"}
                        aria-label="Toggle Log"
                        onClick={() => toggleSide("log")}
                      >
                        <span
                          className={cn(
                            "pm-rail-chev",
                            openSide === "log" && "is-open"
                          )}
                          aria-hidden
                        >
                          <ChevronRight className="size-3.5" strokeWidth={2} />
                        </span>
                        <span
                          className="pm-label"
                          style={{
                            textTransform: "none",
                            letterSpacing: "0.02em",
                          }}
                        >
                          Log
                        </span>
                        <span className="pm-count-pill">{timeline.length}</span>
                      </button>
                      <div className="pm-collapse-h-actions items-center gap-1.5">
                        <button
                          type="button"
                          className="pm-ws-log-add shrink-0"
                          disabled={msgBusy || !fileId}
                          onClick={() => setAddMsgDialogOpen(true)}
                        >
                          Add
                        </button>
                        <div
                          ref={logScopeRef}
                          className="pm-ws-scope shrink-0"
                          data-on={timelineFilter}
                        >
                          <span
                            className="pm-ws-scope-ind"
                            aria-hidden
                            style={{
                              transform: `translateX(${logScopeInd.left}px)`,
                              width: logScopeInd.width,
                              opacity: logScopeInd.width > 0 ? 1 : 0,
                            }}
                          />
                          <button
                            ref={logScopeAllRef}
                            type="button"
                            className={cn(
                              "pm-ws-scope-btn",
                              timelineFilter === "all" && "is-on"
                            )}
                            onClick={() => handleTimelineFilter("all")}
                          >
                            All
                          </button>
                          <button
                            ref={logScopeVerRef}
                            type="button"
                            className={cn(
                              "pm-ws-scope-btn",
                              timelineFilter === "versions" && "is-on"
                            )}
                            onClick={() => handleTimelineFilter("versions")}
                          >
                            Versions
                          </button>
                        </div>
                      </div>
                    </div>
                    <div
                      className={cn(
                        "pm-ws-side-collapse",
                        openSide === "log" && "is-open"
                      )}
                    >
                      <div className="pm-ws-side-collapse-inner">
                        <div className="pm-ws-side-pad pt-0 pm-ws-side-log-body">
                          <ul
                            className={cn(
                              "pm-ws-log-list",
                              logListPhase === "out" && "is-out",
                              logListPhase === "in" && "is-in"
                            )}
                          >
                            {timeline.length === 0 ? (
                              <p className="pm-meta px-2 py-1">No log yet</p>
                            ) : (
                              timeline.map((item) => {
                                if (item.kind === "version") {
                                  /**
                                   * Orphan file version (no paired system_version message).
                                   * Still open dual-pane when we can recover a message by
                                   * time/body; otherwise show note only (not editable yet).
                                   */
                                  const orphanMsg =
                                    detail?.messages?.find((m) => {
                                      if (
                                        (m.owner_type || "").toLowerCase() !==
                                        "system_version"
                                      )
                                        return false
                                      if (
                                        m.created_at &&
                                        item.version.created_at &&
                                        m.created_at === item.version.created_at
                                      )
                                        return true
                                      const body = (m.body || "").trim()
                                      const cm = (
                                        item.version.commit_message || ""
                                      ).trim()
                                      return !!body && !!cm && body === cm
                                    }) ?? null
                                  return (
                                    <li
                                      key={item.id}
                                      className={cn(
                                        "pm-ws-log-item is-version",
                                        orphanMsg && "is-clickable group"
                                      )}
                                      onClick={
                                        orphanMsg
                                          ? () =>
                                              setLogMsgOpen({
                                                message: orphanMsg,
                                                version: item.version,
                                              })
                                          : undefined
                                      }
                                    >
                                      <div className="flex items-center gap-1.5 mb-1 min-w-0">
                                        <span
                                          className="pm-ws-log-dot"
                                          aria-hidden
                                        />
                                        <Badge
                                          variant="secondary"
                                          className="pm-ws-badge is-live shrink-0"
                                        >
                                          version update
                                        </Badge>
                                        {item.version.archived && (
                                          <span className="pm-meta uppercase shrink-0">
                                            archived
                                          </span>
                                        )}
                                        <span className="pm-meta shrink-0">
                                          v{item.version.version_no}
                                        </span>
                                        <span className="ml-auto pm-meta tabular-nums shrink-0 text-right">
                                          {formatTime(item.created_at)}
                                        </span>
                                      </div>
                                      <p className="pm-meta text-[var(--pm-faint)]">
                                        {versionUpdateBody(
                                          item.version.commit_message
                                        )}
                                      </p>
                                    </li>
                                  )
                                }

                                const msg = item.message
                                const isVer = item.isVersionUpdate
                                const canDelete =
                                  !isVer && msg.author_type !== "system"
                                const displayBody = isVer
                                  ? versionUpdateBody(msg.body)
                                  : msg.body || ""

                                return (
                                  <li
                                    key={item.id}
                                    className={cn(
                                      "pm-ws-log-item is-clickable group",
                                      isVer && "is-version"
                                    )}
                                    onClick={() =>
                                      setLogMsgOpen({
                                        message: msg,
                                        version: item.version ?? null,
                                      })
                                    }
                                  >
                                    <div className="flex items-center gap-1.5 mb-1 min-w-0">
                                      {isVer ? (
                                        <>
                                          <span
                                            className="pm-ws-log-dot"
                                            aria-hidden
                                          />
                                          <Badge
                                            variant="secondary"
                                            className="pm-ws-badge is-live shrink-0"
                                          >
                                            version update
                                          </Badge>
                                          {item.version && (
                                            <span className="pm-meta shrink-0">
                                              v{item.version.version_no}
                                            </span>
                                          )}
                                          {item.version?.archived && (
                                            <span className="pm-meta uppercase shrink-0">
                                              archived
                                            </span>
                                          )}
                                        </>
                                      ) : (
                                        <>
                                          <Badge
                                            variant="secondary"
                                            className="pm-ws-badge shrink-0"
                                          >
                                            message
                                          </Badge>
                                          {msg.author_id &&
                                            msg.author_id !== "local" &&
                                            msg.author_id !== "user" && (
                                              <span className="pm-meta shrink-0">
                                                {msg.author_id}
                                              </span>
                                            )}
                                        </>
                                      )}
                                      {msg.edited_at && (
                                        <Badge
                                          variant="outline"
                                          className="pm-ws-badge shrink-0"
                                        >
                                          edited
                                        </Badge>
                                      )}
                                      <div
                                        className={cn(
                                          "ml-auto flex items-center gap-1.5 shrink-0",
                                          /* keep actions visible while armed (same as message rail) */
                                        )}
                                      >
                                        {canDelete && (
                                          <LogMsgDeleteButton
                                            disabled={msgBusy}
                                            onConfirm={() =>
                                              void handleDeleteMessage(msg)
                                            }
                                          />
                                        )}
                                        <span className="pm-meta tabular-nums text-right">
                                          {formatTime(item.created_at)}
                                        </span>
                                      </div>
                                    </div>
                                    <MessageBody
                                      body={displayBody}
                                      className={cn(
                                        "pm-ws-msg-md line-clamp-4",
                                        "max-w-none",
                                        "[&_p]:my-0.5 [&_ul]:my-0.5 [&_ol]:my-0.5 [&_li]:my-0"
                                      )}
                                    />
                                  </li>
                                )
                              })
                            )}
                          </ul>
                        </div>
                      </div>
                    </div>
                  </section>
                </div>

                {/* Bottom action dock — pinned under rail cards */}
                <div className="pm-ws-side-actions">
                  {deleteConfirm ? (
                    <div className="rounded-[var(--pm-r-sm)] bg-[color-mix(in_srgb,var(--pm-danger)_6%,transparent)] p-2.5 space-y-2">
                      <p className="pm-title text-[var(--pm-danger)]">
                        Permanently delete this file?
                      </p>
                      <p className="pm-meta">
                        All paths will be removed:
                      </p>
                      <ul className="pm-meta space-y-0.5 max-h-24 overflow-y-auto">
                        {(detail?.paths?.length ?? 0) === 0 ? (
                          <li className="italic">
                            (no folder paths — orphan file)
                          </li>
                        ) : (
                          detail!.paths.map((p) => (
                            <li key={p.path_id} className="truncate">
                              {p.folder_path || p.folder_id || "—"}
                              {p.source_node_id
                                ? " (via timeline node)"
                                : " (pinned to folder)"}
                            </li>
                          ))
                        )}
                      </ul>
                      <div className="pm-ws-side-actions-row">
                        <button
                          type="button"
                          className="pm-ws-foot-btn pm-ws-foot-btn--danger"
                          disabled={actionBusy}
                          onClick={() => void handleDelete()}
                        >
                          {actionBusy ? (
                            <Loader2 className="animate-spin" />
                          ) : (
                            <Trash2 />
                          )}
                          Confirm
                        </button>
                        <button
                          type="button"
                          className="pm-ws-foot-btn pm-ws-foot-btn--ghost"
                          onClick={() => setDeleteConfirm(false)}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div
                      ref={actionMenuRef}
                      className="pm-ws-side-actions-row overflow-visible"
                    >
                      <button
                        type="button"
                        className="pm-ws-foot-btn pm-ws-foot-btn--pri"
                        disabled={actionBusy}
                        onClick={() => {
                          setActionMenu(null)
                          setUpdateDialogOpen(true)
                        }}
                      >
                        <Upload />
                        Update
                      </button>

                      {/* Archive dropdown — context-aware path vs global */}
                      {(canArchiveCurrentPath ||
                        canArchiveGlobally ||
                        canRestore) && (
                        <div className="relative">
                          <button
                            type="button"
                            className={cn(
                              "pm-ws-foot-btn pm-ws-foot-btn--ghost",
                              actionMenu === "archive" && "is-on"
                            )}
                            disabled={actionBusy}
                            title="Archive options"
                            onClick={() =>
                              setActionMenu((m) =>
                                m === "archive" ? null : "archive"
                              )
                            }
                          >
                            <Archive />
                            Archive
                            <ChevronDown className="opacity-50 !w-3 !h-3" />
                          </button>
                          <SoftMenu
                            open={actionMenu === "archive"}
                            className="absolute left-0 bottom-full mb-1.5 z-50 min-w-[260px] pm-menu--drop-up"
                          >
                            {canRestore && (
                              <ActionMenuItem
                                icon={
                                  <ArchiveRestore className="h-3.5 w-3.5" />
                                }
                                title="Restore"
                                description={
                                  fileArchived
                                    ? "Re-enable search (and restore current path if archived)."
                                    : contextNodeId
                                      ? "Restore this file's path(s) for the current node."
                                      : "Restore this file's current path."
                                }
                                onClick={() => {
                                  setActionMenu(null)
                                  void handleRestore()
                                }}
                              />
                            )}
                            {canArchiveCurrentPath && (
                              <ActionMenuItem
                                icon={<Archive className="h-3.5 w-3.5" />}
                                title="Archive current path"
                                description={
                                  contextNodeId
                                    ? "Grey out node-related path(s) only (group + branch). Leaves other mounts active."
                                    : "Grey out this file on the current folder path only."
                                }
                                onClick={() => {
                                  setActionMenu(null)
                                  void handleArchiveCurrentPath()
                                }}
                              />
                            )}
                            {canArchiveGlobally && (
                              <ActionMenuItem
                                icon={<SearchX className="h-3.5 w-3.5" />}
                                title="Archive globally"
                                description="Exclude this file from search everywhere."
                                onClick={() => {
                                  setActionMenu(null)
                                  void handleArchiveGlobally()
                                }}
                              />
                            )}
                          </SoftMenu>
                        </div>
                      )}

                      {/* Delete dropdown: remove path + permanent delete */}
                      <div className="relative">
                        <button
                          type="button"
                          className={cn(
                            "pm-ws-foot-btn pm-ws-foot-btn--danger",
                            actionMenu === "delete" && "is-on"
                          )}
                          disabled={actionBusy}
                          title="Remove or delete"
                          onClick={() =>
                            setActionMenu((m) =>
                              m === "delete" ? null : "delete"
                            )
                          }
                        >
                          <Trash2 />
                          Delete
                          <ChevronDown className="opacity-50 !w-3 !h-3" />
                        </button>
                        <SoftMenu
                          open={actionMenu === "delete"}
                          className="absolute right-0 bottom-full mb-1.5 z-50 min-w-[260px] pm-menu--drop-up"
                        >
                          {canRemoveCurrentPath && (
                            <ActionMenuItem
                              icon={<X className="h-3.5 w-3.5" />}
                              title="Remove current path"
                              description={
                                contextNodeId
                                  ? "Detach from this node and remove its path(s) (group + branch). Other mounts stay."
                                  : "Remove this file from the current folder path only."
                              }
                              onClick={() => {
                                setActionMenu(null)
                                void handleRemoveCurrentPath()
                              }}
                            />
                          )}
                          <ActionMenuItem
                            icon={<Trash2 className="h-3.5 w-3.5" />}
                            title="Delete file globally"
                            description="Permanently delete this file everywhere."
                            destructive
                            onClick={() => {
                              setActionMenu(null)
                              setDeleteConfirm(true)
                            }}
                          />
                        </SoftMenu>
                      </div>
                    </div>
                  )}
                </div>
                </>
                )}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {fileId && (
        <UpdateFileDialog
          open={updateDialogOpen}
          onOpenChange={setUpdateDialogOpen}
          collectionId={collectionId}
          fileId={fileId}
          currentFilename={
            detail?.display_name || detail?.filename || null
          }
          onSuccess={() => {
            // Exit update dialog + file detail so folder view shows ingest badge
            setUpdateDialogOpen(false)
            void refreshFiles(collectionId)
            onOpenChange(false)
          }}
        />
      )}

      <MessageEditorDialog
        // Stable key — remounting on close would kill exit animation
        key="file-add-msg"
        open={addMsgDialogOpen}
        onOpenChange={setAddMsgDialogOpen}
        title="Add Message"
        initialContent=""
        onSave={(content) => void handleAddMessage(content)}
        readonly={false}
        message={null}
        collectionId={collectionId}
      />

      <LogMessageDialog
        open={!!logMsgOpen}
        onOpenChange={(v) => {
          if (!v) setLogMsgOpen(null)
        }}
        collectionId={collectionId}
        docSource={docSource}
        message={logMsgOpen?.message ?? null}
        version={logMsgOpen?.version ?? null}
        isCurrentVersion={
          !!logMsgOpen?.version &&
          !!detail?.current_version_id &&
          logMsgOpen.version.version_id === detail.current_version_id
        }
        onSaved={(updated) => {
          // Keep open dialog's lock version in sync with server
          setLogMsgOpen((prev) =>
            prev && prev.message.message_id === updated.message_id
              ? { ...prev, message: updated }
              : prev
          )
          void loadDetail()
        }}
        onVersionDeleted={() => {
          setLogMsgOpen(null)
          void loadDetail()
          void refreshFiles(collectionId)
        }}
      />

      <NodePreviewSheet
        collectionId={collectionId}
        nodeId={previewNodeId}
        open={!!previewNodeId}
        onOpenChange={(v) => {
          if (!v) setPreviewNodeId(null)
        }}
        onSelectNode={(nid) => setPreviewNodeId(nid)}
        onOpenAttachment={(fid) => {
          const fromNode = previewNodeId
          // Close node sheet so nested dialog is not trapped under the sheet
          setPreviewNodeId(null)
          // Already viewing this file — nothing else to open
          if (fid === fileId) return
          setNestedDetail({ fileId: fid, contextNodeId: fromNode })
        }}
        onGoToNode={(nid, chainId) => {
          setPreviewNodeId(null)
          onOpenChange(false)
          // Allow dialog exit motion before switching Database → Timeline
          window.setTimeout(() => {
            requestTimelineFocus(nid, chainId ?? undefined)
          }, 280)
        }}
      />

      {/* Nested file detail when opening an attachment from node preview */}
      {nestedDetail && (
        <FileMgmtDetailDialog
          collectionId={collectionId}
          fileId={nestedDetail.fileId}
          open={!!nestedDetail}
          onOpenChange={(v) => {
            if (!v) setNestedDetail(null)
          }}
          onDeleted={() => {
            setNestedDetail(null)
            if (fileId) void loadDetail()
          }}
          onNavigateToFolder={onNavigateToFolder}
          contextNodeId={nestedDetail.contextNodeId}
        />
      )}
    </>
  )
}

// ── subcomponents ──

/**
 * Two-step delete (× → DELETE) — same anti-mis-tap pattern as message sidebar
 * (message-card.tsx · .pm-msg-delete).
 */
function LogMsgDeleteButton({
  disabled,
  onConfirm,
}: {
  disabled?: boolean
  onConfirm: () => void
}) {
  const [deleteArmed, setDeleteArmed] = useState(false)
  const deleteArmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const deleteBtnRef = useRef<HTMLButtonElement>(null)

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

  useEffect(() => {
    return () => {
      if (deleteArmTimerRef.current) clearTimeout(deleteArmTimerRef.current)
    }
  }, [])

  return (
    <button
      ref={deleteBtnRef}
      type="button"
      disabled={disabled}
      className={cn(
        "pm-msg-delete",
        deleteArmed ? "is-confirm opacity-100" : "opacity-0 group-hover:opacity-100",
        "transition-opacity"
      )}
      title={deleteArmed ? "Click again to delete" : "Delete message"}
      aria-label={
        deleteArmed ? "Confirm delete message" : "Delete message"
      }
      aria-expanded={deleteArmed}
      onClick={(e) => {
        e.preventDefault()
        e.stopPropagation()
        if (disabled) return
        if (!deleteArmed) {
          armDelete()
          return
        }
        disarmDelete()
        onConfirm()
      }}
    >
      <span className="pm-msg-delete-x" aria-hidden>
        ×
      </span>
      <span className="pm-msg-delete-label">Delete</span>
    </button>
  )
}

/** Dropdown row — shared Menu primitive. */
function ActionMenuItem({
  icon,
  title,
  description,
  onClick,
  destructive,
}: {
  icon: ReactNode
  title: string
  description: string
  onClick: () => void
  destructive?: boolean
}) {
  return (
    <MenuItem destructive={destructive} onClick={onClick}>
      <span
        className={cn(
          "mt-0.5 shrink-0",
          destructive ? "text-[var(--pm-danger)]" : "text-[var(--pm-faint)]"
        )}
      >
        {icon}
      </span>
      <span className="min-w-0">
        <MenuItemTitle
          className={destructive ? "text-[var(--pm-danger)]" : undefined}
        >
          {title}
        </MenuItemTitle>
        <MenuItemDescription>{description}</MenuItemDescription>
      </span>
    </MenuItem>
  )
}

function SummarySection({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <h5 className="pm-ws-section-label">
        {title}
      </h5>
      <ul className="space-y-1">
        {items.map((item, i) => (
          <li key={i} className="pm-ws-prose-item">
            {item}
          </li>
        ))}
      </ul>
    </div>
  )
}

function PathRow({
  path,
  sourceNodeTitle,
  folderHasPinned = false,
  canUnpin = false,
  busy,
  onNavigate,
  onPromote,
  onUnpin,
}: {
  path: FilePath
  /** Display name of the timeline node that created this path (derived only). */
  sourceNodeTitle?: string | null
  /**
   * True when any path for the same folder is pinned (source_node_id null).
   * Sibling *derived* rows keep their “From node” label; Pin is hidden because
   * the folder is already covered by the pin.
   */
  folderHasPinned?: boolean
  /**
   * True when demote can re-link to a node or drop a pin that has a derived
   * sibling. Plain folder mounts (no node) must not show Unpin — that used to
   * delete the only path row and make the card vanish.
   */
  canUnpin?: boolean
  busy: boolean
  onNavigate: () => void
  onPromote: () => void
  onUnpin: () => void
}) {
  /** Persistent path: source_node_id is null (pin or plain folder mount). */
  const isPersistent = !path.source_node_id
  /** Timeline pin that can be demoted (vs plain “in folder” mount). */
  const isTimelinePin = isPersistent && canUnpin
  const typeLabel = isPersistent
    ? isTimelinePin
      ? "Pinned to folder"
      : "In folder"
    : `From node · ${sourceNodeTitle || "Untitled node"}`
  return (
    <li
      className={cn(
        "pm-ws-path-row",
        path.is_greyed && "opacity-50"
      )}
    >
      <FolderOpen className="h-3.5 w-3.5 mt-0.5 shrink-0 text-[var(--pm-faint)]" />
      <div className="flex-1 min-w-0">
        <button
          type="button"
          className="text-left truncate w-full bg-transparent border-0 p-0 cursor-pointer hover:text-[var(--pm-green)] transition-colors"
          onClick={onNavigate}
          title={path.folder_path || path.folder_id || ""}
          disabled={!path.folder_id}
        >
          {path.folder_path || path.folder_id || "(no folder)"}
        </button>
        <div className="flex items-center gap-1.5 mt-0.5 min-w-0">
          <span
            className="pm-meta truncate"
            title={typeLabel}
          >
            {typeLabel}
          </span>
          {path.is_primary && (
            <Badge variant="outline" className="pm-meta h-4 shrink-0">
              main
            </Badge>
          )}
          {path.is_greyed && (
            <span className="pm-meta text-[var(--pm-danger)] shrink-0">archived</span>
          )}
        </div>
      </div>
      {/* Right column: actions left-aligned with each other across rows */}
      <div className="shrink-0 flex flex-col items-start justify-start pt-0.5 min-w-[7.5rem]">
        {isTimelinePin ? (
          <Button
            size="sm"
            variant="ghost"
            className="pm-ws-action !h-6 justify-start"
            disabled={busy}
            title="Unpin from folder. If a node-derived path exists for the same folder, only the pin is removed."
            onClick={onUnpin}
          >
            <PinOff className="h-3 w-3 mr-0.5" />
            Unpin
          </Button>
        ) : isPersistent ? (
          // Plain folder mount — not a demotable timeline pin
          <span
            className="h-6 px-1.5 pm-meta leading-6 opacity-50"
            title="Folder placement. Use Remove from folder in the footer to unlink."
          >
            —
          </span>
        ) : folderHasPinned ? (
          // Derived sibling: folder already has a real pin — no second Pin action
          <span
            className="h-6 px-1.5 pm-meta leading-6 opacity-50"
            title="This folder is already pinned. Use Unpin on the “Pinned to folder” row."
          >
            —
          </span>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            className="pm-ws-action !h-6 justify-start"
            disabled={busy}
            title="Pin this file to the folder even if the timeline node is removed or archived."
            onClick={onPromote}
          >
            <ArrowUpRight className="h-3 w-3 mr-0.5" />
            Pin to folder
          </Button>
        )}
      </div>
    </li>
  )
}

function NodeRow({
  node,
  onClick,
}: {
  node: FileNodeRef
  onClick: () => void
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "pm-ws-path-row",
          node.greyed && "opacity-50"
        )}
      >
        <GitBranch className="h-3.5 w-3.5 mt-0.5 shrink-0 text-[var(--pm-faint)]" />
        <div className="flex-1 min-w-0">
          <p className="truncate pm-title">
            {node.title || "Untitled node"}
          </p>
          <p className="pm-meta mt-0.5 truncate">
            {[
              node.group_name || (node.group_id ? "Group" : "No group"),
              node.chain_title || (node.chain_id ? "Chain" : null),
              node.node_type,
            ]
              .filter(Boolean)
              .join(" · ")}
            {node.greyed ? " · greyed" : ""}
          </p>
        </div>
        <ChevronRight className="h-3.5 w-3.5 mt-0.5 shrink-0 text-[var(--pm-faint)]" />
      </button>
    </li>
  )
}
