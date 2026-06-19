import { useState, useEffect, useCallback, useRef } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Loader2, Pencil, Check, X, ChevronLeft, ArrowDownToLine, ArrowLeft, ArrowRight, Upload } from "lucide-react"
import { toast } from "sonner"
import {
  getNote,
  getNotes,
  updateNote,
  distillNote,
  triggerPropagation,
  uploadNoteImage,
  type NoteDetail,
  type NoteListItem,
} from "@/api/client"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { NoteSidebarLeft } from "./note-sidebar-left"
import { NoteSidebarRight } from "./note-sidebar-right"

interface NoteEditorDialogProps {
  collection: string
  noteId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

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
  // Snapshot of content right before distill API call — immune to Tiptap round-trip
  const preDistillContentRef = useRef<string | null>(null)

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

  // Track if user has actually made changes (not just loading)
  const userChangedRef = useRef(false)

  // Sync activeNoteId when dialog opens
  useEffect(() => {
    if (open) {
      setActiveNoteId(noteId)
      setNavHistory([])
      setNavIndex(-1)
      setUserEdited(false)
      setPropagateDismissed(false)
      userChangedRef.current = false
    }
  }, [open, noteId])

  // Fetch notes list — only on open
  useEffect(() => {
    if (!open) return
    getNotes(collection)
      .then(res => setNotesList(res.notes ?? []))
      .catch(() => setNotesList([]))
  }, [open, collection])

