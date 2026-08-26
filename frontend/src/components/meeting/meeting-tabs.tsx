import { useState, useEffect, useLayoutEffect, useCallback, useRef, forwardRef, useImperativeHandle, type ReactNode, type RefObject } from "react"
import { createPortal } from "react-dom"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  SoftMenu,
  MenuItem,
  MenuItemTitle,
  MenuItemDescription,
  MENU_SILK_MS,
} from "@/components/ui/menu"
import { Tabs, TabsList, TabsTrigger, TabsIndicator } from "@/components/ui/tabs"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { EditorToolbar } from "@/components/ui/tiptap-editor"
import type { Editor } from "@tiptap/react"
import { cn } from "@/lib/utils"
import {
  Loader2, RefreshCw, Plus, Pencil, Sparkles, ChevronDown, ChevronRight,
  FileText, FolderOpen, GitBranch, Trash2, Download, FileType2,
  ListTodo, ArrowLeftRight,
} from "lucide-react"
import {
  extract, deleteSection,
  regenerateSection, getSectionMd,
  saveSectionMd, updateMeeting, getMeeting,
  uploadMeetingImage,
  allocateSection, deleteSectionAllocation, createCollection,
  generateSectionDescription, getSummaryTranslations, getActiveTranslations,
  getTask, getSectionTodoCandidates,
  type Meeting, type MeetingTab, type ExtractReceipt,
  type TranscriptSegment, type MeetingTodoCandidate,
} from "@/api/client"
import { listChains } from "@/api/file-mgmt"
import type { Chain } from "@/types/file-mgmt"
import { MeetingCreateTodosDialog } from "@/components/meeting/meeting-create-todos-dialog"
import { useShallow } from "zustand/react/shallow"
import { useAppStore } from "@/stores/app-store"
import { useT } from "@/i18n/use-t"
import { formatApiError } from "@/api/http"
import { useBlueprintStream } from "@/hooks/use-blueprint-stream"
import {
  useSectionStream,
  startSectionStream,
  dismissSectionStream,
  getSectionStreamState,
  subscribeSectionStreams,
  sectionStreamHasOutput,
  sectionStreamIsOpenable,
} from "@/hooks/use-section-stream"
import { useTranslationStream, startTranslationStream } from "@/hooks/use-translation-stream"
import { toast } from "sonner"
import { useScrollEdgeFade } from "@/hooks/use-scroll-edge-fade"
import { TranscriptTab, SpeakersTab } from "./transcript-panel"
import { SummaryTranslateControl } from "./summary-translate-control"
import {
  SummaryMarkdownViewer,
  unescapeMarkdownOverEscapes,
} from "./summary-markdown-viewer"
import {
  exportSummaryAsPdf,
  exportSummaryMarkdown,
  safeExportBasename,
} from "@/lib/meeting-summary-export"

/** DatabaseView stays mounted — refresh Files / Timeline after ingest or cancel. */
async function refreshKeepMountedLibrary(collectionId: string | null | undefined) {
  const colId = (collectionId || "").trim()
  if (!colId) return
  try {
    const { useFileMgmtStore } = await import("@/stores/file-mgmt-store")
    await useFileMgmtStore.getState().refreshLibrarySurfaces(colId)
  } catch {
    /* store / Database view may be unmounted */
  }
}

interface Props {
  meetingId: string
  meeting: Meeting
  notesContent: string
  onNotesChange: (value: string) => void
  onMeetingUpdate: (m: Meeting) => void
  onSeekTo: (time: number) => void
  onFocusSentence?: (refId: string) => void
  onActiveTabChange?: (tabId: string) => void
  transcriptSegments: TranscriptSegment[]
  partialText?: string
  focusRef?: { id: string; ts: number } | null
  activeSectionTag?: string
  forceTranscriptTab?: number
  tabBarOffset?: number
  floatingPanelOpen?: boolean
  canShift?: boolean
  playbackTime?: number | null
  className?: string
  /** Controlled Summary section id (e.g. tab_general / section tab). */
  selectedSummaryId?: string
  onSelectedSummaryIdChange?: (tabId: string) => void
  /** Parent binds open-handler for Add Section (section rail header button). */
  onBindOpenAddSection?: (open: () => void) => void
  /** Parent can disable Add Section while meeting is busy. */
  onBusyChange?: (busy: boolean) => void
  /**
   * Full section-rail model for the right side-panel Sections tab.
   * Parent renders the list; actions stay in this module.
   */
  onSectionRailModelChange?: (model: SectionRailModel) => void
  onBindSectionRailActions?: (actions: SectionRailActions) => void
  /** Parent tracks Summary vs Notes (content main tabs only). */
  onMainTabChange?: (tab: string) => void
  /**
   * When a sentence-ref needs Transcript focus, parent opens the side panel
   * Transcript tab (main area no longer hosts Transcript / Speaker).
   */
  onRequestSideTab?: (tab: "sections" | "transcript" | "speaker" | "groups") => void
  /** Hide transcript/speaker panels here — parent hosts them in the side rail. */
  hostTranscriptInParent?: boolean
}

/** Item shown in the top-right Sections rail */
export type SectionRailItem = {
  id: string
  label: string
  hint?: string
  kind: "general" | "section" | "blueprint" | "custom" | "early" | "skeleton"
  /** Multi-select (pre-extract blueprint / custom) */
  selected?: boolean
  /** Currently viewed summary section */
  active?: boolean
  shortLabel?: string
  /**
   * Generated content available (md on disk) or live stream tokens —
   * can switch into Summary. Only for general / section.
   */
  ready?: boolean
  /** Tokens flowing — show Streaming badge; still ready to open. */
  streaming?: boolean
  /**
   * Work in flight before first token (server generating / SSE prefilling).
   * Show Generating badge; still openable when ready is also true.
   */
  generating?: boolean
  /** Section already ingested into a collection (allocated_file_id). */
  ingested?: boolean
}

export type SectionRailModel = {
  thinking: boolean
  busy: boolean
  hasBlueprint: boolean
  hasSections: boolean
  canBreakdown: boolean
  items: SectionRailItem[]
}

export type SectionRailActions = {
  openAddSection: () => void
  selectSection: (id: string) => void
  toggleBlueprint: (id: string) => void
  removeCustom: (index: number) => void
  breakdown: () => void
}

// MarkdownViewer lives in summary-markdown-viewer.tsx (shared with note editor)

// ── Thinking skeleton (shown while LLM is generating) ─────────────

function ThinkingSkeleton() {
  return (
    <div className="pm-meeting-fence-pad">
      <div className="sk-thinking-flow pm-meeting-fence-card rounded-[var(--pm-r,16px)] p-6 pt-10 space-y-4">
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
    </div>
  )
}

// ── Editable section content (readonly view + edit mode) ──────────

const EditableSectionContent = forwardRef<{ startEditing: () => void }, {
  content: string
  onSave: (updated: string) => Promise<void>
  onRefClick: (id: string) => void
  speakerNames: Record<string, string>
  /** Plain title (e.g. General) when not using section name editing */
  title?: ReactNode
  /** Section name editing: prefix like "T1", name text, commit handler */
  titlePrefix?: string
  titleName?: string
  onSaveTitle?: (name: string) => Promise<void>
  metadata?: ReactNode
  toolbar?: ReactNode
  actionsDisabled?: boolean
  editDisabled?: boolean
  stickyOffset?: number
  /** When true, hide inline edit affordance (edit lives in main pill toolbar). */
  hideInlineEdit?: boolean
  /** Host for collection pill on section title row (Choose sits on description row). */
  ingestHostRef?: RefObject<HTMLDivElement | null>
}>(function EditableSectionContent({
  content,
  onSave,
  onRefClick,
  speakerNames,
  title,
  titlePrefix,
  titleName,
  onSaveTitle,
  metadata,
  toolbar,
  actionsDisabled,
  editDisabled,
  stickyOffset: _stickyOffset = 0,
  hideInlineEdit = false,
  ingestHostRef,
}, ref) {
  const t = useT()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(content)
  const [saving, setSaving] = useState(false)
  /** Live Tiptap instance while editing — format strip is hosted above the title. */
  const [editEditor, setEditEditor] = useState<Editor | null>(null)

  // Section title (name only) — pencil next to title
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState(titleName ?? "")
  const titleSavingRef = useRef(false)
  const titleInputRef = useRef<HTMLInputElement>(null)
  const canEditTitle = !!onSaveTitle && titleName !== undefined

  useEffect(() => {
    setDraft(content)
    setEditing(false)
    setEditEditor(null)
  }, [content])

  useEffect(() => {
    setTitleDraft(titleName ?? "")
    setEditingTitle(false)
  }, [titleName])

  useEffect(() => {
    if (!editing) setEditEditor(null)
  }, [editing])

  useEffect(() => {
    if (editingTitle) titleInputRef.current?.focus()
  }, [editingTitle])

  useImperativeHandle(ref, () => ({
    startEditing: () => {
      if (!actionsDisabled && !editDisabled) setEditing(true)
    },
  }), [actionsDisabled, editDisabled])

  const commitTitle = async () => {
    if (!editingTitle || !onSaveTitle) return
    if (titleSavingRef.current) return
    const next = titleDraft.trim()
    if (!next) {
      setTitleDraft(titleName ?? "")
      setEditingTitle(false)
      return
    }
    if (next === (titleName ?? "").trim()) {
      setEditingTitle(false)
      return
    }
    titleSavingRef.current = true
    try {
      await onSaveTitle(next)
      setEditingTitle(false)
    } catch (err) {
      toast.error(t("meeting.saveFailed", { error: formatApiError(err, t) }))
    } finally {
      titleSavingRef.current = false
    }
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      // Tiptap md export escapes ~ _ [ ] ; strip so ~1.5 stays ~1.5 on disk
      const cleaned = unescapeMarkdownOverEscapes(draft)
      await onSave(cleaned)
      setEditing(false)
      setEditEditor(null)
      toast.success(t("common.saved"))
    } catch (err) {
      toast.error(t("meeting.saveFailed", { error: formatApiError(err, t) }))
    }
    setSaving(false)
  }

  const handleCancel = () => {
    setDraft(content)
    setEditing(false)
    setEditEditor(null)
  }

  const editToolbarActions = (
    <div className="flex items-center gap-2">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={handleCancel}
      >
        {t("common.cancel")}
      </Button>
      <Button
        type="button"
        variant="default"
        size="sm"
        onClick={handleSave}
        disabled={saving}
      >
        {saving ? <Loader2 className="size-3.5 animate-spin mr-1" /> : null}
        {t("common.save")}
      </Button>
    </div>
  )

  const titleBlock = canEditTitle ? (
    <div className="pm-meeting-section-title-row group/title min-w-0 flex-1">
      {titlePrefix ? (
        <span className="pm-meeting-section-title-prefix shrink-0">{titlePrefix}</span>
      ) : null}
      {editingTitle ? (
        <input
          ref={titleInputRef}
          className="pm-meeting-section-title-input"
          value={titleDraft}
          onChange={(e) => setTitleDraft(e.target.value)}
          onBlur={() => { void commitTitle() }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault()
              void commitTitle()
            }
            if (e.key === "Escape") {
              e.preventDefault()
              setTitleDraft(titleName ?? "")
              setEditingTitle(false)
            }
          }}
          aria-label={t("meeting.sectionTitle")}
        />
      ) : (
        <>
          <span className="pm-meeting-section-title-text min-w-0">
            {titleName || t("common.untitled")}
          </span>
          {!actionsDisabled && !editDisabled && (
            <button
              type="button"
              className="pm-meeting-section-title-edit-btn"
              onClick={(e) => {
                e.stopPropagation()
                setEditingTitle(true)
              }}
              title={t("meeting.editTitle")}
              aria-label={t("meeting.editTitle")}
            >
              <Pencil className="size-3" />
            </button>
          )}
        </>
      )}
    </div>
  ) : title ? (
    <div className="pm-meeting-title pm-meeting-section-title min-w-0">{title}</div>
  ) : (
    <div className="pm-meeting-section-title min-w-0 flex-1" />
  )

  return (
    <div className="relative min-h-full flex flex-col">
      {toolbar}

      {/*
        Format strip: sticky under tab bar, ABOVE section title.
        Solid paper bg (not transparent whisper over prose).
      */}
      {editing && editEditor && !editEditor.isDestroyed ? (
        <div className="pm-meeting-summary-fmt-bar">
          <EditorToolbar
            editor={editEditor}
            stickyOffset={0}
            actions={editToolbarActions}
          />
        </div>
      ) : null}

      <div className="pm-meeting-body-prose">
        {(title || canEditTitle || ingestHostRef) && (
          <div className="pm-meeting-section-head">
            {titleBlock}
            {/* Collection pill only — width synced to toolbar actions; Choose on description row */}
            {ingestHostRef ? (
              <div
                ref={ingestHostRef}
                className="pm-meeting-section-ingest-slot shrink-0"
              />
            ) : null}
            {!hideInlineEdit && !editing && !actionsDisabled && !editDisabled && (
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => setEditing(true)}
                title={t("common.edit")}
                aria-label={t("common.edit")}
              >
                <Pencil className="size-3.5" />
              </Button>
            )}
          </div>
        )}

        {metadata}

        {/* Prose — same horizontal pad as title / description (body-prose) */}
        <div className="pm-meeting-body-read">
          {editing ? (
            <MarkdownEditor
              value={draft}
              onChange={setDraft}
              minHeight="250px"
              showToolbar={false}
              flush
              onEditorReady={(ed) => setEditEditor(ed as Editor)}
              className="pm-meeting-summary-edit-host"
            />
          ) : (
            <SummaryMarkdownViewer md={content} onRefClick={onRefClick} speakerNames={speakerNames} />
          )}
        </div>
      </div>
    </div>
  )
})

// ── Section metadata (between title bar and content) ──────────────

