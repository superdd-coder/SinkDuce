import { useState, useEffect, useRef } from "react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Button } from "@/components/ui/button"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { Save, X, Sparkles, RefreshCw, Upload } from "lucide-react"
import { GeneratedContent } from "./generated-content"
import { uploadMeetingNotes } from "@/api/client"
import { toast } from "sonner"
import type { TodoItem } from "@/api/client"

interface NotesEditorProps {
  meetingId: string
  notesContent: string
  detail: string | null
  summary: string | null
  todos: TodoItem[] | null
  hasTranscript: boolean
  generating: boolean
  onSaveNotes: (content: string) => void
  onDiscardNotes: () => void
  onGenerate: () => void
  onRegenerate: () => void
  onUpdateGenerated: (data: { summary?: string; todos?: TodoItem[] }) => void
  onNotesUploaded: (content: string) => void
  onDirtyChange?: (dirty: boolean) => void
}

export function NotesEditor({
  meetingId,
  notesContent,
  detail,
  summary,
  todos,
  hasTranscript,
  generating,
  onSaveNotes,
  onDiscardNotes,
  onGenerate,
  onRegenerate,
  onUpdateGenerated,
  onNotesUploaded,
  onDirtyChange,
}: NotesEditorProps) {
  const [activeTab, setActiveTab] = useState("notes")
  const [draft, setDraft] = useState(notesContent)
  const [dirty, setDirty] = useState(false)
  const notesInputRef = useRef<HTMLInputElement>(null)
  const draftRef = useRef(draft)
  const dirtyRef = useRef(dirty)
  const initializedRef = useRef(false)
  const prevMeetingIdRef = useRef(meetingId)

  // Sync dirty ref
  useEffect(() => {
    dirtyRef.current = dirty
    onDirtyChange?.(dirty)
  }, [dirty, onDirtyChange])

  // beforeunload guard
  useEffect(() => {
    if (!dirty) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
    }
    window.addEventListener("beforeunload", handler)
    return () => window.removeEventListener("beforeunload", handler)
  }, [dirty])

  // Reset when meeting changes
  if (prevMeetingIdRef.current !== meetingId) {
    prevMeetingIdRef.current = meetingId
    initializedRef.current = false
  }

  useEffect(() => {
    setDraft(notesContent)
    setDirty(false)
    draftRef.current = notesContent
  }, [notesContent])

  // Set default tab once when data loads
  useEffect(() => {
    if (initializedRef.current) return
    // Wait until at least one piece of content has been resolved
    if (summary === undefined && detail === undefined && notesContent === undefined) return
    if (summary) {
      setActiveTab("summary")
    } else {
      setActiveTab("notes")
    }
    initializedRef.current = true
  }, [summary, detail, notesContent])

  const handleDraftChange = (value: string) => {
    setDraft(value)
    draftRef.current = value
    setDirty(true)
  }

  const handleTabChange = (newTab: string) => {
    if (dirty && activeTab === "notes") {
      toast.warning("You have unsaved notes. Save or discard before switching tabs.")
      return
    }
    setActiveTab(newTab)
  }

  const handleSave = () => {
    onSaveNotes(draftRef.current)
    setDirty(false)
  }

  const handleDiscard = () => {
    setDraft(notesContent)
    draftRef.current = notesContent
    setDirty(false)
    onDiscardNotes()
  }

  const handleUploadNotes = async (file: File) => {
    try {
      const res = await uploadMeetingNotes(meetingId, file)
      onNotesUploaded(res.notes_content)
      toast.success("Notes uploaded")
    } catch (err) {
      toast.error(`Upload failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const hasGeneratedContent = detail || summary || (todos && todos.length > 0)

  return (
    <div className="flex flex-col h-full min-h-0">
      <input
        ref={notesInputRef}
        type="file"
        accept=".md,.txt,.docx"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) handleUploadNotes(file)
          e.target.value = ""
        }}
      />
      <Tabs value={activeTab} onValueChange={handleTabChange} className="flex-1 flex flex-col min-h-0">
        <div className="flex items-center justify-between border-b border-border px-1">
          <TabsList>
            <TabsTrigger value="summary" disabled={!summary}>Summary</TabsTrigger>
            <TabsTrigger value="todo" disabled={!todos?.length}>TODO</TabsTrigger>
            <TabsTrigger value="detail" disabled={!detail}>Detail</TabsTrigger>
            <TabsTrigger value="notes">Notes</TabsTrigger>
          </TabsList>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={() => notesInputRef.current?.click()}>
              <Upload className="h-3 w-3 mr-1" /> Upload Notes
            </Button>
            {hasTranscript && !hasGeneratedContent && (
              <Button size="sm" onClick={onGenerate} disabled={generating}>
                <Sparkles className="h-3 w-3 mr-1" />
                {generating ? "Summarizing..." : "Summarize"}
              </Button>
            )}
            {hasTranscript && hasGeneratedContent && (
              <Button variant="outline" size="sm" onClick={onRegenerate} disabled={generating}>
                <RefreshCw className="h-3 w-3 mr-1" />
                {generating ? "Summarizing..." : "Re-summarize"}
              </Button>
            )}
          </div>
        </div>

        <TabsContent value="summary" className="flex-1 mt-0 min-h-0 overflow-auto p-3">
          <GeneratedContent tab="summary" content={summary} loading={generating} onSave={onUpdateGenerated} />
        </TabsContent>

        <TabsContent value="todo" className="flex-1 mt-0 min-h-0 overflow-auto p-3">
          <GeneratedContent tab="todo" content={null} todos={todos} loading={generating} onSave={onUpdateGenerated} />
        </TabsContent>

        <TabsContent value="detail" className="flex-1 mt-0 min-h-0 overflow-auto p-3">
          <GeneratedContent tab="detail" content={detail} loading={generating} onSave={onUpdateGenerated} />
        </TabsContent>

        <TabsContent value="notes" className="flex-1 mt-0 min-h-0 flex flex-col">
          <div className="flex-1 min-h-0 p-2 overflow-auto">
            <MarkdownEditor
              value={draft}
              onChange={handleDraftChange}
              minHeight="250px"
              placeholder="Write your meeting notes here (Markdown supported)..."
            >
              {dirty && (
                <>
                  <Button size="sm" onClick={handleSave}>
                    <Save className="h-3 w-3 mr-1" /> Save
                  </Button>
                  <Button variant="outline" size="sm" onClick={handleDiscard}>
                    <X className="h-3 w-3 mr-1" /> Discard
                  </Button>
                </>
              )}
            </MarkdownEditor>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}
