import { useState, useEffect, useCallback, useMemo, useRef, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { cn } from "@/lib/utils"
import { Loader2, X, RefreshCw, Plus, Pencil, Sparkles, Check, Database, FolderPlus } from "lucide-react"
import {
  getMeeting, startBreakdown, magicExtract, deleteSection,
  regenerateSection, getSectionMd, generateMeetingSummary,
  saveSectionMd, updateMeeting,
  allocateSection, deleteSectionAllocation, createCollection,
  type Meeting, type MeetingTab, type ProcessingState,
  type TranscriptSegment,
} from "@/api/client"
import { useAppStore } from "@/stores/app-store"
import { toast } from "sonner"

const SAVE_DELAY = 800

interface Props {
  meetingId: string
  meeting: Meeting
  notesContent: string
  onMeetingUpdate: (m: Meeting) => void
  onSeekTo: (time: number) => void
  onFocusSentence?: (refId: string) => void
  onActiveTabChange?: (tabId: string) => void
  transcriptSegments: TranscriptSegment[]
}

interface ExtractedTopic {
  name: string
  description: string
}

/** Poll while meeting is in a non-idle processing state */
function useProcessingPoll(
  meetingId: string,
  state: ProcessingState | undefined,
  onDone: (m: Meeting) => void,
) {
  useEffect(() => {
    if (!state || state === "idle") return
    const poll = setInterval(async () => {
      try {
        const m = await getMeeting(meetingId)
        if (!m.processing_state || m.processing_state === "idle") {
          clearInterval(poll)
          onDone(m)
        }
      } catch { /* ignore */ }
    }, 2000)
    return () => clearInterval(poll)
  }, [meetingId, state, onDone])
}

// ── Inline markdown normalizer ────────────────────────────────────

/** Fix Tiptap-style markdown quirks: extra spaces inside bold/italic/link syntax. */
function normalizeMd(md: string): string {
  return md
    .replace(/\*\*\s+([^*]+?)\s*\*\*/g, "**$1**")
    .replace(/(?<!\*)\*(?!\*)\s+([^*]+?)\s*(?<!\*)\*(?!\*)/g, "*$1*")
}

// ── Markdown viewer with clickable [stt_XXXX] ref buttons ──────────

/** Render inline markdown: **bold**, *italic*, `code`, [stt_XXXX] refs, [priority: X] badges */
function renderInline(text: string, onRefClick: (id: string) => void): ReactNode[] {
  const parts: ReactNode[] = []
  const regex = /(\*\*(.+?)\*\*)|(\*(.+?)\*)|(`(.+?)`)|(\[(?:ref:)?\s*(stt_\d+(?:\s*,\s*stt_\d+)*)\s*\])|(\[priority:\s*(high|medium|low)\s*\])/gi
  let lastIdx = 0
  let match
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIdx) {
      parts.push(<span key={`t${lastIdx}`}>{text.slice(lastIdx, match.index)}</span>)
    }
    if (match[1]) {
      parts.push(<strong key={`b${lastIdx}`}>{match[2]}</strong>)
    } else if (match[3]) {
      parts.push(<em key={`i${lastIdx}`}>{match[4]}</em>)
    } else if (match[5]) {
      parts.push(<code key={`c${lastIdx}`} className="bg-muted px-1 rounded text-xs font-mono">{match[6]}</code>)
    } else if (match[8]) {
      // [stt_0044,stt_0045,stt_0046] — backend guarantees comma-separated format
      const raw = match[8]
      const ids = raw.split(",").map((s) => s.trim()).filter(Boolean)
      const parsed = ids
        .map((id) => ({ id, num: parseInt(id.replace(/^stt_0*/, "") || "0", 10) }))
        .sort((a, b) => a.num - b.num)

      // Group consecutive ids into ranges
      let ri = 0
      while (ri < parsed.length) {
        const start = parsed[ri]
        let end = start
        let rj = ri + 1
        while (rj < parsed.length && parsed[rj].num === end.num + 1) { end = parsed[rj]; rj++ }
        const sl = start.id.replace(/^stt_0*/, "") || "0"
        const el = end.id.replace(/^stt_0*/, "") || "0"
        const label = start.id === end.id ? sl : `${sl}-${el}`
        const allInRange = parsed.slice(ri, rj).map((p) => p.id)
        parts.push(
          <button
            key={`r${lastIdx}${ri}`}
            className="inline-flex items-center px-1 py-0 text-[10px] rounded bg-muted hover:bg-primary/20 font-mono align-baseline"
            onClick={(e) => { e.stopPropagation(); onRefClick(start.id) }}
            title={`Sources: ${allInRange.join(", ")}`}
          >
            {label}
          </button>,
        )
        ri = rj
      }
    } else if (match[10]) {
      // [priority: high/medium/low] → colored badge
      const level = match[10].toLowerCase()
      const colors: Record<string, { bg: string; fg: string }> = {
        high:    { bg: "rgba(140,46,46,0.12)",  fg: "#C06060" },
        medium:  { bg: "rgba(138,101,0,0.10)",   fg: "#B09030" },
        low:     { bg: "rgba(26,94,61,0.10)",    fg: "#5A9070" },
      }
      const c = colors[level] ?? colors.medium
      parts.push(
        <span
          key={`p${lastIdx}`}
          className="inline-flex items-center px-1 py-0 text-[9px] rounded font-medium tracking-wider align-baseline select-none"
          style={{ backgroundColor: c.bg, color: c.fg }}
        >
          {level.toUpperCase()}
        </span>,
      )
    }
    lastIdx = match.index + match[0].length
  }
  if (lastIdx < text.length) {
    parts.push(<span key={`t${lastIdx}`}>{text.slice(lastIdx)}</span>)
  }
  return parts
}

