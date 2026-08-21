/**
 * Unified file detail dialog (folder / timeline / All Files / Quick Chat).
 * Left: Preview / Parse / Summary / Chunks (Ingest run if developer mode).
 * Right: paths, nodes, version + message timeline (when file-mgmt file_id exists).
 * Bottom: update / promote / archive / permanent delete (managed files only).
 *
 * Open via `fileId` and/or document `source` (`__file__:{id}` extracts fileId).
 */

import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import {
  Dialog,
  DialogContent,
} from "@/components/ui/dialog"
import { Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { FileDetailMainPane } from "./file-detail-main-pane"
import {
  FileDetailRollbackDialog,
  FileDetailTitleChrome,
} from "./file-detail-overlays"
import { FileDetailSideRail } from "./file-detail-side-rail"

import type { Editor } from "@tiptap/core"
import { useAppStore } from "@/stores/app-store"
import {
  getFilePreviewUrl,
  getDocSummary,
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
  rollbackFileVersion,
  FileMgmtApiError,
} from "@/api/file-mgmt"
import type {
  FileDetail,
  FilePath,
  FileVersion,
  Message,
} from "@/types/file-mgmt"
import { useShallow } from "zustand/react/shallow"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { NodePreviewSheet } from "@/components/file-mgmt/node-preview-sheet"
import { UpdateFileDialog } from "@/components/file-mgmt/file-detail/update-file-dialog"
import { LogMessageDialog } from "@/components/file-mgmt/file-detail/log-message-dialog"
import { MessageEditorDialog } from "@/components/file-mgmt/folder-view/message-editor-dialog"
import { toast } from "sonner"
import { useT } from "@/i18n/use-t"
import { formatApiError } from "@/api/http"
import {
  _genKey,
  _generating,
  _isMarked,
  _unmarkGenerating,
  buildTimeline,
  fileSource,
  isVersionUpdateMessage,
  parseFileIdFromSource,
} from "./file-detail-utils"

export { parseFileIdFromSource } from "./file-detail-utils"

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

export const FileMgmtDetailDialog = memo(function FileMgmtDetailDialog({
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
  const t = useT()
  const refreshFiles = useFileMgmtStore((s) => s.refreshFiles)
  const currentFolderId = useFileMgmtStore((s) => s.currentFolderId)
  const requestTimelineFocus = useFileMgmtStore((s) => s.requestTimelineFocus)
  const setActiveMeeting = useAppStore((s) => s.setActiveMeeting)
  const setSidebarView = useAppStore((s) => s.setSidebarView)
  const setPendingOpenNote = useAppStore((s) => s.setPendingOpenNote)

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
   * are not ready yet). Subscribe to this file only — other files' ingest
   * polls must not re-render the open dialog (causes preview flicker).
   */
  const ingestProgress = useFileMgmtStore(
    useShallow((s) => {
      const p = fileId ? s.ingestingFiles[fileId] : undefined
      if (!p) return null
      return { taskId: p.taskId, progress: p.progress, message: p.message }
    }),
  )
  const isIngesting = !!ingestProgress

  // Force Preview tab while ingesting; leave other tabs disabled
  useEffect(() => {
    if (isIngesting && activeTab !== "raw") setActiveTab("raw")
  }, [isIngesting, activeTab])

  const developerMode = useAppStore((s) => s.developerMode)
  useEffect(() => {
    if (!developerMode && activeTab === "ingest") setActiveTab("raw")
  }, [developerMode, activeTab])

  /**
   * Source used for chunks / extracted text / doc summary.
   * Meeting & note payloads are keyed by __meeting__: / __note__:, NOT __file__:{id}.
   * Prefer: explicit prop → detail.source (index / SQLite) → synthetic __file__:{id}.
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
  const [rollbackConfirm, setRollbackConfirm] = useState(false)
  const [rollingBack, setRollingBack] = useState(false)
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
        t("fileMgmt.failedLoadFile", { error: formatApiError(err, t) })
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
          toast.error(t("fileMgmt.summaryTimeout"))
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

  // Map chunk offset → Parse pane scroll (lightweight viewer, not TipTap)
  useEffect(() => {
    if (highlightOffset === undefined || !previewContent) return
    const root = document.querySelector("[data-parse-root]")
    if (!(root instanceof HTMLElement)) return
    const rawLen = Math.max(1, previewContent.length)
    const ratio = Math.min(1, Math.max(0, highlightOffset / rawLen))
    const max = root.scrollHeight - root.clientHeight
    if (max > 0) root.scrollTop = ratio * max
  }, [previewContent, highlightOffset, activeTab])

  const isParentChild = chunks.some((c) => c.chunk_type === "parent")
  const groupedChunks = useMemo(() => {
    if (!isParentChild) return null
    // Group by parent_id (not sequential scan) so children still attach when
    // API order is imperfect or children were recovered without version_id.
    const parents = chunks
      .filter((c) => c.chunk_type === "parent")
      .slice()
      .sort((a, b) => (a.chunk_index ?? 0) - (b.chunk_index ?? 0))
    const childrenByParent = new Map<string, ChunkDetail[]>()
    for (const c of chunks) {
      if (c.chunk_type !== "child") continue
      const pid = (c.parent_id || "").trim()
      if (!pid) continue
      const list = childrenByParent.get(pid) || []
      list.push(c)
      childrenByParent.set(pid, list)
    }
    for (const list of childrenByParent.values()) {
      list.sort((a, b) => (a.chunk_index ?? 0) - (b.chunk_index ?? 0))
    }
    return parents.map((parent) => ({
      parent,
      children: childrenByParent.get(parent.id) || [],
    }))
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
      toast.info(t("fileMgmt.stillIngestingPreview"))
      setActiveTab("raw")
      return
    }
    if (tab === "ingest" && !developerMode) {
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
      toast.success(t("fileMgmt.pinnedToFolder"))
      await loadDetail()
      await refreshFiles(collectionId)
    } catch (err) {
      toast.error(
        t("fileMgmt.failed", { error: formatApiError(err, t) })
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
      toast.error(t("fileMgmt.pathFromNode"))
      return
    }
    setActionBusy(true)
    try {
      await demoteFilePath(collectionId, fileId, path.path_id)
      toast.success(t("fileMgmt.unpinnedFolder"))
      await loadDetail()
      await refreshFiles(collectionId)
    } catch (err) {
      toast.error(
        t("fileMgmt.unpinFailed", { error: formatApiError(err, t) })
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
        toast.success(t("fileMgmt.removedFromNode"))
      } else {
        for (const p of contextPaths) {
          await removeFilePath(collectionId, fileId, p.path_id)
        }
        toast.success(t("fileMgmt.removedCurrentPath"))
      }
      await loadDetail()
      await refreshFiles(collectionId)
    } catch (err) {
      toast.error(
        t("fileMgmt.removePathFailed", { error: formatApiError(err, t) })
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
      toast.success(t("fileMgmt.archivedCurrent"))
      await loadDetail()
      await refreshFiles(collectionId)
    } catch (err) {
      toast.error(
        t("fileMgmt.archiveFailed", { error: formatApiError(err, t) })
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
      toast.success(t("fileMgmt.archivedGloballyDone"))
      await loadDetail()
      await refreshFiles(collectionId)
    } catch (err) {
      toast.error(
        t("fileMgmt.archiveFailed", { error: formatApiError(err, t) })
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
      toast.success(t("fileMgmt.restored"))
      await loadDetail()
      await refreshFiles(collectionId)
    } catch (err) {
      toast.error(
        t("fileMgmt.restoreFailed", { error: formatApiError(err, t) })
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
          ? t("fileMgmt.markedDefinitiveWillFeed")
          : t("fileMgmt.clearedDefinitiveExcluded")
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
        t("fileMgmt.failed", { error: formatApiError(err, t) })
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
      toast.success(t("fileMgmt.filePermanentlyDeleted"))
      setDeleteConfirm(false)
      onOpenChange(false)
      await refreshFiles(collectionId)
      onDeleted?.()
    } catch (err) {
      toast.error(
        t("fileMgmt.deleteFailed", { error: formatApiError(err, t) })
      )
    } finally {
      setActionBusy(false)
    }
  }

  /** Make focused historical version current; hard-delete all later versions. */
  const handleRollback = async () => {
    if (!fileId || !focusVersionId || !isHistoricalFocus) return
    setRollingBack(true)
    try {
      const res = await rollbackFileVersion(
        collectionId,
        fileId,
        focusVersionId
      )
      const n = res.deleted_count ?? res.deleted_version_ids?.length ?? 0
      toast.success(
        n > 0
          ? t("fileMgmt.rolledBackDeleted", {
              n: res.version_no,
              count: n,
              s: n === 1 ? "" : "s",
            })
          : t("fileMgmt.rolledBack", { n: res.version_no })
      )
      setRollbackConfirm(false)
      // Reopen as current (drop historical pin)
      await loadDetail()
      await refreshFiles(collectionId)
      onOpenChange(false)
      onDeleted?.()
    } catch (err) {
      toast.error(
        err instanceof FileMgmtApiError
          ? err.message
          : t("fileMgmt.rollbackFailed", { error: formatApiError(err, t) })
      )
    } finally {
      setRollingBack(false)
    }
  }

  const handleDeleteMessage = async (msg: Message) => {
    if (msg.author_type === "system" || isVersionUpdateMessage(msg)) {
      toast.error(t("fileMgmt.versionNotesNoDelete"))
      return
    }
    setMsgBusy(true)
    try {
      await deleteMessage(collectionId, msg.message_id)
      await loadDetail()
    } catch (err) {
      toast.error(
        t("fileMgmt.deleteFailed", { error: formatApiError(err, t) })
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
      toast.success(t("fileMgmt.messageAdded"))
      setAddMsgDialogOpen(false)
      await loadDetail()
    } catch (err) {
      toast.error(
        t("fileMgmt.failed", { error: formatApiError(err, t) })
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
          <FileDetailTitleChrome
            titleName={titleName}
            isIngesting={isIngesting}
            ingestProgress={ingestProgress}
            isHistoricalFocus={isHistoricalFocus}
            focusVersion={focusVersion}
            chunksTotal={chunksTotal}
            detail={detail}
          />

          {loading && isManagedFile && !detail ? (
            <div className="pm-ws-loading flex-1">
              <Loader2 className="h-5 w-5 animate-spin" />
              Loading…
            </div>
          ) : (
            <div className="pm-ws-body">
              {/* ── Left: large nested white content card ── */}
              <FileDetailMainPane
                collectionId={collectionId}
                fileId={fileId}
                source={source}
                docSource={docSource}
                storageFileIdProp={storageFileIdProp}
                isIngesting={isIngesting}
                activeTab={activeTab}
                handleTabChange={handleTabChange}
                chunksTotal={chunksTotal}
                isHistoricalFocus={isHistoricalFocus}
                focusVersionId={focusVersionId}
                focusVersion={focusVersion}
                rollingBack={rollingBack}
                actionBusy={actionBusy}
                setRollbackConfirm={setRollbackConfirm}
                goToLabel={goToLabel}
                handleGoToSource={handleGoToSource}
                viewStorageFile={viewStorageFile}
                currentRawUrl={currentRawUrl}
                downloadUrl={downloadUrl}
                detail={detail}
                previewLoading={previewLoading}
                isUnsupported={isUnsupported}
                chunksLoading={chunksLoading}
                previewContent={previewContent}
                sourceEditorRef={sourceEditorRef}
                chunks={chunks}
                isGenerating={isGenerating}
                summaryLoading={summaryLoading}
                docSummary={docSummary}
                setRenderTick={setRenderTick}
                setActiveTab={setActiveTab}
                setDocSummary={setDocSummary}
                groupedChunks={groupedChunks}
                expandedParents={expandedParents}
                toggleParent={toggleParent}
                handleLocate={handleLocate}
                expandedChunks={expandedChunks}
                highlightedIdx={highlightedIdx}
                toggleChunkExpand={toggleChunkExpand}
              />

              <FileDetailSideRail
                detail={detail}
                isManagedFile={isManagedFile}
                isHistoricalFocus={isHistoricalFocus}
                actionBusy={actionBusy}
                handleToggleDefinitive={handleToggleDefinitive}
                focusVersionId={focusVersionId}
                focusVersion={focusVersion}
                storageFileIdProp={storageFileIdProp}
                viewStorageFile={viewStorageFile}
                openSide={openSide}
                toggleSide={toggleSide}
                onNavigateToFolder={onNavigateToFolder}
                onOpenChange={onOpenChange}
                handlePromote={handlePromote}
                handleUnpin={handleUnpin}
                setPreviewNodeId={setPreviewNodeId}
                timeline={timeline}
                msgBusy={msgBusy}
                fileId={fileId}
                setAddMsgDialogOpen={setAddMsgDialogOpen}
                logScopeRef={logScopeRef}
                timelineFilter={timelineFilter}
                logScopeInd={logScopeInd}
                logScopeAllRef={logScopeAllRef}
                logScopeVerRef={logScopeVerRef}
                handleTimelineFilter={handleTimelineFilter}
                logListPhase={logListPhase}
                setLogMsgOpen={setLogMsgOpen}
                handleDeleteMessage={handleDeleteMessage}
                deleteConfirm={deleteConfirm}
                handleDelete={handleDelete}
                setDeleteConfirm={setDeleteConfirm}
                actionMenuRef={actionMenuRef}
                setActionMenu={setActionMenu}
                setUpdateDialogOpen={setUpdateDialogOpen}
                canArchiveCurrentPath={canArchiveCurrentPath}
                canArchiveGlobally={canArchiveGlobally}
                canRestore={canRestore}
                actionMenu={actionMenu}
                fileArchived={fileArchived}
                contextNodeId={contextNodeId}
                handleRestore={handleRestore}
                handleArchiveCurrentPath={handleArchiveCurrentPath}
                handleArchiveGlobally={handleArchiveGlobally}
                canRemoveCurrentPath={canRemoveCurrentPath}
                handleRemoveCurrentPath={handleRemoveCurrentPath}
              />
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
        title={t("fileMgmt.addMessage")}
        kicker={t("common.file")}
        description={t("fileMgmt.newMessageOnThisFile")}
        initialContent=""
        onSave={(content) => void handleAddMessage(content)}
        readonly={false}
        message={null}
        collectionId={collectionId}
      />

      <LogMessageDialog
        open={!!logMsgOpen}
        onOpenChange={(v) => {
          // Clear immediately — LogMessageDialog.held keeps payload for exit silk
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
        onVersionRolledBack={() => {
          setLogMsgOpen(null)
          void loadDetail()
          void refreshFiles(collectionId)
        }}
      />

      <FileDetailRollbackDialog
        rollbackConfirm={rollbackConfirm}
        setRollbackConfirm={setRollbackConfirm}
        rollingBack={rollingBack}
        focusVersion={focusVersion}
        focusVersionId={focusVersionId}
        handleRollback={handleRollback}
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
})
