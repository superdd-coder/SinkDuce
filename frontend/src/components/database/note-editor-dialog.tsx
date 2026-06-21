import { useState, useEffect, useCallback, useRef } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Loader2, Pencil, Check, X, ChevronLeft, ArrowDownToLine, ArrowLeft, ArrowRight, Upload, FileUp } from "lucide-react"
import { toast } from "sonner"
import {
  getNote,
  getNotes,
  createNote,
  updateNote,
  distillNote,
  triggerPropagation,
  uploadNoteImage,
  type NoteDetail,
  type NoteListItem,
} from "@/api/client"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { preprocessDistillBlocks, EditorToolbar } from "@/components/ui/tiptap-editor"
import { NoteSidebarLeft } from "./note-sidebar-left"
import { NoteSidebarRight } from "./note-sidebar-right"

interface NoteEditorDialogProps {
  collection: string
  noteId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

// Module-level — survives component unmount (dialog close/reopen)
const _perNoteState = new Map<string, { userEdited: boolean; propagateDismissed: boolean }>()

export function NoteEditorDialog({ collection, noteId, open, onOpenChange }: NoteEditorDialogProps) {
  // Notes list — only fetched on open, never reordered during session
  const [notesList, setNotesList] = useState<NoteListItem[]>([])

  // Current note state
  const [currentNote, setCurrentNote] = useState<NoteDetail | null>(null)
  const [activeNoteId, setActiveNoteId] = useState(noteId)
  const [loading, setLoading] = useState(true)
  const [content, setContent] = useState("")
  const [savedContent, setSavedContent] = useState("")

  // Title editing
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState("")

  // Distillation
  const [distilling, setDistilling] = useState(false)
  const [activeBlockId, setActiveBlockId] = useState<string | null>(null)

  // Propagation — button-based, not auto-popup
  const [propagating, setPropagating] = useState(false)
  const [propagateDismissed, setPropagateDismissed] = useState(false) // User clicked "Ignore this time"
  const [propagateDialogOpen, setPropagateDialogOpen] = useState(false)
  const [propagatePreview, setPropagatePreview] = useState<{ links: { source_title: string; target_title: string }[]; total_affected: number } | null>(null)
  const [userEdited, setUserEdited] = useState(false) // Track if user actually edited content

  // UI
  const [leftSidebarCollapsed, setLeftSidebarCollapsed] = useState(false)
  const [dropOver, setDropOver] = useState(false)
  const editorContainerRef = useRef<HTMLDivElement>(null)

  // Navigation history
  const [navHistory, setNavHistory] = useState<string[]>([])
  const [navIndex, setNavIndex] = useState(-1)

  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const contentRef = useRef(content)
  contentRef.current = content
  const latestContentRef = useRef(content)
  // Ref for editor scroll container — reset scroll on note switch
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  // Ref mirror of activeNoteId — always current, immune to async closure staleness
  const activeNoteIdRef = useRef(activeNoteId)
  // Concurrent distill counter (boolean state flickers when one finishes before others)
  const distillCountRef = useRef(0)
  // Serializes server-side content updates for the same note (read-modify-write lock)
  const serverWriteLockRef = useRef<Map<string, Promise<void>>>(new Map())

  // Ref to Tiptap editor instance -- needed for cursor-position calculations during drop
  const tiptapEditorRef = useRef<any>(null)
  const [editorInstance, setEditorInstance] = useState<any>(null)


  // ── Helpers ────────────────────────────────────────────

  const getDistillBlockIds = (md: string): Set<string> => {
    const ids = new Set<string>()
    const re = /:::distill-block\{[^}]*"id"\s*:\s*"([^"]*)"[^}]*\}/g
    let m: RegExpExecArray | null
    while ((m = re.exec(md)) !== null) ids.add(m[1])
    return ids
  }
  const prevBlockIdsRef = useRef<Set<string>>(new Set())

  // ── Navigation helpers ────────────────────────────────

  const navigateToNote = useCallback((id: string) => {
    if (id === activeNoteId) return
    // Push to history
    setNavHistory(prev => {
      const trimmed = prev.slice(0, navIndex + 1)
      return [...trimmed, activeNoteId]
    })
    setNavIndex(prev => prev + 1)
    setActiveNoteId(id)
  }, [activeNoteId, navIndex])

  const goBack = useCallback(() => {
    if (navIndex < 0) return
    const targetId = navHistory[navIndex]
    setNavIndex(prev => prev - 1)
    setActiveNoteId(targetId)
  }, [navHistory, navIndex])

  const goForward = useCallback(() => {
    if (navIndex >= navHistory.length - 1) return
    const targetId = navHistory[navIndex + 2]
    if (targetId) {
      setNavIndex(prev => prev + 1)
      setActiveNoteId(targetId)
    }
  }, [navHistory, navIndex])

  const canGoBack = navIndex >= 0
  const canGoForward = navIndex < navHistory.length - 1

  // ── Data loading ──────────────────────────────────────

  // Baseline content after Tiptap normalization — used to detect real user edits
  // (normalize is deterministic, so same input → same output → no false triggers)
  const baselineContentRef = useRef("")

  // Per-note edit state — uses module-level map to survive dialog close/reopen

  // Ref for notesList title map — avoids fetchNote depending on notesList state
  // (which would cause fetchNote to be recreated on every notesList update,
  // triggering useEffect → setContent → Tiptap re-render → destroy() loop)
  const notesTitleMapRef = useRef<Map<string, string>>(new Map())

  // Sync activeNoteId when dialog opens
  useEffect(() => {
    if (open) {
      setActiveNoteId(noteId)
      activeNoteIdRef.current = noteId
      setNavHistory([])
      setNavIndex(-1)
      setUserEdited(false)
      setPropagateDismissed(false)
      baselineContentRef.current = ""
    } else {
      setEditorInstance(null)
    }
  }, [open, noteId])

  // Keep ref in sync with state (async closures capture stale state values)
  useEffect(() => { activeNoteIdRef.current = activeNoteId }, [activeNoteId])

  // Fetch notes list — only on open
  useEffect(() => {
    if (!open) return
    getNotes(collection)
      .then(res => {
        const notes = res.notes ?? []
        setNotesList(notes)
        notesTitleMapRef.current = new Map(notes.map(n => [n.id, n.title]))
      })
      .catch(() => setNotesList([]))
  }, [open, collection])

  // Fetch note content
  const fetchNote = useCallback(async (id: string) => {
    setLoading(true)
    baselineContentRef.current = "" // Block user-edit detection during load
    // Bug 2 fix: reset title editing state on note switch
    setEditingTitle(false)
    try {
      const note = await getNote(collection, id)

      // Sync distill block titles with current note titles
      let content = note.content || ""
      const titleMap = notesTitleMapRef.current
      if (titleMap.size > 0) {
        content = content.replace(
          /:::distill-block(\{[^}]+\})/g,
          (match: string, jsonAttrs: string) => {
            try {
              const attrs = JSON.parse(jsonAttrs)
              const currentTitle = titleMap.get(attrs.source)
              if (currentTitle && currentTitle !== attrs["source-title"]) {
                attrs["source-title"] = currentTitle
                return `:::distill-block${JSON.stringify(attrs)}`
              }
            } catch { /* ignore parse errors */ }
            return match
          }
        )
      }

      setCurrentNote(note)
      setContent(content)
      setSavedContent(content)
      // Bug 4 fix: reset scroll position to top on note switch
      scrollContainerRef.current?.scrollTo(0, 0)
      // Initialize prevBlockIdsRef for Backspace/Delete removal detection
      prevBlockIdsRef.current = getDistillBlockIds(content)
      // baselineContentRef stays "" here — it will be set on the first
      // onChange from Tiptap (which reflects the actual normalized content).
      // This avoids false user-edit detection from normalize differences.

      // Restore per-note edit state (propagate button visibility)
      const saved = _perNoteState.get(id)
      setUserEdited(saved?.userEdited ?? false)
      setPropagateDismissed(saved?.propagateDismissed ?? false)
    } catch {
      toast.error("Failed to load note")
      setCurrentNote(null)
    } finally {
      setLoading(false)
    }
  }, [collection])

  useEffect(() => {
    if (open) fetchNote(activeNoteId)
  }, [open, activeNoteId, fetchNote])

  // ── Auto-save ─────────────────────────────────────────

  // Encode image URLs in markdown to handle spaces
  const encodeImageUrls = (content: string): string => {
    return content.replace(
      /!\[([^\]]*)\]\(([^)]+)\)/g,
      (_match, alt, url) => {
        // Encode URL to handle spaces and special characters
        const encodedUrl = encodeURI(url)
        return `![${alt}](${encodedUrl})`
      }
    )
  }

  const scheduleSave = useCallback((newContent: string) => {
    // Don't save while baseline hasn't been set (still loading/normalizing)
    if (!baselineContentRef.current) return
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    const id = activeNoteId
    saveTimerRef.current = setTimeout(async () => {
      try {
        // Encode image URLs before saving
        const contentToSave = encodeImageUrls(newContent)
        await updateNote(collection, id, { content: contentToSave })
        setSavedContent(contentToSave)
        // Update baseline so the saved (possibly encoded) content is the new reference
        baselineContentRef.current = newContent
      } catch {
        toast.error("Auto-save failed")
      }
    }, 800)
  }, [collection, activeNoteId])

  const handleContentChange = useCallback((newContent: string) => {
    setContent(newContent)
    latestContentRef.current = newContent

    // Set baseline on first onChange after load (captures Tiptap-normalized content)
    if (!baselineContentRef.current) {
      baselineContentRef.current = newContent
    }

    // Only show propagate button if content actually differs from baseline
    // (normalize is deterministic → same input produces same output → no false triggers)
    const isRealEdit = newContent !== baselineContentRef.current && distillCountRef.current === 0
    if (isRealEdit) {
      setUserEdited(true)
      // Reset dismiss state when user makes new changes
      setPropagateDismissed(false)

      // Detect distill block removal (Backspace/Delete key — not via ✕ button)
      const prevIds = prevBlockIdsRef.current
      if (prevIds.size > 0) {
        const currIds = getDistillBlockIds(newContent)
        const removed = [...prevIds].filter(id => !currIds.has(id))
        if (removed.length > 0) {
          // Distill block(s) were removed — flush save and refresh sidebars
          if (saveTimerRef.current) {
            clearTimeout(saveTimerRef.current)
            saveTimerRef.current = null
          }
          // Persist immediately
          updateNote(collection, activeNoteId, { content: newContent })
            .then(() => setSavedContent(newContent))
            .catch(() => {})
            .finally(() => {
              // Refresh note metadata for sidebars
              getNote(collection, activeNoteId)
                .then(note => {
                  setCurrentNote(note)
                  const extractedIds = new Set(note.references?.map(r => r.source_note_id) ?? [])
                  setNotesList(prev => prev.map(n => ({
                    ...n,
                    is_extracted: extractedIds.has(n.id),
                  })))
                })
                .catch(() => {})
            })
        }
      }
      prevBlockIdsRef.current = getDistillBlockIds(newContent)
    }

    scheduleSave(newContent)
  }, [collection, activeNoteId, scheduleSave])

  // ── Switch / navigate ─────────────────────────────────

  const flushSave = useCallback(() => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
    }
    // Only save if user actually edited — prevents Tiptap initialization
    // normalization from bumping updated_at on every note switch.
    if (content !== savedContent && baselineContentRef.current && content !== baselineContentRef.current) {
      updateNote(collection, activeNoteId, { content }).catch(() => {})
    }
  }, [collection, activeNoteId, content, savedContent])

  const handleSwitchNote = useCallback((id: string) => {
    // Save current note's edit state before switching
    _perNoteState.set(activeNoteId, { userEdited, propagateDismissed })
    flushSave()
    navigateToNote(id)
  }, [flushSave, navigateToNote, activeNoteId, userEdited, propagateDismissed])

  // ── Create new note ─────────────────────────────────

  const handleCreateNote = useCallback(async () => {
    const title = new Date().toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    })
    try {
      flushSave()
      const res = await createNote(collection, title)
      // Refresh notes list
      const notesRes = await getNotes(collection)
      const notes = notesRes.notes ?? []
      setNotesList(notes)
      notesTitleMapRef.current = new Map(notes.map(n => [n.id, n.title]))
      navigateToNote(res.id)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create note")
    }
  }, [collection, flushSave, navigateToNote])

  // ── Title editing ─────────────────────────────────────

  const handleTitleSave = async () => {
    if (!titleDraft.trim() || !currentNote) return
    try {
      await updateNote(collection, currentNote.id, { title: titleDraft.trim() })
      setCurrentNote({ ...currentNote, title: titleDraft.trim() })
      // Update in local list (no re-fetch)
      setNotesList(prev => prev.map(n => n.id === currentNote.id ? { ...n, title: titleDraft.trim() } : n))
      // Bug 1 fix: keep title map in sync so fetchNote can update distill block titles
      notesTitleMapRef.current.set(currentNote.id, titleDraft.trim())
    } catch {
      toast.error("Failed to update title")
    }
    setEditingTitle(false)
  }

  // ── Image upload ──────────────────────────────────────

  const handleImageUpload = useCallback(async (file: File): Promise<string> => {
    const result = await uploadNoteImage(collection, activeNoteId, file)
    return result.url
  }, [collection, activeNoteId])

  // ── Download note as .md file ─────────────────────────

  const handleDownload = useCallback(() => {
    if (!currentNote) return
    const blob = new Blob([content], { type: "text/markdown;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `${currentNote.title}.md`
    a.click()
    URL.revokeObjectURL(url)
    toast.success("Exported")
  }, [currentNote, content])

  // ── Distillation (drop) ───────────────────────────────

  // ── Server-side content update with per-note lock ────
  // Serializes read-modify-write for concurrent distill completions targeting
  // the same note while the user has switched away. Without this lock, two
  // distills that both read→replace→write would overwrite each other.
  const lockedServerReplace = useCallback(async (
    noteId: string,
    pattern: RegExp,
    replacement: string,
  ) => {
    const prev = serverWriteLockRef.current.get(noteId) || Promise.resolve()
    const task = prev.catch(() => {}).then(async () => {
      const { content: server } = await getNote(collection, noteId)
      const patched = (server || "").replace(pattern, replacement)
      if (patched !== server) {
        await updateNote(collection, noteId, { content: patched })
      }
    })
    serverWriteLockRef.current.set(noteId, task)
    await task
  }, [collection])


  /**
   * Convert a drop pixel coordinate to a ProseMirror document position.
   * Uses the editor's posAtCoords directly for accurate placement,
   * then snaps to the nearest block boundary so insertion never lands mid-paragraph.
   */
  const dropCoordsToProseMirrorPos = useCallback((x: number, y: number): number | null => {
    const editor = tiptapEditorRef.current
    if (!editor?.view) return null
    const view = editor.view
    const pos = view.posAtCoords({ left: x, top: y })
    if (!pos) return null
    // Use posAtCoords raw pos directly. Falls between paragraphs when mouse is in the gap,
    return pos.pos
  }, [])
  const handleDrop = useCallback(async (
    sourceNoteId: string,
    dropCoords?: { x: number; y: number },
  ) => {
    if (!currentNote || sourceNoteId === currentNote.id) return
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
    }

    // Snapshot everything that might change after await — closures are stale.
    const targetNoteId = currentNote.id
    const targetContent = contentRef.current
    const sourceNote = notesList.find(n => n.id === sourceNoteId)
    const sourceTitle = sourceNote?.title || sourceNoteId
    const tempBlockId = `distill-loading-${Date.now()}`

    // Build and insert loading placeholder
    const loadingMd = `\n\n:::distill-block{"id":"${tempBlockId}","source":"${sourceNoteId}","source-title":"${sourceTitle}","loading":true}\n⏳ Distilling content from "${sourceTitle}"...\n:::\n\n`
    let loadingContent: string
    if (dropCoords) {
      // Use ProseMirror's posAtCoords + insertContentAt for accurate block-level
      // insertion. ProseMirror's posAtCoords already handles positioning correctly
      // (never lands mid-paragraph), matching how internal NodeView drag-and-drop works.
      const pmPos = dropCoordsToProseMirrorPos(dropCoords.x, dropCoords.y)
      if (pmPos !== null && pmPos >= 0) {
        const editor = tiptapEditorRef.current
        if (editor) {
          // Insert at the correct doc position using preprocessed markdown.
          // Must preprocess distill blocks to HTML divs so the DistillBlock
          // extension can parse them — raw :::distill-block syntax is not
          // recognized by ProseMirror/insertContentAt.
          loadingContent = targetContent + loadingMd // fallback
          const { processed } = preprocessDistillBlocks(loadingMd)
          editor.commands.insertContentAt(pmPos, processed)
          // Read back the full markdown after insertion
          const storage = editor.storage as any
          loadingContent = storage?.markdown?.getMarkdown?.() ?? targetContent + loadingMd
          // Sync ref (React state is already updated by onUpdate → handleContentChange)
          contentRef.current = loadingContent
        } else {
          loadingContent = targetContent + loadingMd
        }
      } else {
        loadingContent = targetContent + loadingMd
      }
    } else {
      // Fallback: append to end
      loadingContent = targetContent + loadingMd
      // Update ref synchronously — concurrent distills read contentRef.current,
      // and React won't re-render until the microtask queue drains.
      contentRef.current = loadingContent
      setContent(loadingContent)
    }

    // Persist loading placeholder to server NOW — the switch-away path
    // reads from server, so the block must be there before distill returns.
    // Also prevents the auto-save from overwriting the result later.
    if (saveTimerRef.current) { clearTimeout(saveTimerRef.current); saveTimerRef.current = null }
    await updateNote(collection, targetNoteId, { content: loadingContent })

    distillCountRef.current++
    setDistilling(true)
    try {
      const res = await distillNote(collection, targetNoteId, sourceNoteId)

      const attrs = JSON.stringify({
        id: res.block_id,
        source: res.source_note_id,
        "source-title": res.source_title,
      })
      const blockMd = `:::distill-block${attrs}\n${res.distilled_content}\n:::`
      const loadingRe = new RegExp(
        `:::distill-block\\{"id":"${tempBlockId}"[^}]*\\}\\n[\\s\\S]*?\\n:::`,
      )

      if (activeNoteIdRef.current === targetNoteId) {
        // User still on target — replace in local editor state.
        // contentRef.current is up-to-date (updated synchronously by any
        // earlier concurrent distill that already completed).
        const cur = contentRef.current.replace(loadingRe, blockMd)
        contentRef.current = cur
        setContent(cur)
        setSavedContent(cur)
        await updateNote(collection, targetNoteId, { content: cur })
        toast.success(`Distilled "${res.source_title}" injected`)
      } else {
        // User switched away — loading placeholder was persisted to server
        // by fetchNote (runs on every note switch). Replace it there.
        // Lock prevents two concurrent distills from read-modify-write races.
        await lockedServerReplace(targetNoteId, loadingRe, blockMd)
      }
    } catch (err) {
      const loadingRe = new RegExp(
        `:::distill-block\\{"id":"${tempBlockId}"[^}]*\\}\\n[\\s\\S]*?\\n:::`,
      )
      if (activeNoteIdRef.current === targetNoteId) {
        const cur = contentRef.current.replace(loadingRe, "")
        contentRef.current = cur
        setContent(cur)
      } else {
        lockedServerReplace(targetNoteId, loadingRe, "").catch(() => {})
      }
      toast.error(err instanceof Error ? err.message : "Distillation failed")
    } finally {
      distillCountRef.current--
      setDistilling(distillCountRef.current > 0)
      // Reset propagate state — distill insertion is not a user edit
      if (distillCountRef.current === 0) {
        setUserEdited(false)
        setPropagateDismissed(false)
      }
    }
  }, [collection, currentNote, notesList, lockedServerReplace, dropCoordsToProseMirrorPos])

  // ── Distill block event listeners ─────────────────────

  useEffect(() => {
    const el = editorContainerRef.current
    if (!el) return
    const handleNavigate = (e: Event) => {
      const noteId = (e as CustomEvent).detail?.noteId
      if (noteId) { flushSave(); navigateToNote(noteId) }
    }
    const handleRemove = async (_e: Event) => {
      // deleteRange has already completed at this point; onChange → handleContentChange
      // has already called setContent + scheduleSave, and latestContentRef is up-to-date.
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current)
        saveTimerRef.current = null
      }
      // Save immediately with the current content (already without the deleted block)
      const currentContent = latestContentRef.current
      try {
        await updateNote(collection, activeNoteId, { content: currentContent })
        setSavedContent(currentContent)
      } catch {
        // Non-critical — the scheduled save will retry
      }
      // Re-fetch note metadata for right-sidebar — use getNote and only update
      // currentNote (references/extracted_into). Do NOT call fetchNote because
      // it calls setContent, which would reset the Tiptap editor and trigger
      // NodeView destroy() callbacks, causing an infinite loop.
      let note: NoteDetail | null = null
      try {
        note = await getNote(collection, activeNoteId)
        setCurrentNote(note)
      } catch {
        // Non-critical — sidebar will update on next navigation
      }
      // Patch notesList in-place: only update is_extracted for the source note(s)
      // whose distill blocks were just removed. Don't re-fetch the full list
      // (which would change sort order due to bumped updated_at).
      if (note) {
        const currentExtractedIds = new Set(note.references?.map(r => r.source_note_id) ?? [])
        setNotesList(prev =>
          prev.map(n => ({
            ...n,
            // is_extracted should only be true if this note still appears as a
            // source in the current note's references
            is_extracted: currentExtractedIds.has(n.id),
          }))
        )
      }
    }
    el.addEventListener("distill:navigate", handleNavigate)
    el.addEventListener("distill:block-remove", handleRemove)
    return () => {
      el.removeEventListener("distill:navigate", handleNavigate)
      el.removeEventListener("distill:block-remove", handleRemove)
    }
  }, [activeNoteId, collection, flushSave, navigateToNote, fetchNote])

  // ── Editor drop zone ──────────────────────────────────

  const handleEditorDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = "copy"
    setDropOver(true)
  }, [])

  const handleEditorDragLeave = useCallback((e: React.DragEvent) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const { clientX: x, clientY: y } = e
    if (x <= rect.left || x >= rect.right || y <= rect.top || y >= rect.bottom) {
      setDropOver(false)
    }
  }, [])

  const handleEditorDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDropOver(false)
    const droppedNoteId = e.dataTransfer.getData("application/note-id")
    if (droppedNoteId) handleDrop(droppedNoteId, { x: e.clientX, y: e.clientY })
  }, [handleDrop])

  // ── Propagation ───────────────────────────────────────

  const showPropagateButton = currentNote?.is_extracted && userEdited && !propagateDismissed

  const handlePropagateClick = async () => {
    if (!currentNote) return
    // Fetch propagation preview first
    try {
      const { getPropagationPreview } = await import("@/api/client")
      const preview = await getPropagationPreview(collection, currentNote.id)
      if (preview.total_affected === 0) {
        toast.info("No notes need updating")
        setPropagateDismissed(true)
        return
      }
      setPropagatePreview(preview)
      setPropagateDialogOpen(true)
    } catch {
      toast.error("Failed to check propagation chain")
    }
  }

  const handlePropagateConfirm = async () => {
    if (!currentNote) return
    setPropagating(true)
    try {
      // Save current content first
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current)
        saveTimerRef.current = null
      }
      await updateNote(collection, currentNote.id, { content })
      setSavedContent(content)

      // Trigger propagation (runs in background)
      // Propagation saves content to server, so save current content first.
      // The backend sets loading=true on downstream notes' distill blocks.
      const propagateNoteId = currentNote.id
      await triggerPropagation(collection, currentNote.id)
      toast.success("Propagation started - distill blocks are updating...")

      // Poll for propagation completion by checking the source note's updated_at.
      // (The backend saves loading onto downstream notes, so checking the source
      // for loading:true won't work.)
      const poll = async () => {
        const initialUpdatedAt = currentNote?.updated_at
        for (let i = 0; i < 30; i++) {
          await new Promise(r => setTimeout(r, 2000))
          try {
            const note = await getNote(collection, propagateNoteId)
            if (note.updated_at !== initialUpdatedAt) {
              // Source note's content changed — propagation completed
              if (activeNoteIdRef.current === propagateNoteId) {
                await fetchNote(propagateNoteId)
              }
              break
            }
          } catch { /* retry */ }
        }
        setPropagateDismissed(true)
        setPropagateDialogOpen(false)
        setPropagatePreview(null)
        setUserEdited(false)
        setPropagating(false)
      }
      poll()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Propagation failed")
      setPropagating(false)
    }
  }

  // ── Select & scroll to distill block ───────────────────

  const handleSelectBlock = useCallback((blockId: string) => {
    setActiveBlockId(blockId)
    // Find the distill-block element by data-block-id attribute in the Tiptap editor DOM
    setTimeout(() => {
      const blockEl = document.querySelector(`.distill-block[data-block-id="${blockId}"]`)
      if (blockEl) {
        blockEl.scrollIntoView({ behavior: 'smooth', block: 'center' })
        // Brief highlight
        const htmlEl = blockEl as HTMLElement
        htmlEl.style.transition = 'outline 0.3s'
        htmlEl.style.outline = '2px solid hsl(var(--primary))'
        htmlEl.style.outlineOffset = '2px'
        setTimeout(() => { htmlEl.style.outline = '' }, 1500)
      }
    }, 100)
  }, [])

  // ── Render ────────────────────────────────────────────

  return (
    <>
    <Dialog
      open={open}
      onOpenChange={(newOpen, eventDetails) => {
        // Block ESC key from closing — only allow close via X button / overlay click
        if (!newOpen && eventDetails.reason === 'escape-key') return
        onOpenChange(newOpen)
      }}
    >
      <DialogContent
        showCloseButton
        className="!max-w-[90vw] !w-[90vw] h-[85vh] p-0 !gap-0 flex flex-col"
      >
        {/* Header */}
        <div className="flex items-center gap-2 px-4 py-3 border-b shrink-0">
          {/* Sidebar toggle */}
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-7 p-0"
            onClick={() => setLeftSidebarCollapsed(!leftSidebarCollapsed)}
          >
            <ChevronLeft className={`h-4 w-4 transition-transform ${leftSidebarCollapsed ? "rotate-180" : ""}`} />
          </Button>

          {/* Nav back/forward */}
          <div className="flex items-center gap-0.5">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0"
              disabled={!canGoBack}
              onClick={goBack}
              title="Back"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0"
              disabled={!canGoForward}
              onClick={goForward}
              title="Forward"
            >
              <ArrowRight className="h-3.5 w-3.5" />
            </Button>
          </div>

          {/* Title */}
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : editingTitle ? (
            <div className="flex items-center gap-1.5 flex-1 min-w-0">
              <Input
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleTitleSave()
                  if (e.key === "Escape") setEditingTitle(false)
                }}
                className="h-7 text-sm font-semibold"
                autoFocus
              />
              <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={handleTitleSave}>
                <Check className="h-3.5 w-3.5" />
              </Button>
              <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => setEditingTitle(false)}>
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
          ) : (
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <span className="font-semibold text-sm truncate">{currentNote?.title}</span>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0 shrink-0"
                onClick={() => {
                  setTitleDraft(currentNote?.title || "")
                  setEditingTitle(true)
                }}
              >
                <Pencil className="h-3 w-3" />
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="h-7 px-2 text-xs gap-1 shrink-0"
                onClick={handleDownload}
                title="Export as .md"
              >
                <FileUp className="h-3.5 w-3.5" />
                Export
              </Button>
            </div>
          )}

          {/* Right side: distilling indicator + propagate button */}
          <div className="flex items-center gap-2 shrink-0 pr-8">
            {distilling && (
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Distilling...
              </div>
            )}
            {showPropagateButton && (
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs border-amber-300 text-amber-700 hover:bg-amber-50 dark:border-amber-700 dark:text-amber-400 dark:hover:bg-amber-950"
                onClick={handlePropagateClick}
                disabled={propagating}
              >
                {propagating ? (
                  <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
                ) : (
                  <Upload className="h-3.5 w-3.5 mr-1" />
                )}
                {propagating ? "Propagating..." : "Propagate Changes"}
              </Button>
            )}
          </div>
        </div>

        {/* Body: 3-panel layout */}
        <div className="flex flex-1 min-h-0">
          {/* Left sidebar */}
          {!leftSidebarCollapsed && (
            <NoteSidebarLeft
              notes={notesList}
              activeNoteId={activeNoteId}
              onSwitchNote={handleSwitchNote}
              onCreateNote={handleCreateNote}
            />
          )}

          {/* Center: Editor — drop target */}
          <div
            ref={editorContainerRef}
            className="flex-1 flex flex-col min-w-0 relative"
            onDragOver={handleEditorDragOver}
            onDragLeave={handleEditorDragLeave}
            onDrop={handleEditorDrop}
          >
            {loading ? (
              <div className="flex-1 flex items-center justify-center text-muted-foreground">
                <Loader2 className="h-6 w-6 animate-spin mr-2" />
                Loading...
              </div>
            ) : (
              <>
                {editorInstance && <EditorToolbar editor={editorInstance} />}
                <div ref={scrollContainerRef} className="flex-1 overflow-y-auto min-h-0">
                  <MarkdownEditor
                    value={content}
                    showToolbar={false}
                    onEditorReady={(editor) => { tiptapEditorRef.current = editor; setEditorInstance(editor) }}
                    onChange={handleContentChange}
                    onImageUpload={handleImageUpload}
                    onNoteLinkClick={(id) => {
                      flushSave()
                      navigateToNote(id)
                    }}
                    className="px-8 py-6"
                    placeholder="Start writing your note..."
                  />
                </div>
              </>
            )}

            {/* Drop overlay */}
            {dropOver && (
              <div className="absolute inset-0 z-50 bg-primary/5 border-2 border-dashed border-primary/40 rounded-md flex flex-col items-center justify-center gap-2 pointer-events-none">
                <ArrowDownToLine className="h-8 w-8 text-primary" />
                <span className="text-sm font-medium text-primary">Drop to distill</span>
              </div>
            )}
          </div>

          {/* Right sidebar */}
          <NoteSidebarRight
            references={currentNote?.references ?? []}
            injectedInto={currentNote?.extracted_into ?? []}
            injectedIntoTitles={new Map(notesList.map(n => [n.id, n.title]))}
            activeBlockId={activeBlockId}
            onSelectBlock={handleSelectBlock}
            onNavigateToNote={(id) => {
              flushSave()
              navigateToNote(id)
            }}
          />
        </div>
      </DialogContent>
    </Dialog>

    {/* Propagation confirmation dialog */}
    <Dialog open={propagateDialogOpen} onOpenChange={setPropagateDialogOpen}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Upload className="h-4 w-4 text-amber-500" />
            Propagate Changes?
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-4 pt-2">
          <p className="text-sm text-muted-foreground">
            Your changes will trigger re-distillation for the following chain:
          </p>
          {propagatePreview && (
            <div className="space-y-2">
              {/* Chain visualization */}
              <div className="bg-muted/50 rounded-lg p-3">
                <div className="flex items-center flex-wrap gap-1.5 text-sm">
                  {(() => {
                    const seen = new Set<string>()
                    const chain: string[] = [currentNote?.title || ""]
                    seen.add(currentNote?.id || "")
                    for (const link of propagatePreview.links) {
                      if (!seen.has(link.target_title)) {
                        seen.add(link.target_title)
                        chain.push(link.target_title)
                      }
                    }
                    return chain.map((title, i) => (
                      <span key={i} className="flex items-center gap-1.5">
                        {i > 0 && <span className="text-muted-foreground">→</span>}
                        <span className={i === 0 ? "font-medium text-primary" : "font-medium"}>
                          {title}
                        </span>
                      </span>
                    ))
                  })()}
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                {propagatePreview.total_affected} note(s) will be updated. Downstream propagations run automatically.
              </p>
            </div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="outline"
              onClick={() => { setPropagateDialogOpen(false); setPropagateDismissed(true) }}
              disabled={propagating}
            >
              Ignore this time
            </Button>
            <Button onClick={handlePropagateConfirm} disabled={propagating}>
              {propagating ? (
                <><Loader2 className="h-4 w-4 animate-spin mr-1" />Propagating...</>
              ) : (
                "Confirm Propagate"
              )}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
    </>
  )
}
