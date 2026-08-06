import { ChevronRight, Loader2 } from "lucide-react"
import {
  useState,
  useEffect,
  useCallback,
  useRef,
  useImperativeHandle,
  forwardRef,
} from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { toast } from "sonner"
import {
  getNotes,
  createNote,
  updateNote,
  deleteNote,
  type NoteListItem,
} from "@/api/client"
import { useAppStore } from "@/stores/app-store"
import { onInfoRefresh, triggerInfoRefresh } from "@/lib/info-refresh"
import { NoteEditorDialog } from "./note-editor-dialog"
import { cn } from "@/lib/utils"

interface NotesCardProps {
  collection: string
  /**
   * default = standalone block with header + Import/New
   * rail = body only (Import/New live on Overview card header)
   */
  variant?: "default" | "rail"
  /** Hide internal toolbar (rail parent owns Import/New). */
  hideToolbar?: boolean
}

export type NotesCardHandle = {
  create: () => void
  openImport: () => void
}

export const NotesCard = forwardRef<NotesCardHandle, NotesCardProps>(
  function NotesCard(
    { collection, variant = "default", hideToolbar = false },
    ref
  ) {
    const [notes, setNotes] = useState<NoteListItem[]>([])
    const [loading, setLoading] = useState(false)
    /** Note id kept mounted through close animation */
    const [activeNoteId, setActiveNoteId] = useState<string | null>(null)
    /** Dialog open flag — set false first so Base UI can play exit motion */
    const [editorOpen, setEditorOpen] = useState(false)
    const [deleteTarget, setDeleteTarget] = useState<{ id: string; title: string } | null>(null)
    /** Like To-do "Completed" — extracted notes collapsed by default */
    const [extractedOpen, setExtractedOpen] = useState(false)

    const hasNotesLoadedRef = useRef(false)
    const fileInputRef = useRef<HTMLInputElement>(null)
    const editorCloseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
    /** Match pm-dialog--silk exit (280ms + buffer), same as todo dialogs */
    const EDITOR_CLOSE_MS = 320

    /**
     * Open note workspace with the same enter path as todo silk dialogs:
     * mount (or stay) with open=false, then flip open=true so Base UI applies
     * data-starting-style → fade-in. Mounting with open already true skips enter.
     */
    const openEditor = useCallback(
      (id: string) => {
        if (editorCloseTimerRef.current) {
          clearTimeout(editorCloseTimerRef.current)
          editorCloseTimerRef.current = null
        }
        const needsEnterAnim = !editorOpen
        setActiveNoteId(id)
        if (needsEnterAnim) {
          setEditorOpen(false)
          // Double rAF: commit closed mount, then open for starting-style transition
          requestAnimationFrame(() => {
            requestAnimationFrame(() => {
              setEditorOpen(true)
            })
          })
        } else {
          setEditorOpen(true)
        }
      },
      [editorOpen]
    )

    const fetchNotes = useCallback(async (opts?: { silent?: boolean }) => {
      if (!collection) return
      const silent = !!opts?.silent && hasNotesLoadedRef.current
      if (!silent) setLoading(true)
      try {
        const res = await getNotes(collection)
        setNotes(res.notes ?? [])
        hasNotesLoadedRef.current = true
      } catch {
        setNotes([])
      } finally {
        if (!silent) setLoading(false)
      }
    }, [collection])

    const handleEditorOpenChange = useCallback(
      (v: boolean) => {
        if (v) {
          setEditorOpen(true)
          return
        }
        // 1) open=false → exit animation  2) then unmount
        setEditorOpen(false)
        if (editorCloseTimerRef.current) {
          clearTimeout(editorCloseTimerRef.current)
        }
        editorCloseTimerRef.current = setTimeout(() => {
          setActiveNoteId(null)
          editorCloseTimerRef.current = null
          void fetchNotes()
        }, EDITOR_CLOSE_MS)
      },
      [fetchNotes]
    )

    useEffect(() => {
      return () => {
        if (editorCloseTimerRef.current) {
          clearTimeout(editorCloseTimerRef.current)
        }
      }
    }, [])

    useEffect(() => {
      hasNotesLoadedRef.current = false
      void fetchNotes()
    }, [fetchNotes])

    useEffect(() => {
      return onInfoRefresh((detail) => {
        if (detail.collectionId && detail.collectionId !== collection) return
        void fetchNotes({ silent: true })
      })
    }, [collection, fetchNotes])

    const { pendingOpenNote, setPendingOpenNote } = useAppStore()
    useEffect(() => {
      if (pendingOpenNote) {
        openEditor(pendingOpenNote)
        setPendingOpenNote(null)
      }
    }, [pendingOpenNote, setPendingOpenNote, openEditor])

    const handleCreate = useCallback(async () => {
      const title = new Date().toLocaleString(undefined, {
        year: "numeric", month: "short", day: "numeric",
        hour: "2-digit", minute: "2-digit",
      })
      try {
        const res = await createNote(collection, title)
        toast.success("Note created")
        await fetchNotes()
        triggerInfoRefresh({ collectionId: collection, reason: "manual" })
        openEditor(res.id)
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Failed to create note")
      }
    }, [collection, fetchNotes, openEditor])

    const openImport = useCallback(() => {
      fileInputRef.current?.click()
    }, [])

    useImperativeHandle(
      ref,
      () => ({
        create: () => {
          void handleCreate()
        },
        openImport,
      }),
      [handleCreate, openImport]
    )

    const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (!file) return
      e.target.value = ""

      try {
        const text = await file.text()
        const title = file.name.replace(/\.(md|txt|markdown)$/i, "") || file.name
        const res = await createNote(collection, title)
        await updateNote(collection, res.id, { content: text })
        toast.success(`Imported "${title}"`)
        await fetchNotes()
        triggerInfoRefresh({ collectionId: collection, reason: "manual" })
        openEditor(res.id)
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Import failed")
      }
    }

    const handleDeleteClick = (e: React.MouseEvent, noteId: string, noteTitle: string) => {
      e.stopPropagation()
      setDeleteTarget({ id: noteId, title: noteTitle })
    }

    const handleDeleteConfirm = async () => {
      if (!deleteTarget) return
      try {
        await deleteNote(collection, deleteTarget.id)
        toast.success(`Deleted "${deleteTarget.title}"`)
        if (activeNoteId === deleteTarget.id) {
          if (editorCloseTimerRef.current) {
            clearTimeout(editorCloseTimerRef.current)
            editorCloseTimerRef.current = null
          }
          setEditorOpen(false)
          setActiveNoteId(null)
        }
        setDeleteTarget(null)
        await fetchNotes()
        triggerInfoRefresh({ collectionId: collection, reason: "manual" })
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Failed to delete note")
      }
    }

    const formatDate = (dateStr: string) => {
      try {
        const date = new Date(dateStr)
        const now = new Date()
        const diffMs = now.getTime() - date.getTime()
        const diffMins = Math.floor(diffMs / 60000)
        const diffHours = Math.floor(diffMs / 3600000)
        const diffDays = Math.floor(diffMs / 86400000)

        if (diffMins < 1) return "just now"
        if (diffMins < 60) return `${diffMins}m ago`
        if (diffHours < 24) return `${diffHours}h ago`
        if (diffDays < 7) return `${diffDays}d ago`
        return date.toLocaleDateString(undefined, { month: "short", day: "numeric" })
      } catch {
        return dateStr
      }
    }

    const unextracted = notes.filter((n) => !n.is_extracted)
    const extracted = notes.filter((n) => n.is_extracted)

    const renderNoteRow = (note: NoteListItem) => (
      <div
        key={note.id}
        className="w-full text-left flex items-center justify-between py-2 border-b border-dashed border-border/40 cursor-pointer transition-colors text-foreground group last:border-0"
        style={{
          background: "none",
          borderLeft: "none",
          borderRight: "none",
          borderTop: "none",
        }}
        onClick={() => openEditor(note.id)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter") openEditor(note.id)
        }}
      >
        <div className="flex flex-col gap-0.5 flex-1 min-w-0">
          <span className="text-xs truncate text-[var(--pm-text,var(--foreground))]">
            {note.title}
          </span>
          <span className="pm-meta truncate">
            {[
              note.is_ingested ? "Ingested" : note.is_extracted ? "Extracted" : "Draft",
              formatDate(note.updated_at),
            ].join(" · ")}
          </span>
        </div>
        <button
          type="button"
          className="pm-meta opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer shrink-0 ml-2 border-none bg-transparent hover:text-[var(--pm-danger,#b42318)]"
          onClick={(e) => handleDeleteClick(e, note.id, note.title)}
        >
          Delete
        </button>
      </div>
    )

    const isRail = variant === "rail"
    const showToolbar = !hideToolbar && !isRail

    return (
      <>
        <div className={cn(isRail && "min-h-0 flex flex-col h-full")}>
          {showToolbar && (
            <div className="flex items-center gap-1.5 mb-2.5 justify-between">
              <div className="pm-label">Notes · {notes.length}</div>
              <div className="flex items-center gap-1.5 ml-auto">
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={openImport}
                >
                  Import
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={() => void handleCreate()}
                >
                  New
                </Button>
              </div>
            </div>
          )}

          {loading ? (
            <div className="flex items-center justify-center py-6 gap-2">
              <Loader2 className="h-4 w-4 animate-spin text-[var(--pm-faint)]" />
              <span className="pm-meta">Loading…</span>
            </div>
          ) : notes.length === 0 ? (
            <p className="pm-meta">No notes yet. Create one to get started.</p>
          ) : (
            <div className={cn(isRail && "flex-1 min-h-0 overflow-auto")}>
              {/* Active / unextracted notes */}
              {unextracted.length === 0 ? (
                <p className="pm-meta py-1">
                  {extracted.length > 0
                    ? "All notes are extracted."
                    : "No notes yet."}
                </p>
              ) : (
                <div>{unextracted.map(renderNoteRow)}</div>
              )}

              {/* Extracted — same silk collapse as To-do Completed */}
              {extracted.length > 0 && (
                <div className="mt-3 pt-2 border-t border-border/40">
                  <button
                    type="button"
                    className="pm-subcollapse-trigger"
                    aria-expanded={extractedOpen}
                    onClick={() => setExtractedOpen((o) => !o)}
                  >
                    <span
                      className={cn(
                        "pm-rail-chev",
                        extractedOpen && "is-open"
                      )}
                      aria-hidden
                    >
                      <ChevronRight className="size-3.5" strokeWidth={2} />
                    </span>
                    Extracted · {extracted.length}
                  </button>
                  <div
                    className={cn(
                      "pm-subcollapse",
                      extractedOpen && "is-open"
                    )}
                  >
                    <div className="pm-subcollapse-panel">
                      <div className="pm-subcollapse-inner mt-1 opacity-90">
                        {extracted.map(renderNoteRow)}
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {activeNoteId ? (
          <NoteEditorDialog
            collection={collection}
            noteId={activeNoteId}
            open={editorOpen}
            onOpenChange={handleEditorOpenChange}
          />
        ) : null}

        <input
          ref={fileInputRef}
          type="file"
          accept=".md,.txt,.markdown"
          className="hidden"
          onChange={handleImportFile}
        />

        <Dialog open={!!deleteTarget} onOpenChange={(v) => { if (!v) setDeleteTarget(null) }}>
          <DialogContent className="pm-dialog max-w-sm !gap-3">
            <DialogHeader className="!gap-1">
              <DialogTitle>Delete Note</DialogTitle>
            </DialogHeader>
            <p className="pm-dialog-body">
              Are you sure you want to delete{" "}
              <span className="font-medium text-[var(--pm-ink)]">
                &ldquo;{deleteTarget?.title}&rdquo;
              </span>
              ? This cannot be undone.
            </p>
            <DialogFooter className="gap-2">
              <Button
                variant="ghost"
                onClick={() => setDeleteTarget(null)}
              >
                Cancel
              </Button>
              <Button
                variant="destructive-solid"
                onClick={handleDeleteConfirm}
              >
                Delete
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </>
    )
  }
)
