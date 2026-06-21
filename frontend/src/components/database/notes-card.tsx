import { useState, useEffect, useCallback, useRef } from "react"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { toast } from "sonner"
import {
  getNotes,
  createNote,
  updateNote,
  type NoteListItem,
} from "@/api/client"
import { NoteEditorDialog } from "./note-editor-dialog"
import { cn } from "@/lib/utils"

interface NotesCardProps {
  collection: string
}

export function NotesCard({ collection }: NotesCardProps) {
  const [notes, setNotes] = useState<NoteListItem[]>([])
  const [loading, setLoading] = useState(false)
  const [activeNoteId, setActiveNoteId] = useState<string | null>(null)

  const fetchNotes = useCallback(async () => {
    if (!collection) return
    setLoading(true)
    try {
      const res = await getNotes(collection)
      setNotes(res.notes ?? [])
    } catch {
      setNotes([])
    } finally {
      setLoading(false)
    }
  }, [collection])

  useEffect(() => {
    fetchNotes()
  }, [fetchNotes])

  const handleCreate = async () => {
    const title = new Date().toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    })
    try {
      const res = await createNote(collection, title)
      toast.success("Note created")
      await fetchNotes()
      setActiveNoteId(res.id)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create note")
    }
  }

  const fileInputRef = useRef<HTMLInputElement>(null)

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
      setActiveNoteId(res.id)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Import failed")
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
    <button
      key={note.id}
      className="w-full text-left flex items-center justify-between py-2.5 border-b cursor-pointer transition-colors hover:opacity-80 border-b border-dashed border-border text-foreground"
      style={{
        background: "none",
        borderLeft: "none",
        borderRight: "none",
        borderTop: "none",
      }}
      onClick={() => setActiveNoteId(note.id)}
    >
      <div className="flex items-center gap-2 flex-1 min-w-0">
        <span className="text-xs truncate">{note.title}</span>
        {note.is_extracted && (
          <span
            className="text-[9px] font-medium uppercase tracking-[0.1em] px-1.5 py-0.5 shrink-0 text-primary"
            style={{ background: "rgba(26,94,61,0.08)", borderRadius: "2px" }}
          >
            extracted
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 shrink-0 ml-2">
        <span
          className={cn("w-1 h-1 rounded-full", note.is_extracted ? "bg-primary" : "bg-transparent")}
        />
        <span className="text-[10px] text-muted-foreground">
          {formatDate(note.updated_at)}
        </span>
      </div>
    </button>
  )

  return (
    <>
      <div>
        {/* Header */}
        <div className="flex items-center justify-between mb-2.5">
          <div
            className="text-[9px] font-semibold uppercase tracking-[0.2em] text-muted-foreground"
          >
            Notes · {notes.length}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="text-[9px] font-medium uppercase tracking-[0.1em] cursor-pointer transition-opacity hover:opacity-80"
              style={{
                background: "none", border: "0.5px solid var(--color-border)",
                padding: "3px 8px", borderRadius: "2px",
                fontFamily: "var(--font-sans)",
              }}
              className="text-[9px] font-medium uppercase tracking-[0.1em] cursor-pointer transition-opacity hover:opacity-80 text-muted-foreground"
            >
              Import
            </button>
            <button
              type="button"
              onClick={handleCreate}
              className="text-[9px] font-semibold uppercase tracking-[0.1em] cursor-pointer transition-opacity hover:opacity-85"
              style={{
                background: "var(--color-primary)", color: "white", border: "none",
                padding: "4px 10px", borderRadius: "2px", fontFamily: "var(--font-sans)",
              }}
              className="text-[9px] font-semibold uppercase tracking-[0.1em] cursor-pointer transition-opacity hover:opacity-85"
            >
              + New Note
            </button>
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-8 gap-2 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span className="text-sm">Loading…</span>
          </div>
        ) : notes.length === 0 ? (
          <p className="text-xs text-muted-foreground">No notes yet. Create one to get started.</p>
        ) : (
          <Tabs defaultValue="unextracted">
            <TabsList className="mb-2 bg-transparent p-0 gap-5 rounded-none border-b border-border">
              <TabsTrigger
                value="unextracted"
                className="text-[10px] font-medium uppercase tracking-[0.12em] px-0 py-1.5 rounded-none border-b-2 bg-transparent data-[state=active]:shadow-none text-muted-foreground"
                style={{ borderColor: "transparent" }}
              >
                Notes ({unextracted.length})
              </TabsTrigger>
              <TabsTrigger
                value="extracted"
                className="text-[10px] font-medium uppercase tracking-[0.12em] px-0 py-1.5 rounded-none border-b-2 bg-transparent data-[state=active]:shadow-none text-muted-foreground"
                style={{ borderColor: "transparent" }}
              >
                Extracted ({extracted.length})
              </TabsTrigger>
            </TabsList>
            <TabsContent value="unextracted">
              {unextracted.length === 0 ? (
                <p className="text-xs py-2 text-muted-foreground">All notes have been extracted.</p>
              ) : (
                <div>{unextracted.map(renderNoteRow)}</div>
              )}
            </TabsContent>
            <TabsContent value="extracted">
              {extracted.length === 0 ? (
                <p className="text-xs py-2 text-muted-foreground">No extracted notes yet.</p>
              ) : (
                <div>{extracted.map(renderNoteRow)}</div>
              )}
            </TabsContent>
          </Tabs>
        )}
      </div>

      {activeNoteId && (
        <NoteEditorDialog
          collection={collection}
          noteId={activeNoteId}
          open={!!activeNoteId}
          onOpenChange={(v) => {
            if (!v) {
              setActiveNoteId(null)
              fetchNotes() // refresh list after closing editor
            }
          }}
        />
      )}

      <input
        ref={fileInputRef}
        type="file"
        accept=".md,.txt,.markdown"
        className="hidden"
        onChange={handleImportFile}
      />
    </>
  )
}
