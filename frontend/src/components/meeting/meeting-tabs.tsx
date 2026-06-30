import { useState, useEffect, useCallback, useMemo, useRef, type ReactNode } from "react"
import { createPortal } from "react-dom"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { cn } from "@/lib/utils"
import { Loader2, X, RefreshCw, Plus, Pencil, Sparkles, ChevronDown, Check } from "lucide-react"
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
import { TranscriptTab, SpeakersTab } from "./transcript-panel"

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
  partialText?: string
  focusRef?: { id: string; ts: number } | null
  activeSectionTag?: string
  floatingPanelOpen?: boolean
  canShift?: boolean
  floatingPanelSlot?: ReactNode
  className?: string
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
  toolbar,
  actionsDisabled,
}: {
  content: string
  onSave: (updated: string) => Promise<void>
  onRefClick: (id: string) => void
  speakerNames: Record<string, string>
  actionButtons?: ReactNode
  title?: ReactNode
  metadata?: ReactNode
  toolbar?: ReactNode
  actionsDisabled?: boolean
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
      {/* Sticky top bar — title left, action buttons right (only when content exists) */}
      {(title || actionButtons) && (
      <div className="sticky top-0 z-10 flex items-center justify-between px-6 py-[14px] -mx-2 bg-background/80 backdrop-blur-sm">
        <div
          className="min-w-0 truncate"
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: "clamp(20px, 2vw, 24px)",
            fontWeight: 400,
            letterSpacing: "-0.01em",
            lineHeight: 1.35,
            color: "var(--ze-ink)",
          }}
        >
          {title}
        </div>
        <div className={cn(
          "flex items-center gap-1 shrink-0 ml-2 transition-all duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]",
          actionsDisabled
            ? "opacity-0 scale-90 pointer-events-none"
            : "opacity-100 scale-100",
        )}>
          {actionButtons}
        </div>
      </div>
      )}

      {/* Metadata slot (between title bar and divider) */}
      {metadata}

      {/* Toolbar slot (own row above the divider) */}
      {toolbar}

      {/* Divider line + edit button — between toolbar and content */}
      <div className="flex items-center justify-between px-6 pt-3">
        <div className="flex-1 h-px bg-border" />
        {!editing && !actionsDisabled && (
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 ml-2 shrink-0"
            onClick={() => setEditing(true)}
            title="Edit"
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>

      {/* Content area */}
      <div className="px-6 pb-4 pt-4">
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
  tabs,
  meetingId,
  onMeetingUpdate,
  onIngestingChange,
}: {
  tab: MeetingTab
  blueprint: Meeting["blueprint"]
  tabs: MeetingTab[]
  meetingId: string
  onMeetingUpdate: (m: Meeting) => void
  onIngestingChange?: (v: boolean) => void
}) {
  const bpEntry = (blueprint ?? []).find((b) => b.tab_id === tab.tab_id)
  const description = bpEntry?.section_description ?? ""
  const sectionDisplayName = bpEntry?.tab_name || tab.name
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
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [switchTarget, setSwitchTarget] = useState<string | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const dropdownContentRef = useRef<HTMLDivElement>(null)
  const { collections, fetchCollections } = useAppStore()
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState(tab.name)
  const [pickerIngesting, setPickerIngesting] = useState(false)

  // Inline editing for section name + description
  const [editingMeta, setEditingMeta] = useState(false)
  const [nameDraft, setNameDraft] = useState(sectionDisplayName)
  const [descDraft, setDescDraft] = useState(description)
  const [savingMeta, setSavingMeta] = useState(false)
  const savingRef = useRef(false)  // sync guard: prevents double-save from blur + click

  // Sync drafts when tab changes
  useEffect(() => {
    setNameDraft(sectionDisplayName)
    setDescDraft(description)
    setEditingMeta(false)
  }, [tab.tab_id, sectionDisplayName, description])

  const commitMeta = async () => {
    if (!editingMeta) return
    if (savingRef.current) return
    if (nameDraft === sectionDisplayName && descDraft === description) {
      setEditingMeta(false)
      return
    }
    savingRef.current = true
    setSavingMeta(true)
    try {
      const bp = blueprint ?? []
      const m = await updateMeeting(meetingId, {
        blueprint: bp.map((b) => {
          if (b.tab_id === tab.tab_id) {
            return { ...b, tab_name: nameDraft, section_description: descDraft }
          }
          return b
        }),
        tabs: (tabs ?? []).map((t) => {
          if (t.tab_id === tab.tab_id) {
            return { ...t, name: nameDraft }
          }
          return t
        }),
      } as any)
      setEditingMeta(false)
      onMeetingUpdate(m)
    } catch (err) {
      toast.error(`Save failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      savingRef.current = false
      setSavingMeta(false)
    }
  }

  // Dropdown click-outside (portal-based, check both container & dropdown)
  useEffect(() => {
    if (!dropdownOpen) return
    const handler = (e: MouseEvent) => {
      if (
        menuRef.current && !menuRef.current.contains(e.target as Node) &&
        dropdownContentRef.current && !dropdownContentRef.current.contains(e.target as Node)
      ) {
        setDropdownOpen(false)
        setCreating(false)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [dropdownOpen])

  // Fetch collections when dropdown opens
  useEffect(() => {
    if (dropdownOpen) {
      fetchCollections()
      setNewName(tab.name)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dropdownOpen, tab.name])

  const handleSelectCollection = (colId: string) => {
    // If already ingested to a different collection, confirm before switching
    if (ingested && colId !== associatedId) {
      setSwitchTarget(colId)
      return
    }
    doIngest(colId)
  }

  const doIngest = async (colId: string) => {
    setDropdownOpen(false)
    setPickerIngesting(true)
    setSwitchTarget(null)
    try {
      // Delete old ingestion if switching
      if (ingested && colId !== associatedId) {
        await deleteSectionAllocation(meetingId, tab.tab_id)
      }
      await handleIngest(colId)
      setDropdownOpen(false)
      setCreating(false)
      fetchCollections()
    } catch { /* error handled in parent */ }
    setPickerIngesting(false)
  }

  const handleCreateAndSelect = async () => {
    if (!newName.trim() || pickerIngesting) return
    // If switching, confirm first
    if (ingested) {
      setSwitchTarget("__new__")
      return
    }
    doCreateAndIngest()
  }

  const doCreateAndIngest = async () => {
    setDropdownOpen(false)
    setPickerIngesting(true)
    setSwitchTarget(null)
    try {
      // Delete old ingestion if switching
      if (ingested) {
        await deleteSectionAllocation(meetingId, tab.tab_id)
      }
      const res = await createCollection(newName.trim())
      if (res.error) throw new Error(res.error)
      const colId = res.id
      if (!colId) throw new Error("No collection ID returned")
      await handleIngest(colId)
      await fetchCollections()
      setDropdownOpen(false)
      setCreating(false)
      toast.success(`Created "${newName.trim()}" and ingested`)
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    }
    setPickerIngesting(false)
  }

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

  useEffect(() => {
    onIngestingChange?.(ingesting)
  }, [ingesting, onIngestingChange])

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

  const tabLabel = (() => {
    const m = tab.tab_id.match(/^tab_sec_(\d+)$/)
    return m ? `(Topic ${parseInt(m[1], 10)})` : tab.tab_id
  })()

  return (
    <div ref={containerRef} className="px-6 py-3 pb-4 flex gap-4 group relative">
      {/* Left column: section title + description */}
      <div className="flex-1 min-w-0 flex flex-col gap-1 relative">
        {/* Edit button — appears on hover at top-right of left column */}
        {!editingMeta && (
          <button
            className="absolute top-0 -right-1 z-10 opacity-0 group-hover:opacity-100 transition-opacity duration-200 h-7 w-7 flex items-center justify-center rounded-sm text-muted-foreground hover:text-foreground hover:bg-accent"
            onClick={() => setEditingMeta(true)}
            title="Edit section"
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
        )}
        {editingMeta ? (
          <>
            <div className="flex items-center gap-0">
              <span
                style={{
                  fontFamily: "var(--font-serif)",
                  fontSize: "clamp(20px, 2vw, 24px)",
                  fontWeight: 400,
                  letterSpacing: "-0.01em",
                  lineHeight: 1.35,
                  color: "var(--ze-ink)",
                }}
              >
                {tabLabel}{" "}
              </span>
              <input
                className="flex-1 text-current bg-transparent border-b border-primary outline-none px-0 py-0.5 min-w-0"
                style={{
                  fontFamily: "var(--font-serif)",
                  fontSize: "clamp(20px, 2vw, 24px)",
                  fontWeight: 400,
                  letterSpacing: "-0.01em",
                  lineHeight: 1.35,
                  color: "var(--ze-ink)",
                }}
                value={nameDraft}
                onChange={(e) => setNameDraft(e.target.value)}
                onBlur={commitMeta}
                onKeyDown={(e) => { if (e.key === "Enter") commitMeta() }}
                autoFocus
              />
            </div>
            <div className="flex items-start gap-2">
              <input
                className="text-xs text-muted-foreground bg-transparent border-b border-border outline-none px-0 py-0.5 flex-1"
                placeholder="Section description..."
                value={descDraft}
                onChange={(e) => setDescDraft(e.target.value)}
                onBlur={commitMeta}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); commitMeta() } }}
              />
              <button
                className="shrink-0 h-6 w-6 flex items-center justify-center rounded-sm text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                onClick={commitMeta}
                disabled={savingMeta}
                title="Done"
              >
                <Check className="h-3.5 w-3.5" />
              </button>
            </div>
          </>
        ) : (
          <>
            <div
              className="min-w-0 truncate"
              style={{
                fontFamily: "var(--font-serif)",
                fontSize: "clamp(20px, 2vw, 24px)",
                fontWeight: 400,
                letterSpacing: "-0.01em",
                lineHeight: 1.35,
                color: "var(--ze-ink)",
              }}
            >
              {tabLabel} {sectionDisplayName}
            </div>
            {description && (
              <p className="text-xs text-muted-foreground leading-relaxed">{description}</p>
            )}
          </>
        )}
      </div>

      {/* Right column: collection buttons */}
      <div className={cn("shrink-0 flex flex-col gap-1.5 items-end", BUTTON_W)} ref={menuRef}>
        {(hasAssociated || ingested) && (
          <button
            type="button"
            disabled={ingesting}
            onClick={displayActive ? () => setCancelOpen(true) : () => handleIngest(associatedId)}
            className={cn(
              "group relative flex items-center justify-center overflow-hidden rounded px-3 py-2 font-sans transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] w-full",
              ingesting && "sk-thinking-flow",
            )}
            style={{
              fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase",
              color: ingesting
                ? "var(--color-primary)"
                : displayActive
                  ? "var(--color-primary)"
                  : "var(--color-muted-foreground)",
            }}
          >
            <span className="relative z-10 whitespace-nowrap">
              {ingesting ? "Ingesting..." : displayName || associatedName}
            </span>
            <span
              className={cn(
                "absolute inset-0 z-0 transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)]",
                ingesting ? "bg-green-wash animate-pulse" : "bg-primary/10",
              )}
              style={{
                transform: displayActive || ingesting ? "scaleX(1)" : "scaleX(0)",
                transformOrigin: "left",
              }}
            />
          </button>
        )}
        <button
          type="button"
          ref={buttonRef}
          disabled={ingesting || pickerIngesting}
          onClick={() => setDropdownOpen(!dropdownOpen)}
          className={cn(
            "group relative flex items-center justify-center overflow-hidden rounded px-3 py-2 font-sans transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] w-full",
            (ingesting || pickerIngesting) && "sk-thinking-flow",
          )}
          style={{
            fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase",
            color: (ingesting || pickerIngesting)
              ? "var(--color-primary)"
              : dropdownOpen
                ? "var(--color-primary-foreground)"
                : "var(--color-muted-foreground)",
          }}
        >
          <span className="relative z-10 whitespace-nowrap text-center">
            {(ingesting || pickerIngesting) ? "Ingesting..." : dropdownOpen ? "Cancel" : "Choose a collection"}
          </span>
          <span
            className="absolute inset-0 z-0 transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] bg-primary"
            style={{
              transform: dropdownOpen ? "scaleX(1)" : "scaleX(0)",
              transformOrigin: dropdownOpen ? "right" : "left",
            }}
          />
        </button>
        {createPortal(
          <div
            ref={dropdownContentRef}
            className={`fixed z-[100] flex-col items-center overflow-hidden rounded border border-primary/30 bg-popover/60 backdrop-blur-md shadow-lg transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] ${
              dropdownOpen
                ? "opacity-100 visible translate-y-0 pointer-events-auto"
                : "opacity-0 invisible translate-y-3 pointer-events-none"
            }`}
            style={{
              width: buttonRef.current ? buttonRef.current.getBoundingClientRect().width : "auto",
              top: menuRef.current ? menuRef.current.getBoundingClientRect().bottom + 4 : 0,
              left: menuRef.current ? menuRef.current.getBoundingClientRect().left : 0,
            }}
          >
            {collections.length === 0 && (
              <div className="px-2 py-3 text-[10px] text-muted-foreground text-center">No collections yet</div>
            )}
            {collections.map((col) => (
              <label
                key={col.id}
                onClick={() => handleSelectCollection(col.id)}
                className={`relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group ${
                  pickerIngesting ? "pointer-events-none opacity-50" : ""
                }`}
              >
                <span className="relative z-10 flex items-center gap-2 px-2 py-2 w-full text-[10px]">
                  <span className={`sk-diamond ${col.id === associatedId ? "on" : ""}`} aria-hidden />
                  <span className="whitespace-normal break-words min-w-0 leading-snug">{col.name}</span>
                </span>
                <span className="absolute inset-0 z-0 bg-primary transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
              </label>
            ))}
            <div className="border-t border-primary/20 w-full">
              {!creating ? (
                <label
                  onClick={() => setCreating(true)}
                  className={`relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group ${
                    pickerIngesting ? "pointer-events-none opacity-50" : ""
                  }`}
                >
                  <span className="relative z-10 flex items-center gap-2 px-2 py-2 w-full text-[10px]">
                    <Plus className="h-3 w-3 shrink-0" />
                    <span>{hasAssociated ? "Create new collection" : `+ ${tab.name}`}</span>
                  </span>
                  <span className="absolute inset-0 z-0 bg-primary transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
                </label>
              ) : (
                <div className="px-2 py-2 flex items-center gap-1.5">
                  <input
                    className="flex-1 border border-border rounded px-2 py-1 text-[10px] bg-background"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder="Collection name"
                    autoFocus
                    onKeyDown={(e) => { if (e.key === "Enter") handleCreateAndSelect() }}
                    onClick={(e) => e.stopPropagation()}
                  />
                  <button
                    className="shrink-0 text-[10px] font-medium uppercase tracking-[0.1em] px-2 py-1 rounded bg-primary text-primary-foreground hover:opacity-80 disabled:opacity-50"
                    onClick={handleCreateAndSelect}
                    disabled={!newName.trim() || pickerIngesting}
                  >
                    Create
                  </button>
                </div>
              )}
            </div>
          </div>,
          document.body
        )}
      </div>

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

      {/* Switch Ingestion Confirm Dialog */}
      <Dialog open={!!switchTarget} onOpenChange={(v) => { if (!v) setSwitchTarget(null) }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Switch Collection</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            This section is already ingested to <span className="font-medium text-foreground">"{associatedName}"</span>.
            {switchTarget === "__new__" ? (
              <>Creating a new collection will delete the existing file snapshot and re-ingest.</>
            ) : (
              <>Switching to <span className="font-medium text-foreground">"{collections.find(c => c.id === switchTarget)?.name || switchTarget}"</span> will delete the existing file snapshot and re-ingest.</>
            )}
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setSwitchTarget(null)}>Cancel</Button>
            <Button variant="destructive" onClick={() => {
              if (switchTarget === "__new__") doCreateAndIngest()
              else if (switchTarget) doIngest(switchTarget)
            }}>Switch</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────

export function MeetingTabs({
  meetingId, meeting, notesContent,
  onMeetingUpdate, onSeekTo, onFocusSentence, onActiveTabChange, transcriptSegments,
  partialText,
  focusRef,
  activeSectionTag,
  floatingPanelOpen,
  canShift = true,
  floatingPanelSlot,
  className,
}: Props) {
  const tabs = meeting.tabs ?? []
  const blueprint = meeting.blueprint ?? []
  const speakerNames: Record<string, string> = meeting.speaker_names ?? {}

  const hasSummary = !!(meeting.detail || tabs.some(t => t.type === "section" && t.md_file_path))
  const [mainTab, setMainTab] = useState(hasSummary ? "summary" : "notes")
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
  const [regenerateConfirmOpen, setRegenerateConfirmOpen] = useState(false)
  const [summaryHoverOpen, setSummaryHoverOpen] = useState(false)
  const summaryHoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const summaryBarRef = useRef<HTMLDivElement>(null)
  const tabContainerRef = useRef<HTMLDivElement>(null)
  const summaryBtnRef = useRef<HTMLButtonElement>(null)
  const notesBtnRef = useRef<HTMLButtonElement>(null)
  const transcriptBtnRef = useRef<HTMLButtonElement>(null)
  const speakerBtnRef = useRef<HTMLButtonElement>(null)
  const [tabIndicator, setTabIndicator] = useState({ left: 0, width: 0 })
  const [sectionIngesting, setSectionIngesting] = useState(false)

  useEffect(() => {
    const container = tabContainerRef.current
    const btn =
      mainTab === "summary" ? summaryBtnRef.current :
      mainTab === "notes" ? notesBtnRef.current :
      mainTab === "transcript" ? transcriptBtnRef.current :
      speakerBtnRef.current
    if (!container || !btn) return
    const containerRect = container.getBoundingClientRect()
    const btnRect = btn.getBoundingClientRect()
    setTabIndicator({
      left: btnRect.left - containerRect.left,
      width: btnRect.width,
    })
  }, [mainTab])

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

  // If processing finished while tab was in background (poll killed), sync busy off.
  useEffect(() => {
    if (!meeting.processing_state || meeting.processing_state === "idle") {
      setBusy(false)
    }
  }, [meeting.processing_state])

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
      // If this section has an ingested file, delete the allocation first
      const targetTab = tabs.find(t => t.tab_id === tabId)
      if ((targetTab as any)?.allocated_file_id) {
        await deleteSectionAllocation(meetingId, tabId)
      }
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
    <div className={cn("flex-1 flex flex-col min-h-0", className)}>

      {/* ── Tab bar: extends right when floating panel opens (wide mode only) ── */}
      <div
        ref={summaryBarRef}
        className={cn(
          "relative flex items-center border-b border-border px-2 shrink-0 transition-[margin-right] duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]",
          floatingPanelOpen && canShift ? "-mr-[320px]" : "mr-0",
        )}
      >
        <div ref={tabContainerRef} className="flex items-center relative">
          <button
            ref={summaryBtnRef}
            className={cn(
              "flex items-center gap-1 w-24 h-9 text-xs font-light uppercase tracking-wider transition-colors duration-300",
              mainTab === "summary"
                ? "text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
            onClick={() => setMainTab("summary")}
            onMouseEnter={() => {
              if (summaryHoverTimer.current) { clearTimeout(summaryHoverTimer.current); summaryHoverTimer.current = null }
              if (hasBlueprint) setSummaryHoverOpen(true)
            }}
            onMouseLeave={() => {
              summaryHoverTimer.current = setTimeout(() => setSummaryHoverOpen(false), 150)
            }}
          >
            Summary
            {hasBlueprint && (
              <ChevronDown className={cn("h-3 w-3 transition-transform duration-200", summaryHoverOpen && "rotate-180")} />
            )}
          </button>
          <button
            ref={notesBtnRef}
            className={cn(
              "h-9 px-3 text-xs font-light uppercase tracking-wider transition-colors duration-300",
              mainTab === "summary" ? "w-24" : "",
              mainTab === "notes"
                ? "text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
            onClick={() => setMainTab("notes")}
          >
            Notes
          </button>
          <button
            ref={transcriptBtnRef}
            className={cn(
              "h-9 px-3 text-xs font-light uppercase tracking-wider transition-colors duration-300",
              mainTab === "transcript"
                ? "text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
            onClick={() => setMainTab("transcript")}
          >
            Transcript
          </button>
          <button
            ref={speakerBtnRef}
            className={cn(
              "h-9 px-3 text-xs font-light uppercase tracking-wider transition-colors duration-300",
              mainTab === "speaker"
                ? "text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
            onClick={() => setMainTab("speaker")}
          >
            Speaker
          </button>
          {/* Sliding green underline */}
          <div
            className="absolute bottom-0 h-[2px] bg-primary pointer-events-none transition-all duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]"
            style={{ left: tabIndicator.left, width: tabIndicator.width }}
          />
        </div>
        {busy && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground ml-2" />}

      {/* Hover dropdown — section picker below the Summary tab */}
        <div
          className={cn(
            "absolute z-50 top-full left-2 mt-0 w-56 overflow-hidden rounded border border-primary/30 bg-popover/60 backdrop-blur-md shadow-lg transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)]",
            summaryHoverOpen
              ? "opacity-100 visible translate-y-0 pointer-events-auto"
              : "opacity-0 invisible -translate-y-3 pointer-events-none",
          )}
          onMouseEnter={() => {
            if (summaryHoverTimer.current) { clearTimeout(summaryHoverTimer.current); summaryHoverTimer.current = null }
            setSummaryHoverOpen(true)
          }}
          onMouseLeave={() => {
            summaryHoverTimer.current = setTimeout(() => setSummaryHoverOpen(false), 150)
          }}
        >
          <button
            onClick={() => { setSelectedSummaryId("tab_general"); setMainTab("summary") }}
            className="relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group"
          >
            <span className="relative z-10 flex items-center gap-2 px-2 py-2 w-full text-[10px]">
              <span className={cn("sk-diamond", isGeneral && "on")} aria-hidden />
              <span className="whitespace-normal break-words min-w-0 leading-snug">General</span>
            </span>
            <span className="absolute inset-0 z-0 bg-primary transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
          </button>
          {sectionTabs.map((tab) => (
            <button
              key={tab.tab_id}
              onClick={() => { setSelectedSummaryId(tab.tab_id); setMainTab("summary") }}
              title={tab.name}
              className="relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group"
            >
              <span className="relative z-10 flex items-center gap-2 px-2 py-2 w-full text-[10px]">
                <span className={cn("sk-diamond", selectedSummaryId === tab.tab_id && "on")} aria-hidden />
                <span className="whitespace-normal break-words min-w-0 leading-snug">{tabShortLabel(tab)}: {(blueprint as any[]).find((b: any) => b.tab_id === tab.tab_id)?.tab_name || tab.name}</span>
              </span>
              <span className="absolute inset-0 z-0 bg-primary transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
            </button>
          ))}

          {/* Breakdown button */}
          {!hasSections && !busy && (
            <div className="border-t border-primary/20 w-full">
              <button
                onClick={() => { setSummaryHoverOpen(false); handleBreakdown() }}
                className="relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group"
              >
                <span className="relative z-10 flex items-center gap-2 px-2 py-2 w-full text-[10px]">
                  <Sparkles className="h-3 w-3 shrink-0" />
                  <span>Breakdown</span>
                </span>
                <span className="absolute inset-0 z-0 bg-primary transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
              </button>
            </div>
          )}

          {/* Add Section */}
          {!busy && (
            <div className="border-t border-primary/20 w-full">
              <button
                onClick={() => { setSummaryHoverOpen(false); setExtractOpen(true) }}
                className="relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group"
              >
                <span className="relative z-10 flex items-center gap-2 px-2 py-2 w-full text-[10px]">
                  <Plus className="h-3 w-3 shrink-0" />
                  <span>Add Section</span>
                </span>
                <span className="absolute inset-0 z-0 bg-primary transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
              </button>
            </div>
          )}
        </div>
      </div>

      {floatingPanelSlot}
      {/* ── Summary Tab ── */}
      <div className={cn(
        "transition-opacity duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]",
        mainTab === "summary"
          ? "flex-1 flex flex-col min-h-0 opacity-100"
          : "opacity-0 invisible absolute inset-0 pointer-events-none",
      )}>
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
                actionsDisabled={sectionIngesting}
                title={
                  isGeneral
                    ? "General"
                    : ""
                }
                actionButtons={null}
                toolbar={
                  isGeneral ? (
                    <div className="flex items-center gap-2 px-6 pb-2">
                      <button
                        type="button"
                        disabled={busy || sectionIngesting}
                        onClick={() => setReSummarizeOpen(true)}
                        title="Re-summarize"
                        className={cn(
                          "inline-flex items-center justify-center gap-2 rounded-md h-8 px-4 text-[11px] font-semibold tracking-[0.1em] uppercase flex-1 select-none transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)]",
                          busy
                            ? "sk-thinking-flow text-[var(--ze-green)]"
                            : "sk-send-btn",
                        )}
                      >
                        <span className="relative z-10 flex items-center gap-2">
                          {busy ? (
                            <>
                              <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--ze-green)]" />
                              Summarizing...
                            </>
                          ) : (
                            <>
                              <RefreshCw className="h-3.5 w-3.5" />
                              RE-SUMMARIZE
                            </>
                          )}
                        </span>
                      </button>
                    </div>
                  ) : (() => {
                        const generating = busy && !isGeneral
                        return (
                          <div className="flex items-center gap-2 px-6 pb-2">
                            {/* Regenerate — AI-COMP-001 SEND idle; AI-COMP-120 flow border when generating */}
                            <button
                              type="button"
                              disabled={busy || sectionIngesting}
                              onClick={() => {
                                // Check if section has ingested file — warn user it will be deleted
                                const selectedTab = tabs.find(t => t.tab_id === selectedSummaryId)
                                if ((selectedTab as any)?.allocated_file_id) {
                                  setRegenerateConfirmOpen(true)
                                } else {
                                  handleRegenerate(selectedSummaryId)
                                }
                              }}
                              title="Regenerate"
                              className={cn(
                                "inline-flex items-center justify-center gap-2 rounded-md h-8 px-4 text-[11px] font-semibold tracking-[0.1em] uppercase flex-1 select-none transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)]",
                                generating
                                  ? "sk-thinking-flow text-[var(--ze-green)]"
                                  : "sk-send-btn",
                              )}
                            >
                              <span className="relative z-10 flex items-center gap-2">
                                {generating ? (
                                  <>
                                    <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--ze-green)]" />
                                    Generating...
                                  </>
                                ) : (
                                  <>
                                    <Sparkles className="h-3.5 w-3.5" />
                                    RE-GENERATE
                                  </>
                                )}
                              </span>
                            </button>
                            {!busy && (
                              <button
                                type="button"
                                className="inline-flex items-center justify-center gap-2 rounded-md h-8 px-4 text-[11px] font-medium tracking-[0.06em] uppercase shrink-0 select-none transition-all duration-300 bg-[rgba(140,46,46,0.08)] text-[#8C2E2E] border border-[rgba(140,46,46,0.2)] hover:bg-[rgba(140,46,46,0.14)] hover:border-[rgba(140,46,46,0.4)]"
                                onClick={() => handleDeleteSection(selectedSummaryId)}
                                title="Delete section"
                                disabled={sectionIngesting}
                              >
                                DELETE
                              </button>
                            )}
                          </div>
                        )
                      })()
                }
                metadata={
                  !isGeneral && selectedTab ? (
                    <SectionMetadata
                      tab={selectedTab}
                      blueprint={blueprint}
                      tabs={tabs}
                      meetingId={meetingId}
                      onMeetingUpdate={onMeetingUpdate}
                      onIngestingChange={setSectionIngesting}
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
      <div className={cn(
        "transition-opacity duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]",
        mainTab === "notes"
          ? "flex-1 flex flex-col min-h-0 opacity-100"
          : "opacity-0 invisible absolute inset-0 pointer-events-none",
      )}>
        <div className="flex-1 min-h-0 overflow-auto">
          <MarkdownEditor
            value={notesDraft}
            onChange={handleNotesChange}
            minHeight="250px"
            placeholder="Write your meeting notes here (Markdown supported)..."
          />
        </div>
      </div>

      {/* ── Transcript Tab ── */}
      <div className={cn(
        "transition-opacity duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]",
        mainTab === "transcript"
          ? "flex-1 flex flex-col min-h-0"
          : "opacity-0 invisible absolute inset-0 pointer-events-none",
      )}>
        <div className="flex-1 min-h-0 overflow-y-auto">
          <TranscriptTab
            segments={transcriptSegments}
            partialText={partialText}
            onSegmentClick={onSeekTo}
            focusRef={focusRef}
            activeSectionTag={activeSectionTag}
            speakerNames={speakerNames}
          />
        </div>
      </div>

      {/* ── Speaker Tab ── */}
      <div className={cn(
        "transition-opacity duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]",
        mainTab === "speaker"
          ? "flex-1 flex flex-col min-h-0"
          : "opacity-0 invisible absolute inset-0 pointer-events-none",
      )}>
        <div className="flex-1 min-h-0 overflow-y-auto">
        <SpeakersTab
          segments={transcriptSegments}
          speakerNames={speakerNames}
          onUpdateSpeakerName={(id, name) => {
            const updated = { ...meeting.speaker_names, [id]: name }
            updateMeeting(meetingId, { speaker_names: updated }).then((m) => {
              onMeetingUpdate(m)
            }).catch(() => {})
          }}
          onSegmentClick={onSeekTo}
          activeSectionTag={activeSectionTag}
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

      {/* Regenerate Section Confirmation Dialog */}
      <Dialog open={regenerateConfirmOpen} onOpenChange={setRegenerateConfirmOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Regenerate Section</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Regenerating will delete the existing ingested file snapshot. The section will be re-extracted from the transcript. Continue?
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setRegenerateConfirmOpen(false)}>Cancel</Button>
            <Button
              onClick={() => {
                setRegenerateConfirmOpen(false)
                handleRegenerate(selectedSummaryId)
              }}
            >
              Regenerate
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
