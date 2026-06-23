import { useState, useEffect, useRef, useCallback } from "react"
import { Tabs, TabsContent, TabsList, TabsTrigger, TabsIndicator } from "@/components/ui/tabs"
import { Button } from "@/components/ui/button"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { Sparkles, RefreshCw, Upload } from "lucide-react"
import { GeneratedContent } from "./generated-content"
import { uploadMeetingNotes, updateMeeting } from "@/api/client"
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
  onSaveNotes?: (content: string) => void
  onDiscardNotes?: () => void
  onGenerate: () => void
  onRegenerate: () => void
  onUpdateGenerated: (data: { summary?: string; todos?: TodoItem[] }) => void
  onNotesUploaded: (content: string) => void
  onDirtyChange?: (dirty: boolean) => void
}

const SAVE_DELAY = 800

export function NotesEditor({
  meetingId,
  notesContent,
  detail,
  summary,
  todos,
  hasTranscript,
  generating,
  onGenerate,
  onRegenerate,
  onUpdateGenerated,
  onNotesUploaded,
  onDirtyChange,
}: NotesEditorProps) {
  const [activeTab, setActiveTab] = useState("notes")
  const [draft, setDraft] = useState(notesContent)
  const notesInputRef = useRef<HTMLInputElement>(null)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const initializedRef = useRef(false)
  const prevMeetingIdRef = useRef(meetingId)
  const baselineRef = useRef(notesContent)

  // Notify parent of dirty state (no dirty tracking = never dirty)
  useEffect(() => {
    onDirtyChange?.(false)
  }, [onDirtyChange])

  // Reset when meeting changes
  if (prevMeetingIdRef.current !== meetingId) {
    prevMeetingIdRef.current = meetingId
    initializedRef.current = false
    baselineRef.current = notesContent
  }

  useEffect(() => {
    setDraft(notesContent)
    baselineRef.current = notesContent
  }, [notesContent])

  // Set default tab once when data loads
  useEffect(() => {
    if (initializedRef.current) return
    if (summary === undefined && detail === undefined && notesContent === undefined) return
    if (summary) {
      setActiveTab("summary")
    } else {
      setActiveTab("notes")
    }
    initializedRef.current = true
  }, [summary, detail, notesContent])

  // Auto-save with debounce
  const scheduleSave = useCallback((content: string) => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(async () => {
      try {
        await updateMeeting(meetingId, { notes: content })
        baselineRef.current = content
      } catch {
        toast.error("Auto-save failed")
      }
    }, SAVE_DELAY)
  }, [meetingId])

  const handleDraftChange = (value: string) => {
    setDraft(value)
    if (value !== baselineRef.current) {
      scheduleSave(value)
    }
  }

  // Cleanup timer on unmount
  useEffect(() => {
    return () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current) }
  }, [])

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
      <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col min-h-0">
        <div className="flex items-center justify-between border-b border-border px-2">
          <TabsList className="relative" variant="line">
            <TabsIndicator renderBeforeHydration />
            <TabsTrigger value="summary" disabled={!summary} className="font-light uppercase tracking-wider after:!opacity-0">Summary</TabsTrigger>
            <TabsTrigger value="todo" disabled={!todos?.length} className="font-light uppercase tracking-wider after:!opacity-0">TODO</TabsTrigger>
            <TabsTrigger value="detail" disabled={!detail} className="font-light uppercase tracking-wider after:!opacity-0">Detail</TabsTrigger>
            <TabsTrigger value="notes" className="font-light uppercase tracking-wider after:!opacity-0">Notes</TabsTrigger>
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

        <TabsContent key={`summary-${activeTab}`} value="summary" className="flex-1 mt-0 min-h-0 overflow-auto p-2 animate-tab-in">
          <GeneratedContent tab="summary" content={summary} loading={generating} onSave={onUpdateGenerated} />
        </TabsContent>

        <TabsContent key={`todo-${activeTab}`} value="todo" className="flex-1 mt-0 min-h-0 overflow-auto p-2 animate-tab-in">
          <GeneratedContent tab="todo" content={null} todos={todos} loading={generating} onSave={onUpdateGenerated} />
        </TabsContent>

        <TabsContent key={`detail-${activeTab}`} value="detail" className="flex-1 mt-0 min-h-0 overflow-auto p-2 animate-tab-in">
          <GeneratedContent tab="detail" content={detail} loading={generating} onSave={onUpdateGenerated} />
        </TabsContent>

        <TabsContent key={`notes-${activeTab}`} value="notes" className="flex-1 mt-0 min-h-0 flex flex-col animate-tab-in">
          <div className="flex-1 min-h-0 p-2 overflow-auto">
            <MarkdownEditor
              value={draft}
              onChange={handleDraftChange}
              minHeight="250px"
              placeholder="Write your recording notes here (Markdown supported)..."
            />
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}
