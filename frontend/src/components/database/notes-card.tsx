import { useState, useEffect, useCallback, useRef } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Loader2, StickyNote, Plus, FileText, FileDown } from "lucide-react"
import { toast } from "sonner"
import {
  getNotes,
  createNote,
  updateNote,
  type NoteListItem,
} from "@/api/client"
import { NoteEditorDialog } from "./note-editor-dialog"

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
      className="w-full text-left flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-accent/50 transition-colors text-sm group"
      onClick={() => setActiveNoteId(note.id)}
    >
      <FileText className="h-4 w-4 text-muted-foreground shrink-0" />
      <span className="flex-1 truncate font-medium">{note.title}</span>
      {note.is_extracted && (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 shrink-0">
          extracted
        </span>
      )}
      <span className="text-xs text-muted-foreground shrink-0">
        {formatDate(note.updated_at)}
      </span>
    </button>
  )

  return (
    <>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <StickyNote className="h-4 w-4" />
            Notes
          </CardTitle>
          <div className="flex items-center gap-1.5">
            <Button variant="outline" size="sm" onClick={() => fileInputRef.current?.click()}>
              <FileDown className="h-4 w-4 mr-1.5" />
              Import
            </Button>
            <Button variant="outline" size="sm" onClick={handleCreate}>
              <Plus className="h-4 w-4 mr-1.5" />
              New Note
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center justify-center py-8 text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin mr-2" />
              Loading notes...
            </div>
          ) : notes.length === 0 ? (
            <p className="text-sm text-muted-foreground">No notes yet. Create one to get started.</p>
          ) : (
            <Tabs defaultValue="unextracted">
              <TabsList className="mb-2">
                <TabsTrigger value="unextracted">
                  Notes ({unextracted.length})
                </TabsTrigger>
                <TabsTrigger value="extracted">
                  Extracted ({extracted.length})
                </TabsTrigger>
              </TabsList>
              <TabsContent value="unextracted">
                {unextracted.length === 0 ? (
                  <p className="text-sm text-muted-foreground py-2">All notes have been extracted.</p>
                ) : (
                  <div className="space-y-0.5">{unextracted.map(renderNoteRow)}</div>
                )}
              </TabsContent>
              <TabsContent value="extracted">
                {extracted.length === 0 ? (
                  <p className="text-sm text-muted-foreground py-2">No extracted notes yet.</p>
                ) : (
                  <div className="space-y-0.5">{extracted.map(renderNoteRow)}</div>
                )}
              </TabsContent>
            </Tabs>
          )}
        </CardContent>
      </Card>

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