function MarkdownViewer({ md, onRefClick, speakerNames }: {
  md: string
  onRefClick: (id: string) => void
  speakerNames: Record<string, string>
}) {
  if (!md) {
    return <p className="text-muted-foreground text-sm py-8 text-center">No content yet.</p>
  }

  // Normalize + resolve [spk:ID] markers and legacy "Speaker X" patterns
  let resolved = normalizeMd(md)
  if (speakerNames && Object.keys(speakerNames).length > 0) {
    resolved = resolved.replace(/\[spk:(\d+)\]/g, (_, id: string) => speakerNames[id] ?? `Speaker ${id}`)
    for (const [id, name] of Object.entries(speakerNames)) {
      resolved = resolved.replace(
        new RegExp(`Speaker ${id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`, "g"),
        name,
      )
    }
  }

  return (
    <div className="prose prose-sm dark:prose-invert max-w-none">
      {resolved.split("\n").map((line, i) => {
        if (!line.trim()) return <div key={i} className="h-3" />
        if (line.startsWith("### ")) return <h3 key={i} className="text-base font-light mb-1.5 mt-2">{renderInline(line.slice(4), onRefClick)}</h3>
        if (line.startsWith("## ")) return <h2 key={i} className="text-lg font-light mb-2 mt-3">{renderInline(line.slice(3), onRefClick)}</h2>
        if (line.startsWith("# ")) return <h1 key={i} className="text-xl font-light mb-3 mt-4">{renderInline(line.slice(2), onRefClick)}</h1>
        if (/^\s*[-*+]\s/.test(line)) {
          return <li key={i} className="text-sm leading-relaxed ml-4">{renderInline(line.replace(/^\s*[-*+]\s/, ""), onRefClick)}</li>
        }
        return <p key={i} className="text-sm leading-relaxed mb-1">{renderInline(line, onRefClick)}</p>
      })}
    </div>
  )
}

// ── Editable section content (readonly view + edit mode) ──────────

function EditableSectionContent({
  content,
  onSave,
  onRefClick,
  speakerNames,
  actionButtons,
  title,
  metadata,
}: {
  content: string
  onSave: (updated: string) => Promise<void>
  onRefClick: (id: string) => void
  speakerNames: Record<string, string>
  actionButtons?: ReactNode
  title?: ReactNode
  metadata?: ReactNode
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(content)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setDraft(content)
    setEditing(false)
  }, [content])

  const handleSave = async () => {
    setSaving(true)
    try {
      const cleaned = draft.replace(/\\([\[\]])/g, "$1")
      await onSave(cleaned)
      setEditing(false)
      toast.success("Saved")
    } catch (err) {
      toast.error(`Save failed: ${err instanceof Error ? err.message : String(err)}`)
    }
    setSaving(false)
  }

  return (
    <div className="relative min-h-full">
      {/* Sticky top bar — title left, action buttons right */}
      <div className="sticky top-0 z-10 flex items-center justify-between px-6 py-2 -mx-2 bg-background/80 backdrop-blur-sm">
        <div className="text-2xl font-bold min-w-0 truncate" style={{ fontFamily: "var(--font-serif)" }}>
          {title}
        </div>
        <div className="flex items-center gap-1 shrink-0 ml-2">
          {!editing && (
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setEditing(true)} title="Edit">
              <Pencil className="h-3.5 w-3.5" />
            </Button>
          )}
          {actionButtons}
        </div>
      </div>

      {/* Metadata slot (between title bar and content) */}
      {metadata}

      {/* Content area */}
      <div className="px-6 pb-4">
        {editing ? (
          <MarkdownEditor value={draft} onChange={setDraft} minHeight="250px" />
        ) : (
          <MarkdownViewer md={content} onRefClick={onRefClick} speakerNames={speakerNames} />
        )}
      </div>

      {/* Floating save/cancel bar — bottom right, sticky */}
      {editing && (
        <div className="sticky bottom-0 z-10 flex justify-end gap-2 px-6 py-2 -mx-2 bg-background/80 backdrop-blur-sm border-t border-border">
          <Button variant="outline" size="sm" onClick={() => { setDraft(content); setEditing(false) }}>
            Cancel
          </Button>
          <Button size="sm" onClick={handleSave} disabled={saving}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : null}
            Save
          </Button>
        </div>
      )}
    </div>
  )
}