const SectionMetadata = forwardRef<{ startEditingDescription: () => void }, {
  tab: MeetingTab
  blueprint: Meeting["blueprint"]
  tabs: MeetingTab[]
  meetingId: string
  onMeetingUpdate: (m: Meeting) => void
  onIngestingChange?: (tabId: string, v: boolean) => void
  /**
   * Parent-tracked ingest for this tab (survives section switch).
   * Local `ingesting` alone is lost when SectionMetadata remounts.
   */
  parentIngesting?: boolean
  hideTitle?: boolean
  /** Portal collection pill into title-row host; Choose a collection stays on description row. */
  ingestHostRef?: RefObject<HTMLDivElement | null>
  /** True while General is being rewritten — ingest would snapshot stale MD. */
  ingestLocked?: boolean
}>(function SectionMetadata({
  tab,
  blueprint,
  tabs,
  meetingId,
  onMeetingUpdate,
  onIngestingChange,
  parentIngesting = false,
  hideTitle,
  ingestHostRef,
  ingestLocked = false,
}, ref) {
  const t = useT()
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
  /** MD edited after allocate — offer manual Update collection (new version). */
  const needsReingest = ingested && !!tab.needs_reingest
  // Three-state pill (P2-02):
  //   1. ingested           → solid green pill, click for actions / update
  //   2. hasSuggestion      → dashed outline pill, click to ingest
  //   3. no suggestion      → "Choose a collection" button
  const hasSuggestion = hasAssociated && !ingested
  const displayName = associatedName
  // "Active" solid style only when actually ingested; suggestion uses dashed style
  const displayActive = ingested
  const displaySuggestion = hasSuggestion

  /** Local only while this instance is mounted; parentIngesting survives tab switches. */
  const [localIngesting, setLocalIngesting] = useState(false)
  const ingesting = localIngesting || parentIngesting
  // Parent notify ref — declared early so handleIngest can call it after unmount
  const onIngestingChangeRef = useRef(onIngestingChange)
  onIngestingChangeRef.current = onIngestingChange
  const [dropdownOpen, setDropdownOpen] = useState(false)
  /** Actions menu when already ingested (open file / files / timeline / remove). */
  const [actionsOpen, setActionsOpen] = useState(false)
  const [cancelOpen, setCancelOpen] = useState(false)
  /**
   * Switch confirm: after collection + chain chosen when already allocated elsewhere.
   * `__new__` = create new collection (main chain only).
   */
  const [switchPending, setSwitchPending] = useState<{
    colId: string
    chainId: string | null
  } | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLElement>(null)
  const topPillRef = useRef<HTMLButtonElement>(null)
  const { collections, fetchCollections, setSidebarView, setActiveCollection, setPendingOpenFile } =
    useAppStore(
      useShallow((s) => ({
        collections: s.collections,
        fetchCollections: s.fetchCollections,
        setSidebarView: s.setSidebarView,
        setActiveCollection: s.setActiveCollection,
        setPendingOpenFile: s.setPendingOpenFile,
      }))
    )
  const generalAlloc = tabs.find(
    (t) => t.tab_id === "tab_general" && !!(t.allocated_file_id || "").trim(),
  )
  const generalAllocColId = generalAlloc?.associated_collection_id || ""
  const generalAllocColName =
    generalAlloc?.associated_collection_name ||
    collections.find((c) => c.id === generalAllocColId)?.name ||
    ""
  const sectionAllocs = tabs.filter(
    (t) => t.tab_id !== "tab_general" && !!(t.allocated_file_id || "").trim(),
  )
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState(tab.name)
  // 首次 ingest：还没 associated collection 时，选中后立刻在顶部按钮显示 pending 名称
  const [pendingName, setPendingName] = useState<string | null>(null)
  /** Last bridge node_id from allocate (fallback: resolve via external_ref). */
  const lastNodeIdRef = useRef<string | null>(null)
  /**
   * Collection dropdown → chain flyout (right of parent menu).
   * flyoutColId = collection row showing chains; chains cached per col.
   */
  const [flyoutColId, setFlyoutColId] = useState<string | null>(null)
  const [chainsByCol, setChainsByCol] = useState<Record<string, Chain[]>>({})
  const [chainFlyoutLoading, setChainFlyoutLoading] = useState(false)
  const flyoutItemRef = useRef<HTMLButtonElement | null>(null)
  const flyoutCloseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** Explicit viewport coords for chain flyout (avoids broken anchor placement). */
  const [flyoutCoords, setFlyoutCoords] = useState<{
    top: number
    left: number
  } | null>(null)
  /** Suggested-pill path: standalone chain menu when multi-chain */
  const [pillChainOpen, setPillChainOpen] = useState(false)
  const [pillChainOptions, setPillChainOptions] = useState<Chain[]>([])
  const [pillChainColId, setPillChainColId] = useState<string | null>(null)
  const [pillChainLoading, setPillChainLoading] = useState(false)
  const [createTodosOpen, setCreateTodosOpen] = useState(false)
  const [createTodosLoading, setCreateTodosLoading] = useState(false)
  const [liveCandidates, setLiveCandidates] = useState<MeetingTodoCandidate[]>(
    [],
  )

  /**
   * Open Create todos — prefer stored candidates (from allocate).
   * Re-parse only when none are stored (legacy allocate / never extracted).
   * Does NOT re-generate on every open (stable list + created_todo_id).
   */
  const openCreateTodos = async () => {
    setActionsOpen(false)
    setCreateTodosOpen(true)
    setCreateTodosLoading(true)
    const cached = tab.todo_candidates || []
    setLiveCandidates(cached)
    try {
      const needExtract = cached.length === 0
      const res = await getSectionTodoCandidates(meetingId, tab.tab_id, {
        refresh: needExtract,
      })
      const list = res.candidates || []
      setLiveCandidates(list)
      // Keep parent meeting in sync when we just persisted a first extract
      if (needExtract) {
        try {
          const m = await getMeeting(meetingId)
          onMeetingUpdate(m)
        } catch {
          /* candidates still shown via liveCandidates */
        }
      }
    } catch (err) {
      toast.error(
        t("meeting.loadTodosFailed", { error: formatApiError(err, t) }),
      )
    } finally {
      setCreateTodosLoading(false)
    }
  }

  const showTopButton = hasAssociated || ingested || !!pendingName
  const topButtonLabel = ingesting
    ? t("library.ingesting")
    : displayName || pendingName || associatedName
  const topButtonIsActive = ingesting || displayActive || !!pendingName

  // Description only — click the text area to edit (title is edited next to the section head)
  const [editingDesc, setEditingDesc] = useState(false)
  const [descDraft, setDescDraft] = useState(description)
  const descSavingRef = useRef(false)
  const descInputRef = useRef<HTMLTextAreaElement>(null)

  useImperativeHandle(ref, () => ({
    startEditingDescription: () => setEditingDesc(true),
  }))

  useEffect(() => {
    setDescDraft(description)
    setEditingDesc(false)
  }, [tab.tab_id, description])

  useEffect(() => {
    if (!editingDesc) return
    const el = descInputRef.current
    if (!el) return
    el.focus()
    // Grow to content so layout matches the display paragraph
    el.style.height = "auto"
    el.style.height = `${el.scrollHeight}px`
  }, [editingDesc])

  const commitDescription = async () => {
    if (!editingDesc) return
    if (descSavingRef.current) return
    const next = descDraft
    if (next === description) {
      setEditingDesc(false)
      return
    }
    descSavingRef.current = true
    try {
      const bp = blueprint ?? []
      const m = await updateMeeting(meetingId, {
        blueprint: bp.map((b) => {
          if (b.blueprint_id === tab.blueprint_id) {
            return { ...b, tab_description: next }
          }
          return b
        }),
        tabs: (tabs ?? []).map((t) => {
          if (t.tab_id === tab.tab_id) {
            return { ...t, description: next, is_dirty: true }
          }
          return t
        }),
      })
      setEditingDesc(false)
      onMeetingUpdate(m)
    } catch (err) {
      toast.error(t("meeting.saveFailed", { error: formatApiError(err, t) }))
    } finally {
      descSavingRef.current = false
    }
  }

  // Fetch collections when dropdown opens
  useEffect(() => {
    if (dropdownOpen) {
      fetchCollections()
      setNewName(tab.name)
    } else {
      setFlyoutColId(null)
      flyoutItemRef.current = null
      setFlyoutCoords(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dropdownOpen, tab.name])

  const clearFlyoutCloseTimer = () => {
    if (flyoutCloseTimerRef.current) {
      clearTimeout(flyoutCloseTimerRef.current)
      flyoutCloseTimerRef.current = null
    }
  }

  const scheduleCloseFlyout = () => {
    clearFlyoutCloseTimer()
    flyoutCloseTimerRef.current = setTimeout(() => {
      setFlyoutColId(null)
      flyoutItemRef.current = null
      setFlyoutCoords(null)
    }, 180)
  }

  const measureFlyoutCoords = (itemEl: HTMLElement | null) => {
    if (!itemEl || typeof window === "undefined") return null
    const menuEl = itemEl.closest("[data-slot='menu']") as HTMLElement | null
    const ir = itemEl.getBoundingClientRect()
    const mr = menuEl?.getBoundingClientRect() ?? ir
    const gap = 6
    const estW = 220
    const estH = 220
    let left = mr.right + gap
    let top = ir.top
    if (left + estW > window.innerWidth - 8) {
      left = Math.max(8, mr.left - estW - gap)
    }
    if (top + estH > window.innerHeight - 8) {
      top = Math.max(8, window.innerHeight - estH - 8)
    }
    if (top < 8) top = 8
    return { top, left }
  }

  const loadChainsForCol = async (colId: string): Promise<Chain[]> => {
    const cached = chainsByCol[colId]
    if (cached) return cached
    setChainFlyoutLoading(true)
    try {
      const chains = await listChains(colId)
      setChainsByCol((prev) => ({ ...prev, [colId]: chains }))
      return chains
    } finally {
      setChainFlyoutLoading(false)
    }
  }

  /**
   * After collection + chain resolved:
   * - switching to another collection → confirm dialog (with chain)
   * - otherwise → ingest immediately
   */
  const commitCollectionAndChain = (
    colId: string,
    chainId: string | null,
  ) => {
    setFlyoutColId(null)
    setFlyoutCoords(null)
    setDropdownOpen(false)
    setPillChainOpen(false)
    setPillChainColId(null)
    if (ingested && colId !== associatedId) {
      setSwitchPending({ colId, chainId })
      return
    }
    void doIngest(colId, chainId)
  }

  const openChainFlyout = async (
    colId: string,
    itemEl: HTMLButtonElement | null,
  ) => {
    clearFlyoutCloseTimer()
    flyoutItemRef.current = itemEl
    // Position immediately from the row (before async load) so flyout never floats away
    const coords = measureFlyoutCoords(itemEl)
    if (coords) setFlyoutCoords(coords)
    try {
      const chains = await loadChainsForCol(colId)
      // Main-only collections: never show the secondary flyout
      const hasBranch = chains.some((c) => !c.is_main)
      if (!hasBranch) {
        setFlyoutColId((cur) => (cur === colId ? null : cur))
        setFlyoutCoords(null)
        return
      }
      // Re-measure after paint (menu may have scrolled / row highlight changed)
      const again = measureFlyoutCoords(flyoutItemRef.current)
      if (again) setFlyoutCoords(again)
      setFlyoutColId(colId)
    } catch (err) {
      toast.error(
        t("common.failedWithError", { error: formatApiError(err, t) }),
      )
      setFlyoutColId((cur) => (cur === colId ? null : cur))
      setFlyoutCoords(null)
    }
  }

  /** Click a collection row: only-main → commit; multi → keep right flyout open. */
  const handleSelectCollection = async (
    colId: string,
    itemEl?: HTMLButtonElement | null,
  ) => {
    try {
      const chains = await loadChainsForCol(colId)
      const hasBranch = chains.some((c) => !c.is_main)
      if (!hasBranch) {
        const main = chains.find((c) => c.is_main)
        commitCollectionAndChain(colId, main?.chain_id ?? null)
        return
      }
      await openChainFlyout(colId, itemEl ?? flyoutItemRef.current)
    } catch (err) {
      toast.error(
        t("common.failedWithError", { error: formatApiError(err, t) }),
      )
    }
  }

  /**
   * Suggested collection pill: load chains; Main-only → ingest; multi → menu under pill.
   */
  const beginIngestWithChainPick = async (colId: string) => {
    setActionsOpen(false)
    setDropdownOpen(false)
    setFlyoutColId(null)
    setPillChainColId(colId)
    setPillChainLoading(true)
    setPillChainOpen(true)
    try {
      const chains = await listChains(colId)
      setPillChainOptions(chains)
      const hasBranch = chains.some((c) => !c.is_main)
      if (!hasBranch) {
        const main = chains.find((c) => c.is_main)
        setPillChainOpen(false)
        setPillChainColId(null)
        toast.info(t("meeting.ingestToMain"))
        await doIngest(colId, main?.chain_id ?? null)
        return
      }
    } catch (err) {
      setPillChainOpen(false)
      setPillChainColId(null)
      toast.error(
        t("common.failedWithError", { error: formatApiError(err, t) }),
      )
    } finally {
      setPillChainLoading(false)
    }
  }

  const doIngest = async (colId: string, chainId?: string | null) => {
    setDropdownOpen(false)
    setFlyoutColId(null)
    setFlyoutCoords(null)
    setPillChainOpen(false)
    setSwitchPending(null)
    const colMeta = collections.find((c) => c.id === colId)
    setPendingName(colMeta?.name || colId)
    try {
      // Delete old allocation first; fail fast — don't proceed if cleanup fails
      if (ingested && colId !== associatedId) {
        const oldCol = associatedId
        await deleteSectionAllocation(meetingId, tab.tab_id)
        await refreshKeepMountedLibrary(oldCol)
      }
    } catch (err) {
      toast.error(t("meeting.failedRemoveAlloc", { error: formatApiError(err, t) }))
      setPendingName(null)
      return
    }
    try {
      await handleIngest(colId, chainId)
      fetchCollections()
    } catch { /* error handled in parent */ }
    setPendingName(null)
  }

  const handleCreateAndSelect = async () => {
    if (!newName.trim() || !!pendingName) return
    // If switching, confirm first (new collection → main chain only)
    if (ingested) {
      setSwitchPending({ colId: "__new__", chainId: null })
      setDropdownOpen(false)
      return
    }
    doCreateAndIngest()
  }

  const doCreateAndIngest = async () => {
    setDropdownOpen(false)
    setPendingName(newName.trim())
    setSwitchPending(null)
    // Delete old allocation first; fail fast
    if (ingested) {
      try {
        await deleteSectionAllocation(meetingId, tab.tab_id)
        await refreshKeepMountedLibrary(associatedId)
      } catch (err) {
        toast.error(t("meeting.failedRemoveAlloc", { error: formatApiError(err, t) }))
        setPendingName(null)
        return
      }
    }
    try {
      const res = await createCollection(newName.trim())
      if (res.error) throw new Error(res.error)
      const colId = res.id
      if (!colId) throw new Error(t("meeting.noCollectionLinked"))
      // New collection only has main chain
      await handleIngest(colId, null)
      await fetchCollections()
      setCreating(false)
      toast.success(t("meeting.createdAndIngested", { name: newName.trim() }))
    } catch (err) {
      toast.error(t("common.failedWithError", { error: formatApiError(err, t) }))
    }
    setPendingName(null)
  }

  const handleIngest = async (colId: string, chainId?: string | null) => {
    // Capture tab id for this job — survives section switch / remount
    const jobTabId = tab.tab_id
    if (localIngesting || parentIngesting || ingestLocked) return
    const overlap = tabs.filter(
      (t) =>
        t.tab_id !== jobTabId &&
        !!(t.allocated_file_id || "").trim() &&
        (t.associated_collection_id || "") === colId,
    )
    if (overlap.length > 0) {
      const generalIn = overlap.some((t) => t.tab_id === "tab_general")
      toast.info(
        jobTabId !== "tab_general" && generalIn
          ? t("meeting.generalAlreadyThis")
          : jobTabId === "tab_general"
            ? t("meeting.sectionAlreadyThis")
            : t("meeting.otherPartThis"),
      )
    }
    setLocalIngesting(true)
    onIngestingChangeRef.current?.(jobTabId, true)
    try {
      const res = await allocateSection(meetingId, jobTabId, colId, chainId)
      // Strip bridge fields before treating as Meeting meta
      const {
        file_id: bridgeFileId,
        task_id: bridgeTaskId,
        node_id: bridgeNodeId,
        source: _bridgeSource,
        collection_id: _bridgeCol,
        chain_id: _bridgeChain,
        todo_candidate_count: _todoCount,
        ...meetingRest
      } = res
      if (bridgeNodeId) lastNodeIdRef.current = bridgeNodeId
      onMeetingUpdate(meetingRest as Meeting)
      const wasUpdate = !!tab.allocated_file_id

      // Node + Meeting folder row exist as soon as allocate returns.
      // DatabaseView stays mounted across sidebar switches — refresh now.
      await refreshKeepMountedLibrary(colId)

      // allocate returns as soon as the file is registered; indexing is async.
      // Keep pill "Ingesting…" until the task finishes — don't toast "done" early.
      if (bridgeTaskId && bridgeFileId) {
        toast.info(
          wasUpdate
            ? t("meeting.collectionUpdateStarted")
            : t("meeting.ingestStartedIndexing"),
        )
        try {
          const { useFileMgmtStore } = await import("@/stores/file-mgmt-store")
          useFileMgmtStore
            .getState()
            ._startTaskPolling(colId, bridgeTaskId, bridgeFileId, {
              silentToast: true,
            })
        } catch { /* ignore store wiring */ }

        let settled = false
        for (let i = 0; i < 120; i++) {
          await new Promise((r) => setTimeout(r, 1500))
          try {
            const task = await getTask(bridgeTaskId)
            if (task.status === "completed") {
              toast.success(wasUpdate ? t("meeting.collectionUpdated") : t("meeting.ingestedToCollection"))
              settled = true
              break
            }
            if (task.status === "failed") {
              toast.error(
                t("meeting.ingestFailed", { error: task.error || task.message || task.filename || t("common.unknown") }),
              )
              settled = true
              break
            }
          } catch {
            // keep polling
          }
        }
        if (!settled) {
          toast.info(t("meeting.ingestBackground"))
        }
      } else {
        toast.success(wasUpdate ? t("meeting.collectionUpdated") : t("meeting.ingestedToCollection"))
      }
    } catch (err) {
      toast.error(t("meeting.ingestFailed", { error: formatApiError(err, t) }))
    } finally {
      setLocalIngesting(false)
      // Always clear the job's tab on parent (even if this instance unmounted)
      onIngestingChangeRef.current?.(jobTabId, false)
    }
  }

  const goToDatabase = (colId: string) => {
    setActiveCollection(colId)
    setSidebarView("database")
  }

  /** Open file detail dialog in Database view. */
  const handleOpenFile = () => {
    setActionsOpen(false)
    const fileId = tab.allocated_file_id
    const colId = associatedId
    if (!fileId || !colId) {
      toast.error(t("meeting.noIngestedFile"))
      return
    }
    goToDatabase(colId)
    // Delay so DatabaseView mounts with the right collection
    setTimeout(() => {
      setPendingOpenFile(fileId)
    }, 80)
  }

  /** Files tab → Meeting system folder → open detail. */
  const handleShowInFiles = () => {
    setActionsOpen(false)
    const fileId = tab.allocated_file_id
    const colId = associatedId
    if (!fileId || !colId) {
      toast.error(t("meeting.noIngestedFile"))
      return
    }
    goToDatabase(colId)
    setTimeout(() => {
      window.dispatchEvent(
        new CustomEvent("open-meeting-file-in-folder", {
          detail: {
            collectionId: colId,
            fileId,
            meetingId,
            tabId: tab.tab_id,
          },
        })
      )
    }, 100)
  }

  /** Timeline tab → focus meeting anchor node. */
  const handleShowOnTimeline = async () => {
    setActionsOpen(false)
    const colId = associatedId
    if (!colId) {
      toast.error(t("meeting.noCollectionLinked"))
      return
    }
    goToDatabase(colId)
    try {
      let nodeId = lastNodeIdRef.current
      if (!nodeId) {
        const { getNodeByExternalRef } = await import("@/api/file-mgmt")
        const node = await getNodeByExternalRef(colId, `meeting:${meetingId}`)
        nodeId = node.node_id
        lastNodeIdRef.current = nodeId
      }
      const { useFileMgmtStore } = await import("@/stores/file-mgmt-store")
      useFileMgmtStore.getState().requestTimelineFocus(nodeId)
    } catch (err) {
      toast.error(
        t("meeting.timelineNodeNotFound", { error: err instanceof Error ? err.message : "" }).trim()
      )
    }
  }

  /**
   * Manual re-ingest to the same collection → new file version (Notes-style).
   * Does not auto-run on save; user clicks Update collection.
   */
  const handleUpdateCollection = async () => {
    setActionsOpen(false)
    const colId = associatedId
    if (!colId || !tab.allocated_file_id) {
      toast.error(t("meeting.sectionNotIngested"))
      return
    }
    await handleIngest(colId, tab.allocated_chain_id || null)
  }

  const handleCancelIngest = async () => {
    const jobTabId = tab.tab_id
    setCancelOpen(false)
    setLocalIngesting(true)
    onIngestingChangeRef.current?.(jobTabId, true)
    try {
      const colId = associatedId
      const m = await deleteSectionAllocation(meetingId, jobTabId)
      onMeetingUpdate(m)
      await refreshKeepMountedLibrary(colId)
      toast.success(t("meeting.ingestionCancelled"))
    } catch (err) {
      toast.error(t("meeting.cancelFailed", { error: formatApiError(err, t) }))
    } finally {
      setLocalIngesting(false)
      onIngestingChangeRef.current?.(jobTabId, false)
    }
  }

  const BUTTON_W = "w-[250px]"
  const portalIngest = !!ingestHostRef
  const pillShellRef = useRef<HTMLDivElement>(null)
  const chooseShellRef = useRef<HTMLDivElement>(null)

  // Re-render when host mounts so createPortal has a target
  const [ingestPortalReady, setIngestPortalReady] = useState(false)
  useLayoutEffect(() => {
    setIngestPortalReady(!!ingestHostRef?.current)
  }, [ingestHostRef, tab.tab_id, showTopButton, displayActive, ingesting])

  /**
   * Ingest column width = sum of main toolbar action buttons
   * (`.pm-meeting-tabs-actions`). Pill (title row) + Choose (description row)
   * share that width and L/R-align under the toolbar.
   */
  useLayoutEffect(() => {
    if (!portalIngest) return
    const shell = containerRef.current?.closest(".pm-meeting-tabs-shell") as HTMLElement | null
    const actions = shell?.querySelector(".pm-meeting-tabs-actions") as HTMLElement | null
    if (!shell || !actions) return
    const apply = () => {
      // Fixed ingest column width (pill + Choose a collection)
      const COL_W = 250
      shell.style.setProperty("--pm-meeting-ingest-col-w", `${COL_W}px`)
    }
    apply()
    const ro = new ResizeObserver(apply)
    ro.observe(actions)
    return () => ro.disconnect()
  }, [portalIngest, tab.tab_id, showTopButton, displayActive, ingesting])

  // SoftMenu click-outside — pill + choose may live in different DOM hosts
  useEffect(() => {
    if (!dropdownOpen && !actionsOpen && !pillChainOpen) return
    const handler = (e: MouseEvent) => {
      const t = e.target as Node
      const inPill = pillShellRef.current?.contains(t)
      const inChoose = chooseShellRef.current?.contains(t)
      const inMenu = (e.target as Element)?.closest?.("[data-slot='menu']")
      if (!inPill && !inChoose && !inMenu) {
        setDropdownOpen(false)
        setActionsOpen(false)
        setCreating(false)
        setFlyoutColId(null)
        setPillChainOpen(false)
        setPillChainColId(null)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [dropdownOpen, actionsOpen, pillChainOpen])

  const flyoutChains = flyoutColId ? chainsByCol[flyoutColId] || [] : []

  const tabLabel = (() => {
    const sections = tabs.filter(t => t.type === "section" && t.md_file_path)
    const idx = sections.findIndex(t => t.tab_id === tab.tab_id)
    return idx >= 0 ? t("meeting.topicN", { n: idx + 1 }) : tab.tab_id
  })()

  /** Collection pill — title row (portal) or top of legacy column */
  const pillControls = showTopButton ? (
    <div
      ref={pillShellRef}
      className={cn(
        portalIngest
          ? "pm-meeting-ingest-col pm-meeting-ingest-col--pill w-full"
          : "w-full",
      )}
    >
      <button
        type="button"
        ref={topPillRef}
        disabled={ingesting || ingestLocked}
        onClick={() => {
          if (displayActive) {
            setDropdownOpen(false)
            setFlyoutColId(null)
            setPillChainOpen(false)
            setActionsOpen((v) => !v)
          } else if (associatedId) {
            setActionsOpen(false)
            void beginIngestWithChainPick(associatedId)
          }
        }}
        title={
          needsReingest
            ? t("meeting.sectionEditedUpdate")
            : displayActive
              ? t("meeting.openActions")
              : displaySuggestion
                ? t("meeting.clickChooseChain")
                : undefined
        }
        className={cn(
          "pm-meeting-ingest-pill",
          portalIngest && "pm-meeting-ingest-pill--toolbar",
          ingesting && "sk-thinking-flow",
          topButtonIsActive && "is-active",
          displaySuggestion && !ingesting && "is-suggest",
        )}
      >
        <span className="relative z-10 flex items-center justify-center gap-1 min-w-0 w-full px-0.5">
          {needsReingest && !ingesting ? (
            <RefreshCw className="size-2.5 shrink-0 opacity-50" strokeWidth={2} aria-label={t("meeting.updateAvailable")} />
          ) : null}
          <span className="truncate min-w-0">{topButtonLabel}</span>
          {displayActive && !ingesting ? (
            <ChevronDown className="size-3 opacity-70 shrink-0" />
          ) : null}
        </span>
      </button>
      <SoftMenu
        open={actionsOpen && displayActive}
        portal
        anchorRef={topPillRef}
        matchAnchorWidth
        exitMs={MENU_SILK_MS}
        className="pm-meeting-ingest-menu"
      >
        <div className="pm-meeting-ingest-menu-label">{t("library.collection")}</div>
        {needsReingest && (
          <MenuItem
            className="pm-meeting-ingest-menu-item"
            disabled={ingesting || ingestLocked}
            onClick={() => void handleUpdateCollection()}
          >
            <RefreshCw strokeWidth={1.75} />
            {t("meeting.updateCollection")}
          </MenuItem>
        )}
        <MenuItem className="pm-meeting-ingest-menu-item" onClick={handleOpenFile}>
          <FileText strokeWidth={1.75} />
          {t("meeting.openFile")}
        </MenuItem>
        <MenuItem className="pm-meeting-ingest-menu-item" onClick={handleShowInFiles}>
          <FolderOpen strokeWidth={1.75} />
          {t("meeting.showInFiles")}
        </MenuItem>
        <MenuItem
          className="pm-meeting-ingest-menu-item"
          onClick={() => void handleShowOnTimeline()}
        >
          <GitBranch strokeWidth={1.75} />
          {t("meeting.showOnTimeline")}
        </MenuItem>
        <MenuItem
          className="pm-meeting-ingest-menu-item"
          onClick={() => {
            void openCreateTodos()
          }}
        >
          <ListTodo strokeWidth={1.75} />
          {t("meeting.createTodosEllipsis")}
        </MenuItem>
        {(tab.tab_id === "tab_general" || tab.type === "general") && (
          <MenuItem
            className="pm-meeting-ingest-menu-item"
            disabled={ingesting || ingestLocked}
            onClick={() => {
              setActionsOpen(false)
              setDropdownOpen(true)
            }}
          >
            <ArrowLeftRight strokeWidth={1.75} />
            {t("meeting.changeCollection")}
          </MenuItem>
        )}
        <div className="pm-meeting-ingest-menu-sep" role="separator" />
        <MenuItem
          className="pm-meeting-ingest-menu-item"
          destructive
          onClick={() => {
            setActionsOpen(false)
            setCancelOpen(true)
          }}
        >
          <Trash2 strokeWidth={1.75} />
          {t("meeting.removeFromCollectionAction")}
        </MenuItem>
      </SoftMenu>
    </div>
  ) : null

  const isGeneralTab = tab.tab_id === "tab_general" || tab.type === "general"
  /** General + already ingested: menus stay mounted, trigger lives on the title-row pill. */
  const hideChooseTrigger = isGeneralTab && showTopButton && portalIngest

  /** Choose a collection — description row (portal layout) or under pill (legacy) */
  const chooseControls = (
    <div
      ref={chooseShellRef}
      className={cn(
        portalIngest
          ? "pm-meeting-ingest-col pm-meeting-ingest-col--choose w-full"
          : "w-full",
        hideChooseTrigger && "hidden",
      )}
    >
      {!hideChooseTrigger && (
      <div ref={buttonRef as RefObject<HTMLDivElement>} className="w-full">
        <button
          type="button"
          disabled={ingesting || ingestLocked}
          onClick={() => {
            setActionsOpen(false)
            setDropdownOpen(!dropdownOpen)
          }}
          className={cn(
            "pm-meeting-ingest-pill",
            portalIngest && "pm-meeting-ingest-pill--toolbar",
            dropdownOpen && "is-active",
          )}
        >
          <span className="relative z-10 truncate min-w-0 px-0.5">
            {dropdownOpen ? t("common.cancel") : t("recall.chooseCollection")}
          </span>
        </button>
      </div>
      )}
      <SoftMenu
        open={dropdownOpen}
        portal
        anchorRef={
          (tab.tab_id === "tab_general" || tab.type === "general") && showTopButton
            ? topPillRef
            : buttonRef
        }
        matchAnchorWidth
        exitMs={MENU_SILK_MS}
        className="pm-meeting-ingest-menu"
      >
        <div className="pm-meeting-ingest-menu-label">{t("common.collections")}</div>
        {tab.tab_id !== "tab_general" && generalAllocColName ? (
          <p className="pm-meeting-ingest-menu-hint">
            {t("meeting.generalAlreadyIn", { name: generalAllocColName })}
          </p>
        ) : null}
        {tab.tab_id === "tab_general" && sectionAllocs.length > 0 ? (
          <p className="pm-meeting-ingest-menu-hint">
            {sectionAllocs.length === 1
              ? t("meeting.sectionAlreadyIn", { name: sectionAllocs[0].name || t("meeting.aSection") })
              : t("meeting.nSectionsAlreadyIn", { n: sectionAllocs.length })}
          </p>
        ) : null}
        <div className="pm-meeting-ingest-menu-scroll">
          {(!Array.isArray(collections) || collections.length === 0) && (
            <div className="pm-meeting-ingest-menu-empty">
              {t("meeting.noCollectionsYet")}
            </div>
          )}
          {(Array.isArray(collections) ? collections : []).map((col) => {
            const cached = chainsByCol[col.id]
            const multi =
              Array.isArray(cached) && cached.some((c) => !c.is_main)
            return (
              <MenuItem
                key={col.id}
                active={col.id === associatedId || flyoutColId === col.id}
                disabled={!!pendingName}
                className="pm-meeting-ingest-menu-item"
                onMouseEnter={(e) => {
                  void openChainFlyout(col.id, e.currentTarget)
                }}
                onMouseLeave={scheduleCloseFlyout}
                onClick={(e) => {
                  void handleSelectCollection(col.id, e.currentTarget)
                }}
              >
                <span className="pm-meeting-ingest-menu-name truncate min-w-0 flex-1 text-left">
                  {col.name}
                </span>
                {tab.tab_id !== "tab_general" &&
                generalAllocColId &&
                col.id === generalAllocColId ? (
                  <span className="pm-meeting-ingest-menu-tag">{t("meeting.general")}</span>
                ) : null}
                {multi ? (
                  <ChevronRight
                    className="pm-meeting-ingest-menu-chevron"
                    strokeWidth={1.75}
                  />
                ) : null}
              </MenuItem>
            )
          })}
        </div>
        <div className="pm-meeting-ingest-menu-sep" role="separator" />
        {!creating ? (
          <MenuItem
            disabled={!!pendingName}
            className="pm-meeting-ingest-menu-item"
            onClick={() => setCreating(true)}
            onMouseEnter={() => {
              clearFlyoutCloseTimer()
              setFlyoutColId(null)
              setFlyoutCoords(null)
            }}
          >
            <Plus strokeWidth={1.75} />
            {hasAssociated ? t("meeting.createNewCollection") : t("meeting.newDotName", { name: tab.name })}
          </MenuItem>
        ) : (
          <div
            className="pm-meeting-ingest-create"
            onClick={(e) => e.stopPropagation()}
            onMouseEnter={() => {
              clearFlyoutCloseTimer()
              setFlyoutColId(null)
              setFlyoutCoords(null)
            }}
          >
            <Input
              className="pm-meeting-ingest-create-input"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder={t("meeting.collectionName")}
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Enter") handleCreateAndSelect()
              }}
            />
            <Button
              type="button"
              variant="ghost"
              size="xs"
              className="pm-meeting-ingest-create-btn"
              onClick={handleCreateAndSelect}
              disabled={!newName.trim() || !!pendingName}
            >
              {t("common.create")}
            </Button>
          </div>
        )}
      </SoftMenu>
      {/* Chain flyout — explicit coords next to parent menu / hovered row */}
      <SoftMenu
        open={
          dropdownOpen &&
          !!flyoutColId &&
          !!flyoutCoords &&
          (chainsByCol[flyoutColId]?.some((c) => !c.is_main) ?? false)
        }
        portal
        placement="right"
        fixedCoords={flyoutCoords}
        repositionKey={flyoutColId}
        exitMs={MENU_SILK_MS}
        className="pm-meeting-ingest-menu"
        onMouseEnter={clearFlyoutCloseTimer}
        onMouseLeave={scheduleCloseFlyout}
      >
        <div className="pm-meeting-ingest-menu-label">
          {chainFlyoutLoading && !(flyoutColId && chainsByCol[flyoutColId])
            ? t("common.loading")
            : t("meeting.timelineChain")}
        </div>
        {flyoutChains.map((ch) => (
          <MenuItem
            key={ch.chain_id}
            className="pm-meeting-ingest-menu-item"
            disabled={ingesting || ingestLocked}
            onClick={() => {
              if (!flyoutColId) return
              commitCollectionAndChain(flyoutColId, ch.chain_id)
            }}
          >
            <GitBranch strokeWidth={1.75} />
            <span className="pm-meeting-ingest-menu-name truncate min-w-0">
              {ch.is_main
                ? ch.title?.trim()
                  ? t("library.mainDot", { title: ch.title })
                  : t("library.main")
                : ch.title?.trim() || t("library.branch")}
            </span>
          </MenuItem>
        ))}
        {!chainFlyoutLoading &&
        flyoutColId &&
        (chainsByCol[flyoutColId]?.length ?? 0) === 0 ? (
          <div className="px-3 py-2.5 text-[12px] text-[var(--pm-faint)] text-center">
            {t("library.noChains")}
          </div>
        ) : null}
      </SoftMenu>
    </div>
  )

  // Legacy side column: pill + choose stacked, fixed width
  const legacyIngestColumn = (
    <div
      className={cn("shrink-0 flex flex-col gap-1.5 items-end", BUTTON_W)}
      ref={menuRef}
    >
      {pillControls}
      {chooseControls}
    </div>
  )

  return (
    <div
      ref={containerRef}
      className={cn(
        "group relative",
        !isGeneralTab &&
          (portalIngest
            ? "pm-meeting-section-meta pm-meeting-section-meta--split"
            : "px-6 py-3 pb-4 flex gap-4"),
      )}
    >
      {/* Left: section title (optional) + click-to-edit description */}
      {!isGeneralTab && (
      <div className="pm-meeting-section-meta-main flex-1 min-w-0 flex flex-col gap-1 relative items-start">
        {!hideTitle && (
          <div className="pm-meeting-title whitespace-normal break-words w-full text-left">
            {tabLabel} {sectionDisplayName}
          </div>
        )}
        {editingDesc ? (
          <textarea
            ref={descInputRef}
            className="pm-meeting-section-desc-input"
            value={descDraft}
            placeholder={t("meeting.addDescription")}
            rows={1}
            onChange={(e) => {
              setDescDraft(e.target.value)
              const el = e.target
              el.style.height = "auto"
              el.style.height = `${el.scrollHeight}px`
            }}
            onBlur={() => { void commitDescription() }}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault()
                setDescDraft(description)
                setEditingDesc(false)
              }
              // Enter commits; Shift+Enter inserts newline
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault()
                void commitDescription()
              }
            }}
            aria-label={t("meeting.sectionDescription")}
          />
        ) : (
          <div className="pm-meeting-section-desc-row group/desc">
            <p
              className={cn(
                "pm-meeting-section-desc",
                !description && "is-empty",
              )}
            >
              {description || t("meeting.addDescription")}
            </p>
            <button
              type="button"
              className="pm-meeting-section-desc-edit-btn"
              onClick={(e) => {
                e.stopPropagation()
                setEditingDesc(true)
              }}
              title={t("meeting.editDescription")}
              aria-label={t("meeting.editDescription")}
            >
              <Pencil className="size-3" />
            </button>
          </div>
        )}
      </div>
      )}

      {/* Ingest:
          - section: pill → title-row host; Choose → description-row right
          - general: Choose or pill on the General title row (no description) */}
      {portalIngest ? (
        <>
          {ingestPortalReady && ingestHostRef?.current
            ? createPortal(
                isGeneralTab
                  ? showTopButton
                    ? pillControls
                    : chooseControls
                  : showTopButton
                    ? pillControls
                    : null,
                ingestHostRef.current,
              )
            : null}
          {isGeneralTab && showTopButton ? chooseControls : null}
          {!isGeneralTab ? (
            <div className="pm-meeting-section-meta-choose shrink-0">
              {chooseControls}
            </div>
          ) : null}
        </>
      ) : (
        legacyIngestColumn
      )}

      {/* Suggested-pill path: chain list under pill when multi-chain */}
      <SoftMenu
        open={pillChainOpen}
        portal
        anchorRef={topPillRef}
        matchAnchorWidth
        exitMs={MENU_SILK_MS}
        className="pm-meeting-ingest-menu"
      >
        <div className="pm-meeting-ingest-menu-label">
          {pillChainLoading ? t("common.loading") : t("meeting.timelineChain")}
        </div>
        {pillChainOptions.map((ch) => (
          <MenuItem
            key={ch.chain_id}
            className="pm-meeting-ingest-menu-item"
            disabled={ingesting || ingestLocked || pillChainLoading}
            onClick={() => {
              if (!pillChainColId) return
              commitCollectionAndChain(pillChainColId, ch.chain_id)
            }}
          >
            <GitBranch strokeWidth={1.75} />
            <span className="pm-meeting-ingest-menu-name truncate min-w-0">
              {ch.is_main
                ? ch.title?.trim()
                  ? t("library.mainDot", { title: ch.title })
                  : t("library.main")
                : ch.title?.trim() || t("library.branch")}
            </span>
          </MenuItem>
        ))}
      </SoftMenu>

      <MeetingCreateTodosDialog
        open={createTodosOpen}
        onOpenChange={setCreateTodosOpen}
        collectionId={associatedId}
        chainId={tab.allocated_chain_id || null}
        meetingId={meetingId}
        defaultSectionTabId={tab.tab_id}
        candidates={
          liveCandidates.length > 0
            ? liveCandidates
            : tab.todo_candidates || []
        }
        loading={createTodosLoading}
        title={t("fileMgmt.createTodosSummary")}
        onCreated={(_n, meeting) => {
          if (meeting) onMeetingUpdate(meeting)
        }}
      />

      {/* Cancel Ingestion Confirm Dialog */}
      <Dialog open={cancelOpen} onOpenChange={setCancelOpen}>
        <DialogContent
          className="pm-dialog pm-dialog--silk sm:max-w-sm"
          showCloseButton={false}
          overlayClassName="pm-dialog-overlay--silk"
        >
          <DialogHeader>
            <DialogKicker>{t("library.collection")}</DialogKicker>
            <DialogTitle>{t("meeting.removeFromCollection")}</DialogTitle>
            <DialogDescription>
              {t("meeting.removeFromCollectionBody", { name: associatedName })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" size="sm" onClick={() => setCancelOpen(false)}>{t("common.cancel")}</Button>
            <Button type="button" variant="destructive-solid" size="sm" onClick={handleCancelIngest}>{t("common.remove")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Switch Ingestion Confirm Dialog — chain already chosen when multi-chain */}
      <Dialog
        open={!!switchPending}
        onOpenChange={(v) => {
          if (!v) setSwitchPending(null)
        }}
      >
        <DialogContent
          className="pm-dialog pm-dialog--silk sm:max-w-sm"
          showCloseButton={false}
          overlayClassName="pm-dialog-overlay--silk"
        >
          <DialogHeader>
            <DialogKicker>{t("library.collection")}</DialogKicker>
            <DialogTitle>{t("meeting.switchCollection")}</DialogTitle>
            <DialogDescription>
              {switchPending?.colId === "__new__" ? (
                t("meeting.switchCollectionNew", { name: associatedName })
              ) : (
                t("meeting.switchCollectionBody", {
                  from: associatedName,
                  to: collections.find((c) => c.id === switchPending?.colId)?.name || switchPending?.colId || "",
                  chain: switchPending?.chainId
                    ? t("meeting.ontoSelectedChain")
                    : t("meeting.mainChainParen"),
                })
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setSwitchPending(null)}
            >
              {t("common.cancel")}
            </Button>
            <Button
              type="button"
              variant="destructive-solid"
              size="sm"
              onClick={() => {
                if (!switchPending) return
                if (switchPending.colId === "__new__") doCreateAndIngest()
                else void doIngest(switchPending.colId, switchPending.chainId)
              }}
            >
              {t("common.switch")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
})

// ── Main component ────────────────────────────────────────────────

export function MeetingTabs({
  meetingId, meeting, notesContent, onNotesChange,
  onMeetingUpdate, onSeekTo, onFocusSentence, onActiveTabChange, transcriptSegments,
  partialText,
  focusRef,
  activeSectionTag,
  forceTranscriptTab,
  tabBarOffset = 0,
  floatingPanelOpen,
  canShift = true,
  playbackTime,
  className,
  selectedSummaryId: selectedSummaryIdProp,
  onSelectedSummaryIdChange,
  onBindOpenAddSection,
  onBusyChange,
  onSectionRailModelChange,
  onBindSectionRailActions,
  onMainTabChange,
  onRequestSideTab,
  hostTranscriptInParent = true,
}: Props) {
  const t = useT()
  const tabs = meeting.tabs ?? []
  const speakerNames: Record<string, string> = meeting.speaker_names ?? {}

  // ── Blueprint SSE streaming ──────────────────────────────
  // Stable callback ref for auto-fetch when streaming completed while user was away
  const onCompletedAwayRef = useRef(onMeetingUpdate)
  onCompletedAwayRef.current = onMeetingUpdate
  const loadTabContentRef = useRef<(tabId: string) => Promise<void>>(async () => {})
  const bpStreamCtrlRef = useRef<{ start: () => void; abort: () => void; dismissStreaming: () => void } | null>(null)
  /** Dedup local stream-end effect vs hook onCompletedAway (both fire when viewing). */
  const summaryHandledAtRef = useRef(0)

  const viewedMeetingIdRef = useRef(meetingId)
  viewedMeetingIdRef.current = meetingId

  const handleCompletedAway = useCallback((mid: string) => {
    // Local isStreaming effect already seeded chips + reloaded while viewing
    if (Date.now() - summaryHandledAtRef.current < 2500) {
      bpStreamCtrlRef.current?.dismissStreaming()
      return
    }
    summaryHandledAtRef.current = Date.now()
    getMeeting(mid).then((m) => {
      if (viewedMeetingIdRef.current !== mid) return
      onCompletedAwayRef.current(m)
      loadedTabsRef.current.delete("tab_general")
      void loadTabContentRef.current("tab_general")
      toast.success(t("meeting.summaryGenerated"))
    }).catch(() => {
      if (viewedMeetingIdRef.current === mid) {
        toast.error(t("meeting.failedFetchMeeting"))
      }
    }).finally(() => {
      if (viewedMeetingIdRef.current === mid) {
        bpStreamCtrlRef.current?.dismissStreaming()
      }
    })
  }, [t])

  const [bpStream, bpStreamCtrl] = useBlueprintStream(meetingId, handleCompletedAway)
  bpStreamCtrlRef.current = bpStreamCtrl
  // Use meeting.blueprint when available; fall back to early-completion streaming data
  const blueprint = (meeting.blueprint && meeting.blueprint.length > 0)
    ? meeting.blueprint
    : (bpStream.earlyBlueprint ?? [])

  const wasStreamingRef = useRef(false)

  // Has summary if any tab has content available (.md file or streaming)
  const hasSummary = !!(tabs.some(t => (t.type === "section" || t.tab_id === "tab_general") && t.md_file_path))
  const [mainTab, setMainTab] = useState(hasSummary ? "summary" : "notes")

  // Prefer Summary while generating or once content exists — never force Notes
  // mid-generation (that hid the stream + fence and left a bare Summarize CTA).
  useEffect(() => {
    const generating =
      bpStream.isStreaming ||
      bpStream.summaryGenState !== "idle" ||
      meeting.processing_state === "summarizing" ||
      meeting.processing_state === "extracting"
    if (hasSummary || generating) {
      setMainTab((prev) => (prev === "notes" ? "summary" : prev))
    }
  }, [
    hasSummary,
    bpStream.isStreaming,
    bpStream.summaryGenState,
    meeting.processing_state,
  ])

  // Summary stream starts only when the user clicks Summarize (speaker gate →
  // handleEnterStudio / startBlueprintStream, or Re-summarize / Summarize CTA).
  // Auto-start on transcript-ready skipped Speakers and jumped into Studio.
  useEffect(() => {
    if (!forceTranscriptTab) return
    if (hostTranscriptInParent) {
      onRequestSideTab?.("transcript")
    } else {
      setMainTab("transcript")
    }
  }, [forceTranscriptTab, hostTranscriptInParent, onRequestSideTab])
  useEffect(() => {
    onMainTabChange?.(mainTab)
  }, [mainTab, onMainTabChange])
  // Keep main tab on content surfaces only (Summary | Notes)
  useEffect(() => {
    if (hostTranscriptInParent && (mainTab === "transcript" || mainTab === "speaker")) {
      setMainTab(hasSummary ? "summary" : "notes")
    }
  }, [hostTranscriptInParent, mainTab, hasSummary])
  const contentStickyOffset = tabBarOffset + 36
  const [selectedSummaryIdInternal, setSelectedSummaryIdInternal] = useState("tab_general")
  const selectedSummaryId = selectedSummaryIdProp ?? selectedSummaryIdInternal
  const setSelectedSummaryId = useCallback((tabId: string) => {
    if (selectedSummaryIdProp === undefined) setSelectedSummaryIdInternal(tabId)
    onSelectedSummaryIdChange?.(tabId)
    setMainTab("summary")
  }, [selectedSummaryIdProp, onSelectedSummaryIdChange])
  const editableSectionRef = useRef<{ startEditing: () => void }>(null)
  const exportBtnRef = useRef<HTMLButtonElement>(null)
  const sectionIngestHostRef = useRef<HTMLDivElement>(null)
  const contentScrollRef = useRef<HTMLDivElement>(null)
  const [exportMenuOpen, setExportMenuOpen] = useState(false)

  /**
   * Section switch: soft-fade main body (tab bar + toolbar actions stay put).
   * Layout-paint new section at opacity 0 → short hold → fade in.
   */
  const [sectionContentFaded, setSectionContentFaded] = useState(false)
  const sectionSwapSkipRef = useRef(true)
  const sectionSwapGenRef = useRef(0)
  useLayoutEffect(() => {
    if (sectionSwapSkipRef.current) {
      sectionSwapSkipRef.current = false
      return
    }
    sectionSwapGenRef.current += 1
    setSectionContentFaded(true)
  }, [selectedSummaryId])
  useEffect(() => {
    if (!sectionContentFaded) return
    const gen = sectionSwapGenRef.current
    const t = window.setTimeout(() => {
      if (sectionSwapGenRef.current !== gen) return
      setSectionContentFaded(false)
    }, 40)
    return () => window.clearTimeout(t)
  }, [sectionContentFaded, selectedSummaryId])
  // Reset skip when meeting changes so first paint is not faded
  useEffect(() => {
    sectionSwapSkipRef.current = true
    setSectionContentFaded(false)
  }, [meetingId])

  // External section-rail click (prop change) → Summary surface; skip mount
  const skipExtSectionSync = useRef(true)
  useEffect(() => {
    skipExtSectionSync.current = true
  }, [meetingId])
  useEffect(() => {
    if (selectedSummaryIdProp === undefined) return
    if (skipExtSectionSync.current) {
      skipExtSectionSync.current = false
      return
    }
    setMainTab("summary")
  }, [selectedSummaryIdProp])

  const [tabMdContents, setTabMdContents] = useState<Record<string, string>>({})

  // ── Summary translation ──────────────────────────────────
  // Per-tab language view (null = original). Persisted to localStorage so the
  // last-selected language is restored when the user returns to a tab.
  const langStorageKey = useCallback(
    (tabId: string) => `meeting:summaryLang:${meetingId}:${tabId}`,
    [meetingId],
  )
  const [summaryLang, setSummaryLang] = useState<Record<string, string | null>>({})
  const [translations, setTranslations] = useState<Record<string, Record<string, string>>>({})
  const [availableLangs, setAvailableLangs] = useState<Record<string, string[]>>({})

  const refreshTranslations = useCallback(async (tabId: string) => {
    try {
      const res = await getSummaryTranslations(meetingId, tabId)
      setAvailableLangs((prev) => ({ ...prev, [tabId]: res.languages ?? [] }))
    } catch { /* non-fatal: green dots just won't show */ }
  }, [meetingId])

  const handleTranslationDone = useCallback((tabId: string, lang: string, md: string, cached: boolean) => {
    setTranslations((prev) => ({ ...prev, [tabId]: { ...prev[tabId], [lang]: md } }))
    setAvailableLangs((prev) => ({
      ...prev,
      [tabId]: Array.from(new Set([...(prev[tabId] ?? []), lang])),
    }))
    if (!cached) toast.success(t("meeting.translationReady", { lang }))
  }, [t])

  const handleTranslationError = useCallback((tabId: string, _lang: string, message: string, kind: "remote" | "network") => {
    if (kind === "network") {
      // Transient connection drop (e.g. flaky network).  Keep the persisted
      // language view so a reload resumes the stream; don't revert or clear.
      toast.warning(t("meeting.translationLost"))
      return
    }
    // Genuine backend failure (e.g. content moderation): revert to original
    // and clear the persisted view so it isn't retried automatically.
    toast.error(t("meeting.translationFailed", { error: message }))
    setSummaryLang((prev) => ({ ...prev, [tabId]: null }))
    try { localStorage.removeItem(langStorageKey(tabId)) } catch { /* ignore */ }
  }, [langStorageKey, t])

  const handleSelectLang = useCallback((tabId: string, lang: string | null) => {
    setSummaryLang((prev) => ({ ...prev, [tabId]: lang }))
    try {
      if (lang) localStorage.setItem(langStorageKey(tabId), lang)
      else localStorage.removeItem(langStorageKey(tabId))
    } catch { /* localStorage unavailable */ }
    if (!lang) return
    if (translations[tabId]?.[lang]) return   // already cached client-side
    // Single streaming path: the backend serves cache instantly, re-attaches
    // to an in-progress task (replaying missed tokens), or starts fresh.
    startTranslationStream(meetingId, tabId, lang)
  }, [meetingId, translations, langStorageKey])

  // Restore the persisted language view + green-dot list when the tab changes.
  useEffect(() => {
    const tabId = selectedSummaryId
    let stored: string | null = null
    try { stored = localStorage.getItem(langStorageKey(tabId)) } catch { /* ignore */ }
    refreshTranslations(tabId)
    if (stored && !translations[tabId]?.[stored]) {
      // Cache-aware stream: revisiting never re-generates; an in-progress
      // translation (e.g. after a refresh) replays and continues.
      void handleSelectLang(tabId, stored)
      return
    }
    if (stored) {
      setSummaryLang((prev) => ({ ...prev, [tabId]: stored }))
      return
    }
    // No persisted preference — fall back to server-side truth: if a
    // translation is actively generating for this tab (e.g. the user refreshed
    // mid-stream and localStorage was unavailable), reconnect to it.
    getActiveTranslations(meetingId)
      .then(({ active }) => {
        const act = active?.find((a) => a.tab_id === tabId)
        if (act) void handleSelectLang(tabId, act.language)
      })
      .catch(() => { /* non-fatal */ })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSummaryId])

  // Subscribe to the translation stream for the currently-viewed tab+language.
  // Managed globally: survives view switches, replays missed tokens on refresh.
  const activeSummaryLang = summaryLang[selectedSummaryId] ?? null
  const tStream = useTranslationStream(meetingId, selectedSummaryId, activeSummaryLang, {
    onDone: handleTranslationDone,
    onError: handleTranslationError,
  })

  // ── Section SSE streaming ────────────────────────────────
  const isGeneralSelected = selectedSummaryId === "tab_general"
  const onSectionCompletedAwayRef = useRef(onMeetingUpdate)
  onSectionCompletedAwayRef.current = onMeetingUpdate

  const handleSectionCompletedAway = useCallback((mid: string, tid: string) => {
    // Always dismiss the *completed* tab (not whatever is selected now).
    getMeeting(mid).then((m) => {
      onSectionCompletedAwayRef.current(m)
      loadedTabsRef.current.delete(tid)
      loadTabContent(tid)
      dismissSectionStream(mid, tid)
    }).catch(() => {
      dismissSectionStream(mid, tid)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meetingId])

  const [sectionStream] = useSectionStream(
    meetingId,
    !isGeneralSelected ? selectedSummaryId : null,
    handleSectionCompletedAway,
  )

  // Auto-start section streams for all generating tabs (once per tab per session)
  const startedSectionStreamsRef = useRef<Set<string>>(new Set())
  const kickSectionStreams = useCallback(
    (mid: string, list: MeetingTab[] | undefined | null) => {
      for (const tab of list || []) {
        if (tab.type !== "section" || tab.processing_state !== "generating") continue
        const key = `${mid}::${tab.tab_id}`
        if (startedSectionStreamsRef.current.has(key)) continue
        startedSectionStreamsRef.current.add(key)
        startSectionStream(mid, tab.tab_id)
      }
    },
    [],
  )
  useEffect(() => {
    startedSectionStreamsRef.current = new Set()
  }, [meetingId])
  useEffect(() => {
    kickSectionStreams(meetingId, tabs)
  }, [tabs, meetingId, kickSectionStreams])

  // Track streaming completion for the *selected* section only.
  // Tab switches re-baseline — they must never look like a true→false edge
  // (that was dismissing the newly selected live stream and clearing Streaming).
  const sectionWasStreamingRef = useRef(false)
  const sectionStreamMeetingRef = useRef(meetingId)
  const streamingTabRef = useRef<string | null>(
    isGeneralSelected ? null : selectedSummaryId,
  )
  useEffect(() => {
    const tabId = isGeneralSelected ? null : selectedSummaryId

    if (
      streamingTabRef.current !== tabId ||
      sectionStreamMeetingRef.current !== meetingId
    ) {
      streamingTabRef.current = tabId
      sectionStreamMeetingRef.current = meetingId
      sectionWasStreamingRef.current = !!(tabId && sectionStream.isStreaming)
      return
    }

    if (
      tabId &&
      sectionWasStreamingRef.current &&
      !sectionStream.isStreaming
    ) {
      const completedTab = tabId
      const mid = meetingId
      // Seed last tokens so SummaryMarkdownViewer paints speaker/ref chips immediately
      const interim = sectionStream.streamingMd || ""
      if (interim.trim()) {
        setTabMdContents((prev) => ({ ...prev, [completedTab]: interim }))
        loadedTabsRef.current.delete(completedTab)
      }
      getMeeting(mid).then((m) => {
        onMeetingUpdate(m)
        loadedTabsRef.current.delete(completedTab)
        loadTabContent(completedTab)
        dismissSectionStream(mid, completedTab)
        toast.success(t("meeting.sectionGenerated"))
      }).catch(() => {
        dismissSectionStream(mid, completedTab)
        toast.error(t("meeting.failedFetchSection"))
      })
    }
    sectionWasStreamingRef.current = !!(tabId && sectionStream.isStreaming)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sectionStream.isStreaming, selectedSummaryId, isGeneralSelected, meetingId])

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
  // Always pin meetingId so switching to another idle meeting cannot fire "complete" toasts.
  type PendingAction =
    | { type: "summarize"; meetingId: string }
    | { type: "re_summarize"; meetingId: string }
    | { type: "extract"; meetingId: string }
    | { type: "regenerate"; meetingId: string; tabId: string; hadAllocation: boolean }
    | null
  const [pendingAction, setPendingAction] = useState<PendingAction>(null)
  const serverBusy = !!(meeting.processing_state && meeting.processing_state !== "idle")
  // Only this meeting's pending action contributes to local busy chrome
  const pendingForThisMeeting =
    !!pendingAction && pendingAction.meetingId === meetingId
  const streamingBusy = bpStream.summaryGenState !== "idle" || bpStream.blueprintGenState !== "idle" || sectionStream.isStreaming
  const busy = serverBusy || pendingForThisMeeting || streamingBusy

  // Expose open-Add-Section + busy (legacy + rail header)
  useEffect(() => {
    onBindOpenAddSection?.(() => setAddSectionOpen(true))
  }, [onBindOpenAddSection])
  useEffect(() => {
    onBusyChange?.(busy)
  }, [busy, onBusyChange])

  // Track whether server was ever busy since pendingAction was set.
  // Guards against the intermediate render where pendingAction is set but
  // the meeting prop (serverBusy) hasn't been updated yet.
  const serverWasBusyRef = useRef(false)
  useEffect(() => {
    // Only accumulate "was busy" for the meeting that owns the pending action
    // (or the painted meeting when no foreign pending action is open).
    if (pendingAction && pendingAction.meetingId !== meetingId) return
    if (serverBusy) serverWasBusyRef.current = true
  }, [serverBusy, meetingId, pendingAction])

  // Meeting switch: re-baseline busy tracking. Never toast for another meeting's work.
  useEffect(() => {
    serverWasBusyRef.current = !!(
      meeting.processing_state && meeting.processing_state !== "idle"
    )
    // Landed back on a meeting whose extract/regen finished while away → settle quietly
    // (no toast on the other meeting; refresh data if needed).
    if (
      pendingAction &&
      pendingAction.meetingId === meetingId &&
      (!meeting.processing_state || meeting.processing_state === "idle") &&
      !serverWasBusyRef.current
    ) {
      setPendingAction(null)
      if (pendingAction.type === "extract" || pendingAction.type === "regenerate") {
        loadedTabsRef.current.clear()
        setTabMdContents({})
      }
      getMeeting(meetingId)
        .then((m) => onMeetingUpdate(m))
        .catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on switch
  }, [meetingId])

  // Unified cleanup: same-meeting only — busy → idle while this meeting is painted
  useEffect(() => {
    if (!pendingAction) return
    if (pendingAction.meetingId !== meetingId) return
    if (serverBusy || !serverWasBusyRef.current) return

    serverWasBusyRef.current = false
    const action = pendingAction
    setPendingAction(null)
    // Perform action-specific cleanup
    switch (action.type) {
      case "summarize":
      case "re_summarize":
        toast.success(action.type === "re_summarize" ? t("meeting.summaryRegenerated") : t("meeting.summaryGenerated"))
        break
      case "extract":
        // Clear loaded-tabs cache so section tabs re-fetch newly generated content
        loadedTabsRef.current.clear()
        setTabMdContents({})
        toast.success(t("meeting.extractComplete"))
        break
      case "regenerate":
        // Delete old allocation AFTER successful regeneration
        if (action.hadAllocation) {
          const colId = meeting.tabs?.find((t) => t.tab_id === action.tabId)
            ?.associated_collection_id
          deleteSectionAllocation(meetingId, action.tabId)
            .then(() => refreshKeepMountedLibrary(colId))
            .catch(() => { /* best effort */ })
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
        toast.success(t("meeting.regenerateComplete"))
        break
    }
    // Refresh meeting data from server (picks up new tabs/sections)
    getMeeting(meetingId).then((m) => {
      onMeetingUpdate(m)
    }).catch(() => {
      // Fall back to in-memory meeting if fetch fails
      onMeetingUpdate(meeting)
    })
  }, [meeting.processing_state, pendingAction, meetingId, serverBusy, meeting, onMeetingUpdate])

  const [reSummarizeOpen, setReSummarizeOpen] = useState(false)
  const [regenerateConfirmOpen, setRegenerateConfirmOpen] = useState(false)
  const [deleteSectionTarget, setDeleteSectionTarget] = useState<string | null>(null)
  const sectionMetaRef = useRef<{ startEditingDescription: () => void }>(null)
  // editableSectionRef declared near selectedSummaryId (main tab tools)

  const summaryBarRef = useRef<HTMLDivElement>(null)
  const [ingestingTabs, setIngestingTabs] = useState<Set<string>>(new Set())

  const [notesEditor, setNotesEditor] = useState<Editor | null>(null)

  // ── Load section markdown when tab is selected ─────────────
  const loadedTabsRef = useRef<Set<string>>(new Set())   // successfully loaded
  const inFlightRef = useRef<Set<string>>(new Set())     // currently fetching (dedup)

  // Parent no longer remounts us on meeting switch — clear section md caches
  useEffect(() => {
    loadedTabsRef.current = new Set()
    inFlightRef.current = new Set()
    setTabMdContents({})
    setLoadingTabs(new Set())
    setSummaryLang({})
    setTranslations({})
    setAvailableLangs({})
    setIngestingTabs(new Set())
  }, [meetingId]) // eslint-disable-line react-hooks/exhaustive-deps -- seed notes from first paint of this meeting

  const loadTabContent = useCallback(async (tabId: string) => {
    // Already loaded → skip
    if (loadedTabsRef.current.has(tabId)) return
    // Already in-flight → skip (dedup concurrent calls)
    if (inFlightRef.current.has(tabId)) return
    inFlightRef.current.add(tabId)

    setLoadingTabs((prev) => new Set(prev).add(tabId))
    try {
      const md = await getSectionMd(meetingId, tabId)
      if (md) {
        loadedTabsRef.current.add(tabId)   // mark loaded ONLY on success
        setTabMdContents((prev) => ({ ...prev, [tabId]: md }))
      }
      // Missing/empty file: do not store "" — that blocks live SSE tokens
      // (`"" ?? streamingMd` stays empty).
    } catch {
      /* retry next select */
    }
    inFlightRef.current.delete(tabId)
    setLoadingTabs((prev) => {
      const next = new Set(prev)
      next.delete(tabId)
      return next
    })
  }, [meetingId])
  loadTabContentRef.current = loadTabContent

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

  const handleNotesImageUpload = useCallback(async (file: File) => {
    const result = await uploadMeetingImage(meetingId, file)
    return result.url
  }, [meetingId])

  // ── Actions ─────────────────────────────────────────────────
  const doExtract = async (receipts: ExtractReceipt[]) => {
    if (receipts.length === 0) {
      toast.error(t("meeting.selectOneSection"))
      return
    }
    // Close dialog first; defer busy chrome so main layout doesn’t thrash under silk exit
    setAddSectionOpen(false)
    setAddForm({ name: "", description: "", blueprintId: null })
    const extractMeetingId = meetingId
    const markBusy = () =>
      setPendingAction({ type: "extract", meetingId: extractMeetingId })
    // Double rAF: dialog exit paint settles before toolbar / content path reacts
    requestAnimationFrame(() => {
      requestAnimationFrame(markBusy)
    })
    try {
      const updated = await extract(extractMeetingId, receipts)
      setSelectedBlueprintIds(new Set())
      setCustomReceipts([])
      // Notify parent to start polling (meeting now has processing_state="extracting")
      onMeetingUpdate(updated)
      // Don't wait for a later tabs-effect: refresh used to be required
      // before SSE / Tagger started.
      kickSectionStreams(extractMeetingId, updated.tabs)
    } catch (err) {
      setPendingAction((prev) =>
        prev?.type === "extract" && prev.meetingId === extractMeetingId ? null : prev,
      )
      toast.error(t("meeting.extractFailed", { error: formatApiError(err, t) }))
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
    if (!name) { toast.error(t("meeting.enterSectionName")); return }
    setGeneratingDesc(true)
    try {
      const res = await generateSectionDescription(meetingId, name)
      if (res.found && res.description) {
        setAddForm(prev => ({ ...prev, description: res.description ?? prev.description }))
        toast.success(t("meeting.descriptionGenerated"))
      } else {
        toast.warning(t("meeting.nameNotDiscussed", { name }))
      }
    } catch (err) {
      toast.error(t("common.failedWithError", { error: formatApiError(err, t) }))
    }
    setGeneratingDesc(false)
  }

  const handleAddOrExtract = async () => {
    if (busy) { toast.error(t("meeting.meetingProcessing")); return }
    const name = addForm.name.trim()
    if (!name) { toast.error(t("meeting.sectionNameRequired")); return }
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
    await doExtract([receipt])
  }

  const handleSummarize = async () => {
    loadedTabsRef.current.delete("tab_general")
    bpStreamCtrl.start()
  }

  const handleReSummarize = async () => {
    setReSummarizeOpen(false)
    loadedTabsRef.current.delete("tab_general")
    setTabMdContents((prev) => {
      if (!("tab_general" in prev)) return prev
      const next = { ...prev }
      delete next.tab_general
      return next
    })
    bpStreamCtrl.start()
  }

  // ── Detect streaming finish → prepared chips + fetch meeting ────────────────
  // When the full blueprint stream ends, persist + toast. Live chips already
  // come from SummaryMarkdownViewer on streamingMd / settledSummaryMd.
  const summaryStreamMeetingRef = useRef(meetingId)
  const lastSeededGeneralRef = useRef("")
  useEffect(() => {
    const settled = (bpStream.settledSummaryMd || "").trim()
    if (!settled || lastSeededGeneralRef.current === settled) return
    lastSeededGeneralRef.current = settled
    setTabMdContents((prev) =>
      prev.tab_general === settled ? prev : { ...prev, tab_general: settled },
    )
    loadedTabsRef.current.delete("tab_general")
  }, [bpStream.settledSummaryMd])

  useEffect(() => {
    if (summaryStreamMeetingRef.current !== meetingId) {
      summaryStreamMeetingRef.current = meetingId
      wasStreamingRef.current = bpStream.isStreaming
      lastSeededGeneralRef.current = ""
      return
    }
    const was = wasStreamingRef.current
    wasStreamingRef.current = bpStream.isStreaming
    if (!was || bpStream.isStreaming) return

    summaryHandledAtRef.current = Date.now()
    const interim =
      bpStream.settledSummaryMd || bpStream.streamingMd || ""
    if (interim.trim()) {
      setTabMdContents((prev) => ({ ...prev, tab_general: interim }))
      loadedTabsRef.current.delete("tab_general")
    }
    // Drop generating gate (isStreaming already false); clear buffer after seed
    bpStreamCtrl.dismissStreaming()

    const finishedId = meetingId
    getMeeting(finishedId).then((m) => {
      if (viewedMeetingIdRef.current !== finishedId) return
      onMeetingUpdate(m)
      loadedTabsRef.current.delete("tab_general")
      void loadTabContent("tab_general")
      toast.success(t("meeting.summaryGenerated"))
    }).catch(() => {
      if (viewedMeetingIdRef.current === finishedId) {
        toast.error(t("meeting.failedFetchMeeting"))
      }
    })
  }, [bpStream.isStreaming, bpStream.streamingMd, bpStream.settledSummaryMd, bpStreamCtrl, meetingId, onMeetingUpdate, loadTabContent])

  const handleDeleteSection = (tabId: string) => {
    setDeleteSectionTarget(tabId)
  }

  const confirmDeleteSection = async () => {
    const tabId = deleteSectionTarget
    if (!tabId) return
    setDeleteSectionTarget(null)
    const colId = meeting.tabs?.find((t) => t.tab_id === tabId)
      ?.associated_collection_id
    try {
      const m = await deleteSection(meetingId, tabId)
      onMeetingUpdate(m)
      await refreshKeepMountedLibrary(colId)
      if (selectedSummaryId === tabId) setSelectedSummaryId("tab_general")
      setTabMdContents((prev) => {
        const next = { ...prev }
        delete next[tabId]
        return next
      })
      toast.success(t("meeting.sectionDeleted"))
    } catch (err) {
      toast.error(t("meeting.deleteFailed", { error: formatApiError(err, t) }))
    }
  }

  const handleRegenerate = async (tabId: string) => {
    // Remember if section was ingested so we can clean up on success
    const targetTab = tabs.find(t => t.tab_id === tabId)
    const hadAllocation = !!targetTab?.allocated_file_id
    const regenMeetingId = meetingId
    loadedTabsRef.current.delete(tabId)
    setTabMdContents((prev) => {
      if (!(tabId in prev)) return prev
      const next = { ...prev }
      delete next[tabId]
      return next
    })
    setPendingAction({
      type: "regenerate",
      meetingId: regenMeetingId,
      tabId,
      hadAllocation,
    })
    try {
      const updated = await regenerateSection(regenMeetingId, tabId)
      // Notify parent to start polling (meeting now has processing_state="extracting")
      onMeetingUpdate(updated)
      kickSectionStreams(regenMeetingId, updated.tabs)
    } catch (err) {
      setPendingAction((prev) =>
        prev?.type === "regenerate" && prev.meetingId === regenMeetingId ? null : prev,
      )
      toast.error(t("meeting.regenerateFailed", { error: formatApiError(err, t) }))
    }
  }

  const handleSaveSectionTitle = async (tabId: string, name: string) => {
    const next = name.trim()
    if (!next) return
    const bp = blueprint ?? []
    const target = tabs.find((t) => t.tab_id === tabId)
    const m = await updateMeeting(meetingId, {
      blueprint: bp.map((b) => {
        if (target?.blueprint_id && b.blueprint_id === target.blueprint_id) {
          return { ...b, tab_name: next }
        }
        return b
      }),
      tabs: (tabs ?? []).map((t) => {
        if (t.tab_id === tabId) {
          return { ...t, name: next, is_dirty: true }
        }
        return t
      }),
    })
    onMeetingUpdate(m)
  }

  const handleSaveSection = async (tabId: string, content: string) => {
    const res = await saveSectionMd(meetingId, tabId, content)
    setTabMdContents((prev) => ({ ...prev, [tabId]: content }))
    if (tabId === "tab_general") {
      loadedTabsRef.current.add("tab_general")
    }
    // Prefer server meeting; always mark needs_reingest when this tab is allocated
    // so the pill icon updates even if meta was stale client-side.
    const base = res.meeting ?? meeting
    const tabs = (base.tabs ?? []).map((t) => {
      if (t.tab_id !== tabId) return t
      const allocated = !!(t.allocated_file_id || "").trim()
      if (!allocated) return t
      return { ...t, needs_reingest: true }
    })
    onMeetingUpdate({ ...base, tabs })
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
    toast.info(t("meeting.reference", { id: clean }), { duration: 2000 })
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

  // Re-render rail when any section stream ticks (ready / Streaming badges)
  const [sectionStreamTick, setSectionStreamTick] = useState(0)
  useEffect(() => {
    return subscribeSectionStreams(() => {
      setSectionStreamTick((n) => n + 1)
    })
  }, [])

  // ── Top-right Section rail model (full feature parity with old toolbar card) ──
  useEffect(() => {
    if (!onSectionRailModelChange) return
    // Thinking = any live / server summary gen — keep General + fence skeletons visible
    const thinking =
      bpStream.blueprintGenState !== "idle" ||
      bpStream.summaryGenState !== "idle" ||
      bpStream.isStreaming ||
      meeting.processing_state === "summarizing" ||
      pendingAction?.type === "summarize" ||
      pendingAction?.type === "re_summarize"
    const items: SectionRailItem[] = []

    // General always present once we have any summary surface / blueprint path
    if (hasBlueprint || hasSections || hasSummary || thinking) {
      const generalTab = tabs.find((t) => t.tab_id === "tab_general")
      const hasGeneralMd =
        !!generalTab?.md_file_path ||
        hasSummary ||
        !!tabMdContents["tab_general"] ||
        !!bpStream.settledSummaryMd
      const genStreaming = bpStream.summaryGenState === "streaming"
      const genGenerating =
        !genStreaming &&
        !hasGeneralMd &&
        (bpStream.summaryGenState === "prefilling" ||
          ((pendingAction?.type === "summarize" ||
            pendingAction?.type === "re_summarize" ||
            meeting.processing_state === "summarizing") &&
            !bpStream.streamingMd))
      items.push({
        id: "tab_general",
        label: t("meeting.general"),
        kind: "general",
        active: selectedSummaryId === "tab_general",
        ready: !!(hasGeneralMd || genStreaming || genGenerating || bpStream.isStreaming),
        streaming: genStreaming,
        generating: genGenerating,
        ingested: !!(generalTab?.allocated_file_id) && !ingestingTabs.has("tab_general"),
      })
    }

    if (hasSections) {
      const sections = tabs.filter((t) => t.type === "section")
      sections.forEach((sec, idx) => {
        const bp = blueprint.find((b) => b.blueprint_id === sec.blueprint_id)
        const stream = getSectionStreamState(meetingId, sec.tab_id)
        const hasMd = !!sec.md_file_path
        const streaming = !hasMd && sectionStreamHasOutput(stream)
        const serverGenerating = sec.processing_state === "generating"
        // Waiting for first token: server gen, SSE prefilling, or stream open without tokens yet
        const generating =
          !hasMd &&
          !streaming &&
          (serverGenerating ||
            stream.isStreaming ||
            stream.genState === "prefilling" ||
            sectionStreamIsOpenable(stream))
        // Openable: disk md, live SSE, or server still generating
        const ready =
          hasMd ||
          sectionStreamIsOpenable(stream) ||
          serverGenerating ||
          streaming
        items.push({
          id: sec.tab_id,
          label: sec.name || bp?.tab_name || t("meeting.section"),
          hint: sec.description || bp?.tab_description || undefined,
          kind: "section",
          active: selectedSummaryId === sec.tab_id,
          shortLabel: `T${idx + 1}`,
          ready,
          streaming,
          generating,
          // Hide checkmark while this tab is still indexing after allocate
          ingested: !!sec.allocated_file_id && !ingestingTabs.has(sec.tab_id),
        })
      })
    } else if (hasBlueprint) {
      // Pre-extract: multi-select blueprint pills
      for (const b of blueprint) {
        if (b.tab_name?.toLowerCase() === "other") continue
        items.push({
          id: b.blueprint_id,
          label: b.tab_name || t("meeting.section"),
          hint: b.tab_description || undefined,
          kind: "blueprint",
          selected: selectedBlueprintIds.has(b.blueprint_id),
        })
      }
      customReceipts.forEach((c, i) => {
        items.push({
          id: `custom:${i}`,
          label: c.name,
          hint: c.description || undefined,
          kind: "custom",
          selected: true,
        })
      })
    } else if (thinking) {
      const early = (bpStream.earlyBlueprint ?? [])
        .filter((b) => b.tab_name && b.tab_name.toLowerCase() !== "other")
      if (early.length > 0) {
        early.forEach((b, i) => {
          items.push({
            id: `early:${i}`,
            label: b.tab_name,
            hint: b.tab_description || undefined,
            kind: "early",
          })
        })
      } else {
        ;[1, 2, 3].forEach((i) => {
          items.push({ id: `sk:${i}`, label: "", kind: "skeleton" })
        })
      }
    }

    onSectionRailModelChange({
      thinking,
      busy,
      hasBlueprint,
      hasSections,
      canBreakdown:
        !hasSections &&
        (selectedBlueprintIds.size + customReceipts.length) > 0 &&
        !busy,
      items,
    })
  }, [
    onSectionRailModelChange,
    bpStream.blueprintGenState,
    bpStream.summaryGenState,
    bpStream.isStreaming,
    bpStream.earlyBlueprint,
    hasSections,
    hasBlueprint,
    hasSummary,
    tabs,
    blueprint,
    selectedBlueprintIds,
    customReceipts,
    selectedSummaryId,
    busy,
    meeting.processing_state,
    pendingAction,
    meetingId,
    sectionStreamTick,
    bpStream.streamingMd,
    ingestingTabs,
  ])

  useEffect(() => {
    if (!onBindSectionRailActions) return
    onBindSectionRailActions({
      openAddSection: () => setAddSectionOpen(true),
      selectSection: (id) => setSelectedSummaryId(id),
      toggleBlueprint: (id) => {
        setSelectedBlueprintIds((prev) => {
          const next = new Set(prev)
          if (next.has(id)) next.delete(id)
          else next.add(id)
          return next
        })
      },
      removeCustom: (index) => {
        setCustomReceipts((prev) => prev.filter((_, j) => j !== index))
      },
      breakdown: () => { void handleBreakdown() },
    })
  }, [onBindSectionRailActions, setSelectedSummaryId, handleBreakdown])

  const getTabContent = (tabId: string): string => {
    if (tabId === "tab_general") {
      return (
        tabMdContents["tab_general"] ||
        bpStream.settledSummaryMd ||
        bpStream.streamingMd ||
        ""
      )
    }
    // Live SSE wins while this tab is generating (cached "" or stale md
    // must not hide tokens).
    if (
      tabId === selectedSummaryId &&
      sectionStream.streamingMd &&
      (sectionStream.isStreaming ||
        sectionStream.genState === "streaming" ||
        sectionStream.genState === "prefilling")
    ) {
      return sectionStream.streamingMd
    }
    return (
      tabMdContents[tabId] ||
      (tabId === selectedSummaryId ? sectionStream.streamingMd : "") ||
      ""
    )
  }

  const selectedTab = tabs.find((t) => t.tab_id === selectedSummaryId)
  const isGeneral = selectedSummaryId === "tab_general"
  /** Only while General tokens are still arriving — not while blueprint runs. */
  const generalSummaryWriting =
    bpStream.summaryGenState === "prefilling" ||
    bpStream.summaryGenState === "streaming"
  const isTabGenerating = selectedTab?.processing_state === "generating"
  /** Live SSE for the selected section (survives processing_state lag). */
  const sectionLive =
    !isGeneral &&
    (sectionStream.isStreaming ||
      sectionStream.genState === "prefilling" ||
      sectionStream.genState === "streaming")
  /** Prefill/thinking only — once tokens exist, use SummaryMarkdownViewer like translation. */
  const generalWaitingTokens =
    isGeneral &&
    generalSummaryWriting &&
    !bpStream.streamingMd &&
    !bpStream.settledSummaryMd
  const sectionWaitingTokens =
    !isGeneral &&
    (isTabGenerating ||
      sectionLive ||
      loadingTabs.has(selectedSummaryId)) &&
    !sectionStream.streamingMd

  // ── Summary translation view ─────────────────────────────
  // Content priority: live stream > cached translation > original summary.
  const activeLang = activeSummaryLang
  const activeTranslation = activeLang ? translations[selectedSummaryId]?.[activeLang] : undefined
  const streamingTranslationMd = activeLang ? tStream.streamingMd : ""
  const isTranslating = !!(activeLang && tStream.isStreaming)
  const viewingTranslation = !!activeLang && (isTranslating || !!streamingTranslationMd || !!activeTranslation)
  const displayContent = streamingTranslationMd
    ? streamingTranslationMd
    : (activeTranslation ?? getTabContent(selectedSummaryId))

  const exportSectionLabel = isGeneral
    ? t("meeting.general")
    : selectedTab
      ? `${tabShortLabel(selectedTab)} ${selectedTab.name || (blueprint as { blueprint_id?: string; tab_name?: string }[]).find((b) => b.blueprint_id === selectedTab.blueprint_id)?.tab_name || ""}`.trim()
      : t("common.summary")
  const exportFilenameBase = safeExportBasename([
    meeting.title,
    exportSectionLabel,
    activeLang || undefined,
  ])

  const handleExportMarkdown = () => {
    setExportMenuOpen(false)
    const body = displayContent || ""
    if (!body.trim()) {
      toast.error(t("meeting.nothingToExport"))
      return
    }
    try {
      exportSummaryMarkdown({
        filenameBase: exportFilenameBase,
        markdown: body,
        speakerNames,
      })
      toast.success(t("meeting.markdownDownloaded"))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("meeting.exportFailed"))
    }
  }

  const handleExportPdf = () => {
    setExportMenuOpen(false)
    const body = displayContent || ""
    if (!body.trim()) {
      toast.error(t("meeting.nothingToExport"))
      return
    }
    try {
      exportSummaryAsPdf({
        title: exportFilenameBase,
        markdown: body,
        speakerNames,
      })
      toast.message(t("meeting.printDialogOpened"), {
        description: t("meeting.printDialogHint"),
      })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("meeting.pdfExportFailed"))
    }
  }

  /* Main content card: top/bottom scroll edge fades (white paper) */
  const contentEdgeFade = useScrollEdgeFade(
    contentScrollRef,
    `${meetingId}:${selectedSummaryId}:${mainTab}:${String(displayContent || "").length}`,
  )

  // Close export SoftMenu on outside click
  useEffect(() => {
    if (!exportMenuOpen) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (exportBtnRef.current?.contains(t)) return
      const menu = document.querySelector('[data-slot="menu"][data-export-summary]')
      if (menu?.contains(t)) return
      setExportMenuOpen(false)
    }
    document.addEventListener("mousedown", onDown)
    return () => document.removeEventListener("mousedown", onDown)
  }, [exportMenuOpen])

  return (
    <div className={cn("pm-meeting-tabs-shell flex flex-col min-h-0 flex-1", className)}>

      {/* ── Tab bar inside content card: Summary | Notes + tools ── */}
      <div
        ref={summaryBarRef}
        className={cn(
          "pm-meeting-tabs-bar flex items-center gap-2 shrink-0",
          floatingPanelOpen && canShift && "is-panel-open",
        )}
        style={{ top: tabBarOffset }}
      >
        <Tabs
          value={mainTab === "transcript" || mainTab === "speaker" ? (hasSummary ? "summary" : "notes") : mainTab}
          onValueChange={(v) => setMainTab(v)}
          className="gap-0"
        >
          <TabsList className="pm-pill-tabs relative">
            <TabsIndicator className="pm-tabs-indicator" renderBeforeHydration />
            <TabsTrigger value="summary" disabled={!hasSummary && !hasTranscript}>
              {t("common.summary")}
            </TabsTrigger>
            <TabsTrigger value="notes">{t("common.notes")}</TabsTrigger>
          </TabsList>
        </Tabs>
        {busy && <Loader2 className="size-3.5 animate-spin text-[var(--pm-faint)] shrink-0" />}

        {mainTab === "summary" && (hasBlueprint || tabs.some(t => t.tab_id === "tab_general" && t.md_file_path)) && (
          <div className="pm-meeting-tabs-actions">
            <SummaryTranslateControl
              generatedLangs={availableLangs[selectedSummaryId] ?? []}
              activeLang={activeLang}
              translating={isTranslating}
              disabled={isTabGenerating || ingestingTabs.has(selectedSummaryId)}
              onSelect={(lang) => void handleSelectLang(selectedSummaryId, lang)}
              onOpen={() => void refreshTranslations(selectedSummaryId)}
            />
            {isGeneral && (
              <Button
                type="button"
                variant={busy ? "secondary" : "ghost"}
                size="sm"
                className={cn(busy && "sk-thinking-flow")}
                disabled={busy || ingestingTabs.size > 0}
                onClick={() => setReSummarizeOpen(true)}
                title={t("meeting.reSummarizeAction")}
              >
                {busy ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <RefreshCw className="size-3.5" />
                )}
                {busy ? t("meeting.summarizing") : t("meeting.reSummarizeAction")}
              </Button>
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              ref={exportBtnRef}
              disabled={
                isTabGenerating ||
                ingestingTabs.has(selectedSummaryId) ||
                !String(displayContent || "").trim()
              }
              onClick={() => setExportMenuOpen((v) => !v)}
              title={t("meeting.exportSummary")}
              aria-label={t("meeting.exportSummary")}
              aria-expanded={exportMenuOpen}
            >
              <Download className="size-3.5" />
            </Button>
            <SoftMenu
              open={exportMenuOpen}
              portal
              anchorRef={exportBtnRef}
              align="end"
              exitMs={MENU_SILK_MS}
              className="pm-meeting-export-menu min-w-[220px]"
              data-export-summary=""
            >
              <div className="pm-meeting-export-menu-label" aria-hidden>
                {t("common.export")}
              </div>
              <MenuItem onClick={handleExportMarkdown} className="pm-meeting-export-item">
                <span className="pm-meeting-export-icon" aria-hidden>
                  <FileText className="size-3.5" />
                </span>
                <span className="min-w-0 flex-1">
                  <MenuItemTitle>{t("meeting.markdown")}</MenuItemTitle>
                  <MenuItemDescription>{t("meeting.exportMdDesc")}</MenuItemDescription>
                </span>
              </MenuItem>
              <MenuItem onClick={handleExportPdf} className="pm-meeting-export-item">
                <span className="pm-meeting-export-icon" aria-hidden>
                  <FileType2 className="size-3.5" />
                </span>
                <span className="min-w-0 flex-1">
                  <MenuItemTitle>{t("meeting.pdf")}</MenuItemTitle>
                  <MenuItemDescription>{t("meeting.exportPdfDesc")}</MenuItemDescription>
                </span>
              </MenuItem>
            </SoftMenu>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              disabled={isTabGenerating || ingestingTabs.has(selectedSummaryId) || viewingTranslation}
              onClick={() => editableSectionRef.current?.startEditing()}
              title={t("common.edit")}
              aria-label={t("common.edit")}
            >
              <Pencil className="size-3.5" />
            </Button>
            {/* Section tools: Re-generate + Delete — stay in toolbar so width stays stable */}
            {!isGeneral && selectedTab ? (
              <>
                <Button
                  type="button"
                  variant={isTabGenerating ? "secondary" : "ghost"}
                  size="sm"
                  className={cn("shrink-0", isTabGenerating && "sk-thinking-flow")}
                  disabled={isTabGenerating || ingestingTabs.has(selectedSummaryId) || busy}
                  onClick={() => {
                    if (selectedTab.allocated_file_id) {
                      setRegenerateConfirmOpen(true)
                    } else {
                      void handleRegenerate(selectedSummaryId)
                    }
                  }}
                  title={t("meeting.reGenerateSection")}
                  aria-label={t("meeting.reGenerateSection")}
                >
                  {isTabGenerating ? (
                    <Loader2 className="size-3.5 animate-spin mr-1" />
                  ) : (
                    <Sparkles className="size-3.5 mr-1" />
                  )}
                  {isTabGenerating ? t("meeting.generating") : t("meeting.reGenerate")}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="shrink-0 text-[var(--pm-danger,#b42318)] hover:text-[var(--pm-danger,#b42318)] hover:bg-[color-mix(in_srgb,var(--pm-danger,#b42318)_8%,transparent)]"
                  onClick={() => handleDeleteSection(selectedSummaryId)}
                  title={t("meeting.deleteSection")}
                  aria-label={t("meeting.deleteSection")}
                  disabled={
                    isTabGenerating ||
                    busy ||
                    ingestingTabs.has(selectedSummaryId)
                  }
                >
                  <Trash2 className="size-3.5 mr-1" />
                  {t("common.delete")}
                </Button>
              </>
            ) : null}
          </div>
        )}
      </div>

      {mainTab === "notes" ? (
        <div className="pm-meeting-notes-fmt-bar">
          {notesEditor && !notesEditor.isDestroyed ? (
            <EditorToolbar editor={notesEditor} stickyOffset={0} />
          ) : null}
        </div>
      ) : null}

      {/* Body scrolls inside the content card; tab bar + notes fmt stay fixed above */}
      <div className={cn("pm-meeting-content-scroll-shell", mainTab === "notes" && "is-notes")}>
        <div
          ref={contentScrollRef}
          className="pm-meeting-content-scroll"
        >
      {/* ── Summary Tab ── */}
      <div className={cn(
        "transition-opacity duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]",
        mainTab === "summary"
          ? "flex flex-col opacity-100"
          : "hidden",
      )}>
        <div
          className={cn(
            "min-h-0 pm-meeting-content-swap",
            sectionContentFaded && "is-faded",
          )}
        >
          {(() => {
            /* Summary generating: always fence / stream — never empty "No content yet"
             * even when early blueprint already landed (hasBlueprint=true).
             * Do NOT gate on bare streamingMd after isStreaming ends — that kept
             * ReactMarkdown (no speaker/sentence chips) until a full page refresh. */
            if (generalWaitingTokens) {
              return (
                  <div className="pm-meeting-fence-pad">
                    <div className="sk-thinking-flow pm-meeting-fence-card rounded-[var(--pm-r,16px)] p-5 space-y-4 min-h-[200px]">
                      <div className="flex items-center gap-2">
                        <Loader2 className="h-4 w-4 animate-spin" style={{ color: "var(--ze-green)" }} />
                        <span className="pm-label">{t("fileMgmt.generatingSummary")}</span>
                      </div>
                      {bpStream.thinkingText ? (
                        <details className="mb-2" open>
                          <summary className="text-xs text-muted-foreground cursor-pointer select-none">
                            {t("meeting.thinkingEllipsis")}
                          </summary>
                          <p className="text-xs text-muted-foreground/60 mt-2 leading-relaxed whitespace-pre-wrap t-mono-family max-h-32 overflow-auto">
                            {bpStream.thinkingText}
                          </p>
                        </details>
                      ) : null}
                      {[1, 2, 3, 4, 5].map((i) => (
                        <div
                          key={`sum-sk-${i}`}
                          className="h-4 rounded animate-pulse"
                          style={{
                            background: "oklch(0.38 0.08 160 / 0.12)",
                            width: `${50 + i * 10}%`,
                          }}
                        />
                      ))}
                    </div>
                  </div>
              )
            }

            if (sectionWaitingTokens) {
              if (isGeneral) return <ThinkingSkeleton />
              return (
                <div className="flex flex-col min-h-0 overflow-auto">
                  <div className="px-6 pt-6 pb-3">
                    <div className="flex items-start gap-2">
                      <span className="pm-meeting-title shrink-0">
                        {tabShortLabel(selectedTab!)} {selectedTab?.name}
                      </span>
                      <Loader2 className="size-3.5 animate-spin mt-1.5 shrink-0 text-[var(--pm-green)]" />
                    </div>
                    {selectedTab?.description && (
                      <p className="pm-meta leading-relaxed mt-1">{selectedTab.description}</p>
                    )}
                  </div>
                  <div className="pm-meeting-fence-pad flex-1">
                    <div className="sk-thinking-flow pm-meeting-fence-card rounded-[var(--pm-r,16px)] p-6 pt-10 space-y-4">
                      <div className="h-6 w-1/3 rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.12)" }} />
                      <div className="space-y-3 pt-2">
                        <div className="h-3 w-full rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.1s" }} />
                        <div className="h-3 w-5/6 rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.3s" }} />
                        <div className="h-3 w-4/6 rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.5s" }} />
                        <div className="h-3 w-full rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.2s" }} />
                        <div className="h-3 w-3/6 rounded animate-pulse" style={{ background: "oklch(0.38 0.08 160 / 0.08)", animationDelay: "0.6s" }} />
                      </div>
                    </div>
                  </div>
                </div>
              )
            }

            const hasGeneralMd =
              !!tabMdContents["tab_general"] ||
              !!bpStream.settledSummaryMd ||
              !!bpStream.streamingMd ||
              !!tabs.some((t) => t.tab_id === "tab_general" && t.md_file_path)
            if (!hasBlueprint && !hasGeneralMd) {
              return (
                <div className="flex items-center justify-center h-full min-h-[160px]">
                  {hasTranscript ? (
                    <Button variant="default" size="sm" onClick={handleSummarize}>
                      <Sparkles className="size-3.5 mr-1.5" /> {t("meeting.summarize")}
                    </Button>
                  ) : (
                    <p className="pm-meta">{t("meeting.noContentYet")}</p>
                  )}
                </div>
              )
            }

            return null
          })()}
          {/* Same viewer as translation — chips/fonts update as tokens arrive */}
          {!generalWaitingTokens &&
          !sectionWaitingTokens &&
          (hasBlueprint ||
            !!tabMdContents["tab_general"] ||
            !!bpStream.settledSummaryMd ||
            !!bpStream.streamingMd ||
            !!tabMdContents[selectedSummaryId] ||
            (!isGeneral && !!sectionStream.streamingMd) ||
            tabs.some((t) => t.tab_id === "tab_general" && t.md_file_path)) ? (
            <>
              <EditableSectionContent
                ref={editableSectionRef}
                content={displayContent}
                onSave={async (draft) => handleSaveSection(selectedSummaryId, draft)}
                onRefClick={handleRefClick}
                speakerNames={speakerNames}
                actionsDisabled={ingestingTabs.has(selectedSummaryId)}
                editDisabled={viewingTranslation}
                hideInlineEdit
                stickyOffset={contentStickyOffset}
                ingestHostRef={selectedTab ? sectionIngestHostRef : undefined}
                title={isGeneral ? t("meeting.general") : undefined}
                titlePrefix={
                  !isGeneral && selectedTab ? tabShortLabel(selectedTab) : undefined
                }
                titleName={
                  !isGeneral && selectedTab
                    ? selectedTab.name ||
                      blueprint.find((b) => b.blueprint_id === selectedTab.blueprint_id)?.tab_name ||
                      ""
                    : undefined
                }
                onSaveTitle={
                  !isGeneral && selectedTab
                    ? (name) => handleSaveSectionTitle(selectedTab.tab_id, name)
                    : undefined
                }
                metadata={
                  selectedTab ? (
                    <SectionMetadata
                      key={selectedTab.tab_id}
                      ref={sectionMetaRef}
                      tab={selectedTab}
                      blueprint={blueprint}
                      tabs={tabs}
                      meetingId={meetingId}
                      onMeetingUpdate={onMeetingUpdate}
                      hideTitle
                      ingestHostRef={sectionIngestHostRef}
                      ingestLocked={isGeneral && generalSummaryWriting}
                      parentIngesting={ingestingTabs.has(selectedTab.tab_id)}
                      onIngestingChange={(tabId, v) => {
                        setIngestingTabs((prev) => {
                          const has = prev.has(tabId)
                          if (v && has) return prev
                          if (!v && !has) return prev
                          const next = new Set(prev)
                          if (v) next.add(tabId)
                          else next.delete(tabId)
                          return next
                        })
                      }}
                    />
                  ) : undefined
                }
              />
            </>
          ) : null}
        </div>
      </div>

      {/* ── Notes Tab ── */}
      <div className={cn(
        "transition-opacity duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]",
        mainTab === "notes" ? "flex flex-col opacity-100" : "hidden",
      )}>
        <div className="pm-meeting-notes-card">
          <MarkdownEditor
            value={notesContent}
            onChange={onNotesChange}
            minHeight="400px"
            showToolbar={false}
            placeholder={t("meeting.writeNotesPh")}
            onImageUpload={handleNotesImageUpload}
            onEditorReady={(ed) => setNotesEditor(ed as Editor)}
          />
        </div>
      </div>

      {/* Transcript / Speaker live in the parent side rail when hostTranscriptInParent */}
      {!hostTranscriptInParent && (
        <>
          <div className={cn(
            "transition-opacity duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]",
            mainTab === "transcript" ? "flex flex-col" : "hidden",
          )}>
            <div className="pm-meeting-panel-card">
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

          <div className={cn(
            "transition-opacity duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]",
            mainTab === "speaker" ? "flex flex-col" : "hidden",
          )}>
            <div className="pm-meeting-panel-card">
              <SpeakersTab
                segments={transcriptSegments}
                speakerNames={speakerNames}
                meetingId={meetingId}
                speakerMatches={meeting.speaker_matches}
                slotsStatus={meeting.speaker_slots_status}
                slotsMs={meeting.speaker_slots_ms}
                onPersonAssigned={(m) => {
                  onMeetingUpdate(m)
                  void import("@/components/ui/tiptap-editor").then((mod) => {
                    mod.invalidateMeetingSpeakerCache(meetingId)
                  })
                }}
                onSegmentClick={onSeekTo}
                activeSectionTag={activeSectionTag}
              />
            </div>
          </div>
        </>
      )}
        </div>{/* /.pm-meeting-content-scroll */}
        <div
          className={cn(
            "pm-rail-edge-fade pm-rail-edge-fade--top",
            contentEdgeFade.top && "is-visible",
          )}
          aria-hidden
        />
        <div
          className={cn(
            "pm-rail-edge-fade pm-rail-edge-fade--bottom",
            contentEdgeFade.bottom && "is-visible",
          )}
          aria-hidden
        />
      </div>{/* /.pm-meeting-content-scroll-shell */}

      {/* Add Section Dialog — premium silk form */}
      <Dialog open={addSectionOpen} onOpenChange={(open) => {
        setAddSectionOpen(open)
        if (!open) setAddForm({ name: "", description: "", blueprintId: null })
      }}>
        <DialogContent
          className="pm-dialog pm-dialog--silk pm-meeting-add-section-dialog sm:max-w-[560px]"
          overlayClassName="pm-dialog-overlay--silk"
        >
          <DialogHeader>
            <DialogKicker>{t("meeting.section")}</DialogKicker>
            <DialogTitle>
              {hasSections ? t("meeting.extractASection") : t("meeting.addToBreakdown")}
            </DialogTitle>
            <DialogDescription>
              {hasSections
                ? t("meeting.extractSectionHelp")
                : t("meeting.addBreakdownHelp")}
            </DialogDescription>
          </DialogHeader>

          {(() => {
            const bpItems = hasSections
              ? blueprint.filter((b) => !tabs.some((t) => t.blueprint_id === b.blueprint_id))
              : []
            const hasBpRail = bpItems.length > 0

            const formFields = (
              <div className="pm-meeting-add-section-form">
                <div className="pm-meeting-add-section-field">
                  <label className="pm-meeting-add-section-label" htmlFor="add-section-name">
                    {t("common.name")}
                    <span className="pm-meeting-add-section-req" aria-hidden>*</span>
                  </label>
                  <Input
                    id="add-section-name"
                    className="pm-meeting-add-section-input"
                    placeholder={t("meeting.sectionNamePh")}
                    value={addForm.name}
                    onChange={(e) => {
                      setAddForm((prev) => ({ ...prev, name: e.target.value, blueprintId: null }))
                    }}
                  />
                </div>
                <div className="pm-meeting-add-section-field">
                  <div className="pm-meeting-add-section-label-row">
                    <label className="pm-meeting-add-section-label" htmlFor="add-section-desc">
                      {t("common.description")}
                    </label>
                    <button
                      type="button"
                      className="pm-meeting-add-section-ai"
                      disabled={generatingDesc}
                      onClick={handleGenerateDesc}
                      title={t("meeting.generateFromGeneral")}
                    >
                      {generatingDesc ? (
                        <Loader2 className="size-3 animate-spin" />
                      ) : (
                        <Sparkles className="size-3" />
                      )}
                      <span>{generatingDesc ? t("meeting.writing") : t("meeting.suggest")}</span>
                    </button>
                  </div>
                  <div
                    className={cn(
                      "pm-meeting-add-section-textarea-shell",
                      generatingDesc && "sk-flow-full",
                    )}
                  >
                    <Textarea
                      id="add-section-desc"
                      className="pm-meeting-add-section-textarea"
                      placeholder={t("meeting.sectionCoverPh")}
                      value={addForm.description}
                      onChange={(e) => {
                        setAddForm((prev) => ({
                          ...prev,
                          description: e.target.value,
                          blueprintId: null,
                        }))
                      }}
                      rows={7}
                    />
                  </div>
                </div>
              </div>
            )

            if (!hasBpRail) {
              return <div className="pm-meeting-add-section-body">{formFields}</div>
            }

            return (
              <div className="pm-meeting-add-section-body pm-meeting-add-section-body--split">
                <aside className="pm-meeting-add-section-bp" aria-label={t("meeting.blueprintTopics")}>
                  <p className="pm-meeting-add-section-bp-label">{t("meeting.fromBlueprint")}</p>
                  <div className="pm-meeting-add-section-bp-list">
                    {bpItems.map((b) => {
                      const on = addForm.blueprintId === b.blueprint_id
                      return (
                        <button
                          key={b.blueprint_id}
                          type="button"
                          onClick={() => {
                            setAddForm({
                              name: b.tab_name,
                              description: b.tab_description,
                              blueprintId: b.blueprint_id,
                            })
                          }}
                          className={cn(
                            "pm-meeting-add-section-bp-item",
                            on && "is-active",
                          )}
                        >
                          <span className="pm-meeting-add-section-bp-name">{b.tab_name}</span>
                          {b.tab_description ? (
                            <span className="pm-meeting-add-section-bp-hint">
                              {b.tab_description}
                            </span>
                          ) : null}
                        </button>
                      )
                    })}
                  </div>
                </aside>
                <div className="pm-meeting-add-section-main">{formFields}</div>
              </div>
            )
          })()}

          <DialogFooter className="pm-meeting-add-section-footer">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="pm-meeting-add-section-cancel"
              onClick={() => {
                setAddSectionOpen(false)
                setAddForm({ name: "", description: "", blueprintId: null })
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button
              type="button"
              variant="default"
              size="sm"
              className="pm-meeting-add-section-submit"
              disabled={!addForm.name.trim()}
              onClick={handleAddOrExtract}
            >
              {hasSections ? t("meeting.extractSection") : t("meeting.addToList")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Re-summarize Confirmation Dialog */}
      <Dialog open={reSummarizeOpen} onOpenChange={setReSummarizeOpen}>
        <DialogContent
          className="pm-dialog pm-dialog--silk sm:max-w-sm"
          showCloseButton={false}
          overlayClassName="pm-dialog-overlay--silk"
        >
          <DialogHeader>
            <DialogKicker>{t("common.summary")}</DialogKicker>
            <DialogTitle>{t("meeting.reSummarize")}</DialogTitle>
            <DialogDescription>
              {t("meeting.reSummarizeBody")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" size="sm" onClick={() => setReSummarizeOpen(false)}>{t("common.cancel")}</Button>
            <Button type="button" variant="default" size="sm" onClick={handleReSummarize}>{t("meeting.reSummarizeAction")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Regenerate Section Confirmation Dialog */}
      <Dialog open={regenerateConfirmOpen} onOpenChange={setRegenerateConfirmOpen}>
        <DialogContent
          className="pm-dialog pm-dialog--silk sm:max-w-sm"
          showCloseButton={false}
          overlayClassName="pm-dialog-overlay--silk"
        >
          <DialogHeader>
            <DialogKicker>{t("meeting.section")}</DialogKicker>
            <DialogTitle>{t("meeting.regenerateSection")}</DialogTitle>
            <DialogDescription>
              {t("meeting.regenerateSectionBody")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" size="sm" onClick={() => setRegenerateConfirmOpen(false)}>{t("common.cancel")}</Button>
            <Button
              type="button"
              variant="default"
              size="sm"
              onClick={() => {
                setRegenerateConfirmOpen(false)
                handleRegenerate(selectedSummaryId)
              }}
            >
              {t("recall.regenerate")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Section Confirmation Dialog */}
      <Dialog open={!!deleteSectionTarget} onOpenChange={(v) => { if (!v) setDeleteSectionTarget(null) }}>
        <DialogContent
          className="pm-dialog pm-dialog--silk sm:max-w-sm"
          showCloseButton={false}
          overlayClassName="pm-dialog-overlay--silk"
        >
          <DialogHeader>
            <DialogKicker>{t("meeting.section")}</DialogKicker>
            <DialogTitle>{t("meeting.deleteSectionQ")}</DialogTitle>
            <DialogDescription>
              {t("meeting.deleteSectionBody")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" size="sm" onClick={() => setDeleteSectionTarget(null)}>{t("common.cancel")}</Button>
            <Button type="button" variant="destructive-solid" size="sm" onClick={confirmDeleteSection}>{t("common.delete")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
