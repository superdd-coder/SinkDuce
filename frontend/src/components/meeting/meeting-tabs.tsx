import { useState, useEffect, useCallback, useRef, forwardRef, useImperativeHandle, type ReactNode } from "react"
import { createPortal } from "react-dom"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { cn } from "@/lib/utils"
import { Loader2, X, RefreshCw, Plus, Pencil, Sparkles, ChevronDown } from "lucide-react"
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import {
  extract, deleteSection,
  regenerateSection, getSectionMd,
  saveSectionMd, updateMeeting, getMeeting,
  allocateSection, deleteSectionAllocation, createCollection,
  generateSectionDescription,
  type Meeting, type MeetingTab, type ExtractReceipt,
  type TranscriptSegment,
} from "@/api/client"
import { useAppStore } from "@/stores/app-store"
import { useBlueprintStream } from "@/hooks/use-blueprint-stream"
import { useSectionStream, startSectionStream } from "@/hooks/use-section-stream"
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
  playbackTime?: number
  className?: string
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
  const regex = /(\*\*(.+?)\*\*)|(\*(.+?)\*)|(`(.+?)`)|(\[(?:ref:)?\s*(stt_\d+(?:\s*,\s*stt_\d+)*)\s*\])|(【(?:ref:)?\s*(stt_\d+(?:\s*,\s*stt_\d+)*)\s*】)|(\[priority:\s*(high|medium|low)\s*\])|(【priority:\s*(high|medium|low)\s*】)/gi
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
      parts.push(<code key={`c${lastIdx}`} className="bg-muted px-1 rounded text-xs t-mono-family">{match[6]}</code>)
    } else if (match[8] || match[10]) {
      // [stt_0044,stt_0045,stt_0046] or 【stt_0044,stt_0045,stt_0046】
      const raw = (match[8] || match[10])!
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
            className="inline-flex items-center px-1 py-0 text-[10px] rounded bg-muted hover:bg-primary/20 t-mono-family align-baseline"
            onClick={(e) => { e.stopPropagation(); onRefClick(start.id) }}
            title={`Sources: ${allInRange.join(", ")}`}
          >
            {label}
          </button>,
        )
        ri = rj
      }
    } else if (match[12] || match[14]) {
      // [priority: high/medium/low] or 【priority: high/medium/low】
      const level = (match[12] || match[14])!.toLowerCase()
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

// ── Thinking skeleton (shown while LLM is generating) ─────────────

function ThinkingSkeleton() {
  return (
    <div className="sk-thinking-flow rounded-lg p-6 pt-10 space-y-4">
      {/* Title line */}
      <div className="h-6 w-1/3 rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.12)" }} />
      {/* Content lines */}
      <div className="space-y-3 pt-2">
        <div className="h-3 w-full rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.1s" }} />
        <div className="h-3 w-5/6 rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.3s" }} />
        <div className="h-3 w-4/6 rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.5s" }} />
        <div className="h-3 w-full rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.2s" }} />
        <div className="h-3 w-3/6 rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.6s" }} />
      </div>
      {/* Subtitle */}
      <div className="h-4 w-1/4 rounded animate-pulse pt-2" style={{ background: "oklch(0.38 0.08 160 / 0.1)", animationDelay: "0.4s" }} />
      <div className="space-y-3 pt-1">
        <div className="h-3 w-full rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.7s" }} />
        <div className="h-3 w-2/3 rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.9s" }} />
      </div>
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
  stickyOffset = 0,
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
  stickyOffset?: number
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
      {/* Sticky header with title (General only) */}
      {(title || actionButtons) && (
      <div className="sticky z-10 -mx-2 bg-background/80 backdrop-blur-sm" style={{ top: `${stickyOffset}px` }}>
        <div className="flex items-center justify-between px-6 py-2">
          <div
            className="min-w-0 truncate t-body-family"
            style={{
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
        <div className="flex items-center justify-between px-6 pb-1">
          <div className="flex-1 h-px bg-border" />
          {!editing && !actionsDisabled && (
            <Button variant="ghost" size="icon" className="h-7 w-7 ml-2 shrink-0" onClick={() => setEditing(true)} title="Edit">
              <Pencil className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>
      )}

      {/* Metadata slot (between title bar and divider) */}
      {metadata}

      {/* Toolbar slot (own row above the divider) */}
      {toolbar}

      {/* Section tabs: divider + edit (below metadata/toolbar, above content) */}
      {!title && !editing && !actionsDisabled && (
      <div className="flex items-center justify-between px-6 pt-3 pb-1">
        <div className="flex-1 h-px bg-border" />
        <Button variant="ghost" size="icon" className="h-7 w-7 ml-2 shrink-0" onClick={() => setEditing(true)} title="Edit">
          <Pencil className="h-3.5 w-3.5" />
        </Button>
      </div>
      )}

      {/* Content area */}
      <div className="px-6 pb-4 pt-4">
        {editing ? (
          <MarkdownEditor
            value={draft}
            onChange={setDraft}
            minHeight="250px"
            stickyToolbarOffset={stickyOffset + ((title || actionButtons) ? 53 : 0)}
            toolbarActions={
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  className="text-[11px] font-medium tracking-[0.06em] uppercase transition-colors"
                  style={{ color: "var(--color-muted-foreground)" }}
                  onMouseEnter={(e) => (e.currentTarget.style.color = "#1A5E3D")}
                  onMouseLeave={(e) => (e.currentTarget.style.color = "var(--color-muted-foreground)")}
                  onClick={() => { setDraft(content); setEditing(false) }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="inline-flex items-center justify-center h-7 px-4 text-[11px] font-semibold tracking-[0.08em] uppercase rounded-full transition-colors"
                  style={{ backgroundColor: "var(--color-primary)", color: "var(--color-primary-foreground)" }}
                  onClick={handleSave}
                  disabled={saving}
                >
                  {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : null}
                  Save
                </button>
              </div>
            }
          />
        ) : (
          <MarkdownViewer md={content} onRefClick={onRefClick} speakerNames={speakerNames} />
        )}
      </div>
    </div>
  )
}

// ── Section metadata (between title bar and content) ──────────────

const SectionMetadata = forwardRef<{ startEditing: () => void }, {
  tab: MeetingTab
  blueprint: Meeting["blueprint"]
  tabs: MeetingTab[]
  meetingId: string
  onMeetingUpdate: (m: Meeting) => void
  onIngestingChange?: (tabId: string, v: boolean) => void
  hideTitle?: boolean
}>(function SectionMetadata({
  tab,
  blueprint,
  tabs,
  meetingId,
  onMeetingUpdate,
  onIngestingChange,
  hideTitle,
}, ref) {
  const bpEntry = (blueprint ?? []).find((b) => b.blueprint_id === tab.blueprint_id)
  // Tab now carries its own description (set at extract time).
  // Fall back to blueprint for tabs created before the description field existed.
  const description = tab.description || bpEntry?.tab_description || ""
  const sectionDisplayName = tab.name || bpEntry?.tab_name || ""
  const associatedName = tab.associated_collection_name || bpEntry?.associated_collection_name || ""
  const associatedId = tab.associated_collection_id || bpEntry?.associated_collection_id || ""
  const hasAssociated = !!associatedName
  // Consider "ingested" when tab has an allocated_file_id (already persisted)
  const ingested = !!tab.allocated_file_id
  // Three-state pill (P2-02):
  //   1. ingested           → solid green pill, click to cancel
  //   2. hasSuggestion      → dashed outline pill, click to ingest
  //   3. no suggestion      → "Choose a collection" button
  const hasSuggestion = hasAssociated && !ingested
  const displayName = associatedName
  // "Active" solid style only when actually ingested; suggestion uses dashed style
  const displayActive = ingested
  const displaySuggestion = hasSuggestion

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
  // 首次 ingest：还没 associated collection 时，选中后立刻在顶部按钮显示 pending 名称
  const [pendingName, setPendingName] = useState<string | null>(null)

  const showTopButton = hasAssociated || ingested || !!pendingName
  const topButtonLabel = ingesting ? "Ingesting..." : (displayName || pendingName || associatedName)
  const topButtonIsActive = ingesting || displayActive || !!pendingName

  // Inline editing for section name + description
  const [editingMeta, setEditingMeta] = useState(false)
  const [nameDraft, setNameDraft] = useState(sectionDisplayName)
  const [descDraft, setDescDraft] = useState(description)
  const savingRef = useRef(false)  // sync guard: prevents double-save from blur + click
  const editContainerRef = useRef<HTMLDivElement>(null)

  useImperativeHandle(ref, () => ({
    startEditing: () => setEditingMeta(true),
  }))

  // Click-outside handler for inline editing
  useEffect(() => {
    if (!editingMeta) return
    const handler = (e: MouseEvent) => {
      if (editContainerRef.current && !editContainerRef.current.contains(e.target as Node)) {
        commitMeta()
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editingMeta, nameDraft, descDraft])

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
    try {
      const bp = blueprint ?? []
      const m = await updateMeeting(meetingId, {
        blueprint: bp.map((b) => {
          if (b.blueprint_id === tab.blueprint_id) {
            return { ...b, tab_name: nameDraft, tab_description: descDraft }
          }
          return b
        }),
        tabs: (tabs ?? []).map((t) => {
          if (t.tab_id === tab.tab_id) {
            return { ...t, name: nameDraft, description: descDraft, is_dirty: true }
          }
          return t
        }),
      })
      setEditingMeta(false)
      onMeetingUpdate(m)
    } catch (err) {
      toast.error(`Save failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      savingRef.current = false
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
    const colMeta = collections.find((c) => c.id === colId)
    setPendingName(colMeta?.name || colId)
    setSwitchTarget(null)
    try {
      // Delete old allocation first; fail fast — don't proceed if cleanup fails
      if (ingested && colId !== associatedId) {
        await deleteSectionAllocation(meetingId, tab.tab_id)
      }
    } catch (err) {
      toast.error(`Failed to remove old allocation: ${err instanceof Error ? err.message : String(err)}`)
      setPendingName(null)
      return
    }
    try {
      await handleIngest(colId)
      fetchCollections()
    } catch { /* error handled in parent */ }
    setPendingName(null)
  }

  const handleCreateAndSelect = async () => {
    if (!newName.trim() || !!pendingName) return
    // If switching, confirm first
    if (ingested) {
      setSwitchTarget("__new__")
      return
    }
    doCreateAndIngest()
  }

  const doCreateAndIngest = async () => {
    setDropdownOpen(false)
    setPendingName(newName.trim())
    setSwitchTarget(null)
    // Delete old allocation first; fail fast
    if (ingested) {
      try {
        await deleteSectionAllocation(meetingId, tab.tab_id)
      } catch (err) {
        toast.error(`Failed to remove old allocation: ${err instanceof Error ? err.message : String(err)}`)
        setPendingName(null)
        return
      }
    }
    try {
      const res = await createCollection(newName.trim())
      if (res.error) throw new Error(res.error)
      const colId = res.id
      if (!colId) throw new Error("No collection ID returned")
      await handleIngest(colId)
      await fetchCollections()
      setCreating(false)
      toast.success(`Created "${newName.trim()}" and ingested`)
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    }
    setPendingName(null)
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
    onIngestingChange?.(tab.tab_id, ingesting)
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
    const sections = tabs.filter(t => t.type === "section" && t.md_file_path)
    const idx = sections.findIndex(t => t.tab_id === tab.tab_id)
    return idx >= 0 ? `(Topic ${idx + 1})` : tab.tab_id
  })()

  return (
    <div ref={containerRef} className="px-6 py-3 pb-4 flex gap-4 group relative">
      {/* Left column: section title + description */}
      <div className="flex-1 min-w-0 flex flex-col gap-1 relative items-start">
        {/* Edit button — appears on hover at top-right of left column (or always visible when title is hidden) */}
        {!editingMeta && (
          <button
            className={cn(
              "h-7 w-7 flex items-center justify-center rounded-sm text-muted-foreground hover:text-foreground hover:bg-accent transition-opacity duration-200",
              hideTitle
                ? "absolute top-0 -right-1 opacity-0 group-hover:opacity-100"
                : "absolute top-0 -right-1 opacity-0 group-hover:opacity-100",
            )}
            onClick={() => setEditingMeta(true)}
            title="Edit section"
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
        )}
        {editingMeta ? (
          <div ref={editContainerRef} className="flex flex-col gap-1 w-full">
            <div className="flex items-center gap-0">
              <span
                className="t-body-family"
                style={{
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
                className="flex-1 text-current bg-transparent border-b border-primary outline-none px-0 py-0.5 min-w-0 t-body-family"
                style={{
                  fontSize: "clamp(20px, 2vw, 24px)",
                  fontWeight: 400,
                  letterSpacing: "-0.01em",
                  lineHeight: 1.35,
                  color: "var(--ze-ink)",
                }}
                value={nameDraft}
                onChange={(e) => setNameDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commitMeta() } }}
                autoFocus
              />
            </div>
            <textarea
              className="text-xs text-muted-foreground bg-transparent border-b border-border outline-none px-0 py-1 flex-1 resize-none min-h-[80px]"
              placeholder="Section description..."
              value={descDraft}
              onChange={(e) => setDescDraft(e.target.value)}
              rows={4}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); commitMeta() } }}
            />
          </div>
        ) : (
          <>
            {!hideTitle && (
            <div
              className="whitespace-normal break-words w-full t-body-family"
              style={{
                fontSize: "clamp(20px, 2vw, 24px)",
                fontWeight: 400,
                letterSpacing: "-0.01em",
                lineHeight: 1.35,
                color: "var(--ze-ink)",
                textAlign: "left",
              }}
            >
              {tabLabel} {sectionDisplayName}
            </div>
            )}
            {description && (
              <p className="text-xs text-muted-foreground leading-relaxed">{description}</p>
            )}
          </>
        )}
      </div>

      {/* Right column: collection buttons */}
      <div className={cn("shrink-0 flex flex-col gap-1.5 items-end", BUTTON_W)} ref={menuRef}>
        {showTopButton && (
          <button
            type="button"
            disabled={ingesting}
            onClick={displayActive ? () => setCancelOpen(true) : () => handleIngest(associatedId)}
            title={displayActive ? "Click to cancel ingestion" : displaySuggestion ? "Click to ingest" : undefined}
            className={cn(
              "group relative z-0 flex items-center justify-center overflow-hidden rounded px-3 py-2 t-sans-family transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] w-full",
              ingesting && "sk-thinking-flow",
              // Dashed outline for suggestion state (not yet ingested)
              displaySuggestion && !ingesting && "border border-dashed border-green-600/40",
            )}
            style={{
              fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase",
              color: topButtonIsActive ? "var(--color-primary)" : "var(--color-muted-foreground)",
            }}
          >
            <span className="relative z-10 whitespace-nowrap">
              {topButtonLabel}
            </span>
            <span
              className={cn(
                "absolute inset-0 z-0 transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)]",
                ingesting ? "bg-green-wash animate-pulse" : displayActive ? "bg-primary/10" : "",
              )}
              style={{
                transform: displayActive ? "scaleX(1)" : displaySuggestion ? "scaleX(0)" : "scaleX(0)",
                transformOrigin: "left",
              }}
            />
          </button>
        )}
        <button
          type="button"
          ref={buttonRef}
          disabled={ingesting}
          onClick={() => setDropdownOpen(!dropdownOpen)}
          className="group relative z-0 flex items-center justify-center overflow-hidden rounded px-3 py-2 t-sans-family transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] w-full"
          style={{
            fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase",
            color: dropdownOpen
              ? "var(--color-primary-foreground)"
              : "var(--color-muted-foreground)",
          }}
        >
          <span className="relative z-10 whitespace-nowrap text-center">
            {dropdownOpen ? "Cancel" : "Choose a collection"}
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
            className={`fixed z-50 flex-col items-center overflow-hidden rounded border border-primary/30 bg-popover/60 backdrop-blur-md shadow-lg transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] ${
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
                  pendingName ? "pointer-events-none opacity-50" : ""
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
                    pendingName ? "pointer-events-none opacity-50" : ""
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
                    disabled={!newName.trim() || !!pendingName}
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
})

// ── Main component ────────────────────────────────────────────────

export function MeetingTabs({
  meetingId, meeting, notesContent,
  onMeetingUpdate, onSeekTo, onFocusSentence, onActiveTabChange, transcriptSegments,
  partialText,
  focusRef,
  activeSectionTag,
  floatingPanelOpen,
  canShift = true,
  playbackTime = 0,
  className,
}: Props) {
  const tabs = meeting.tabs ?? []
  const speakerNames: Record<string, string> = meeting.speaker_names ?? {}

  // ── Blueprint SSE streaming ──────────────────────────────
  // Stable callback ref for auto-fetch when streaming completed while user was away
  const onCompletedAwayRef = useRef(onMeetingUpdate)
  onCompletedAwayRef.current = onMeetingUpdate

  const handleCompletedAway = useCallback((mid: string) => {
    getMeeting(mid).then((m) => {
      onCompletedAwayRef.current(m)
      // Reload General tab from .md file after summarization
      loadedTabsRef.current.delete("tab_general")
      loadTabContent("tab_general")
      toast.success("Summary generated")
    }).catch(() => {
      toast.error("Failed to fetch updated meeting")
    }).finally(() => {
      // Always clear streaming state so normal path can render
      bpStreamCtrl.dismissStreaming()
    })
  }, [])

  const [bpStream, bpStreamCtrl] = useBlueprintStream(meetingId, handleCompletedAway)
  // Use meeting.blueprint when available; fall back to early-completion streaming data
  const blueprint = (meeting.blueprint && meeting.blueprint.length > 0)
    ? meeting.blueprint
    : (bpStream.earlyBlueprint ?? [])

  const wasStreamingRef = useRef(false)

  // Has summary if any tab has content available (.md file or streaming)
  const hasSummary = !!(tabs.some(t => (t.type === "section" || t.tab_id === "tab_general") && t.md_file_path))
  const [mainTab, setMainTab] = useState(hasSummary ? "summary" : "notes")

  // Sync mainTab when summary content appears/disappears (e.g. after async meeting load or summarization)
  useEffect(() => {
    if (hasSummary) {
      setMainTab((prev) => prev === "notes" ? "summary" : prev)
    } else {
      setMainTab("notes")
    }
  }, [hasSummary])
  const [selectedSummaryId, setSelectedSummaryId] = useState("tab_general")
  const [tabMdContents, setTabMdContents] = useState<Record<string, string>>({})

  // ── Section SSE streaming ────────────────────────────────
  const isGeneralSelected = selectedSummaryId === "tab_general"
  const onSectionCompletedAwayRef = useRef(onMeetingUpdate)
  onSectionCompletedAwayRef.current = onMeetingUpdate

  const handleSectionCompletedAway = useCallback((mid: string, tid: string) => {
    getMeeting(mid).then((m) => {
      onSectionCompletedAwayRef.current(m)
      loadedTabsRef.current.delete(tid)
      loadTabContent(tid)
      sectionCtrlRef.current.dismiss()
    }).catch(() => {
      sectionCtrlRef.current.dismiss()
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meetingId])

  const [sectionStream, sectionStreamCtrl] = useSectionStream(
    meetingId,
    !isGeneralSelected ? selectedSummaryId : null,
    handleSectionCompletedAway,
  )
  const sectionCtrlRef = useRef(sectionStreamCtrl)
  sectionCtrlRef.current = sectionStreamCtrl

  // Auto-start section streams for all generating tabs (once per tab per session)
  const startedSectionStreamsRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    const generatingTabs = tabs.filter(
      t => t.type === "section" && t.processing_state === "generating"
    )
    for (const tab of generatingTabs) {
      const key = `${meetingId}::${tab.tab_id}`
      if (!startedSectionStreamsRef.current.has(key)) {
        startedSectionStreamsRef.current.add(key)
        startSectionStream(meetingId, tab.tab_id)
      }
    }
  }, [tabs, meetingId])

  // Track streaming completion for the selected section
  const sectionWasStreamingRef = useRef(false)
  const prevSectionTabRef = useRef(selectedSummaryId)
  useEffect(() => {
    // Reset tracking when switching to a different section tab
    if (prevSectionTabRef.current !== selectedSummaryId) {
      sectionWasStreamingRef.current = false
      prevSectionTabRef.current = selectedSummaryId
    }

    if (!isGeneralSelected && !sectionStream.isStreaming && sectionWasStreamingRef.current) {
      // Section stream just finished
      getMeeting(meetingId).then((m) => {
        onMeetingUpdate(m)
        loadedTabsRef.current.delete(selectedSummaryId)
        loadTabContent(selectedSummaryId)
        sectionStreamCtrl.dismiss()
        toast.success("Section generated")
      }).catch(() => {
        sectionStreamCtrl.dismiss()
        toast.error("Failed to fetch updated section")
      })
    }
    sectionWasStreamingRef.current = sectionStream.isStreaming
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sectionStream.isStreaming, selectedSummaryId, isGeneralSelected])

  // Notify parent of active tab changes (for transcript tag highlighting)
  useEffect(() => {
    onActiveTabChange?.(selectedSummaryId)
  }, [selectedSummaryId, onActiveTabChange])
  const [loadingTabs, setLoadingTabs] = useState<Set<string>>(new Set())
  const [selectedBlueprintIds, setSelectedBlueprintIds] = useState<Set<string>>(new Set())
  const [customReceipts, setCustomReceipts] = useState<Array<{ name: string; description: string }>>([])
  const [addSectionOpen, setAddSectionOpen] = useState(false)
  const [addForm, setAddForm] = useState<{ name: string; description: string; blueprintId: string | null }>({
    name: "", description: "", blueprintId: null,
  })
  const [generatingDesc, setGeneratingDesc] = useState(false)
  // ── Unified busy + polling (P2-01) ─────────────────────────────────
  // busy = server side still processing OR we just fired an action (before server state updates)
  type PendingAction =
    | { type: "summarize" }
    | { type: "re_summarize" }
    | { type: "extract" }
    | { type: "regenerate"; tabId: string; hadAllocation: boolean }
    | null
  const [pendingAction, setPendingAction] = useState<PendingAction>(null)
  const serverBusy = !!(meeting.processing_state && meeting.processing_state !== "idle")
  const streamingBusy = bpStream.summaryGenState !== "idle" || bpStream.blueprintGenState !== "idle" || sectionStream.isStreaming
  const busy = serverBusy || !!pendingAction || streamingBusy

  // Track whether server was ever busy since pendingAction was set.
  // Guards against the intermediate render where pendingAction is set but
  // the meeting prop (serverBusy) hasn't been updated yet.
  const serverWasBusyRef = useRef(false)
  useEffect(() => {
    if (serverBusy) serverWasBusyRef.current = true
  }, [serverBusy])

  // Unified cleanup: when server goes idle AFTER being busy due to our action
  useEffect(() => {
    if (pendingAction && !serverBusy && serverWasBusyRef.current) {
      serverWasBusyRef.current = false
      const action = pendingAction
      setPendingAction(null)
      // Perform action-specific cleanup
      switch (action.type) {
        case "summarize":
        case "re_summarize":
          toast.success(action.type === "re_summarize" ? "Summary regenerated" : "Summary generated")
          break
        case "extract":
          // Clear loaded-tabs cache so section tabs re-fetch newly generated content
          loadedTabsRef.current.clear()
          setTabMdContents({})
          toast.success("Extract complete")
          break
        case "regenerate":
          // Delete old allocation AFTER successful regeneration
          if (action.hadAllocation) {
            deleteSectionAllocation(meetingId, action.tabId).catch(() => { /* best effort */ })
          }
          loadedTabsRef.current.delete(action.tabId)
          setTabMdContents((prev) => {
            const next = { ...prev }
            delete next[action.tabId]
            return next
          })
          // Re-trigger load for the regenerated tab
          getSectionMd(meetingId, action.tabId).then((md) => {
            if (md !== null) setTabMdContents((prev) => ({ ...prev, [action.tabId]: md }))
          }).catch(() => {})
          toast.success("Regenerate complete")
          break
      }
      // Refresh meeting data from server (picks up new tabs/sections)
      getMeeting(meetingId).then((m) => {
        onMeetingUpdate(m)
      }).catch(() => {
        // Fall back to in-memory meeting if fetch fails
        onMeetingUpdate(meeting)
      })
    }
  }, [meeting.processing_state, pendingAction])

  const [reSummarizeOpen, setReSummarizeOpen] = useState(false)
  const [regenerateConfirmOpen, setRegenerateConfirmOpen] = useState(false)
  const [deleteSectionTarget, setDeleteSectionTarget] = useState<string | null>(null)
  const sectionMetaRef = useRef<{ startEditing: () => void }>(null)
  const [summaryHoverOpen, setSummaryHoverOpen] = useState(false)
  const summaryHoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [dropdownPos, setDropdownPos] = useState<{ top: number; left: number } | null>(null)

  // Track dropdown position continuously when open (follows button on scroll)
  useEffect(() => {
    if (!summaryHoverOpen) return
    const update = () => {
      const rect = summaryBtnRef.current?.getBoundingClientRect()
      if (rect) setDropdownPos({ top: rect.bottom, left: rect.left })
    }
    update()
    window.addEventListener("scroll", update, { passive: true, capture: true })
    return () => window.removeEventListener("scroll", update, { capture: true })
  }, [summaryHoverOpen])

  const summaryBarRef = useRef<HTMLDivElement>(null)
  const tabContainerRef = useRef<HTMLDivElement>(null)
  const summaryBtnRef = useRef<HTMLButtonElement>(null)
  const notesBtnRef = useRef<HTMLButtonElement>(null)
  const transcriptBtnRef = useRef<HTMLButtonElement>(null)
  const speakerBtnRef = useRef<HTMLButtonElement>(null)
  const [tabIndicator, setTabIndicator] = useState({ left: 0, width: 0 })
  const [ingestingTabs, setIngestingTabs] = useState<Set<string>>(new Set())

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

  // ── Load section markdown when tab is selected ─────────────
  const loadedTabsRef = useRef<Set<string>>(new Set())   // successfully loaded
  const inFlightRef = useRef<Set<string>>(new Set())     // currently fetching (dedup)

  const loadTabContent = useCallback(async (tabId: string) => {
    // Already loaded → skip
    if (loadedTabsRef.current.has(tabId)) return
    // Already in-flight → skip (dedup concurrent calls)
    if (inFlightRef.current.has(tabId)) return
    inFlightRef.current.add(tabId)

    setLoadingTabs((prev) => new Set(prev).add(tabId))
    try {
      const md = await getSectionMd(meetingId, tabId)
      if (md !== null) {
        loadedTabsRef.current.add(tabId)   // mark loaded ONLY on success
        setTabMdContents((prev) => ({ ...prev, [tabId]: md }))
      } else {
        setTabMdContents((prev) => ({ ...prev, [tabId]: "" }))
        // NOT marked as loaded → will retry next time
      }
    } catch {
      setTabMdContents((prev) => ({ ...prev, [tabId]: "" }))
    }
    inFlightRef.current.delete(tabId)
    setLoadingTabs((prev) => {
      const next = new Set(prev)
      next.delete(tabId)
      return next
    })
  }, [meetingId])

  useEffect(() => {
    if (selectedSummaryId && selectedSummaryId !== "tab_general") {
      loadTabContent(selectedSummaryId)
    }
  }, [selectedSummaryId, loadTabContent])

  useEffect(() => {
    const sectionTabs = tabs.filter(t => t.type === "section" && t.md_file_path)
    for (const t of sectionTabs) {
      loadTabContent(t.tab_id)
    }
    // Also load General tab content from .md file when it has md_file_path.
    // loadTabContent skips if already loaded; streaming-done effect
    // explicitly clears the cache on re-summarize.
    const generalTab = tabs.find(t => t.tab_id === "tab_general" && t.md_file_path)
    if (generalTab) {
      loadTabContent("tab_general")
    }
  }, [tabs])  // eslint-disable-line react-hooks/exhaustive-deps

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
  const doExtract = async (receipts: ExtractReceipt[]) => {
    if (receipts.length === 0) {
      toast.error("Select at least one section")
      return
    }
    setAddSectionOpen(false)
    setPendingAction({ type: "extract" })
    try {
      const updated = await extract(meetingId, receipts)
      setSelectedBlueprintIds(new Set())
      setCustomReceipts([])
      // Notify parent to start polling (meeting now has processing_state="extracting")
      onMeetingUpdate(updated)
    } catch (err) {
      setPendingAction(null)
      toast.error(`Extract failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleBreakdown = async () => {
    const bp = blueprint
    const receipts: ExtractReceipt[] = []
    for (const id of selectedBlueprintIds) {
      const item = bp.find(b => b.blueprint_id === id)
      if (item) {
        receipts.push({
          source: "blueprint",
          blueprint_id: item.blueprint_id,
          name: item.tab_name,
          description: item.tab_description,
        })
      }
    }
    for (const c of customReceipts) {
      receipts.push({ source: "custom", name: c.name, description: c.description })
    }
    await doExtract(receipts)
  }

  const handleGenerateDesc = async () => {
    const name = addForm.name.trim()
    if (!name) { toast.error("Enter a section name first"); return }
    setGeneratingDesc(true)
    try {
      const res = await generateSectionDescription(meetingId, name)
      if (res.found && res.description) {
        setAddForm(prev => ({ ...prev, description: res.description ?? prev.description }))
        toast.success("Description generated")
      } else {
        toast.warning(`"${name}" does not appear to be discussed in this meeting`)
      }
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    }
    setGeneratingDesc(false)
  }

  const handleAddOrExtract = async () => {
    if (busy) { toast.error("Meeting is processing. Please wait until the current operation completes."); return }
    const name = addForm.name.trim()
    if (!name) { toast.error("Section name is required"); return }
    if (!hasSections) {
      // Before breakdown: add to receipt list
      if (addForm.blueprintId) {
        setSelectedBlueprintIds((prev) => {
          const next = new Set(prev)
          next.add(addForm.blueprintId!)
          return next
        })
      } else {
        setCustomReceipts((prev) => [...prev, { name, description: addForm.description.trim() }])
      }
      setAddForm({ name: "", description: "", blueprintId: null })
      setAddSectionOpen(false)
      return
    }
    // After breakdown: single extract
    const receipt: ExtractReceipt = { source: "custom", name, description: addForm.description.trim() }
    if (addForm.blueprintId) {
      const item = blueprint.find(b => b.blueprint_id === addForm.blueprintId)
      if (item) {
        receipt.source = "blueprint"
        receipt.blueprint_id = item.blueprint_id
      }
    }
    setAddForm({ name: "", description: "", blueprintId: null })
    await doExtract([receipt])
  }

  const handleSummarize = async () => {
    bpStreamCtrl.start()
  }

  const handleReSummarize = async () => {
    setReSummarizeOpen(false)
    bpStreamCtrl.start()
  }

  // ── Detect streaming finish → fetch meeting ────────────────
  const streamingDoneRef = useRef(false)
  useEffect(() => {
    if (!bpStream.isStreaming && wasStreamingRef.current) {
      streamingDoneRef.current = true
    }
    wasStreamingRef.current = bpStream.isStreaming
  }, [bpStream.isStreaming])

  // Fetch meeting when streaming completes (deferred so panel exit anim plays)
  useEffect(() => {
    if (!streamingDoneRef.current) return
    streamingDoneRef.current = false
    getMeeting(meetingId).then((m) => {
      onMeetingUpdate(m)
      // Reload General tab from .md file after summarization
      loadedTabsRef.current.delete("tab_general")
      loadTabContent("tab_general")
      bpStreamCtrl.dismissStreaming()
      toast.success("Summary generated")
    }).catch(() => {
      bpStreamCtrl.dismissStreaming()  // clear streaming state even on error
      toast.error("Failed to fetch updated meeting")
    })
  }, [bpStream.isStreaming])

  const handleDeleteSection = (tabId: string) => {
    setDeleteSectionTarget(tabId)
  }

  const confirmDeleteSection = async () => {
    const tabId = deleteSectionTarget
    if (!tabId) return
    setDeleteSectionTarget(null)
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
    // Remember if section was ingested so we can clean up on success
    const targetTab = tabs.find(t => t.tab_id === tabId)
    const hadAllocation = !!targetTab?.allocated_file_id
    setPendingAction({ type: "regenerate", tabId, hadAllocation })
    try {
      const updated = await regenerateSection(meetingId, tabId)
      // Notify parent to start polling (meeting now has processing_state="extracting")
      onMeetingUpdate(updated)
    } catch (err) {
      setPendingAction(null)
      toast.error(`Regenerate failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleSaveSection = async (tabId: string, content: string) => {
    await saveSectionMd(meetingId, tabId, content)
    setTabMdContents((prev) => ({ ...prev, [tabId]: content }))
    if (tabId === "tab_general") {
      // Mark as loaded so tabs effect won't re-fetch
      loadedTabsRef.current.add("tab_general")
      onMeetingUpdate({ ...meeting })
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
  const hasSections = tabs.some((t) => t.type === "section")

  /** Dynamically compute sequential label: T1, T2, ... based on section tab order.
   *  Includes generating tabs so labels don't shift when generation completes. */
  function tabShortLabel(tab: MeetingTab): string {
    const sections = tabs.filter(t => t.type === "section")
    const idx = sections.findIndex(t => t.tab_id === tab.tab_id)
    return idx >= 0 ? `T${idx + 1}` : tab.tab_id
  }

  const sectionTabs = tabs.filter(
    (t) => t.type === "section",
  )

  const getTabContent = (tabId: string): string => {
    if (tabId === "tab_general") {
      return tabMdContents["tab_general"] ?? (bpStream.streamingMd || "")
    }
    // For sections, prefer loaded content but fall back to streaming markdown
    return tabMdContents[tabId] ?? ((tabId === selectedSummaryId ? sectionStream.streamingMd : "") || "")
  }

  const selectedTab = tabs.find((t) => t.tab_id === selectedSummaryId)
  const isGeneral = selectedSummaryId === "tab_general"
  const isTabGenerating = selectedTab?.processing_state === "generating"

  return (
    <div className={cn("flex flex-col", className)}>

      {/* ── Tab bar: sticky below meeting title, extends right when floating panel opens ── */}
      <div
        ref={summaryBarRef}
        className={cn(
          "sticky flex items-center border-b border-border px-2 shrink-0 transition-[margin-right] duration-300 ease-[cubic-bezier(0.23,1,0.32,1)] bg-background z-10",
          floatingPanelOpen && canShift ? "-mr-[320px]" : "mr-0",
        )}
        style={{ top: 0 }}
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
              if (hasBlueprint) {
                const rect = summaryBtnRef.current?.getBoundingClientRect()
                if (rect) setDropdownPos({ top: rect.bottom, left: rect.left })
                setSummaryHoverOpen(true)
              }
            }}
            onMouseLeave={() => {
              summaryHoverTimer.current = setTimeout(() => setSummaryHoverOpen(false), 150)
            }}
          >
            Summary
            {(hasBlueprint || bpStream.blueprintGenState !== "idle") && (
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

      {/* Hover dropdown — section picker below the Summary tab (portal to avoid overflow clipping) */}
        {dropdownPos && createPortal(
        <div
          className={cn(
            "fixed z-50 w-56 overflow-hidden rounded border border-primary/30 bg-popover/60 backdrop-blur-md shadow-lg transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)]",
            summaryHoverOpen
              ? "opacity-100 visible translate-y-0 pointer-events-auto"
              : "opacity-0 invisible -translate-y-3 pointer-events-none",
          )}
          style={{ top: dropdownPos.top, left: dropdownPos.left }}
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
              <span className="whitespace-normal break-words min-w-0 leading-snug text-left">General</span>
            </span>
            <span className="absolute inset-0 z-0 bg-primary transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
          </button>
          {/* Blueprint-generating skeleton: pulsing placeholder cards */}
          {bpStream.blueprintGenState === "prefilling" && (
            <>
              {[1, 2, 3].map((i) => (
                <div
                  key={`bp-sk-${i}`}
                  className="flex items-center gap-2 px-2 py-2"
                >
                  <span className="sk-diamond opacity-30" aria-hidden />
                  <span className="h-3 bg-muted-foreground/20 rounded animate-pulse" style={{ width: `${60 + i * 15}%` }} />
                </div>
              ))}
            </>
          )}
          {sectionTabs.map((tab) => (
            <button
              key={tab.tab_id}
              onClick={() => { setSelectedSummaryId(tab.tab_id); setMainTab("summary") }}
              title={tab.name}
              className="relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group"
            >
              <span className="relative z-10 flex items-center gap-2 px-2 py-2 w-full text-[10px]">
                <span className={cn("sk-diamond", selectedSummaryId === tab.tab_id && "on")} aria-hidden />
                <span className="whitespace-normal break-words min-w-0 leading-snug">{tabShortLabel(tab)}: {(blueprint as any[]).find((b: any) => b.blueprint_id === tab.blueprint_id)?.tab_name || tab.name}</span>
              </span>
              <span className="absolute inset-0 z-0 bg-primary transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
            </button>
          ))}

        </div>
        , document.body)}
      </div>

      {/* ── Summary Tab ── */}
      <div className={cn(
        "transition-opacity duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]",
        mainTab === "summary"
          ? "flex flex-col opacity-100"
          : "hidden",
      )}>
        {/* Content area — scroll handled by parent */}
        <div className="min-h-[400px]">
          {/* ═══ Blueprint skeleton — stays until streaming fully ends ═══ */}
          {(bpStream.blueprintGenState !== "idle" || bpStream.isStreaming) && isGeneral && !hasSections && (
            <div className="px-6 pt-6">
              <div className="sk-thinking-flow rounded-lg p-5 space-y-3">
                <div className="flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin" style={{ color: "var(--ze-green)" }} />
                  <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                    Section breakdown
                  </span>
                </div>
                {[1, 2, 3].map((i) => (
                  <div
                    key={`bp-sk-${i}`}
                    className="h-8 rounded animate-pulse"
                    style={{ background: "oklch(0.38 0.08 160 / 0.12)", width: `${40 + i * 20}%` }}
                  />
                ))}
              </div>
            </div>
          )}

          {/* ═══ Summary area: streaming or normal content ═══ */}
          {bpStream.isStreaming && isGeneral ? (
            <div className="p-6">
              {bpStream.summaryGenState === "prefilling" && (
                <div className="sk-thinking-flow rounded-lg p-5 space-y-4">
                  <div className="flex items-center gap-2">
                    <Loader2 className="h-4 w-4 animate-spin" style={{ color: "var(--ze-green)" }} />
                    <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                      Generating summary…
                    </span>
                  </div>
                  {bpStream.thinkingText && (
                    <details className="mb-2" open>
                      <summary className="text-xs text-muted-foreground cursor-pointer select-none">
                        Thinking…
                      </summary>
                      <p className="text-xs text-muted-foreground/60 mt-2 leading-relaxed whitespace-pre-wrap t-mono-family max-h-32 overflow-auto">
                        {bpStream.thinkingText}
                      </p>
                    </details>
                  )}
                  {[1, 2, 3, 4, 5].map((i) => (
                    <div
                      key={`sum-sk-${i}`}
                      className="h-4 rounded animate-pulse"
                      style={{ background: "oklch(0.38 0.08 160 / 0.12)", width: `${50 + i * 10}%` }}
                    />
                  ))}
                </div>
              )}
              {bpStream.summaryGenState === "streaming" && (
                <div className="prose prose-sm dark:prose-invert max-w-none">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {normalizeMd(bpStream.streamingMd)}
                  </ReactMarkdown>
                </div>
              )}
              {bpStream.summaryGenState === "idle" && bpStream.streamingMd && (
                /* Summary done, waiting for meeting refresh — show completed markdown */
                <div className="prose prose-sm dark:prose-invert max-w-none">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {normalizeMd(bpStream.streamingMd)}
                  </ReactMarkdown>
                </div>
              )}
            </div>
          ) : (isTabGenerating || (isGeneral && !hasBlueprint && (meeting.processing_state === "summarizing" || pendingAction?.type === "summarize" || pendingAction?.type === "re_summarize"))) || (!isGeneral && loadingTabs.has(selectedSummaryId)) ? (
            isGeneral ? (
              <ThinkingSkeleton />
            ) : (
              /* Section tab generating — show streaming content or skeleton */
              (() => {
                const secGenState = sectionStream.genState
                const hasStreamingContent = secGenState === "streaming" || (secGenState === "idle" && sectionStream.streamingMd)
                if (hasStreamingContent) {
                  return (
                    <div className="flex flex-col min-h-0 overflow-auto">
                      {/* Section header */}
                      <div className="px-6 pt-6 pb-3">
                        <div className="flex items-start gap-2">
                          <span
                            className="shrink-0 t-body-family"
                            style={{
                              fontSize: "clamp(20px, 2vw, 24px)",
                              fontWeight: 400,
                              letterSpacing: "-0.01em",
                              lineHeight: 1.35,
                              color: "var(--ze-ink)",
                            }}
                          >
                            {tabShortLabel(selectedTab!)} {selectedTab?.name}
                          </span>
                          {sectionStream.isStreaming && (
                            <Loader2 className="h-4 w-4 animate-spin mt-1.5 shrink-0" style={{ color: "var(--ze-green)" }} />
                          )}
                        </div>
                        {selectedTab?.description && (
                          <p className="text-xs text-muted-foreground leading-relaxed mt-1">{selectedTab.description}</p>
                        )}
                        <div className="flex-1 h-px bg-border mt-4" />
                      </div>
                      {/* Streaming markdown content */}
                      <div className="px-6 flex-1">
                        <div className="prose prose-sm dark:prose-invert max-w-none">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {normalizeMd(sectionStream.streamingMd)}
                          </ReactMarkdown>
                        </div>
                      </div>
                    </div>
                  )
                }
                // Prefilling state — show skeleton
                return (
                  <div className="flex flex-col min-h-0 overflow-auto">
                    {/* Section header */}
                    <div className="px-6 pt-6 pb-3">
                      <div className="flex items-start gap-2">
                        <span
                          className="shrink-0 t-body-family"
                          style={{
                            fontSize: "clamp(20px, 2vw, 24px)",
                            fontWeight: 400,
                            letterSpacing: "-0.01em",
                            lineHeight: 1.35,
                            color: "var(--ze-ink)",
                          }}
                        >
                          {tabShortLabel(selectedTab!)} {selectedTab?.name}
                        </span>
                        <Loader2 className="h-4 w-4 animate-spin mt-1.5 shrink-0" style={{ color: "var(--ze-green)" }} />
                      </div>
                      {selectedTab?.description && (
                        <p className="text-xs text-muted-foreground leading-relaxed mt-1">{selectedTab.description}</p>
                      )}
                      <div className="flex-1 h-px bg-border mt-4" />
                    </div>
                    {/* Skeleton card — same visual style as General prefilling */}
                    <div className="px-6 flex-1">
                      <div className="sk-thinking-flow rounded-lg p-6 pt-10 space-y-4">
                        <div className="h-6 w-1/3 rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.12)" }} />
                        <div className="space-y-3 pt-2">
                          <div className="h-3 w-full rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.1s" }} />
                          <div className="h-3 w-5/6 rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.3s" }} />
                          <div className="h-3 w-4/6 rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.5s" }} />
                          <div className="h-3 w-full rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.2s" }} />
                          <div className="h-3 w-3/6 rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.6s" }} />
                        </div>
                        <div className="h-4 w-1/4 rounded animate-pulse pt-2" style={{ background: "oklch(0.38 0.08 160 / 0.1)", animationDelay: "0.4s" }} />
                        <div className="space-y-3 pt-1">
                          <div className="h-3 w-full rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.7s" }} />
                          <div className="h-3 w-2/3 rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.9s" }} />
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })()
            )
          ) : !hasBlueprint && !tabs.some(t => t.tab_id === "tab_general" && t.md_file_path) ? (
            <div className="flex items-center justify-center h-full">
              {hasTranscript ? (
                <Button variant="outline" size="sm" onClick={handleSummarize}>
                  <Sparkles className="h-3.5 w-3.5 mr-1.5" /> Summarize
                </Button>
              ) : (
                <p className="text-muted-foreground text-sm">No content yet.</p>
              )}
            </div>
          ) : (
            <>
              <EditableSectionContent
                content={getTabContent(selectedSummaryId)}
                onSave={async (draft) => handleSaveSection(selectedSummaryId, draft)}
                onRefClick={handleRefClick}
                speakerNames={speakerNames}
                actionsDisabled={ingestingTabs.has(selectedSummaryId)}
                stickyOffset={36}
                title={
                  isGeneral
                    ? "General"
                    : selectedTab ? (
                        <span className="group/title inline-flex items-center gap-1.5">
                          <span>{tabShortLabel(selectedTab)} {selectedTab.name || (blueprint as any[]).find((b: any) => b.blueprint_id === selectedTab.blueprint_id)?.tab_name || ""}</span>
                          <button
                            className="opacity-0 group-hover/title:opacity-100 transition-opacity duration-200 h-6 w-6 flex items-center justify-center rounded-sm text-muted-foreground hover:text-foreground hover:bg-accent shrink-0"
                            onClick={(e) => { e.stopPropagation(); sectionMetaRef.current?.startEditing() }}
                            title="Edit section name"
                          >
                            <Pencil className="h-3 w-3" />
                          </button>
                        </span>
                      ) : ""
                }
                actionButtons={null}
                toolbar={
                  isGeneral ? (
                    <div className="px-4 pt-3 pb-4 space-y-3">
                      <button
                        type="button"
                        disabled={busy || ingestingTabs.size > 0}
                        onClick={() => setReSummarizeOpen(true)}
                        title="Re-summarize"
                        className={cn(
                          "inline-flex items-center justify-center gap-2 rounded-md h-8 px-4 text-[11px] font-semibold tracking-[0.1em] uppercase flex-1 select-none transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] w-full",
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

                      {/* ── Sections (v3) ── */}
                      {hasBlueprint && (
                        <div className="border border-border rounded-md p-3 space-y-2">
                          <div className="flex items-center gap-2">
                            <span className="text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">Sections</span>
                            <span className="flex-1 h-px bg-border/50" />
                          </div>

                          {!hasSections ? (
                            <>
                              <div className="space-y-1.5">
                                {blueprint.map((b) => {
                                  const isSelected = selectedBlueprintIds.has(b.blueprint_id)
                                  const pill = (
                                    <button
                                      key={b.blueprint_id}
                                      disabled={busy}
                                      onClick={() => {
                                        setSelectedBlueprintIds((prev) => {
                                          const next = new Set(prev)
                                          if (next.has(b.blueprint_id)) next.delete(b.blueprint_id)
                                          else next.add(b.blueprint_id)
                                          return next
                                        })
                                      }}
                                      className={cn(
                                        "w-full text-left inline-flex items-center gap-2 px-3 py-2 rounded-full cursor-pointer transition-all text-xs font-medium",
                                        busy && isSelected
                                          ? "sk-thinking-flow text-primary"
                                          : isSelected
                                            ? "border border-primary/40 text-primary bg-primary/5"
                                            : "border border-border text-muted-foreground hover:text-primary hover:border-primary/30 bg-transparent",
                                      )}
                                    >
                                      <span className={cn(
                                        "w-2 h-2 rounded-full shrink-0 transition-colors",
                                        isSelected ? "bg-primary" : "border border-muted-foreground/30",
                                      )} />
                                      {b.tab_name}
                                    </button>
                                  )
                                  if (!b.tab_description) return <span key={b.blueprint_id} className="block">{pill}</span>
                                  return (
                                    <Tooltip key={b.blueprint_id}>
                                      <TooltipTrigger render={pill} />
                                      <TooltipContent side="top" className="max-w-xs px-2.5 py-1.5 text-[11px] bg-[#0A120E] text-[#FAFAF7] rounded-[3px]">
                                        {b.tab_description}
                                      </TooltipContent>
                                    </Tooltip>
                                  )
                                })}
                                {customReceipts.map((c, i) => (
                                  <button
                                    key={`cus-${i}`}
                                    disabled={busy}
                                    onClick={() => { if (!busy) setCustomReceipts((prev) => prev.filter((_, j) => j !== i)) }}
                                    className={cn(
                                      "w-full text-left inline-flex items-center gap-2 px-3 py-2 rounded-full cursor-pointer transition-all text-xs font-medium",
                                      busy
                                        ? "sk-thinking-flow text-primary"
                                        : "border border-primary/40 text-primary bg-primary/5",
                                    )}
                                  >
                                    <span className="w-2 h-2 rounded-full bg-primary shrink-0" />
                                    {c.name}
                                    <X className="h-3 w-3 ml-auto shrink-0 text-muted-foreground hover:text-primary" />
                                  </button>
                                ))}
                              </div>
                              <div className="flex items-center gap-2 pt-1">
                                <button
                                  onClick={() => setAddSectionOpen(true)}
                                  disabled={busy}
                                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full cursor-pointer text-xs font-medium border border-dashed border-border text-muted-foreground hover:text-primary hover:border-primary/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                  <Plus className="h-3.5 w-3.5" />
                                  Add Section
                                </button>
                                {customReceipts.length + selectedBlueprintIds.size > 0 && (
                                  <button
                                    onClick={handleBreakdown}
                                    disabled={busy}
                                    className={cn(
                                      "inline-flex items-center gap-1.5 px-4 py-1.5 rounded-full cursor-pointer text-xs font-semibold uppercase tracking-wider transition-all",
                                      busy
                                        ? "sk-thinking-flow text-primary"
                                        : "bg-primary text-primary-foreground hover:bg-primary/80",
                                    )}
                                  >
                                    {busy ? "Extracting..." : "Breakdown"}
                                  </button>
                                )}
                              </div>
                            </>
                          ) : (
                            <>
                              <div className="space-y-1.5">
                                {sectionTabs.map((tab) => (
                                  <button
                                    key={tab.tab_id}
                                    onClick={() => { setSelectedSummaryId(tab.tab_id); setMainTab("summary") }}
                                    className={cn(
                                      "group w-full text-left inline-flex items-center gap-2 px-3 py-2 rounded-full cursor-pointer transition-all text-xs font-medium",
                                      selectedSummaryId === tab.tab_id
                                        ? "bg-primary text-primary-foreground"
                                        : "bg-[rgba(61,175,115,0.12)] text-[#2D8A5E] hover:bg-[rgba(61,175,115,0.20)]",
                                    )}
                                  >
                                    <span className={cn(
                                      "sk-diamond",
                                      selectedSummaryId === tab.tab_id && "on",
                                    )} aria-hidden />
                                    <span className={cn(
                                      "text-[10px] font-semibold uppercase tracking-wider shrink-0",
                                      selectedSummaryId === tab.tab_id ? "text-primary-foreground/70" : "",
                                    )}>
                                      {tabShortLabel(tab)}
                                    </span>
                                    {tab.name}
                                  </button>
                                ))}
                              </div>
                              <button
                                onClick={() => setAddSectionOpen(true)}
                                disabled={busy}
                                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full cursor-pointer text-xs font-medium border border-dashed border-border text-muted-foreground hover:text-primary hover:border-primary/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                              >
                                <Plus className="h-3.5 w-3.5" />
                                Add Section
                              </button>
                            </>
                          )}
                        </div>
                      )}
                    </div>
                  ) : (() => {
                        const generating = isTabGenerating
                        return (
                          <div className="flex items-center gap-2 px-6 pb-2">
                            {/* Regenerate — visible when is_dirty (user modified name/description) */}
                            {!!selectedTab?.is_dirty && (
                            <button
                              type="button"
                              disabled={isTabGenerating || ingestingTabs.has(selectedSummaryId)}
                              onClick={() => {
                                if (selectedTab?.allocated_file_id) {
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
                            )}
                            {!isTabGenerating && !busy && (
                              <button
                                type="button"
                                className="text-[11px] font-medium tracking-[0.06em] uppercase shrink-0 select-none transition-colors duration-200 text-muted-foreground hover:text-[#8C2E2E] ml-auto"
                                onClick={() => handleDeleteSection(selectedSummaryId)}
                                title="Delete section"
                                disabled={ingestingTabs.has(selectedSummaryId)}
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
                      ref={sectionMetaRef}
                      tab={selectedTab}
                      blueprint={blueprint}
                      tabs={tabs}
                      meetingId={meetingId}
                      onMeetingUpdate={onMeetingUpdate}
                      hideTitle
                      onIngestingChange={(tabId, v) => {
                        setIngestingTabs(prev => {
                          const next = new Set(prev);
                          if (v) { next.add(tabId); } else { next.delete(tabId); }
                          return next;
                        });
                      }}
                    />
                  ) : undefined
                }
              />


            </>
          )}
        </div>
      </div>

      {/* ── Notes Tab ── */}
      <div className={cn(
        "transition-opacity duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]",
        mainTab === "notes"
          ? "flex flex-col opacity-100"
          : "hidden",
      )}>
        <div>
          <MarkdownEditor
            value={notesDraft}
            onChange={handleNotesChange}
            minHeight="400px"
            stickyToolbarOffset={36}
            placeholder="Write your meeting notes here (Markdown supported)..."
          />
        </div>
      </div>

      {/* ── Transcript Tab ── */}
      <div className={cn(
        "transition-opacity duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]",
        mainTab === "transcript"
          ? "flex flex-col"
          : "hidden",
      )}>
        <div className="min-h-[400px]">
          <TranscriptTab
            segments={transcriptSegments}
            partialText={partialText}
            onSegmentClick={onSeekTo}
            focusRef={focusRef}
            activeSectionTag={activeSectionTag}
            speakerNames={speakerNames}
            tabs={tabs}
            playbackTime={playbackTime}
          />
        </div>
      </div>

      {/* ── Speaker Tab ── */}
      <div className={cn(
        "transition-opacity duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]",
        mainTab === "speaker"
          ? "flex flex-col"
          : "hidden",
      )}>
        <div className="min-h-[400px]">
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

      {/* Add Section Dialog */}
      <Dialog open={addSectionOpen} onOpenChange={(open) => {
        setAddSectionOpen(open)
        if (!open) setAddForm({ name: "", description: "", blueprintId: null })
      }}>
        <DialogContent className="!max-w-[90vw] sm:!max-w-3xl">
          <DialogHeader>
            <DialogTitle>Add Section</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {hasSections
              ? "Define a topic to extract. It will be processed immediately."
              : "Add a section to the breakdown list. Click Breakdown to process all selected sections."}
          </p>
          {hasSections ? (
            <div className="flex gap-5 mt-3">
              {/* Left sidebar: unextracted blueprint items — collection selector style */}
              {(() => {
                const bpItems = blueprint.filter(b => !tabs.some(t => t.blueprint_id === b.blueprint_id))
                if (bpItems.length === 0) return null
                return (
                  <div className="w-1/3 shrink-0 overflow-hidden rounded border border-primary/20 bg-popover/40 backdrop-blur-sm">
                    <p className="text-[10px] font-semibold uppercase tracking-[0.15em] text-muted-foreground px-3 py-2.5 border-b border-border/50">Blueprint</p>
                    <div className="max-h-[280px] overflow-y-auto py-1">
                      {bpItems.map(b => (
                        <button
                          key={b.blueprint_id}
                          onClick={() => {
                            setAddForm({
                              name: b.tab_name,
                              description: b.tab_description,
                              blueprintId: b.blueprint_id,
                            })
                          }}
                          className="relative flex items-center gap-2.5 w-full cursor-pointer overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group text-left"
                        >
                          <span className="relative z-10 flex items-center gap-2.5 px-3 py-2 w-full text-[11px]">
                            <span className={cn("sk-diamond", addForm.blueprintId === b.blueprint_id && "on")} aria-hidden />
                            <span className="whitespace-normal break-words min-w-0 leading-snug">{b.tab_name}</span>
                          </span>
                          <span className="absolute inset-0 z-0 bg-primary transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
                        </button>
                      ))}
                    </div>
                  </div>
                )
              })()}
              {/* Right: form fields — same height as sidebar */}
              <div className={cn("flex-1 min-w-0 space-y-4", blueprint.filter(b => !tabs.some(t => t.blueprint_id === b.blueprint_id)).length === 0 ? "" : "self-stretch flex flex-col justify-between")}>
                <div className="space-y-4">
                  <div>
                    <label className="text-[10px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">Section Name *</label>
                    <input
                      className="w-full border border-border rounded px-3 py-2.5 text-sm bg-background mt-1.5 focus:border-primary/50 focus:outline-none transition-colors"
                      placeholder="e.g. Vendor Negotiation"
                      value={addForm.name}
                      onChange={(e) => {
                        setAddForm(prev => ({ ...prev, name: e.target.value, blueprintId: null }))
                      }}
                    />
                  </div>
                  <div>
                    <label className="text-[10px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">Description</label>
                    <div className={cn("relative mt-1.5", generatingDesc && "sk-flow-full rounded")}>
                      <textarea
                        className={cn(
                          "w-full rounded px-3 py-2.5 text-sm bg-background resize-none min-h-[200px]",
                          generatingDesc ? "border-0" : "border border-border focus:border-primary/50 focus:outline-none transition-colors"
                        )}
                        placeholder="e.g. Discussion of the supplier contract renewal for Client X. Covers pricing negotiation strategy, delivery timeline adjustments, quality assurance requirements, and the phased rollout decision. Signals: mentions of the client name, contract terms, supplier performance, or renewal timeline."
                        value={addForm.description}
                        onChange={(e) => {
                          setAddForm(prev => ({ ...prev, description: e.target.value, blueprintId: null }))
                        }}
                        rows={8}
                      />
                      <button
                        type="button"
                        disabled={generatingDesc}
                        onClick={handleGenerateDesc}
                        className="absolute bottom-2.5 right-2.5 h-8 w-8 flex items-center justify-center rounded-full bg-[rgba(61,175,115,0.12)] text-[#2D8A5E] hover:bg-[rgba(61,175,115,0.22)] transition-colors"
                        title="Generate description from General Summary"
                      >
                        {generatingDesc ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <div className="space-y-4 mt-3">
              <div>
                <label className="text-[10px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">Section Name *</label>
                <input
                  className="w-full border border-border rounded px-3 py-2.5 text-sm bg-background mt-1.5 focus:border-primary/50 focus:outline-none transition-colors"
                  placeholder="e.g. Vendor Negotiation"
                  value={addForm.name}
                  onChange={(e) => {
                    setAddForm(prev => ({ ...prev, name: e.target.value, blueprintId: null }))
                  }}
                />
              </div>
              <div>
                <label className="text-[10px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">Description</label>
                <div className={cn("relative mt-1.5", generatingDesc && "sk-flow-full rounded")}>
                  <textarea
                    className={cn(
                      "w-full rounded px-3 py-2.5 text-sm bg-background resize-none min-h-[200px]",
                      generatingDesc ? "border-0" : "border border-border focus:border-primary/50 focus:outline-none transition-colors"
                    )}
                    placeholder="e.g. Discussion of the supplier contract renewal for Client X. Covers pricing negotiation strategy, delivery timeline adjustments, quality assurance requirements, and the phased rollout decision. Signals: mentions of the client name, contract terms, supplier performance, or renewal timeline."
                    value={addForm.description}
                    onChange={(e) => {
                      setAddForm(prev => ({ ...prev, description: e.target.value, blueprintId: null }))
                    }}
                    rows={8}
                  />
                  <button
                    type="button"
                    disabled={generatingDesc}
                    onClick={handleGenerateDesc}
                    className="absolute bottom-2.5 right-2.5 h-8 w-8 flex items-center justify-center rounded-full bg-[rgba(61,175,115,0.12)] text-[#2D8A5E] hover:bg-[rgba(61,175,115,0.22)] transition-colors"
                    title="Generate description from General Summary"
                  >
                    {generatingDesc ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                  </button>
                </div>
              </div>
            </div>
          )}
          <div className="flex justify-end gap-2 pt-4">
            <Button variant="outline" onClick={() => { setAddSectionOpen(false); setAddForm({ name: "", description: "", blueprintId: null }) }}>Cancel</Button>
            <Button onClick={handleAddOrExtract}>{hasSections ? "Extract" : "Add"}</Button>
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

      {/* Delete Section Confirmation Dialog */}
      <Dialog open={!!deleteSectionTarget} onOpenChange={(v) => { if (!v) setDeleteSectionTarget(null) }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete Section</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Delete this section? This removes all its tags from the transcript.
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setDeleteSectionTarget(null)}>Cancel</Button>
            <Button variant="destructive" onClick={confirmDeleteSection}>Delete</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