  // Fetch note content
  const fetchNote = useCallback(async (id: string) => {
    setLoading(true)
    userChangedRef.current = false // Reset change tracking
    try {
      const note = await getNote(collection, id)

      // Sync distill block titles with current note titles
      let content = note.content || ""
      if (notesList.length > 0) {
        const titleMap = new Map(notesList.map(n => [n.id, n.title]))
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

      // Mark as ready for user changes after a short delay (to ignore initial onChange)
      setTimeout(() => {
        userChangedRef.current = true
      }, 500)
    } catch {
      toast.error("Failed to load note")
      setCurrentNote(null)
    } finally {
      setLoading(false)
    }
  }, [collection, notesList])

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
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    const id = activeNoteId
    saveTimerRef.current = setTimeout(async () => {
      try {
        // Encode image URLs before saving
        const contentToSave = encodeImageUrls(newContent)
        await updateNote(collection, id, { content: contentToSave })
        setSavedContent(contentToSave)
      } catch {
        toast.error("Auto-save failed")
      }
    }, 800)
  }, [collection, activeNoteId])

  const handleContentChange = useCallback((newContent: string) => {
    setContent(newContent)

    // Only show propagate button if user actually made changes (not from loading)
    if (userChangedRef.current) {
      setUserEdited(true)
      // Reset dismiss state when user makes new changes
      setPropagateDismissed(false)
    }

    scheduleSave(newContent)
  }, [scheduleSave])

  // ── Switch / navigate ─────────────────────────────────

  const flushSave = useCallback(() => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
    }
    if (content !== savedContent) {
      updateNote(collection, activeNoteId, { content }).catch(() => {})
    }
  }, [collection, activeNoteId, content, savedContent])

  const handleSwitchNote = useCallback((id: string) => {
    flushSave()
    navigateToNote(id)
  }, [flushSave, navigateToNote])

  // ── Title editing ─────────────────────────────────────

  const handleTitleSave = async () => {
    if (!titleDraft.trim() || !currentNote) return
    try {
      await updateNote(collection, currentNote.id, { title: titleDraft.trim() })
      setCurrentNote({ ...currentNote, title: titleDraft.trim() })
      // Update in local list (no re-fetch)
      setNotesList(prev => prev.map(n => n.id === currentNote.id ? { ...n, title: titleDraft.trim() } : n))
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

  // ── Distillation (drop) ───────────────────────────────

  const handleDrop = useCallback(async (sourceNoteId: string) => {
    if (!currentNote || sourceNoteId === currentNote.id) return
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
    }

    // Get source note title from notesList
    const sourceNote = notesList.find(n => n.id === sourceNoteId)
    const sourceTitle = sourceNote?.title || sourceNoteId

    // Generate temporary block ID
    const tempBlockId = `distill-loading-${Date.now()}`

    // Insert loading placeholder immediately
    const loadingMd = `\n\n:::distill-block{"id":"${tempBlockId}","source":"${sourceNoteId}","source-title":"${sourceTitle}","loading":true}\n⏳ Distilling content from "${sourceTitle}"...\n:::\n\n`
    const loadingContent = contentRef.current + loadingMd
    setContent(loadingContent)
    // Snapshot the exact content with the loading block — Tiptap round-trip
    // drops the "loading" attr from serialized markdown, so contentRef.current
    // may no longer match the original placeholder pattern after onUpdate fires.
    preDistillContentRef.current = loadingContent

    setDistilling(true)
    try {
      const res = await distillNote(collection, currentNote.id, sourceNoteId)

      // Replace loading placeholder with real content
      const attrs = JSON.stringify({
        id: res.block_id,
        source: res.source_note_id,
        "source-title": res.source_title,
      })
      const blockMd = `:::distill-block${attrs}\n${res.distilled_content}\n:::`

      // Use the snapshot (not contentRef) — it still has "loading":true so the regex matches
      const loadingPattern = `:::distill-block\\{"id":"${tempBlockId}"[^}]*\\}\\n[\\s\\S]*?\\n:::`
      const newContent = (preDistillContentRef.current ?? contentRef.current).replace(
        new RegExp(loadingPattern),
        blockMd
      )
      preDistillContentRef.current = null

      setContent(newContent)
      setSavedContent(newContent)
      await updateNote(collection, currentNote.id, { content: newContent })
      await fetchNote(currentNote.id)
      toast.success(`Distilled "${res.source_title}" injected`)
    } catch (err) {
      // Remove loading placeholder on error
      const src = preDistillContentRef.current ?? contentRef.current
      preDistillContentRef.current = null
      const errorContent = src.replace(
        new RegExp(`:::distill-block\\{"id":"${tempBlockId}"[^}]*\\}\\n[\\s\\S]*?\\n:::`),
        ""
      )
      setContent(errorContent)
      toast.error(err instanceof Error ? err.message : "Distillation failed")
    } finally {
      setDistilling(false)
    }
  }, [collection, currentNote, fetchNote, notesList])

  // ── Distill block event listeners ─────────────────────

  useEffect(() => {
    const el = editorContainerRef.current
    if (!el) return
    const handleNavigate = (e: Event) => {
      const noteId = (e as CustomEvent).detail?.noteId
      if (noteId) { flushSave(); navigateToNote(noteId) }
    }
    const handleRemove = () => {
      setTimeout(() => fetchNote(activeNoteId), 200)
    }
    el.addEventListener("distill:navigate", handleNavigate)
    el.addEventListener("distill:block-remove", handleRemove)
    return () => {
      el.removeEventListener("distill:navigate", handleNavigate)
      el.removeEventListener("distill:block-remove", handleRemove)
    }
  }, [activeNoteId, flushSave, navigateToNote, fetchNote])

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
    if (droppedNoteId) handleDrop(droppedNoteId)
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

      // Set all distill blocks to loading state in the UI
      const loadingContent = content.replace(
        /:::distill-block(\{[^}]+\})/g,
        (match: string, jsonAttrs: string) => {
          try {
            const attrs = JSON.parse(jsonAttrs)
            attrs.loading = true
            return `:::distill-block${JSON.stringify(attrs)}`
          } catch {
            return match
          }
        }
      )
      setContent(loadingContent)

      // Trigger propagation (runs in background)
      await triggerPropagation(collection, currentNote.id)
      toast.success("Propagation started - distill blocks are updating...")

      // Wait a bit then refresh to get updated content
      setTimeout(async () => {
        await fetchNote(currentNote.id)
        setPropagateDismissed(true)
        setPropagateDialogOpen(false)
        setPropagatePreview(null)
        setUserEdited(false)
        setPropagating(false)
      }, 3000) // Refresh after 3 seconds
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
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton
        className="!max-w-[90vw] !w-[90vw] h-[85vh] p-0 flex flex-col"
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
              <div className="flex-1 overflow-y-auto">
                <MarkdownEditor
                  value={content}
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