// ── Section metadata (between title bar and content) ──────────────

function SectionMetadata({
  tab,
  blueprint,
  meetingId,
  onMeetingUpdate,
}: {
  tab: MeetingTab
  blueprint: Meeting["blueprint"]
  meetingId: string
  onMeetingUpdate: (m: Meeting) => void
}) {
  const bpEntry = (blueprint ?? []).find((b) => b.tab_id === tab.tab_id)
  const description = bpEntry?.section_description ?? ""
  const associatedName = tab.associated_collection_name || bpEntry?.associated_collection_name || ""
  const associatedId = tab.associated_collection_id || bpEntry?.associated_collection_id || ""
  const hasAssociated = !!associatedName
  // Consider "ingested" when tab has an allocated_file_id (already persisted)
  const ingested = !!(tab as any).allocated_file_id
  // If ingested but from a different collection than the original association,
  // show that collection name instead
  const displayName = ingested ? associatedName : (hasAssociated ? associatedName : "")
  const displayActive = ingested

  const [ingesting, setIngesting] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [cancelOpen, setCancelOpen] = useState(false)

  const handleIngest = async (colId: string) => {
    if (ingesting) return
    setIngesting(true)
    try {
      const m = await allocateSection(meetingId, tab.tab_id, colId)
      onMeetingUpdate(m)
      toast.success("Ingested to collection")
    } catch (err) {
      toast.error(`Ingest failed: ${err instanceof Error ? err.message : String(err)}`)
    }
    setIngesting(false)
  }

  const handleCancelIngest = async () => {
    setCancelOpen(false)
    setIngesting(true)
    try {
      const m = await deleteSectionAllocation(meetingId, tab.tab_id)
      onMeetingUpdate(m)
      toast.success("Ingestion cancelled")
    } catch (err) {
      toast.error(`Cancel failed: ${err instanceof Error ? err.message : String(err)}`)
    }
    setIngesting(false)
  }

  const BUTTON_W = "w-[172px]"

  return (
    <div className="px-6 py-2 flex gap-4">
      {/* Left: section description */}
      <div className="flex-1 min-w-0">
        {description && (
          <p className="text-xs text-muted-foreground leading-relaxed">{description}</p>
        )}
      </div>

      {/* Right: collection buttons */}
      <div className={cn("shrink-0 flex flex-col gap-1.5 items-end", BUTTON_W)}>
        {(hasAssociated || ingested) && (
          <Button
            variant={displayActive ? "default" : "outline"}
            size="sm"
            className={cn("h-7 text-xs w-full justify-start", ingesting && "opacity-50")}
            disabled={ingesting}
            onClick={displayActive ? () => setCancelOpen(true) : () => handleIngest(associatedId)}
          >
            {ingesting ? (
              <Loader2 className="h-3 w-3 animate-spin mr-1 shrink-0" />
            ) : displayActive ? (
              <Check className="h-3 w-3 mr-1 shrink-0" />
            ) : (
              <Database className="h-3 w-3 mr-1 shrink-0" />
            )}
            <span className="truncate">{displayName || associatedName}</span>
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          className="h-7 text-xs w-full justify-start"
          onClick={() => setPickerOpen(true)}
        >
          <FolderPlus className="h-3 w-3 mr-1 shrink-0" />
          Choose a collection
        </Button>
      </div>

      {/* Collection Picker Dialog */}
      <CollectionPickerDialog
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        tabName={tab.name}
        hasAssociated={hasAssociated}
        onIngested={handleIngest}
      />

      {/* Cancel Ingestion Confirm Dialog */}
      <Dialog open={cancelOpen} onOpenChange={setCancelOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Cancel Ingestion</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            This will remove the section content from "{associatedName}" and delete the file snapshot. Continue?
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setCancelOpen(false)}>Cancel</Button>
            <Button variant="destructive" onClick={handleCancelIngest}>Remove</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

// ── Collection picker / creator dialog ───────────────────────────

function CollectionPickerDialog({
  open,
  onOpenChange,
  tabName,
  hasAssociated,
  onIngested,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  tabName: string
  hasAssociated: boolean
  onIngested: (colId: string) => Promise<void>
}) {
  const { collections, fetchCollections } = useAppStore()
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState(tabName)
  const [ingesting, setIngesting] = useState(false)

  useEffect(() => {
    if (open) {
      fetchCollections()
      setCreating(false)
      setNewName(tabName)
    }
  }, [open, fetchCollections, tabName])

  const handleSelectExisting = async (colId: string) => {
    setIngesting(true)
    try {
      await onIngested(colId)
      onOpenChange(false)
    } catch { /* error handled in parent */ }
    setIngesting(false)
  }

  const handleCreateAndIngest = async () => {
    if (!newName.trim()) return
    setIngesting(true)
    try {
      const res = await createCollection(newName.trim())
      if (res.error) throw new Error(res.error)
      const colId = res.id
      if (!colId) throw new Error("No collection ID returned")
      await onIngested(colId)
      await fetchCollections()
      onOpenChange(false)
      toast.success(`Created "${newName.trim()}" and ingested`)
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    }
    setIngesting(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Choose a collection</DialogTitle>
        </DialogHeader>

        {!creating ? (
          <>
            <div className="max-h-48 overflow-y-auto space-y-0.5 -mx-1">
              {collections.length === 0 && (
                <p className="text-xs text-muted-foreground text-center py-4">No collections yet</p>
              )}
              {collections.map((col) => (
                <button
                  key={col.id}
                  className="w-full text-left px-3 py-2 text-sm rounded hover:bg-accent transition-colors flex items-center gap-2"
                  onClick={() => handleSelectExisting(col.id)}
                  disabled={ingesting}
                >
                  <Database className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                  <span className="truncate">{col.name}</span>
                </button>
              ))}
            </div>

            <div className="pt-2 border-t border-border">
              {hasAssociated ? (
                <Button variant="outline" size="sm" className="w-full text-xs" onClick={() => setCreating(true)}>
                  <Plus className="h-3 w-3 mr-1" /> Create new collection
                </Button>
              ) : (
                <Button variant="outline" size="sm" className="w-full text-xs border-dashed" onClick={() => setCreating(true)}>
                  <Plus className="h-3 w-3 mr-1" />+ {tabName}
                </Button>
              )}
            </div>
          </>
        ) : (
          <>
            <div className="space-y-2">
              <label className="text-xs text-muted-foreground">Collection name</label>
              <input
                className="w-full border border-border rounded px-2 py-1.5 text-sm bg-background"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="Collection name"
                autoFocus
                onKeyDown={(e) => { if (e.key === "Enter") handleCreateAndIngest() }}
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" size="sm" onClick={() => setCreating(false)} disabled={ingesting}>
                Back
              </Button>
              <Button size="sm" onClick={handleCreateAndIngest} disabled={ingesting || !newName.trim()}>
                {ingesting ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : null}
                Create & Ingest
              </Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

// ── Main component ────────────────────────────────────────────────

export function MeetingTabs({
  meetingId, meeting, notesContent,
  onMeetingUpdate, onSeekTo, onFocusSentence, onActiveTabChange, transcriptSegments,
}: Props) {
  const tabs = meeting.tabs ?? []
  const blueprint = meeting.blueprint ?? []
  const speakerNames: Record<string, string> = meeting.speaker_names ?? {}

  const [mainTab, setMainTab] = useState("summary")
  const [selectedSummaryId, setSelectedSummaryId] = useState("tab_general")
  const [tabMdContents, setTabMdContents] = useState<Record<string, string>>({})

  // Notify parent of active tab changes (for transcript tag highlighting)
  useEffect(() => {
    onActiveTabChange?.(selectedSummaryId)
  }, [selectedSummaryId, onActiveTabChange])
  const [loadingTabs, setLoadingTabs] = useState<Set<string>>(new Set())
  const [extractOpen, setExtractOpen] = useState(false)
  const [extractTopics, setExtractTopics] = useState<ExtractedTopic[]>([{ name: "", description: "" }])
  const [busy, setBusy] = useState(!!(meeting.processing_state && meeting.processing_state !== "idle"))
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [reSummarizeOpen, setReSummarizeOpen] = useState(false)

  // Clear polling on unmount (prevents stale meeting data leaking on switch)
  useEffect(() => {
    return () => { if (pollRef.current) if (pollRef.current) clearInterval(pollRef.current) }
  }, [])
  const [notesDraft, setNotesDraft] = useState(notesContent)
  const notesBaselineRef = useRef(notesContent)
  const notesSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const prevNotesContentRef = useRef(notesContent)

  // Sync notes draft when parent content changes from external source
  if (prevNotesContentRef.current !== notesContent) {
    prevNotesContentRef.current = notesContent
    if (notesDraft === notesBaselineRef.current) {
      setNotesDraft(notesContent)
      notesBaselineRef.current = notesContent
    }
  }

  // ── Poll for processing completion ─────────────────────────
  const handleProcessingDone = useCallback((m: Meeting) => {
    onMeetingUpdate(m)
    setBusy(false)
    toast.success("Processing complete")
  }, [onMeetingUpdate])
  useProcessingPoll(meetingId, meeting.processing_state, handleProcessingDone)

  // ── Load section markdown when tab is selected ─────────────
  const loadTabContent = useCallback(async (tabId: string) => {
    if (tabMdContents[tabId] !== undefined) return
    setLoadingTabs((prev) => new Set(prev).add(tabId))
    try {
      const md = await getSectionMd(meetingId, tabId)
      if (md !== null) {
        setTabMdContents((prev) => ({ ...prev, [tabId]: md }))
      } else {
        setTabMdContents((prev) => ({ ...prev, [tabId]: "" }))
      }
    } catch {
      setTabMdContents((prev) => ({ ...prev, [tabId]: "" }))
    }
    setLoadingTabs((prev) => {
      const next = new Set(prev)
      next.delete(tabId)
      return next
    })
  }, [meetingId, tabMdContents])

  useEffect(() => {
    if (selectedSummaryId && selectedSummaryId !== "tab_general") {
      loadTabContent(selectedSummaryId)
    }
  }, [selectedSummaryId, loadTabContent])

  // Seed general tab from meeting.detail
  useEffect(() => {
    if (meeting.detail) {
      setTabMdContents((prev) => {
        if (prev["tab_general"] !== undefined) return prev
        return { ...prev, tab_general: meeting.detail ?? "" }
      })
    }
  }, [meeting.detail])

  // ── Notes auto-save ────────────────────────────────────────
  const scheduleNotesSave = useCallback((content: string) => {
    if (notesSaveTimerRef.current) clearTimeout(notesSaveTimerRef.current)
    notesSaveTimerRef.current = setTimeout(async () => {
      try {
        // Unescape brackets that Tiptap escapes (\[ → [)
      const cleaned = content.replace(/\\([\[\]])/g, "$1")
      await updateMeeting(meetingId, { notes: cleaned })
        notesBaselineRef.current = cleaned
      } catch { /* ignore */ }
    }, SAVE_DELAY)
  }, [meetingId])

  const handleNotesChange = (value: string) => {
    setNotesDraft(value)
    if (value !== notesBaselineRef.current) {
      scheduleNotesSave(value)
    }
  }

  useEffect(() => {
    return () => { if (notesSaveTimerRef.current) clearTimeout(notesSaveTimerRef.current) }
  }, [])

  // ── Actions ─────────────────────────────────────────────────
  const handleBreakdown = async () => {
    setBusy(true)
    try {
      await startBreakdown(meetingId)
      // Poll until breakdown completes, same pattern as handleReSummarize
      pollRef.current = setInterval(async () => {
        try {
          const m = await getMeeting(meetingId)
          if (!m.processing_state || m.processing_state === "idle") {
            if (pollRef.current) clearInterval(pollRef.current)
            onMeetingUpdate(m)
            setBusy(false)
            toast.success("Breakdown complete")
          }
        } catch { /* ignore */ }
      }, 2000)
    } catch (err) {
      setBusy(false)
      toast.error(`Breakdown failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleSummarize = async () => {
    setBusy(true)
    try {
      await generateMeetingSummary(meetingId)
      pollRef.current = setInterval(async () => {
        try {
          const m = await getMeeting(meetingId)
          if (!m.processing_state || m.processing_state === "idle") {
            if (pollRef.current) clearInterval(pollRef.current)
            onMeetingUpdate(m)
            setTabMdContents({})
            setBusy(false)
            toast.success("Summary generated")
          }
        } catch { /* ignore */ }
      }, 2000)
    } catch (err) {
      setBusy(false)
      toast.error(`Summarize failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleReSummarize = async () => {
    setReSummarizeOpen(false)
    setBusy(true)
    try {
      await generateMeetingSummary(meetingId)
      pollRef.current = setInterval(async () => {
        try {
          const m = await getMeeting(meetingId)
          if (!m.processing_state || m.processing_state === "idle") {
            if (pollRef.current) clearInterval(pollRef.current)
            onMeetingUpdate(m)
            setTabMdContents({})
            setBusy(false)
            toast.success("Summary regenerated")
          }
        } catch { /* ignore */ }
      }, 2000)
    } catch (err) {
      setBusy(false)
      toast.error(`Re-summarize failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleMagicExtract = async () => {
    const validTopics = extractTopics.filter((t) => t.name.trim())
    if (validTopics.length === 0) {
      toast.error("At least one topic with a name is required")
      return
    }
    setExtractOpen(false)
    setBusy(true)
    try {
      // If on a section tab, overwrite it instead of creating a new one
      const targetTabId = selectedSummaryId !== "tab_general" ? selectedSummaryId : undefined
      await magicExtract(meetingId, validTopics, targetTabId)
      setExtractTopics([{ name: "", description: "" }])
      // Poll until extract completes
      pollRef.current = setInterval(async () => {
        try {
          const m = await getMeeting(meetingId)
          if (!m.processing_state || m.processing_state === "idle") {
            if (pollRef.current) clearInterval(pollRef.current)
            onMeetingUpdate(m)
            setBusy(false)
            toast.success("Extract complete")
          }
        } catch { /* ignore */ }
      }, 2000)
    } catch (err) {
      setBusy(false)
      toast.error(`Extract failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleDeleteSection = async (tabId: string) => {
    if (!window.confirm("Delete this section? This removes all its tags.")) return
    try {
      const m = await deleteSection(meetingId, tabId)
      onMeetingUpdate(m)
      if (selectedSummaryId === tabId) setSelectedSummaryId("tab_general")
      setTabMdContents((prev) => {
        const next = { ...prev }
        delete next[tabId]
        return next
      })
      toast.success("Section deleted")
    } catch (err) {
      toast.error(`Delete failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleRegenerate = async (tabId: string) => {
    setBusy(true)
    try {
      await regenerateSection(meetingId, tabId)
      // Poll until regenerate completes (same pattern as handleMagicExtract)
      pollRef.current = setInterval(async () => {
        try {
          const m = await getMeeting(meetingId)
          if (!m.processing_state || m.processing_state === "idle") {
            if (pollRef.current) clearInterval(pollRef.current)
            onMeetingUpdate(m)
            setTabMdContents((prev) => {
              const next = { ...prev }
              delete next[tabId]
              return next
            })
            setBusy(false)
            toast.success("Regenerate complete")
          }
        } catch { /* ignore */ }
      }, 2000)
    } catch (err) {
      setBusy(false)
      toast.error(`Regenerate failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleSaveSection = async (tabId: string, content: string) => {
    await saveSectionMd(meetingId, tabId, content)
    setTabMdContents((prev) => ({ ...prev, [tabId]: content }))
    if (tabId === "tab_general") {
      onMeetingUpdate({ ...meeting, detail: content })
    }
  }

  // ── Sentence ID → time map (use sentence_id from backend when available) ──
  const sentenceTimeMap: Record<string, number> = {}
  transcriptSegments.forEach((seg, idx) => {
    const sid = seg.sentence_id
    if (sid) {
      // Backend returns full ID like "756f0b7c_stt_0044" — store both forms
      sentenceTimeMap[sid] = seg.start
      // Also store the short form for partial matching
      const short = sid.replace(/^.*(_stt_\d+)$/, "$1")
      if (short && short !== sid) sentenceTimeMap[short] = seg.start
    }
    // Fallback: use array index for segments without sentence_id
    const paddedIdx = String(idx).padStart(4, "0")
    if (!sentenceTimeMap[`_stt_${paddedIdx}`]) {
      sentenceTimeMap[`_stt_${paddedIdx}`] = seg.start
    }
  })

  const handleRefClick = (refId: string) => {
    // Remove any leading bracket/whitespace that might have leaked in
    const clean = refId.replace(/^\[?/, "").trim()
    // Exact match
    for (const [key, time] of Object.entries(sentenceTimeMap)) {
      if (key.endsWith(clean)) {
        onSeekTo(time)
        onFocusSentence?.(clean)
        return
      }
    }
    // Fallback: try "stt_" + number format
    const withPrefix = clean.startsWith("stt_") ? clean : `stt_${clean}`
    if (withPrefix !== clean) {
      for (const [key, time] of Object.entries(sentenceTimeMap)) {
        if (key.endsWith(withPrefix)) {
          onSeekTo(time)
          onFocusSentence?.(withPrefix)
          return
        }
      }
    }
    // Fallback: LLM may concatenate IDs (e.g. stt_003638 → try stt_0036, stt_0038)
    const num = clean.replace(/^stt_/, "")
    if (num.length > 4 && /^\d+$/.test(num)) {
      for (let i = 4; i <= num.length; i += 4) {
        const chunk = `stt_${num.slice(i - 4, i)}`
        for (const [key, time] of Object.entries(sentenceTimeMap)) {
          if (key.endsWith(chunk)) {
            onSeekTo(time)
            onFocusSentence?.(chunk)
            return
          }
        }
      }
    }
    toast.info(`Reference: ${clean}`, { duration: 2000 })
  }

  // ── Render helpers ──────────────────────────────────────────
  const hasTranscript = transcriptSegments.length > 0 || !!meeting.transcript_path
  const hasBlueprint = blueprint.length > 0
  const hasSections = tabs.some((t) => t.type === "section" && t.md_file_path)

  /** tab_sec_01 → T1, tab_sec_02 → T2 */
  function tabShortLabel(tab: MeetingTab): string {
    const m = tab.tab_id.match(/^tab_sec_(\d+)$/)
    if (m) return `T${parseInt(m[1], 10)}`
    return tab.tab_id
  }

  const sectionTabs = tabs.filter(
    (t) => t.type === "section" && t.md_file_path && t.name.toLowerCase() !== "other",
  )

  const getTabContent = (tabId: string): string => {
    if (tabId === "tab_general") return tabMdContents["tab_general"] ?? meeting.detail ?? ""
    return tabMdContents[tabId] ?? ""
  }

  const selectedTab = tabs.find((t) => t.tab_id === selectedSummaryId)
  const isGeneral = selectedSummaryId === "tab_general"

  // ── Aggregate todos from all section tabs ────────────────────
  interface AggregatedTodo {
    assignee: string
    task: string
    priority: string
    section: string
    rawLine: string
  }
  const aggregatedTodos: AggregatedTodo[] = useMemo(() => {
    const results: AggregatedTodo[] = []
    const seen = new Set<string>()
    const todoRe = /^[-*+]\s+(.+?)\s+\[priority:\s*(high|medium|low)\s*\]/i
    const spkLeadingRe = /^\[spk:(\d+)\]\s+/
    for (const tab of tabs) {
      if (tab.type !== "section" || !tab.md_file_path) continue
      const md = tabMdContents[tab.tab_id]
      if (!md) continue
      for (const line of md.split("\n")) {
        const trimmed = line.trim()
        const m = todoRe.exec(trimmed)
        if (!m) continue
        const rawBody = m[1].trim()
        const priority = m[2].toLowerCase()
        let assignee = ""
        let task = rawBody
        const spkMatch = spkLeadingRe.exec(rawBody)
        if (spkMatch) {
          assignee = speakerNames[spkMatch[1]] ?? `Speaker ${spkMatch[1]}`
          task = rawBody.slice(spkMatch[0].length).trim()
        } else {
          const nameMatch = /^(\S{1,8})\s+(.+)$/u.exec(rawBody)
          if (nameMatch) {
            assignee = nameMatch[1]
            task = nameMatch[2]
          } else {
            assignee = "?"
          }
        }
        task = task.replace(
          /\[spk:(\d+)\]/g,
          (_, id: string) => speakerNames[id] ?? `Speaker ${id}`,
        )
        task = task.replace(
          /\[spk:\?\]\s*(?:\([^)]*\b(likely|possibly|maybe|probably)\s+(\w+)\))?/gi,
          (_, _hint?: string, name?: string) => name ?? "?",
        )
        const sig = `${assignee}|${task.slice(0, 60)}`
        if (seen.has(sig)) continue
        seen.add(sig)
        results.push({ assignee, task, priority, section: tab.name, rawLine: line })
      }
    }
    return results
  }, [tabs, tabMdContents, speakerNames])

  return (
    <div className="flex-1 flex flex-col min-h-0">

      {/* ── Tab bar: Summary | Notes (fixed width) ── */}
      <div className="flex items-center border-b border-border px-2 shrink-0">
        <div className="flex items-center">
          <button
            className={cn(
              "w-24 h-9 text-xs font-light uppercase tracking-wider border-b-2 transition-colors",
              mainTab === "summary"
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
            onClick={() => setMainTab("summary")}
          >
            Summary
          </button>
          <button
            className={cn(
              "w-24 h-9 text-xs font-light uppercase tracking-wider border-b-2 transition-colors",
              mainTab === "notes"
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
            onClick={() => setMainTab("notes")}
          >
            Notes
          </button>
        </div>
        {busy && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground ml-2" />}
      </div>

      {/* ── Summary Tab ── */}
      <div className={cn("flex-1 flex min-h-0", mainTab !== "summary" && "hidden")}>
        {/* Secondary vertical menu */}
        <div className="w-48 border-r border-border overflow-y-auto shrink-0 py-2 space-y-0.5">
          {hasBlueprint && (
            <>
              <button
                className={cn(
                  "w-full text-left px-3 py-1.5 text-xs hover:bg-accent transition-colors truncate",
                  isGeneral && "bg-accent font-medium",
                )}
                onClick={() => setSelectedSummaryId("tab_general")}
              >
                General
              </button>
              {sectionTabs.map((tab) => (
                <button
                  key={tab.tab_id}
                  className={cn(
                    "w-full text-left px-3 py-1.5 text-xs hover:bg-accent transition-colors truncate",
                    selectedSummaryId === tab.tab_id && "bg-accent font-medium",
                  )}
                  onClick={() => setSelectedSummaryId(tab.tab_id)}
                  title={tab.name}
                >
                  {tabShortLabel(tab)}: {tab.name}
                </button>
              ))}
            </>
          )}

          {/* Breakdown button */}
          {hasBlueprint && !hasSections && !busy && (
            <div className="px-2 pt-2">
              <Button variant="outline" size="sm" className="w-full text-xs" onClick={handleBreakdown}>
                Breakdown
              </Button>
            </div>
          )}

          {/* Add Section button (last item in secondary menu) */}
          {hasBlueprint && !busy && (
            <div className="px-2 pt-2">
              <Button variant="ghost" size="sm" className="w-full justify-start text-xs" onClick={() => setExtractOpen(true)}>
                <Plus className="h-3 w-3 mr-1" /> Add Section
              </Button>
            </div>
          )}
        </div>

        {/* Content area */}
        <div className="flex-1 min-h-0 overflow-auto">
          {!hasBlueprint ? (
            <div className="flex items-center justify-center h-full">
              {hasTranscript && !busy ? (
                <Button variant="outline" size="sm" onClick={handleSummarize}>
                  <Sparkles className="h-3.5 w-3.5 mr-1.5" /> Summarize
                </Button>
              ) : (
                <p className="text-muted-foreground text-sm">No content yet.</p>
              )}
            </div>
          ) : loadingTabs.has(selectedSummaryId) ? (
            <div className="flex items-center gap-2 text-muted-foreground text-sm py-8 justify-center">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading...
            </div>
          ) : (
            <>
              <EditableSectionContent
                content={getTabContent(selectedSummaryId)}
                onSave={async (draft) => handleSaveSection(selectedSummaryId, draft)}
                onRefClick={handleRefClick}
                speakerNames={speakerNames}
                title={
                  isGeneral
                    ? "General"
                    : selectedTab
                      ? `${tabShortLabel(selectedTab)}: ${selectedTab.name}`
                      : ""
                }
                actionButtons={
                  <>
                    {isGeneral ? (
                      <Button
                        variant="ghost" size="icon" className="h-7 w-7"
                        onClick={() => setReSummarizeOpen(true)}
                        title="Re-summarize" disabled={busy}
                      >
                        <RefreshCw className="h-3.5 w-3.5" />
                      </Button>
                    ) : (
                      <>
                        <Button
                          variant="ghost" size="icon" className="h-7 w-7"
                          onClick={() => handleRegenerate(selectedSummaryId)}
                          title="Regenerate" disabled={busy}
                        >
                          <RefreshCw className={cn("h-3.5 w-3.5", busy && "animate-spin")} />
                        </Button>
                        <Button
                          variant="ghost" size="icon" className="h-7 w-7 hover:text-destructive"
                          onClick={() => handleDeleteSection(selectedSummaryId)}
                          title="Delete section" disabled={busy}
                        >
                          <X className="h-3.5 w-3.5" />
                        </Button>
                      </>
                    )}
                  </>
                }
                metadata={
                  !isGeneral && selectedTab ? (
                    <SectionMetadata
                      tab={selectedTab}
                      blueprint={blueprint}
                      meetingId={meetingId}
                      onMeetingUpdate={onMeetingUpdate}
                    />
                  ) : undefined
                }
              />
              {/* Aggregated todos — only on General tab */}
              {isGeneral && hasSections && aggregatedTodos.length > 0 && (
                <div className="px-3 pb-4 mt-4 pt-3 border-t">
                  <h3 className="text-sm font-medium mb-2">Action Items</h3>
                  {(() => {
                    const bySection = new Map<string, AggregatedTodo[]>()
                    for (const t of aggregatedTodos) {
                      const list = bySection.get(t.section) || []
                      list.push(t)
                      bySection.set(t.section, list)
                    }
                    return Array.from(bySection).map(([section, todos]) => (
                      <div key={section} className="mb-2">
                        <h4 className="text-xs text-muted-foreground mb-1">{section}</h4>
                        <ul className="space-y-0.5 ml-4">
                          {todos.map((todo, i) => (
                            <li key={i} className="text-sm">
                              <strong>{todo.assignee}</strong>: {todo.task}{" "}
                              <em className="text-muted-foreground text-xs">({todo.priority})</em>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))
                  })()}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* ── Notes Tab ── */}
      <div className={cn("flex-1 min-h-0 flex flex-col", mainTab !== "notes" && "hidden")}>
        <div className="flex items-center px-3 pt-2 pb-1 shrink-0">
          <span className="text-xs text-muted-foreground uppercase tracking-wider">Notes</span>
        </div>
        <div className="flex-1 min-h-0 overflow-auto px-2 pb-2">
          <MarkdownEditor
            value={notesDraft}
            onChange={handleNotesChange}
            minHeight="250px"
            placeholder="Write your meeting notes here (Markdown supported)..."
          />
        </div>
      </div>

      {/* Magic Extract Dialog */}
      <Dialog open={extractOpen} onOpenChange={setExtractOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Magic Extract</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Define one or more topics to extract from this meeting. Each topic becomes a new section tab.
          </p>
          <div className="space-y-3 mt-2">
            {extractTopics.map((topic, idx) => (
              <div key={idx} className="flex gap-2 items-start">
                <div className="flex-1 space-y-1.5">
                  <input
                    className="w-full border border-border rounded px-2 py-1.5 text-sm bg-background"
                    placeholder="Topic name"
                    value={topic.name}
                    onChange={(e) => {
                      const next = [...extractTopics]
                      next[idx] = { ...next[idx], name: e.target.value }
                      setExtractTopics(next)
                    }}
                  />
                  <input
                    className="w-full border border-border rounded px-2 py-1.5 text-sm bg-background"
                    placeholder="Description (optional)"
                    value={topic.description}
                    onChange={(e) => {
                      const next = [...extractTopics]
                      next[idx] = { ...next[idx], description: e.target.value }
                      setExtractTopics(next)
                    }}
                  />
                </div>
                {extractTopics.length > 1 && (
                  <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0" onClick={() => setExtractTopics((prev) => prev.filter((_, i) => i !== idx))}>
                    <X className="h-4 w-4" />
                  </Button>
                )}
              </div>
            ))}
          </div>
          <Button variant="outline" size="sm" className="mt-2" onClick={() => setExtractTopics((prev) => [...prev, { name: "", description: "" }])}>
            + Add another topic
          </Button>
          <div className="flex justify-end gap-2 pt-4">
            <Button variant="outline" onClick={() => setExtractOpen(false)}>Cancel</Button>
            <Button onClick={handleMagicExtract}>Extract</Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Re-summarize Confirmation Dialog */}
      <Dialog open={reSummarizeOpen} onOpenChange={setReSummarizeOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Re-summarize Meeting</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Re-summarizing will overwrite the existing General summary and section breakdown. Continue?
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setReSummarizeOpen(false)}>Cancel</Button>
            <Button onClick={handleReSummarize}>Re-summarize</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
