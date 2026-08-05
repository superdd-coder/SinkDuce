import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import {
  Loader2, ArrowDownToLine, Pencil, Check, X, Trash2, Download,
  PanelRight, Database, RefreshCw, Share2, Columns2, ChevronDown,
  MoreVertical,
} from "lucide-react"
import { toast } from "sonner"
import {
  getNote,
  updateNote,
  deleteNote,
  distillNote,
  distillMeetingIntoNote,
  uploadNoteImage,
  ingestNote,
  reingestNote,
  getTask,
  triggerPropagation,
  type NoteDetail,
} from "@/api/client"
import { deleteFile } from "@/api/file-mgmt"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import {
  preprocessDistillBlocks,
  postprocessDistillBlocks,
  EditorToolbar,
} from "@/components/ui/tiptap-editor"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog"
import { SoftMenu, MenuItem } from "@/components/ui/menu"
import { _triggerFilesRefresh } from "@/components/database/database-view"
import { triggerInfoRefresh } from "@/lib/info-refresh"
import { cn } from "@/lib/utils"

const _ingestingIds = new Set<string>()

// ── Distill jobs survive dialog unmount (close mid-distill must still land) ──

/** Replace loading fence in markdown by temp block id. */
function replaceLoadingFence(md: string, tempId: string, replacement: string): string {
  const re = new RegExp(
    String.raw`:::distill-block\{[^\n}]*"id"\s*:\s*"${tempId}"[^\n}]*\}\n[\s\S]*?\n:::`,
  )
  if (re.test(md)) return md.replace(re, replacement)
  const re2 = new RegExp(
    String.raw`:::distill-block\{[^\n]*"id"\s*:\s*"${tempId}"[^\n]*\}\n[\s\S]*?\n:::`,
  )
  return md.replace(re2, replacement)
}

type DistillApiResult = {
  block_id: string
  source_note_id: string
  source_title: string
  distilled_content: string
}

/**
 * Run distill API and always persist the result to the note on the server.
 * Independent of React mount lifecycle — safe after dialog close.
 */
async function persistDistillResult(opts: {
  collection: string
  noteId: string
  tempBlockId: string
  distillFn: () => Promise<DistillApiResult>
}): Promise<
  | { ok: true; finalContent: string; result: DistillApiResult }
  | { ok: false; error: string; cleanedContent?: string }
> {
  const { collection, noteId, tempBlockId, distillFn } = opts
  try {
    const res = await distillFn()
    const blockMd = `:::distill-block${JSON.stringify({
      id: res.block_id,
      source: res.source_note_id,
      "source-title": res.source_title,
    })}\n${res.distilled_content}\n:::`

    // Always re-read server content (UI may be gone / stale)
    const remote = await getNote(collection, noteId)
    let patched = replaceLoadingFence(remote.content || "", tempBlockId, blockMd)
    if (patched === (remote.content || "")) {
      // Loading fence missing (race/close before save) — append result
      patched = `${remote.content || ""}\n\n${blockMd}\n`.replace(/\n{3,}/g, "\n\n")
    }
    await updateNote(collection, noteId, { content: patched })
    return { ok: true, finalContent: patched, result: res }
  } catch (err) {
    const message = err instanceof Error ? err.message : "Distillation failed"
    // Best-effort: strip loading fence from server
    try {
      const remote = await getNote(collection, noteId)
      const cleaned = replaceLoadingFence(remote.content || "", tempBlockId, "")
        .replace(/\n{3,}/g, "\n\n")
      if (cleaned !== (remote.content || "")) {
        await updateNote(collection, noteId, { content: cleaned })
      }
      return { ok: false, error: message, cleanedContent: cleaned }
    } catch {
      return { ok: false, error: message }
    }
  }
}

interface NotePaneProps {
  collection: string
  noteId: string
  focused: boolean
  onFocus: () => void
  /**
   * Visual focus chrome (header tint / hairline). Off for single-pane —
   * selection emphasis only matters in dual-pane.
   */
  showFocusChrome?: boolean
  onNoteMeta?: (note: NoteDetail) => void
  onNavigateSource: (sourceId: string) => void
  /** Title changed — parent updates notes list */
  onTitleChange?: (noteId: string, title: string) => void
  /** Note deleted — parent updates panes / list */
  onDeleted?: (noteId: string) => void
  /** Close this pane (dual-page X) */
  onClosePane?: () => void
  showClose?: boolean
  /** Split into second page — only when not already split */
  onSplit?: () => void
  showSplit?: boolean
  /** Close whole dialog (e.g. go to folder) */
  onCloseDialog?: () => void
  className?: string
  /** Changes when document identity switches — drives soft content enter */
  docSwapKey?: string
}

