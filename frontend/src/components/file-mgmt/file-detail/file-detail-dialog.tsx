/**
 * Unified file detail dialog (folder / timeline / All Files / Quick Chat).
 * Left: Source / Raw / Summary / Chunks.
 * Right: paths, nodes, version + message timeline (when file-mgmt file_id exists).
 * Bottom: update / promote / archive / permanent delete (managed files only).
 *
 * Open via `fileId` and/or document `source` (`__file__:{id}` extracts fileId).
 */

import {
  useCallback,
  useEffect,
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
   * Source/Raw/Chunks bind to that version's blob, not the current latest.
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
  const isIngesting = !!(fileId && ingestingFiles[fileId])

  // Block opening detail while this file is still ingesting
  useEffect(() => {
    if (!open || !isIngesting) return
    toast.info("File is still ingesting — open it when the progress finishes.")
    onOpenChange(false)
  }, [open, isIngesting, onOpenChange])

  const [detail, setDetail] = useState<FileDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [activeTab, setActiveTab] = useState("source")

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
      setActiveTab("source")
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

  // Storage blob for Source/Raw.
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

  const handleLocate = (chunk: ChunkDetail) => {
    setHighlightOffset(chunk.char_offset)
    setActiveTab("source")
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
        open={open && !isIngesting && !!(fileId || source)}
        onOpenChange={onOpenChange}
      >
        <DialogContent className="!max-w-[90vw] !w-[90vw] h-[85vh] flex flex-col p-4 gap-3">
          <DialogHeader className="shrink-0">
            <DialogTitle className="flex items-center gap-2 min-w-0">
              <span className="truncate font-light" title={titleName}>
                {titleName}
              </span>
              {isHistoricalFocus && (
                <Badge
                  variant="secondary"
                  className="text-[10px] shrink-0 border-transparent bg-muted text-muted-foreground"
                >
                  {focusVersion
                    ? `v${focusVersion.version_no} · old version`
                    : "old version"}
                </Badge>
              )}
              {chunksTotal > 0 && (
                <Badge variant="secondary" className="ml-1 shrink-0">
                  {chunksTotal} chunks
                </Badge>
              )}
              {detail?.archived && (
                <Badge variant="secondary" className="shrink-0 text-[10px]">
                  archived
                </Badge>
              )}
              {detail?.unsupported && !isHistoricalFocus && (
                <Badge variant="outline" className="shrink-0 text-[10px]">
                  unsupported
                </Badge>
              )}
              {detail?.is_definitive && (
                <Star className="h-3.5 w-3.5 shrink-0 text-[var(--ze-green,#1A5E3D)] fill-[var(--ze-green,#1A5E3D)]" />
              )}
            </DialogTitle>
          </DialogHeader>

          {loading && isManagedFile && !detail ? (
            <div className="flex-1 flex items-center justify-center text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin mr-2" />
              Loading…
            </div>
          ) : (
            <div className="flex-1 flex gap-4 overflow-hidden min-h-0">
              {/* ── Left 60%: full-height tab content ── */}
              <div className="w-[60%] flex flex-col min-h-0 min-w-0">
                <Tabs
                  value={activeTab}
                  onValueChange={setActiveTab}
                  className="flex flex-col h-full min-h-0"
                >
                  <div className="flex items-center justify-between gap-2 shrink-0 mb-2">
                    <TabsList variant="line" className="relative">
                      <TabsIndicator renderBeforeHydration />
                      <TabsTrigger
                        value="source"
                        className="font-light uppercase tracking-wider after:!opacity-0 data-[state=active]:text-primary"
                      >
                        Source
                      </TabsTrigger>
                      <TabsTrigger
                        value="raw"
                        className="font-light uppercase tracking-wider after:!opacity-0 data-[state=active]:text-primary"
                      >
                        Raw
                      </TabsTrigger>
                      <TabsTrigger
                        value="summary"
                        className="font-light uppercase tracking-wider after:!opacity-0 data-[state=active]:text-primary"
                      >
                        Summary
                      </TabsTrigger>
                      <TabsTrigger
                        value="chunks"
                        className="font-light uppercase tracking-wider after:!opacity-0 data-[state=active]:text-primary"
                      >
                        Chunks
                        {chunksTotal > 0 && (
                          <span className="ml-1.5 tabular-nums text-[10px] text-muted-foreground font-normal normal-case tracking-normal">
                            {chunksTotal}
                          </span>
                        )}
                      </TabsTrigger>
                    </TabsList>
                    {goToLabel && (
                      <button
                        type="button"
                        className="text-[10px] font-medium uppercase tracking-[0.1em] text-primary hover:opacity-80 transition-opacity cursor-pointer t-sans-family shrink-0"
                        style={{ background: "none", border: "none" }}
                        onClick={handleGoToSource}
                      >
                        {goToLabel}
                      </button>
                    )}
                  </div>

                  {/* Source — full left pane */}
                  <TabsContent
                    value="source"
                    className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
                  >
                    <div className="h-full min-h-0 overflow-hidden rounded-lg border border-border">
                      {previewLoading ||
                      (!isUnsupported && chunksLoading && !previewContent) ? (
                        <div className="flex items-center justify-center h-full text-muted-foreground">
                          <Loader2 className="h-5 w-5 animate-spin mr-2" />
                          Loading…
                        </div>
                      ) : isUnsupported ? (
                        <div className="flex flex-col items-center justify-center h-full text-sm text-muted-foreground p-6 text-center gap-2">
                          <p>No source text for this version (unsupported type).</p>
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
                              <p className="text-[10px] text-muted-foreground mb-2">
                                Source reconstructed from this version’s chunks
                              </p>
                            ) : null}
                            {chunks.map((chunk, i) => (
                              <p
                                key={chunk.id || i}
                                className="text-sm leading-relaxed whitespace-pre-wrap"
                              >
                                {chunk.text}
                              </p>
                            ))}
                          </div>
                        </ScrollArea>
                      ) : (
                        <div className="flex flex-col items-center justify-center h-full text-sm text-muted-foreground p-4 text-center gap-2">
                          <p>No extracted text for this version.</p>
                        </div>
                      )}
                    </div>
                  </TabsContent>

                  {/* Raw — browser-native File Viewer (PDF / Office / md / txt) */}
                  <TabsContent
                    value="raw"
                    className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
                  >
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
                      className="h-full"
                    />
                  </TabsContent>

                  {/* Summary */}
                  <TabsContent
                    value="summary"
                    className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
                  >
                    <ScrollArea className="h-full rounded-lg border border-border">
                      <div className="p-4">
                        {isGenerating ? (
                          <div className="flex flex-col items-center justify-center py-8 gap-3 text-muted-foreground">
                            <Loader2 className="h-5 w-5 animate-spin" />
                            <p className="text-sm">Generating summary…</p>
                          </div>
                        ) : isUnsupported ? (
                          <div className="flex flex-col items-center justify-center py-8 gap-2 px-4 text-center">
                            <p className="text-sm text-muted-foreground">
                              No summary for this unsupported version.
                            </p>
                          </div>
                        ) : summaryLoading ? (
                          <div className="flex items-center justify-center py-8 text-muted-foreground">
                            <Loader2 className="h-5 w-5 animate-spin mr-2" />
                            Loading summary…
                          </div>
                        ) : docSummary ? (
                          <div className="space-y-4">
                            {isHistoricalFocus && (
                              <p className="text-[11px] text-muted-foreground px-0.5">
                                Summary for this version (read-only). Re-summarize
                                is only available on the current version.
                              </p>
                            )}
                            {detail?.is_definitive && !isHistoricalFocus && (
                              <p className="text-[11px] text-muted-foreground flex items-center gap-1.5 px-0.5">
                                <Star className="h-3 w-3 text-[var(--ze-green,#1A5E3D)] fill-[var(--ze-green,#1A5E3D)]" />
                                Definitive — included in Collection Summary
                              </p>
                            )}
                            {!isHistoricalFocus && (
                              <div className="flex justify-end">
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="font-light uppercase tracking-wider text-primary"
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
                                <p className="text-sm text-muted-foreground">
                                  No summary available for this document.
                                </p>
                              )}
                          </div>
                        ) : isHistoricalFocus ? (
                          <div className="flex flex-col items-center justify-center py-8 gap-2 px-4 text-center">
                            <p className="text-sm text-muted-foreground">
                              No summary stored for this version.
                            </p>
                            <p className="text-xs text-muted-foreground max-w-sm">
                              Summarize / Re-summarize is only available for the
                              current version.
                            </p>
                          </div>
                        ) : (
                          <div className="flex flex-col items-center justify-center py-8 gap-3">
                            <p className="text-sm text-muted-foreground">
                              No summary available for this document.
                            </p>
                            <Button
                              variant="outline"
                              size="sm"
                              className="font-light uppercase tracking-wider text-primary border-primary"
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
                    <div className="h-full min-h-0 overflow-hidden rounded-lg border border-border flex flex-col">
                      <ScrollArea className="flex-1 min-h-0">
                        <div className="p-3 space-y-2">
                          {chunksLoading ? (
                            <div className="flex items-center justify-center py-12 text-muted-foreground">
                              <Loader2 className="h-5 w-5 animate-spin mr-2" />
                              Loading chunks…
                            </div>
                          ) : chunks.length === 0 ? (
                            <div className="flex flex-col items-center justify-center gap-2 p-6 text-center">
                              <p className="text-sm text-muted-foreground">
                                {isHistoricalFocus
                                  ? "No chunks for this old version."
                                  : isUnsupported
                                    ? "No chunks — current version is not supported for ingest."
                                    : "No chunks for this version."}
                              </p>
                              <p className="text-xs text-muted-foreground max-w-sm leading-relaxed">
                                {isHistoricalFocus
                                  ? "Chunks are stored per version_id. Older uploads may predate version tracking, or this blob was never ingested. Source/Raw still show the original file when available."
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
                                  className="border border-border rounded-md overflow-hidden"
                                >
                                  <button
                                    type="button"
                                    className="w-full text-left p-3 hover:bg-accent/50 transition-colors flex items-start gap-2"
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
                                          className="text-[10px]"
                                        >
                                          Parent #{group.parent.chunk_index}
                                        </Badge>
                                        <Badge
                                          variant="outline"
                                          className="text-[10px]"
                                        >
                                          {group.children.length} children
                                        </Badge>
                                        <div
                                          role="button"
                                          tabIndex={0}
                                          title="Locate in Source"
                                          className="ml-auto p-0.5 rounded hover:bg-accent text-muted-foreground cursor-pointer"
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
                                      <p className="text-sm text-muted-foreground line-clamp-3 whitespace-pre-wrap">
                                        {group.parent.text}
                                      </p>
                                    </div>
                                  </button>
                                  {isExpanded && (
                                    <div className="border-t border-border bg-muted/30 p-3 space-y-2 pl-8">
                                      <p className="text-sm whitespace-pre-wrap">
                                        {group.parent.text}
                                      </p>
                                      {group.children.map((child) => (
                                        <div
                                          key={child.id}
                                          className="border border-border rounded-lg p-3 bg-background cursor-pointer hover:bg-accent/50"
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
                                              className="text-[10px]"
                                            >
                                              Child #{child.chunk_index}
                                            </Badge>
                                            <Crosshair className="h-3 w-3 ml-auto text-muted-foreground" />
                                          </div>
                                          <p className="text-sm whitespace-pre-wrap">
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
                                    "border rounded-lg p-3 transition-all",
                                    highlightedIdx === chunk.chunk_index
                                      ? "border-primary ring-1 ring-primary/30 bg-primary/5"
                                      : "border-border"
                                  )}
                                >
                                  <div className="flex items-center gap-2 mb-2">
                                    <Badge
                                      variant="outline"
                                      className="text-[10px]"
                                    >
                                      Chunk #{chunk.chunk_index}
                                    </Badge>
                                    {chunk.heading_path && (
                                      <span className="text-[10px] text-muted-foreground truncate">
                                        {chunk.heading_path}
                                      </span>
                                    )}
                                    <button
                                      type="button"
                                      title="Locate in Source"
                                      className="ml-auto p-0.5 rounded hover:bg-accent text-muted-foreground"
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
                                        "text-sm leading-relaxed whitespace-pre-wrap",
                                        !expanded && "line-clamp-4"
                                      )}
                                    >
                                      {chunk.text}
                                    </p>
                                    {!expanded &&
                                      (chunk.text?.length ?? 0) > 200 && (
                                        <span className="text-[10px] text-primary mt-1 inline-block">
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

              {/* ── Right 40% ── */}
              <div className="w-[40%] flex flex-col min-h-0 min-w-0 gap-3">
                {!detail ? (
                  <div className="flex-1 flex items-center justify-center p-6 text-center rounded-lg border border-dashed border-border">
                    <p className="text-xs text-muted-foreground leading-relaxed max-w-[220px]">
                      {isManagedFile
                        ? "Could not load file management metadata."
                        : "This document is not a managed file. Paths, versions, and archive actions are unavailable. You can still read Source, Summary, and Chunks."}
                    </p>
                  </div>
                ) : (
                <>
                <ScrollArea className="flex-1 min-h-0">
                  <div className="space-y-4 pr-2">
                    {/* Meta — for historical open, show THIS version's blob fields */}
                    <section>
                      <h4 className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground mb-2">
                        Metadata
                        {isHistoricalFocus ? (
                          <span className="ml-1.5 normal-case tracking-normal font-normal text-muted-foreground/80">
                            · this version
                          </span>
                        ) : null}
                      </h4>
                      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                        {isHistoricalFocus &&
                        (focusVersionId || focusVersion || storageFileIdProp) ? (
                          <>
                            <dt className="text-muted-foreground">Version ID</dt>
                            <dd
                              className="font-mono truncate text-[11px]"
                              title={
                                focusVersionId ||
                                focusVersion?.version_id ||
                                versionIdProp ||
                                ""
                              }
                            >
                              {focusVersionId ||
                                focusVersion?.version_id ||
                                versionIdProp ||
                                "—"}
                            </dd>
                            <dt className="text-muted-foreground">Version</dt>
                            <dd>
                              v{focusVersion?.version_no ?? "—"}
                              {focusVersion?.archived ? " · archived" : ""}
                              {" · old"}
                            </dd>
                            <dt className="text-muted-foreground">Filename</dt>
                            <dd
                              className="truncate"
                              title={
                                viewStorageFile ||
                                storageFileIdProp ||
                                ""
                              }
                            >
                              {viewStorageFile ||
                                storageFileIdProp ||
                                "—"}
                            </dd>
                            <dt className="text-muted-foreground">Ext</dt>
                            <dd>
                              {(
                                viewStorageFile ||
                                focusVersion?.storage_file_id ||
                                storageFileIdProp ||
                                ""
                              )
                                .split(".")
                                .pop()
                                ?.toLowerCase() || "—"}
                            </dd>
                            <dt className="text-muted-foreground">Created</dt>
                            <dd>
                              {formatTime(
                                focusVersion?.created_at || detail?.created_at
                              )}
                            </dd>
                            <dt className="text-muted-foreground">By</dt>
                            <dd>
                              {focusVersion?.created_by ||
                                detail?.created_by ||
                                "local"}
                            </dd>
                            {focusVersion?.commit_message ? (
                              <>
                                <dt className="text-muted-foreground">Note</dt>
                                <dd
                                  className="truncate"
                                  title={focusVersion.commit_message}
                                >
                                  {focusVersion.commit_message}
                                </dd>
                              </>
                            ) : null}
                            <dt className="text-muted-foreground">
                              Managed file
                            </dt>
                            <dd
                              className="font-mono truncate text-[11px] text-muted-foreground"
                              title={detail.file_id}
                            >
                              {detail.file_id}
                            </dd>
                          </>
                        ) : (
                          <>
                            <dt className="text-muted-foreground">File ID</dt>
                            <dd
                              className="font-mono truncate text-[11px]"
                              title={detail.file_id}
                            >
                              {detail.file_id}
                            </dd>
                            <dt className="text-muted-foreground">Ext</dt>
                            <dd>{detail?.original_ext || "—"}</dd>
                            <dt className="text-muted-foreground">Filename</dt>
                            <dd
                              className="truncate"
                              title={
                                detail?.filename || detail?.display_name || ""
                              }
                            >
                              {detail?.filename ||
                                detail?.display_name ||
                                "—"}
                            </dd>
                            <dt className="text-muted-foreground">Created</dt>
                            <dd>{formatTime(detail?.created_at)}</dd>
                            <dt className="text-muted-foreground">By</dt>
                            <dd>{detail?.created_by || "local"}</dd>
                          </>
                        )}
                        <dt className="text-muted-foreground">Versions</dt>
                        <dd>{detail?.versions?.length ?? 0}</dd>
                      </dl>
                      <div className="mt-2">
                        <TooltipProvider delay={300}>
                          <Tooltip>
                            <TooltipTrigger
                              render={
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-7 text-[11px] px-2"
                                  disabled={actionBusy || !detail}
                                  onClick={() => void handleToggleDefinitive()}
                                >
                                  <Star
                                    className={cn(
                                      "h-3 w-3 mr-1",
                                      detail?.is_definitive &&
                                        "text-[var(--ze-green,#1A5E3D)] fill-[var(--ze-green,#1A5E3D)]"
                                    )}
                                  />
                                  {detail?.is_definitive
                                    ? "Clear definitive"
                                    : "Mark definitive"}
                                </Button>
                              }
                            />
                            <TooltipContent
                              side="bottom"
                              className="max-w-[240px]"
                            >
                              Definitive files feed Collection Summary (and show
                              a star). Summary is kept if you clear the flag.
                            </TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      </div>
                    </section>

                    {/* Paths */}
                    <section>
                      <h4 className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground mb-2">
                        Paths ({detail?.paths?.length ?? 0})
                      </h4>
                      {(detail?.paths?.length ?? 0) === 0 ? (
                        <p className="text-xs text-muted-foreground">
                          No folder paths (orphan / root file)
                        </p>
                      ) : (
                        <ul className="space-y-1.5">
                          {detail!.paths.map((p) => {
                            const srcNode = p.source_node_id
                              ? detail!.nodes.find(
                                  (n) => n.node_id === p.source_node_id
                                )
                              : undefined
                            // Same folder: if any path is pinned, treat all mounts as pinned in UI
                            const folderHasPinned =
                              !!p.folder_id &&
                              detail!.paths.some(
                                (q) =>
                                  q.folder_id === p.folder_id &&
                                  !q.source_node_id
                              )
                            // Unpin only when demote can re-link to a node or drop
                            // a pin that has a derived sibling — never on a lone
                            // plain-folder mount (that would delete the path card).
                            const canUnpin =
                              !p.source_node_id &&
                              (!!p.folder_id &&
                                (detail!.paths.some(
                                  (q) =>
                                    q.folder_id === p.folder_id &&
                                    !!q.source_node_id
                                ) ||
                                  detail!.nodes.length > 0))
                            return (
                              <PathRow
                                key={p.path_id}
                                path={p}
                                sourceNodeTitle={
                                  srcNode?.title?.trim() ||
                                  (p.source_node_id ? "Untitled node" : null)
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
                    </section>

                    {/* Nodes */}
                    <section>
                      <h4 className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground mb-2">
                        Nodes ({detail?.nodes?.length ?? 0})
                      </h4>
                      {(detail?.nodes?.length ?? 0) === 0 ? (
                        <p className="text-xs text-muted-foreground">
                          Not attached to any node
                        </p>
                      ) : (
                        <ul className="space-y-1.5">
                          {detail!.nodes.map((n) => (
                            <NodeRow
                              key={n.node_id}
                              node={n}
                              onClick={() => setPreviewNodeId(n.node_id)}
                            />
                          ))}
                        </ul>
                      )}
                    </section>

                    {/* Version + message log */}
                    <section>
                      <div className="flex items-center justify-between mb-2 gap-2">
                        <h4 className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                          Log
                        </h4>
                        <div className="flex items-center gap-2 min-w-0">
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-6 text-[10px] px-2 shrink-0"
                            disabled={msgBusy || !fileId}
                            onClick={() => setAddMsgDialogOpen(true)}
                          >
                            Add message
                          </Button>
                          <div className="flex rounded-md border border-border overflow-hidden text-[10px] shrink-0">
                            <button
                              type="button"
                              className={cn(
                                "px-2 py-0.5 transition-colors",
                                timelineFilter === "all"
                                  ? "bg-primary/10 text-primary"
                                  : "text-muted-foreground hover:bg-muted/50"
                              )}
                              onClick={() => setTimelineFilter("all")}
                            >
                              All
                            </button>
                            <button
                              type="button"
                              className={cn(
                                "px-2 py-0.5 border-l border-border transition-colors",
                                timelineFilter === "versions"
                                  ? "bg-primary/10 text-primary"
                                  : "text-muted-foreground hover:bg-muted/50"
                              )}
                              onClick={() => setTimelineFilter("versions")}
                            >
                              Versions
                            </button>
                          </div>
                        </div>
                      </div>

                      <ul className="space-y-2">
                        {timeline.length === 0 ? (
                          <p className="text-xs text-muted-foreground">
                            No log yet
                          </p>
                        ) : (
                          timeline.map((item) => {
                            if (item.kind === "version") {
                              // Display-only orphan version (no linked message)
                              return (
                                <li
                                  key={item.id}
                                  className="rounded-md border border-border/50 bg-muted/20 p-2 text-xs"
                                >
                                  <div className="flex items-center gap-1.5 mb-1 min-w-0">
                                    <Badge
                                      variant="secondary"
                                      className="text-[9px] shrink-0 border-transparent bg-[var(--ze-green,#1A5E3D)]/15 text-[var(--ze-green,#1A5E3D)]"
                                    >
                                      version update
                                    </Badge>
                                    {item.version.archived && (
                                      <span className="text-[9px] text-muted-foreground uppercase shrink-0">
                                        archived
                                      </span>
                                    )}
                                    <span className="text-[9px] text-muted-foreground shrink-0">
                                      v{item.version.version_no}
                                    </span>
                                    <span className="ml-auto text-[10px] text-muted-foreground tabular-nums shrink-0 text-right">
                                      {formatTime(item.created_at)}
                                    </span>
                                  </div>
                                  <p className="text-muted-foreground">
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
                                  "rounded-md border p-2 text-xs group cursor-pointer transition-colors",
                                  "hover:border-primary/30 hover:bg-muted/20",
                                  isVer
                                    ? "border-border/50 bg-muted/20"
                                    : "border-border/40"
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
                                      <Badge
                                        variant="secondary"
                                        className="text-[9px] shrink-0 border-transparent bg-[var(--ze-green,#1A5E3D)]/15 text-[var(--ze-green,#1A5E3D)]"
                                      >
                                        version update
                                      </Badge>
                                      {item.version && (
                                        <span className="text-[9px] text-muted-foreground shrink-0">
                                          v{item.version.version_no}
                                        </span>
                                      )}
                                      {item.version?.archived && (
                                        <span className="text-[9px] text-muted-foreground uppercase shrink-0">
                                          archived
                                        </span>
                                      )}
                                    </>
                                  ) : (
                                    <>
                                      <Badge
                                        variant="secondary"
                                        className="text-[9px] shrink-0"
                                      >
                                        message
                                      </Badge>
                                      {/* author_id "local" is the single-user default — hide it */}
                                      {msg.author_id &&
                                        msg.author_id !== "local" &&
                                        msg.author_id !== "user" && (
                                          <span className="text-[10px] text-muted-foreground shrink-0">
                                            {msg.author_id}
                                          </span>
                                        )}
                                    </>
                                  )}
                                  {msg.edited_at && (
                                    <Badge
                                      variant="outline"
                                      className="text-[9px] shrink-0"
                                    >
                                      edited
                                    </Badge>
                                  )}
                                  <div className="ml-auto flex items-center gap-0.5 shrink-0">
                                    {canDelete && (
                                      <button
                                        type="button"
                                        className="p-0.5 rounded hover:bg-muted text-destructive opacity-0 group-hover:opacity-100 transition-opacity"
                                        title="Delete"
                                        disabled={msgBusy}
                                        onClick={(e) => {
                                          e.stopPropagation()
                                          void handleDeleteMessage(msg)
                                        }}
                                      >
                                        <Trash2 className="h-3 w-3" />
                                      </button>
                                    )}
                                    <span className="text-[10px] text-muted-foreground tabular-nums text-right">
                                      {formatTime(item.created_at)}
                                    </span>
                                  </div>
                                </div>
                                <MessageBody
                                  body={displayBody}
                                  className="prose prose-xs dark:prose-invert max-w-none text-xs line-clamp-4"
                                />
                              </li>
                            )
                          })
                        )}
                      </ul>
                    </section>
                  </div>
                </ScrollArea>

                {/* Bottom actions */}
                <div className="shrink-0 border-t border-border pt-2 space-y-2">
                  {deleteConfirm ? (
                    <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-2 space-y-2">
                      <p className="text-xs font-medium text-destructive">
                        Permanently delete this file?
                      </p>
                      <p className="text-[11px] text-muted-foreground">
                        All paths will be removed:
                      </p>
                      <ul className="text-[11px] text-muted-foreground space-y-0.5 max-h-24 overflow-y-auto">
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
                      <div className="flex gap-1.5">
                        <Button
                          size="sm"
                          variant="destructive"
                          className="h-7 text-[11px]"
                          disabled={actionBusy}
                          onClick={() => void handleDelete()}
                        >
                          {actionBusy ? (
                            <Loader2 className="h-3 w-3 animate-spin mr-1" />
                          ) : (
                            <Trash2 className="h-3 w-3 mr-1" />
                          )}
                          Confirm delete
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 text-[11px]"
                          onClick={() => setDeleteConfirm(false)}
                        >
                          Cancel
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <div
                      ref={actionMenuRef}
                      className="flex flex-nowrap items-center gap-1.5 overflow-visible"
                    >
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 text-[11px] shrink-0"
                        disabled={actionBusy}
                        onClick={() => {
                          setActionMenu(null)
                          setUpdateDialogOpen(true)
                        }}
                      >
                        <Upload className="h-3 w-3 mr-1" />
                        Update file
                      </Button>

                      {/* Archive dropdown — context-aware path vs global */}
                      {(canArchiveCurrentPath ||
                        canArchiveGlobally ||
                        canRestore) && (
                        <div className="relative shrink-0">
                          <Button
                            size="sm"
                            variant="outline"
                            className={cn(
                              "h-7 text-[11px]",
                              actionMenu === "archive" && "bg-accent"
                            )}
                            disabled={actionBusy}
                            title="Archive options"
                            onClick={() =>
                              setActionMenu((m) =>
                                m === "archive" ? null : "archive"
                              )
                            }
                          >
                            <Archive className="h-3 w-3 mr-1" />
                            Archive
                            <ChevronDown className="h-3 w-3 ml-0.5 opacity-60" />
                          </Button>
                          {actionMenu === "archive" && (
                            <div
                              className="absolute left-0 bottom-full mb-1 z-50 min-w-[260px] rounded-md border border-border bg-background text-foreground shadow-lg py-1"
                              role="menu"
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
                            </div>
                          )}
                        </div>
                      )}

                      {/* Delete dropdown: remove path + permanent delete */}
                      <div className="relative shrink-0">
                        <Button
                          size="sm"
                          variant="outline"
                          className={cn(
                            "h-7 text-[11px] text-destructive border-destructive/30 hover:bg-destructive/10",
                            actionMenu === "delete" && "bg-destructive/10"
                          )}
                          disabled={actionBusy}
                          title="Remove or delete"
                          onClick={() =>
                            setActionMenu((m) =>
                              m === "delete" ? null : "delete"
                            )
                          }
                        >
                          <Trash2 className="h-3 w-3 mr-1" />
                          Delete
                          <ChevronDown className="h-3 w-3 ml-0.5 opacity-60" />
                        </Button>
                        {actionMenu === "delete" && (
                          <div
                            className="absolute right-0 bottom-full mb-1 z-50 min-w-[260px] rounded-md border border-border bg-background text-foreground shadow-lg py-1"
                            role="menu"
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
                          </div>
                        )}
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
        onSaved={() => {
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

/** Dropdown row — same layout as folder-view toolbar MenuItem. */
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
    <button
      type="button"
      role="menuitem"
      className={cn(
        "w-full flex items-start gap-2 px-2.5 py-2 text-left hover:bg-accent/80 transition-colors",
        destructive && "hover:bg-destructive/10"
      )}
      onClick={onClick}
    >
      <span
        className={cn(
          "mt-0.5 shrink-0",
          destructive ? "text-destructive" : "text-muted-foreground"
        )}
      >
        {icon}
      </span>
      <span className="min-w-0">
        <span
          className={cn(
            "block text-xs font-medium",
            destructive ? "text-destructive" : "text-foreground"
          )}
        >
          {title}
        </span>
        <span className="block text-[10px] text-muted-foreground leading-snug">
          {description}
        </span>
      </span>
    </button>
  )
}

function SummarySection({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
        {title}
      </h5>
      <ul className="space-y-1">
        {items.map((item, i) => (
          <li key={i} className="text-sm leading-relaxed">
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
        "flex items-start gap-1.5 rounded-md border border-border/40 px-2 py-1.5 text-xs",
        path.is_greyed && "opacity-50"
      )}
    >
      <FolderOpen className="h-3.5 w-3.5 mt-0.5 shrink-0 text-muted-foreground" />
      <div className="flex-1 min-w-0">
        <button
          type="button"
          className="text-left truncate w-full hover:text-primary transition-colors"
          onClick={onNavigate}
          title={path.folder_path || path.folder_id || ""}
          disabled={!path.folder_id}
        >
          {path.folder_path || path.folder_id || "(no folder)"}
        </button>
        <div className="flex items-center gap-1.5 mt-0.5 min-w-0">
          <span
            className="text-[10px] text-muted-foreground truncate"
            title={typeLabel}
          >
            {typeLabel}
          </span>
          {path.is_primary && (
            <Badge variant="outline" className="text-[8px] h-4 shrink-0">
              main
            </Badge>
          )}
          {path.is_greyed && (
            <span className="text-[9px] text-amber-600 shrink-0">archived</span>
          )}
        </div>
      </div>
      {/* Right column: actions left-aligned with each other across rows */}
      <div className="shrink-0 flex flex-col items-start justify-start pt-0.5 min-w-[7.5rem]">
        {isTimelinePin ? (
          <Button
            size="sm"
            variant="ghost"
            className="h-6 px-1.5 text-[10px] justify-start text-muted-foreground hover:text-foreground"
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
            className="h-6 px-1.5 text-[10px] text-muted-foreground/50 leading-6"
            title="Folder placement. Use Remove from folder in the footer to unlink."
          >
            —
          </span>
        ) : folderHasPinned ? (
          // Derived sibling: folder already has a real pin — no second Pin action
          <span
            className="h-6 px-1.5 text-[10px] text-muted-foreground/50 leading-6"
            title="This folder is already pinned. Use Unpin on the “Pinned to folder” row."
          >
            —
          </span>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            className="h-6 px-1.5 text-[10px] justify-start"
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
          "w-full text-left flex items-start gap-1.5 rounded-md border border-border/40 px-2 py-1.5 text-xs hover:bg-accent/50 transition-colors",
          node.greyed && "opacity-50"
        )}
      >
        <GitBranch className="h-3.5 w-3.5 mt-0.5 shrink-0 text-muted-foreground" />
        <div className="flex-1 min-w-0">
          <p className="truncate font-medium">
            {node.title || "Untitled node"}
          </p>
          <p className="text-[10px] text-muted-foreground mt-0.5 truncate">
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
        <ChevronRight className="h-3.5 w-3.5 mt-0.5 shrink-0 text-muted-foreground" />
      </button>
    </li>
  )
}