/**
 * Self-contained note editor pane for dual-page layout.
 * Each pane owns Title / Delete / Export / Go to folder / Ingest chrome.
 */
export function NotePane({
  collection,
  noteId,
  focused,
  onFocus,
  showFocusChrome = true,
  onNoteMeta,
  onNavigateSource,
  onTitleChange,
  onDeleted,
  onClosePane,
  showClose,
  onSplit,
  showSplit,
  onCloseDialog,
  className,
  docSwapKey,
}: NotePaneProps) {
  const [loading, setLoading] = useState(true)
  /** After first note loads, further switches keep chrome and soft-fade body */
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false)
  const [content, setContent] = useState("")
  const [dropOver, setDropOver] = useState(false)
  const [distilling, setDistilling] = useState(false)
  const [editorInstance, setEditorInstance] = useState<any>(null)

  const [note, setNote] = useState<NoteDetail | null>(null)
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState("")
  const [deleteOpen, setDeleteOpen] = useState(false)

  const [ingesting, setIngesting] = useState(false)
  const [ingested, setIngested] = useState(false)
  const [ingestedHash, setIngestedHash] = useState<string | null>(null)
  const [liveHash, setLiveHash] = useState<string | null>(null)
  const [managedFileId, setManagedFileId] = useState<string | null>(null)
  const [propagating, setPropagating] = useState(false)
  const [propagateDismissed, setPropagateDismissed] = useState(false)
  const [updateMenuOpen, setUpdateMenuOpen] = useState(false)
  const [moreMenuOpen, setMoreMenuOpen] = useState(false)

  const contentRef = useRef("")
  const baselineRef = useRef("")
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const editorRef = useRef<any>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const updateMenuRef = useRef<HTMLDivElement>(null)
  const moreMenuRef = useRef<HTMLDivElement>(null)
  const noteIdRef = useRef(noteId)
  noteIdRef.current = noteId
  const distillCountRef = useRef(0)

  const sha256Hex = useCallback(async (text: string) => {
    const buf = await crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(text || "")
    )
    return Array.from(new Uint8Array(buf))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("")
  }, [])

  const applyMeta = useCallback(
    async (n: NoteDetail) => {
      setNote(n)
      setIngested(!!n.is_ingested)
      setManagedFileId(n.file_id ?? null)
      setIngestedHash(n.is_ingested ? (n.ingested_content_hash ?? null) : null)
      setIngesting(_ingestingIds.has(n.id))
      onNoteMeta?.(n)
      const h = await sha256Hex(n.content || "")
      setLiveHash(h)
    },
    [onNoteMeta, sha256Hex]
  )

  // Re-emit meta when this pane gains focus so distill rail matches focused note.
  // Depend only on focused + note.id — not full note identity — to avoid parent
  // re-renders thrashing the editor mid-interaction.
  useEffect(() => {
    if (focused && note) onNoteMeta?.(note)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional: only on focus / note switch
  }, [focused, note?.id])

  // Load note — chrome stays; body remounts with soft enter (no hard blank wipe)
  useEffect(() => {
    let cancelled = false
    setEditingTitle(false)
    setPropagateDismissed(false)
    if (!noteId || !collection) {
      setLoading(false)
      setContent("")
      contentRef.current = ""
      setNote(null)
      return
    }
    setLoading(true)
    baselineRef.current = ""
    // Drop previous document body immediately; shell + dim overlay stay
    setContent("")
    contentRef.current = ""
    setEditorInstance(null)
    editorRef.current = null
    ;(async () => {
      try {
        const n = await getNote(collection, noteId)
        if (cancelled) return
        const c = n.content || ""
        setContent(c)
        contentRef.current = c
        await applyMeta(n)
        setLoading(false)
        setHasLoadedOnce(true)
      } catch {
        if (!cancelled) {
          toast.error("Failed to load note")
          setLoading(false)
        }
      }
    })()
    return () => {
      cancelled = true
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current)
        saveTimerRef.current = null
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collection, noteId])

  // Live content hash for reingest dirty detection
  useEffect(() => {
    let cancelled = false
    void sha256Hex(content).then((h) => {
      if (!cancelled) setLiveHash(h)
    })
    return () => {
      cancelled = true
    }
  }, [content, sha256Hex])

  const needsReingest =
    ingested && !!ingestedHash && !!liveHash && liveHash !== ingestedHash

  const scheduleSave = useCallback(
    (value: string) => {
      if (!baselineRef.current) return
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
      const id = noteIdRef.current
      saveTimerRef.current = setTimeout(async () => {
        try {
          await updateNote(collection, id, { content: value })
        } catch {
          toast.error("Auto-save failed")
        }
      }, 800)
    },
    [collection]
  )

  const handleChange = useCallback(
    (value: string) => {
      setContent(value)
      contentRef.current = value
      if (!baselineRef.current) {
        baselineRef.current = value
        return
      }
      if (value !== baselineRef.current) {
        setPropagateDismissed(false)
      }
      scheduleSave(value)
    },
    [scheduleSave]
  )

  const handleImageUpload = useCallback(
    async (file: File) => {
      const result = await uploadNoteImage(collection, noteIdRef.current, file)
      return result.url
    },
    [collection]
  )

  // ── Title ─────────────────────────────────────────────

  const handleTitleSave = async () => {
    if (!titleDraft.trim() || !note) return
    try {
      await updateNote(collection, note.id, { title: titleDraft.trim() })
      const next = { ...note, title: titleDraft.trim() }
      setNote(next)
      onTitleChange?.(note.id, titleDraft.trim())
      onNoteMeta?.(next)
    } catch {
      toast.error("Failed to update title")
    }
    setEditingTitle(false)
  }

  // ── Delete ────────────────────────────────────────────

  const handleDeleteConfirm = async () => {
    if (!note) return
    const nid = note.id
    const title = note.title
    const fid = managedFileId
    try {
      if (fid) {
        try {
          await deleteFile(collection, fid)
        } catch { /* continue */ }
      }
      await deleteNote(collection, nid)
      toast.success(
        fid
          ? `Deleted "${title}" and all file versions`
          : `Deleted "${title}"`
      )
      try {
        const { useFileMgmtStore } = await import("@/stores/file-mgmt-store")
        await useFileMgmtStore.getState().refreshFiles(collection, { silent: true })
      } catch { /* ignore */ }
      triggerInfoRefresh({ collectionId: collection, reason: "note-uningest" })
      _triggerFilesRefresh()
      setDeleteOpen(false)
      onDeleted?.(nid)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete note")
    }
  }

  // ── Export ────────────────────────────────────────────

  const handleDownload = () => {
    if (!note) return
    const blob = new Blob([contentRef.current || ""], {
      type: "text/markdown;charset=utf-8",
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `${note.title || "note"}.md`
    a.click()
    URL.revokeObjectURL(url)
    toast.success("Exported")
  }

  // ── Go to folder ──────────────────────────────────────

  const handleGoToFolder = () => {
    if (!managedFileId || !note) {
      toast.error("This note is not linked to a folder file yet — ingest first")
      return
    }
    onCloseDialog?.()
    window.dispatchEvent(
      new CustomEvent("open-note-file-in-folder", {
        detail: {
          collectionId: collection,
          fileId: managedFileId,
          noteId: note.id,
        },
      })
    )
  }

  // ── Ingest ────────────────────────────────────────────

  const afterIngestRefresh = async (nid: string, title: string) => {
    _triggerFilesRefresh()
    try {
      const { useFileMgmtStore } = await import("@/stores/file-mgmt-store")
      await useFileMgmtStore.getState().refreshFiles(collection, { silent: true })
    } catch { /* ignore */ }
    triggerInfoRefresh({ collectionId: collection, reason: "note-ingest" })
    try {
      const n = await getNote(collection, nid)
      if (noteIdRef.current === nid) await applyMeta(n)
    } catch { /* ignore */ }
    toast.success(`"${title}" ingestion complete`)
  }

  const handleIngestClick = async () => {
    if (!note) return
    const nid = note.id
    const title = note.title
    // Flush autosave
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
    }
    try {
      await updateNote(collection, nid, { content: contentRef.current })
    } catch { /* best-effort */ }

    _ingestingIds.add(nid)
    setIngesting(true)
    try {
      if (needsReingest || (ingested && managedFileId)) {
        const res = await reingestNote(collection, nid)
        toast.success("Reingest started (new file version)")
        const taskId = res.task_id
        const fileId = res.file_id || managedFileId
        if (taskId && fileId) {
          try {
            const { useFileMgmtStore } = await import("@/stores/file-mgmt-store")
            useFileMgmtStore.getState()._startTaskPolling(collection, taskId, fileId, {
              silentToast: true,
            })
          } catch { /* ignore */ }
        }
        if (taskId) {
          for (let i = 0; i < 90; i++) {
            await new Promise((r) => setTimeout(r, 2000))
            try {
              const task = await getTask(taskId)
              if (task.status === "completed") {
                _ingestingIds.delete(nid)
                if (noteIdRef.current === nid) setIngesting(false)
                await afterIngestRefresh(nid, title)
                return
              }
              if (task.status === "failed") {
                throw new Error(task.error || "Reingest failed")
              }
            } catch (e) {
              if (e instanceof Error && e.message !== "Reingest failed" && i < 89) continue
              throw e
            }
          }
        }
        _ingestingIds.delete(nid)
        if (noteIdRef.current === nid) setIngesting(false)
      } else {
        await ingestNote(collection, nid)
        toast.success("Ingestion started")
        for (let i = 0; i < 45; i++) {
          await new Promise((r) => setTimeout(r, 2000))
          try {
            const n = await getNote(collection, nid)
            if (n.is_ingested) {
              _ingestingIds.delete(nid)
              if (noteIdRef.current === nid) {
                setIngesting(false)
                await applyMeta(n)
              }
              await afterIngestRefresh(nid, title)
              return
            }
          } catch { /* retry */ }
        }
        _ingestingIds.delete(nid)
        if (noteIdRef.current === nid) setIngesting(false)
        toast.error("Ingestion is taking longer than expected — check Files later")
      }
    } catch (err) {
      _ingestingIds.delete(nid)
      if (noteIdRef.current === nid) setIngesting(false)
      toast.error(err instanceof Error ? err.message : "Ingestion failed")
    }
  }

  // ── Propagate ─────────────────────────────────────────

  const canSyncChanges = !!note?.is_extracted && !propagateDismissed

  // Close action menus on outside click / Escape
  useEffect(() => {
    if (!updateMenuOpen && !moreMenuOpen) return
    const onPointer = (e: MouseEvent) => {
      const t = e.target as Node
      if (updateMenuRef.current?.contains(t)) return
      if (moreMenuRef.current?.contains(t)) return
      setUpdateMenuOpen(false)
      setMoreMenuOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setUpdateMenuOpen(false)
        setMoreMenuOpen(false)
      }
    }
    document.addEventListener("mousedown", onPointer)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onPointer)
      document.removeEventListener("keydown", onKey)
    }
  }, [updateMenuOpen, moreMenuOpen])

  // Close menus when switching notes
  useEffect(() => {
    setUpdateMenuOpen(false)
    setMoreMenuOpen(false)
  }, [noteId])

  const handlePropagate = async () => {
    if (!note) return
    setPropagating(true)
    setUpdateMenuOpen(false)
    try {
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current)
        saveTimerRef.current = null
      }
      await updateNote(collection, note.id, { content: contentRef.current })
      await triggerPropagation(collection, note.id)
      toast.success("Sync Changes started")
      setPropagateDismissed(true)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Sync Changes failed")
    } finally {
      setPropagating(false)
    }
  }

  // ── Distill drop ──────────────────────────────────────

  /** Read live markdown from Tiptap (keeps loading flags via postprocess). */
  const readEditorMarkdown = useCallback((): string => {
    const editor = editorRef.current
    if (!editor) return contentRef.current
    try {
      const storage = editor.storage as any
      const raw = storage?.markdown?.getMarkdown?.() ?? ""
      return postprocessDistillBlocks(raw) || contentRef.current
    } catch {
      return contentRef.current
    }
  }, [])

  const syncContent = useCallback((md: string) => {
    contentRef.current = md
    setContent(md)
  }, [])

  /** Hot-update a distill NodeView by blockId (loading → result) without full remount. */
  const patchDistillNode = useCallback(
    (
      blockId: string,
      next: {
        blockId?: string
        sourceNoteId?: string
        sourceTitle?: string
        text?: string
        loading?: boolean
      } | null, // null = delete node
    ): boolean => {
      const editor = editorRef.current
      if (!editor?.state) return false
      let foundPos: number | null = null
      let foundNode: any = null
      editor.state.doc.descendants((node: any, pos: number) => {
        if (node.type?.name === "distillBlock" && node.attrs?.blockId === blockId) {
          foundPos = pos
          foundNode = node
          return false
        }
      })
      if (foundPos == null || !foundNode) return false
      if (next === null) {
        editor
          .chain()
          .focus()
          .deleteRange({ from: foundPos, to: foundPos + foundNode.nodeSize })
          .run()
        return true
      }
      const tr = editor.state.tr.setNodeMarkup(foundPos, undefined, {
        ...foundNode.attrs,
        ...next,
      })
      editor.view.dispatch(tr)
      return true
    },
    []
  )

  const insertDistillBlock = useCallback(
    async (
      sourceId: string,
      sourceTitle: string,
      distillFn: () => Promise<DistillApiResult>,
      dropCoords?: { x: number; y: number },
    ) => {
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current)
        saveTimerRef.current = null
      }
      const targetId = noteIdRef.current
      const tempBlockId = `distill-loading-${Date.now()}`
      const loadingBody = `⏳ Distilling "${sourceTitle}"...`
      const loadingMd = `\n\n:::distill-block{"id":"${tempBlockId}","source":"${sourceId}","source-title":${JSON.stringify(sourceTitle)},"loading":true}\n${loadingBody}\n:::\n\n`

      // ── 1) Insert loading placeholder (UI) ──
      let insertedViaPm = false
      if (dropCoords && editorRef.current?.view) {
        const pos = editorRef.current.view.posAtCoords({
          left: dropCoords.x,
          top: dropCoords.y,
        })
        if (pos) {
          const { processed } = preprocessDistillBlocks(loadingMd.trim() + "\n\n")
          editorRef.current.commands.insertContentAt(pos.pos, processed)
          insertedViaPm = true
        }
      }

      let loadingContent: string
      if (insertedViaPm) {
        loadingContent = readEditorMarkdown()
        if (!loadingContent.includes(tempBlockId)) {
          loadingContent = `${contentRef.current}${loadingMd}`
        }
      } else {
        loadingContent = `${contentRef.current}${loadingMd}`
      }
      // Only touch React state if still on this note
      if (noteIdRef.current === targetId) {
        syncContent(loadingContent)
        baselineRef.current = loadingContent
      }

      // Persist loading fence BEFORE distill — must land even if dialog closes next
      try {
        await updateNote(collection, targetId, { content: loadingContent })
      } catch {
        toast.error("Failed to save distill placeholder — retry")
        return
      }

      distillCountRef.current++
      if (noteIdRef.current === targetId) setDistilling(true)

      // ── 2) Distill + always write result to server (survives unmount) ──
      const outcome = await persistDistillResult({
        collection,
        noteId: targetId,
        tempBlockId,
        distillFn,
      })

      // ── 3) Optional UI sync if this pane is still open on the same note ──
      const stillHere = noteIdRef.current === targetId
      if (outcome.ok) {
        toast.success(`Distilled "${outcome.result.source_title}"`)
        if (stillHere) {
          const res = outcome.result
          const patchedPm = patchDistillNode(tempBlockId, {
            blockId: res.block_id,
            sourceNoteId: res.source_note_id,
            sourceTitle: res.source_title,
            text: res.distilled_content,
            loading: false,
          })
          let finalMd = outcome.finalContent
          if (patchedPm) {
            const live = readEditorMarkdown()
            if (!live.includes(tempBlockId)) finalMd = live
          }
          syncContent(finalMd)
          baselineRef.current = finalMd
          try {
            const n = await getNote(collection, targetId)
            await applyMeta(n)
          } catch { /* ignore */ }
        }
      } else {
        toast.error(outcome.error)
        if (stillHere) {
          const removed = patchDistillNode(tempBlockId, null)
          let next = removed
            ? readEditorMarkdown()
            : (outcome.cleanedContent ??
              replaceLoadingFence(contentRef.current, tempBlockId, ""))
          next = next.replace(/\n{3,}/g, "\n\n")
          syncContent(next)
          baselineRef.current = next
        }
      }

      distillCountRef.current--
      if (noteIdRef.current === targetId) {
        setDistilling(distillCountRef.current > 0)
      }
    },
    [collection, applyMeta, readEditorMarkdown, syncContent, patchDistillNode]
  )

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      setDropOver(false)
      onFocus()

      const meetingId = e.dataTransfer.getData("application/meeting-id")
      if (meetingId) {
        const tabId =
          e.dataTransfer.getData("application/meeting-tab-id") || "tab_general"
        const title =
          e.dataTransfer.getData("application/meeting-title") || meetingId
        await insertDistillBlock(
          `meeting:${meetingId}:${tabId}`,
          title,
          () =>
            distillMeetingIntoNote(
              collection,
              noteIdRef.current,
              meetingId,
              tabId
            ),
          { x: e.clientX, y: e.clientY }
        )
        return
      }

      const sourceNoteId = e.dataTransfer.getData("application/note-id")
      if (sourceNoteId && sourceNoteId !== noteIdRef.current) {
        const title =
          e.dataTransfer.getData("application/note-title") || sourceNoteId
        await insertDistillBlock(
          sourceNoteId,
          title,
          () => distillNote(collection, noteIdRef.current, sourceNoteId),
          { x: e.clientX, y: e.clientY }
        )
      }
    },
    [collection, insertDistillBlock, onFocus]
  )

  // ── Render ────────────────────────────────────────────

  const softLoading = loading && hasLoadedOnce
  const hardLoading = loading && !hasLoadedOnce

  /**
   * Mark this pane as focused (for distill rail / chrome highlight).
   * Actions always run against this pane's own noteId/state — never the
   * sibling pane — even if we were not focused when the user clicked.
   */
  const claimFocus = useCallback(() => {
    if (!focused) onFocus()
  }, [focused, onFocus])

  /**
   * After pane focus / distill rail layout thrash, PM sometimes leaves a
   * full-document selection. Collapse it to a caret so "click to focus"
   * never looks like select-all.
   */
  useLayoutEffect(() => {
    if (!focused) return
    const collapseAccidentalAll = () => {
      const editor = editorRef.current
      if (!editor || editor.isDestroyed) return
      try {
        const { from, to, empty } = editor.state.selection
        if (empty) return
        const size = editor.state.doc.content.size
        // Entire (or near-entire) doc selected → treat as bug, keep caret
        if (size > 4 && to - from >= size - 2) {
          const pos = Math.min(Math.max(from, 1), size)
          editor.commands.setTextSelection(pos)
        }
      } catch {
        /* ignore */
      }
    }
    collapseAccidentalAll()
    const t0 = window.setTimeout(collapseAccidentalAll, 0)
    // Rail open eases ~360ms; catch selection reset mid-layout
    const t1 = window.setTimeout(collapseAccidentalAll, 40)
    const t2 = window.setTimeout(collapseAccidentalAll, 120)
    return () => {
      window.clearTimeout(t0)
      window.clearTimeout(t1)
      window.clearTimeout(t2)
    }
  }, [focused])

  /**
   * Non-editor chrome padding only — ProseMirror uses onEditorFocus instead
   * so we never intercept mousedown/mouseup of a text click.
   */
  const handleShellMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (focused) return
      if (e.button !== 0) return
      const t = e.target as HTMLElement | null
      if (t?.closest?.(".pm-ws-pane-h, [data-slot='menu'], .ProseMirror, .tiptap-editor")) {
        return
      }
      // Clicked empty shell around editor
      claimFocus()
    },
    [focused, claimFocus]
  )

  return (
    <div
      ref={containerRef}
      className={cn(
        "flex-1 flex flex-col min-w-0 min-h-0 relative",
        className,
        softLoading && "is-doc-loading"
      )}
      onMouseDown={handleShellMouseDown}
      onDragOver={(e) => {
        e.preventDefault()
        e.dataTransfer.dropEffect = "copy"
        setDropOver(true)
      }}
      onDragLeave={(e) => {
        const rect = e.currentTarget.getBoundingClientRect()
        const { clientX: x, clientY: y } = e
        if (x <= rect.left || x >= rect.right || y <= rect.top || y >= rect.bottom) {
          setDropOver(false)
        }
      }}
      onDrop={handleDrop}
    >
      {/*
        Chrome is always interactive — even when this pane is not focused.
        stopPropagation so editor-area focus deferral never races tool clicks.
        claimFocus() runs in the same click handler as the action (React batches),
        so the action still binds to THIS pane's noteId.
      */}
      <div
        className={cn("pm-ws-pane-h", showFocusChrome && "is-focus")}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* Row 1: title + close */}
        <div className="flex items-center gap-1 px-2.5 pt-2 pb-0.5 min-h-8">
          {editingTitle ? (
            <div className="flex items-center gap-1 flex-1 min-w-0">
              <Input
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void handleTitleSave()
                  if (e.key === "Escape") setEditingTitle(false)
                }}
                className="h-7 pm-ws-pane-title flex-1 min-w-0"
                autoFocus
              />
              <Button
                variant="ghost"
                size="sm"
                className="pm-ws-icon-btn"
                onClick={() => {
                  claimFocus()
                  void handleTitleSave()
                }}
              >
                <Check className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="pm-ws-icon-btn"
                onClick={() => {
                  claimFocus()
                  setEditingTitle(false)
                }}
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
          ) : (
            <>
              <span
                className="pm-ws-pane-title flex-1"
                onClick={claimFocus}
              >
                {note?.title || "Note"}
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="pm-ws-icon-btn !h-6 !w-6"
                onClick={() => {
                  claimFocus()
                  setTitleDraft(note?.title || "")
                  setEditingTitle(true)
                }}
                title="Rename"
              >
                <Pencil className="h-3 w-3" />
              </Button>
            </>
          )}
          {showSplit && onSplit && (
            <Button
              variant="ghost"
              size="sm"
              className="pm-ws-action"
              onClick={() => {
                claimFocus()
                onSplit()
              }}
              title="Split into second page"
            >
              <Columns2 className="h-3.5 w-3.5" />
              Split
            </Button>
          )}
          {showClose && onClosePane && (
            <Button
              variant="ghost"
              size="sm"
              className="pm-ws-icon-btn !h-6 !w-6"
              onClick={() => {
                // Close this pane only — do not claim focus first (would steal from sibling)
                onClosePane()
              }}
              title="Close page"
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>

        {/*
          Row 2 toolbar:
          left  — Ingested status tag
          right — Detail · Ingest | Update▾ · ⋮ (Download / Delete)
        */}
        <div className="flex flex-nowrap items-center gap-1 px-2 pb-2 min-w-0">
          <div className="flex items-center min-w-0 flex-1">
            {ingested && (
              <span className="pm-ws-status">
                {needsReingest ? "Ingested · edited" : "Ingested"}
              </span>
            )}
          </div>

          <div className="flex items-center gap-0.5 shrink-0">
            {managedFileId && (
              <Button
                variant="ghost"
                size="sm"
                className="pm-ws-action"
                onClick={() => {
                  claimFocus()
                  handleGoToFolder()
                }}
                title="Open file detail"
              >
                <PanelRight className="h-3.5 w-3.5" />
                Detail
              </Button>
            )}

            {/* Not ingested → Ingest; ingested → Update menu */}
            {!ingested ? (
              <Button
                variant="ghost"
                size="sm"
                className="pm-ws-action"
                onClick={() => {
                  claimFocus()
                  void handleIngestClick()
                }}
                disabled={ingesting || loading}
                title="Ingest note into the collection"
              >
                {ingesting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Database className="h-3.5 w-3.5" />
                )}
                {ingesting ? "Ingesting…" : "Ingest"}
              </Button>
            ) : (
              <div ref={updateMenuRef} className="relative">
                <Button
                  variant="ghost"
                  size="sm"
                  className={cn(
                    "pm-ws-action",
                    updateMenuOpen && "is-on"
                  )}
                  onClick={() => {
                    claimFocus()
                    setMoreMenuOpen(false)
                    setUpdateMenuOpen((v) => !v)
                  }}
                  disabled={ingesting || propagating || loading}
                  title="Update ingested note"
                  aria-haspopup="menu"
                  aria-expanded={updateMenuOpen}
                >
                  {ingesting || propagating ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <RefreshCw className="h-3.5 w-3.5" />
                  )}
                  {ingesting ? "Updating…" : "Update"}
                  <ChevronDown
                    className={cn(
                      "h-3 w-3 opacity-70 transition-transform duration-[180ms] ease-[cubic-bezier(0.32,0.72,0,1)]",
                      updateMenuOpen && "rotate-180"
                    )}
                  />
                </Button>
                <SoftMenu
                  open={updateMenuOpen}
                  className="absolute right-0 top-full mt-1 z-50 min-w-[11.5rem]"
                >
                  {canSyncChanges && (
                    <MenuItem
                      onClick={() => {
                        claimFocus()
                        void handlePropagate()
                      }}
                      disabled={propagating}
                    >
                      <Share2 className="h-3.5 w-3.5 shrink-0" />
                      Sync Changes
                    </MenuItem>
                  )}
                  <MenuItem
                    onClick={() => {
                      claimFocus()
                      setUpdateMenuOpen(false)
                      void handleIngestClick()
                    }}
                    disabled={ingesting}
                  >
                    <RefreshCw className="h-3.5 w-3.5 shrink-0" />
                    Reingest
                  </MenuItem>
                </SoftMenu>
              </div>
            )}

            {/* ⋮ more: Download + Delete */}
            <div ref={moreMenuRef} className="relative">
              <Button
                variant="ghost"
                size="sm"
                className={cn(
                  "pm-ws-icon-btn",
                  moreMenuOpen && "is-on"
                )}
                onClick={() => {
                  claimFocus()
                  setUpdateMenuOpen(false)
                  setMoreMenuOpen((v) => !v)
                }}
                title="More actions"
                aria-label="More actions"
                aria-haspopup="menu"
                aria-expanded={moreMenuOpen}
              >
                <MoreVertical className="h-3.5 w-3.5" />
              </Button>
              <SoftMenu
                open={moreMenuOpen}
                className="absolute right-0 top-full mt-1 z-50 min-w-[10rem]"
              >
                <MenuItem
                  onClick={() => {
                    claimFocus()
                    setMoreMenuOpen(false)
                    handleDownload()
                  }}
                >
                  <Download className="h-3.5 w-3.5 shrink-0" />
                  Download
                </MenuItem>
                <MenuItem
                  destructive
                  onClick={() => {
                    claimFocus()
                    setMoreMenuOpen(false)
                    setDeleteOpen(true)
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5 shrink-0" />
                  Delete
                </MenuItem>
              </SoftMenu>
            </div>
          </div>
        </div>
      </div>

      {hardLoading ? (
        <div className="pm-ws-loading flex-1">
          <Loader2 className="h-5 w-5 animate-spin" />
          Loading…
        </div>
      ) : (
        <div
          key={docSwapKey ?? noteId}
          className="pm-ws-doc-body relative flex-1 flex flex-col min-h-0 min-w-0 is-doc-swap"
        >
          {softLoading && (
            <div className="absolute inset-0 z-[1] flex items-center justify-center pointer-events-none">
              <Loader2 className="h-4 w-4 animate-spin text-[var(--pm-muted)] opacity-70" />
            </div>
          )}
          {editorInstance && !softLoading && (
            <EditorToolbar editor={editorInstance} />
          )}
          {distilling && (
            <div className="pm-ws-status-bar">
              <Loader2 className="h-3 w-3 animate-spin" />
              Distilling…
            </div>
          )}
          {!softLoading && (
            <div className="pm-ws-editor">
              <MarkdownEditor
                value={content}
                showToolbar={false}
                onEditorReady={(editor) => {
                  editorRef.current = editor
                  setEditorInstance(editor)
                }}
                onEditorFocus={claimFocus}
                onChange={handleChange}
                onImageUpload={handleImageUpload}
                onNoteLinkClick={(id) => onNavigateSource(id)}
                className="px-6 py-4"
                placeholder="Start writing your note..."
              />
            </div>
          )}
        </div>
      )}

      {dropOver && (
        <div className="pm-ws-drop">
          <ArrowDownToLine className="h-8 w-8" />
          <span>Drop to distill</span>
        </div>
      )}

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="pm-dialog max-w-sm !gap-3">
          <DialogHeader className="!gap-1">
            <DialogTitle>
              {managedFileId ? "Delete File Globally" : "Delete Note"}
            </DialogTitle>
          </DialogHeader>
          <p className="pm-dialog-body">
            {managedFileId ? (
              <>
                Permanently delete{" "}
                <span className="font-medium text-[var(--pm-ink)]">
                  &quot;{note?.title}&quot;
                </span>{" "}
                and its managed file, including all versions. This cannot be undone.
              </>
            ) : (
              <>
                Delete{" "}
                <span className="font-medium text-[var(--pm-ink)]">
                  &quot;{note?.title}&quot;
                </span>
                ? This cannot be undone.
              </>
            )}
          </p>
          <DialogFooter className="gap-2">
            <Button
              variant="ghost"
              onClick={() => setDeleteOpen(false)}
            >
              Cancel
            </Button>
            <Button
              variant="destructive-solid"
              onClick={() => void handleDeleteConfirm()}
            >
              {managedFileId ? "Delete all versions" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
