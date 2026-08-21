import {
  useState,
  useEffect,
  useLayoutEffect,
  useCallback,
  useRef,
} from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { FieldLabel } from "@/components/ui/field-label"
import { ScrollArea } from "@/components/ui/scroll-area"
import { DropdownSelect, type DropdownSelectOption } from "@/components/ui/dropdown-select"
import {
  Plus,
  Trash2,
  BookOpen,
  FileDown,
  FileUp,
  FileSpreadsheet,
  FileText,
  Pin,
} from "lucide-react"
import {
  getHotWordsLibraries, getHotWordsLibrary, createHotWordsLibrary,
  updateHotWordsLibrary, deleteHotWordsLibrary,
  downloadHotWordsTemplate, importHotWordsLibrary,
  setPinnedHotWordsLibraries, exportHotWordsLibrary,
  type HotWordsLibrary, type HotWordsLibrarySummary, type HotWordItem,
} from "@/api/client"
import {
  SoftMenu,
  MenuItem,
  MenuItemTitle,
  MenuItemDescription,
  MENU_SILK_MS,
} from "@/components/ui/menu"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"

/** Client-only row key for stable list animation (not sent to API). */
type WordRow = HotWordItem & { uid: string }

let wordUidSeq = 0
function newWordUid() {
  wordUidSeq += 1
  return `hw-${Date.now().toString(36)}-${wordUidSeq}`
}

function toWordRows(words: HotWordItem[]): WordRow[] {
  return words.map((w) => ({
    uid: newWordUid(),
    text: w.text,
    weight: w.weight,
    lang: w.lang ?? "",
  }))
}

function toHotWordItems(rows: WordRow[]): HotWordItem[] {
  return rows.map(({ text, weight, lang }) => ({
    text,
    weight,
    lang: lang || undefined,
  }))
}

const WORD_LIST_EASE = "cubic-bezier(0.32, 0.72, 0, 1)"
const WORD_LIST_MS = 300
/** Keep in sync with `.pm-settings-hw-main-body` opacity transition */
const MAIN_FADE_MS = 220

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  )
}

function waitMs(ms: number) {
  return new Promise<void>((resolve) => {
    window.setTimeout(resolve, ms)
  })
}

/**
 * Two-step delete (× → Delete) — same anti-mis-tap pattern as message cards
 * (message-card.tsx · .pm-msg-delete).
 */
function SlideConfirmDeleteButton({
  onConfirm,
  title,
  className,
}: {
  onConfirm: () => void
  title?: string
  className?: string
}) {
  const t = useT()
  const actionTitle = title ?? t("common.delete")
  const [deleteArmed, setDeleteArmed] = useState(false)
  const deleteArmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const deleteBtnRef = useRef<HTMLButtonElement>(null)

  const disarmDelete = useCallback(() => {
    setDeleteArmed(false)
    if (deleteArmTimerRef.current) {
      clearTimeout(deleteArmTimerRef.current)
      deleteArmTimerRef.current = null
    }
  }, [])

  const armDelete = useCallback(() => {
    setDeleteArmed(true)
    if (deleteArmTimerRef.current) clearTimeout(deleteArmTimerRef.current)
    deleteArmTimerRef.current = setTimeout(() => disarmDelete(), 4000)
  }, [disarmDelete])

  useEffect(() => {
    if (!deleteArmed) return
    const onPointerDown = (ev: Event) => {
      const t = ev.target as Node | null
      if (t && deleteBtnRef.current?.contains(t)) return
      disarmDelete()
    }
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") disarmDelete()
    }
    const t = window.setTimeout(() => {
      document.addEventListener("pointerdown", onPointerDown, true)
      document.addEventListener("keydown", onKey, true)
    }, 0)
    return () => {
      window.clearTimeout(t)
      document.removeEventListener("pointerdown", onPointerDown, true)
      document.removeEventListener("keydown", onKey, true)
    }
  }, [deleteArmed, disarmDelete])

  useEffect(() => {
    return () => {
      if (deleteArmTimerRef.current) clearTimeout(deleteArmTimerRef.current)
    }
  }, [])

  return (
    <button
      ref={deleteBtnRef}
      type="button"
      className={cn(
        "pm-msg-delete",
        deleteArmed ? "is-confirm opacity-100" : "opacity-40 group-hover:opacity-100",
        className,
      )}
      title={deleteArmed ? t("settings.clickAgainDelete") : actionTitle}
      aria-label={deleteArmed ? t("common.confirm") : actionTitle}
      aria-expanded={deleteArmed}
      onClick={(e) => {
        e.preventDefault()
        e.stopPropagation()
        if (!deleteArmed) {
          armDelete()
          return
        }
        disarmDelete()
        onConfirm()
      }}
    >
      {/* Idle: × only. Armed: Delete text only (no × icon). */}
      {!deleteArmed ? (
        <span className="pm-msg-delete-x" aria-hidden>
          ×
        </span>
      ) : (
        <span className="pm-msg-delete-label is-solo">{t("common.delete")}</span>
      )}
    </button>
  )
}

/**
 * DashScope cloud transcription languages (file adapter SUPPORTED_LANGUAGE_HINTS).
 * Keep in sync with `DashScopeFileTranscriptionProvider` — exclude `auto`.
 * Empty = no language limit (recommended when unsure).
 */
const HOT_WORD_LANG_KEYS: { value: string; key: string }[] = [
  { value: "", key: "common.any" },
  { value: "zh", key: "settings.hwLangZh" },
  { value: "en", key: "settings.hwLangEn" },
  { value: "ja", key: "settings.hwLangJa" },
  { value: "ko", key: "settings.hwLangKo" },
  { value: "vi", key: "settings.hwLangVi" },
  { value: "th", key: "settings.hwLangTh" },
  { value: "id", key: "settings.hwLangId" },
  { value: "ms", key: "settings.hwLangMs" },
]

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  /**
   * When opened from another dialog (e.g. meeting hot-words picker),
   * stack above the parent dialog (higher z-index overlay + popup).
   */
  nested?: boolean
}

export function HotWordsManager({ open, onOpenChange, nested = false }: Props) {
  const t = useT()
  const langOptions: DropdownSelectOption[] = HOT_WORD_LANG_KEYS.map((o) => ({
    value: o.value,
    label: t(o.key),
  }))
  const [libraries, setLibraries] = useState<HotWordsLibrarySummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedLib, setSelectedLib] = useState<HotWordsLibrary | null>(null)
  /** Editable word list with stable UIDs for FLIP animation */
  const [wordRows, setWordRows] = useState<WordRow[]>([])
  const [isDirty, setIsDirty] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  /** Main pane opacity — fade out on switch, fade in when library is ready */
  const [mainIn, setMainIn] = useState(true)
  /** Row currently collapsing out (kept in DOM until anim ends) */
  const [leavingUid, setLeavingUid] = useState<string | null>(null)

  /* Sliding mint indicator under the active library row */
  const listRef = useRef<HTMLDivElement>(null)
  const itemRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const [indicator, setIndicator] = useState({ top: 0, height: 0 })
  const [indicatorReady, setIndicatorReady] = useState(false)

  /* Word list FLIP: previous tops + entering uid */
  const wordListRef = useRef<HTMLDivElement>(null)
  const wordTopsRef = useRef<Map<string, number>>(new Map())
  const pendingEnterUidRef = useRef<string | null>(null)
  const skipWordFlipRef = useRef(true)

  /* Autosave — latest snapshot + debounce */
  const selectedIdRef = useRef(selectedId)
  const selectedLibRef = useRef(selectedLib)
  const wordRowsRef = useRef(wordRows)
  const isDirtyRef = useRef(isDirty)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const saveInFlightRef = useRef(false)
  selectedIdRef.current = selectedId
  selectedLibRef.current = selectedLib
  wordRowsRef.current = wordRows
  isDirtyRef.current = isDirty

  const captureWordTops = useCallback(() => {
    const map = new Map<string, number>()
    const root = wordListRef.current
    if (!root) {
      wordTopsRef.current = map
      return
    }
    root.querySelectorAll<HTMLElement>("[data-word-uid]").forEach((el) => {
      const uid = el.dataset.wordUid
      if (uid) map.set(uid, el.getBoundingClientRect().top)
    })
    wordTopsRef.current = map
  }, [])

  const fetchList = useCallback(async () => {
    try {
      const list = await getHotWordsLibraries()
      setLibraries(list)
      setPinnedIds(list.filter((l) => l.is_pinned).map((l) => l.id))
    } catch { /* ignore */ }
  }, [])

  const clearSaveTimer = useCallback(() => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
    }
  }, [])

  /** Persist current draft. Does not remount word rows (keeps focus / FLIP keys). */
  const flushSave = useCallback(async () => {
    clearSaveTimer()
    const id = selectedIdRef.current
    const lib = selectedLibRef.current
    const rows = wordRowsRef.current
    if (!id || !lib || !isDirtyRef.current || lib.is_system) return
    if (saveInFlightRef.current) {
      saveTimerRef.current = setTimeout(() => {
        void flushSave()
      }, 120)
      return
    }

    saveInFlightRef.current = true
    setIsSaving(true)
    const payload = {
      name: lib.name,
      description: lib.description,
      words: toHotWordItems(rows),
    }
    try {
      const updated = await updateHotWordsLibrary(id, payload)
      if (selectedIdRef.current === id) {
        setSelectedLib((prev) =>
          prev && prev.id === id
            ? {
                ...prev,
                name: updated.name,
                description: updated.description,
                updated_at: updated.updated_at,
              }
            : prev,
        )
        const stillSame =
          selectedLibRef.current?.name === payload.name &&
          selectedLibRef.current?.description === payload.description &&
          JSON.stringify(toHotWordItems(wordRowsRef.current)) ===
            JSON.stringify(payload.words)
        if (stillSame) {
          setIsDirty(false)
          isDirtyRef.current = false
        }
      }
      await fetchList()
    } catch {
      toast.error(t("library.autoSaveFailed"))
    } finally {
      saveInFlightRef.current = false
      setIsSaving(false)
    }
  }, [clearSaveTimer, fetchList, t])

  const scheduleSave = useCallback(() => {
    setIsDirty(true)
    isDirtyRef.current = true
    clearSaveTimer()
    saveTimerRef.current = setTimeout(() => {
      void flushSave()
    }, 450)
  }, [clearSaveTimer, flushSave])

  useEffect(() => {
    return () => clearSaveTimer()
  }, [clearSaveTimer])

  useEffect(() => {
    if (open) fetchList()
  }, [open, fetchList])

  /*
   * Load library on selection with a real out → swap → in fade.
   * Must wait for fade-out to paint + finish; otherwise a fast fetch
   * batches mainIn false/true and the transition never shows.
   * Switch path flushes autosave first so we can clear the debounce timer here.
   */
  useEffect(() => {
    let cancelled = false
    let fadeInRaf1 = 0
    let fadeInRaf2 = 0
    clearSaveTimer()

    if (!selectedId) {
      setMainIn(false)
      skipWordFlipRef.current = true
      ;(async () => {
        if (!prefersReducedMotion()) await waitMs(MAIN_FADE_MS)
        if (cancelled) return
        setSelectedLib(null)
        setWordRows([])
        setLeavingUid(null)
        setIsDirty(false)
        isDirtyRef.current = false
        fadeInRaf1 = requestAnimationFrame(() => {
          fadeInRaf2 = requestAnimationFrame(() => {
            if (!cancelled) setMainIn(true)
          })
        })
      })()
      return () => {
        cancelled = true
        if (fadeInRaf1) cancelAnimationFrame(fadeInRaf1)
        if (fadeInRaf2) cancelAnimationFrame(fadeInRaf2)
      }
    }

    setMainIn(false)
    skipWordFlipRef.current = true

    ;(async () => {
      /* Let opacity:0 paint, then wait for CSS fade-out */
      await new Promise<void>((r) => {
        requestAnimationFrame(() => requestAnimationFrame(() => r()))
      })
      if (cancelled) return
      if (!prefersReducedMotion()) await waitMs(MAIN_FADE_MS)
      if (cancelled) return

      try {
        const lib = await getHotWordsLibrary(selectedId)
        if (cancelled) return
        setSelectedLib(lib)
        setWordRows(toWordRows(lib.words))
        setLeavingUid(null)
        setIsDirty(false)
        isDirtyRef.current = false
        if (lib.is_pinned) {
          setPinnedIds((ids) => (ids.includes(lib.id) ? ids : [...ids, lib.id]))
        }

        /* Content at opacity 0 → next frames fade in */
        fadeInRaf1 = requestAnimationFrame(() => {
          fadeInRaf2 = requestAnimationFrame(() => {
            if (cancelled) return
            setMainIn(true)
            requestAnimationFrame(() => {
              if (cancelled) return
              captureWordTops()
              skipWordFlipRef.current = false
            })
          })
        })
      } catch {
        if (!cancelled) {
          toast.error(t("settings.failedLoadLibrary"))
          setMainIn(true)
        }
      }
    })()

    return () => {
      cancelled = true
      if (fadeInRaf1) cancelAnimationFrame(fadeInRaf1)
      if (fadeInRaf2) cancelAnimationFrame(fadeInRaf2)
    }
  }, [selectedId, captureWordTops, clearSaveTimer])

  /* FLIP: existing rows slide when list order/length changes */
  useLayoutEffect(() => {
    const root = wordListRef.current
    if (!root) return

    const enterUid = pendingEnterUidRef.current
    pendingEnterUidRef.current = null

    if (skipWordFlipRef.current || prefersReducedMotion()) {
      captureWordTops()
      return
    }

    const prev = wordTopsRef.current
    const next = new Map<string, number>()

    root.querySelectorAll<HTMLElement>("[data-word-uid]").forEach((el) => {
      const uid = el.dataset.wordUid
      if (!uid || el.dataset.wordLeaving === "1") return
      const top = el.getBoundingClientRect().top
      next.set(uid, top)

      if (uid === enterUid) {
        el.animate(
          [
            { opacity: 0, transform: "translateY(-10px) scale(0.98)" },
            { opacity: 1, transform: "translateY(0) scale(1)" },
          ],
          { duration: WORD_LIST_MS, easing: WORD_LIST_EASE, fill: "both" },
        )
        return
      }

      const oldTop = prev.get(uid)
      if (oldTop == null) return
      const dy = oldTop - top
      if (Math.abs(dy) < 0.5) return
      el.animate(
        [
          { transform: `translateY(${dy}px)` },
          { transform: "translateY(0)" },
        ],
        { duration: WORD_LIST_MS, easing: WORD_LIST_EASE },
      )
    })

    wordTopsRef.current = next
  }, [wordRows, leavingUid, captureWordTops])

  /* Place / slide the focus pill under the active row */
  useEffect(() => {
    if (!selectedId) {
      setIndicatorReady(false)
      return
    }
    const activeEl = itemRefs.current.get(selectedId)
    if (!activeEl) return
    setIndicator({
      top: activeEl.offsetTop,
      height: activeEl.offsetHeight,
    })
    requestAnimationFrame(() => setIndicatorReady(true))
  }, [selectedId, libraries])

  const [importMenuOpen, setImportMenuOpen] = useState(false)
  const [importing, setImporting] = useState(false)
  const [pinnedIds, setPinnedIds] = useState<string[]>([])
  const [settingPin, setSettingPin] = useState(false)
  const importAnchorRef = useRef<HTMLDivElement>(null)
  const importFileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!importMenuOpen) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (importAnchorRef.current?.contains(t)) return
      const el = t instanceof Element ? t : t.parentElement
      if (el?.closest?.(".pm-settings-hw-import-menu, .pm-settings-hw-import-anchor")) return
      setImportMenuOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setImportMenuOpen(false)
    }
    document.addEventListener("mousedown", onDown)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onDown)
      document.removeEventListener("keydown", onKey)
    }
  }, [importMenuOpen])

  const handleNew = async () => {
    try {
      await flushSave()
      const lib = await createHotWordsLibrary({ name: t("settings.newLibrary") })
      await fetchList()
      setSelectedId(lib.id)
    } catch { toast.error(t("settings.failedCreateLibrary")) }
  }

  const handleDownloadTemplate = (format: "csv" | "xlsx") => {
    downloadHotWordsTemplate(format)
    setImportMenuOpen(false)
    toast.success(
      format === "csv" ? t("settings.csvDownloading") : t("settings.excelDownloading"),
    )
  }

  const handleImportFile = async (file: File | null | undefined) => {
    if (!file) return
    setImportMenuOpen(false)
    setImporting(true)
    try {
      await flushSave()
      const lib = await importHotWordsLibrary(file)
      await fetchList()
      setSelectedId(lib.id)
      toast.success(
        t("settings.importedLibrary", { name: lib.name, n: lib.words?.length ?? 0 }),
      )
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("settings.importFailed"))
    } finally {
      setImporting(false)
      if (importFileRef.current) importFileRef.current.value = ""
    }
  }

  const handleTogglePin = async () => {
    if (!selectedId) return
    const next = pinnedIds.includes(selectedId)
      ? pinnedIds.filter((id) => id !== selectedId)
      : [...pinnedIds, selectedId]
    setSettingPin(true)
    try {
      await flushSave()
      const res = await setPinnedHotWordsLibraries(next)
      setPinnedIds(res.pinned_library_ids)
      await fetchList()
      toast.success(
        next.includes(selectedId)
          ? t("settings.pinnedToast")
          : t("settings.unpinnedToast"),
      )
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("settings.failedPin"))
    } finally {
      setSettingPin(false)
    }
  }

  const handleExport = () => {
    if (!selectedId) return
    exportHotWordsLibrary(selectedId, selectedLib?.name)
    toast.success(t("settings.excelExportStarted"))
  }

  const handleDelete = async (id: string) => {
    try {
      if (selectedId === id) clearSaveTimer()
      await deleteHotWordsLibrary(id)
      if (selectedId === id) {
        setSelectedId(null)
        setSelectedLib(null)
        setWordRows([])
        setIsDirty(false)
        isDirtyRef.current = false
      }
      await fetchList()
      toast.success(t("settings.libraryDeleted"))
    } catch { toast.error(t("settings.failedDelete")) }
  }

  const updateField = (field: "name" | "description", value: string) => {
    if (!selectedLib) return
    setSelectedLib({ ...selectedLib, [field]: value })
    scheduleSave()
  }

  const updateWord = (uid: string, field: keyof HotWordItem, value: string | number) => {
    setWordRows((rows) =>
      rows.map((r) => (r.uid === uid ? { ...r, [field]: value } : r)),
    )
    scheduleSave()
  }

  /** Prepend empty row at top; peers FLIP-slide down */
  const addWord = () => {
    if (!selectedLib) return
    captureWordTops()
    const uid = newWordUid()
    pendingEnterUidRef.current = uid
    setWordRows((rows) => [
      { uid, text: "", weight: 4, lang: "" },
      ...rows,
    ])
    scheduleSave()
    /* Keep new empty field in view */
    requestAnimationFrame(() => {
      const el = wordListRef.current?.querySelector<HTMLElement>(
        `[data-word-uid="${uid}"]`,
      )
      el?.scrollIntoView({ block: "nearest", behavior: "smooth" })
      el?.querySelector<HTMLInputElement>("input")?.focus()
    })
  }

  /** Collapse row out, then remove so peers slide up into the gap */
  const removeWord = async (uid: string) => {
    if (leavingUid) return
    const slot = wordListRef.current?.querySelector<HTMLElement>(
      `[data-word-uid="${uid}"]`,
    )

    captureWordTops()

    if (slot && !prefersReducedMotion()) {
      setLeavingUid(uid)
      const h = slot.getBoundingClientRect().height
      const mb = getComputedStyle(slot).marginBottom || "6px"
      slot.dataset.wordLeaving = "1"
      slot.style.overflow = "hidden"
      slot.style.pointerEvents = "none"
      try {
        /* Height + margin collapse → rows below ease upward in layout */
        await slot.animate(
          [
            {
              opacity: 1,
              height: `${h}px`,
              marginBottom: mb,
              transform: "scale(1)",
            },
            {
              opacity: 0,
              height: "0px",
              marginBottom: "0px",
              transform: "scale(0.98)",
            },
          ],
          {
            duration: WORD_LIST_MS - 40,
            easing: WORD_LIST_EASE,
            fill: "forwards",
          },
        ).finished
      } catch {
        /* animation cancelled */
      }
      /* Layout already final after collapse — seed tops, then unmount (no jump) */
      wordTopsRef.current.delete(uid)
      captureWordTops()
      wordTopsRef.current.delete(uid)
    }

    setWordRows((rows) => rows.filter((r) => r.uid !== uid))
    setLeavingUid(null)
    scheduleSave()
  }

  const handleSwitchLibrary = (id: string) => {
    if (id === selectedId) return
    void (async () => {
      await flushSave()
      setSelectedId(id)
    })()
  }

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      void (async () => {
        await flushSave()
        onOpenChange(false)
      })()
      return
    }
    onOpenChange(true)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className={cn(
          "pm-dialog pm-dialog--silk pm-settings-hw-dialog",
          "sm:max-w-6xl h-[80vh]",
          "!animate-none data-open:!animate-none data-closed:!animate-none",
          nested && "pm-dialog-layer-nested",
        )}
        overlayClassName={cn(
          "pm-dialog-overlay--silk",
          nested && "pm-dialog-layer-nested-overlay",
        )}
      >
        <DialogHeader className="shrink-0">
          <DialogKicker>{t("nav.settings")}</DialogKicker>
          <DialogTitle>{t("settings.hotWords")}</DialogTitle>
        </DialogHeader>

        <div className="pm-settings-hw">
          {/* Left rail */}
          <div className="pm-settings-hw-rail">
            <div className="pm-settings-hw-rail-head">
              <span className="pm-label text-[var(--pm-ink)]">{t("common.libraries")}</span>
              <div
                ref={importAnchorRef}
                className="pm-settings-hw-rail-actions pm-settings-hw-import-anchor"
              >
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  title={t("settings.importLibrary")}
                  aria-label={t("settings.importLibrary")}
                  aria-expanded={importMenuOpen}
                  aria-haspopup="menu"
                  disabled={importing}
                  onClick={() => setImportMenuOpen((v) => !v)}
                >
                  <FileDown className="h-4 w-4" strokeWidth={2} />
                </Button>
                <input
                  ref={importFileRef}
                  type="file"
                  accept=".csv,.xlsx,.xlsm,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                  className="sr-only"
                  tabIndex={-1}
                  onChange={(e) => {
                    void handleImportFile(e.target.files?.[0])
                  }}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  onClick={handleNew}
                  title={t("settings.newLibrary")}
                  aria-label={t("settings.newLibrary")}
                >
                  <Plus className="h-4 w-4" />
                </Button>
                {/* Right edge flush with rail actions / sidebar padding */}
                <SoftMenu
                  open={importMenuOpen}
                  exitMs={MENU_SILK_MS}
                  className="pm-settings-hw-import-menu"
                >
                  <div className="pm-settings-hw-import-menu-label" aria-hidden>
                    {t("common.import")}
                  </div>
                  <MenuItem
                    type="button"
                    className="pm-settings-hw-import-item"
                    onClick={() => handleDownloadTemplate("csv")}
                  >
                    <span className="pm-settings-hw-import-icon" aria-hidden>
                      <FileText className="size-3.5" strokeWidth={2} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <MenuItemTitle>{t("settings.csvTemplate")}</MenuItemTitle>
                      <MenuItemDescription>.csv</MenuItemDescription>
                    </span>
                  </MenuItem>
                  <MenuItem
                    type="button"
                    className="pm-settings-hw-import-item"
                    onClick={() => handleDownloadTemplate("xlsx")}
                  >
                    <span className="pm-settings-hw-import-icon" aria-hidden>
                      <FileSpreadsheet className="size-3.5" strokeWidth={2} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <MenuItemTitle>{t("settings.excelTemplate")}</MenuItemTitle>
                      <MenuItemDescription>.xlsx</MenuItemDescription>
                    </span>
                  </MenuItem>
                  <div className="pm-settings-hw-import-divider" role="separator" />
                  <MenuItem
                    type="button"
                    className="pm-settings-hw-import-item"
                    disabled={importing}
                    onClick={() => importFileRef.current?.click()}
                  >
                    <span className="pm-settings-hw-import-icon is-accent" aria-hidden>
                      <FileDown className="size-3.5" strokeWidth={2} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <MenuItemTitle>
                        {importing ? t("settings.importing") : t("settings.importFile")}
                      </MenuItemTitle>
                      <MenuItemDescription>{t("settings.csvExcel")}</MenuItemDescription>
                    </span>
                  </MenuItem>
                </SoftMenu>
              </div>
            </div>
            <ScrollArea className="flex-1 min-h-0">
              <div ref={listRef} className="pm-settings-hw-lib-list">
                {selectedId ? (
                  <div
                    className={cn(
                      "pm-settings-hw-indicator",
                      indicatorReady && "is-ready",
                    )}
                    style={{
                      transform: `translateY(${indicator.top}px)`,
                      height: indicator.height,
                    }}
                    aria-hidden
                  />
                ) : null}
                {libraries.map((lib) => (
                  <div
                    key={lib.id}
                    ref={(el) => {
                      if (el) itemRefs.current.set(lib.id, el)
                      else itemRefs.current.delete(lib.id)
                    }}
                    role="button"
                    tabIndex={0}
                    className={cn(
                      "group pm-settings-hw-lib",
                      selectedId === lib.id && "is-active",
                    )}
                    onClick={() => handleSwitchLibrary(lib.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault()
                        handleSwitchLibrary(lib.id)
                      }
                    }}
                  >
                    <div className="truncate flex-1 min-w-0">
                      <div className="pm-rail-name truncate flex items-center gap-1.5">
                        <span className="truncate">{lib.name}</span>
                        {lib.is_system && (
                          <span className="pm-settings-hw-default-pill" title={t("settings.systemLibrary")}>
                            {t("common.system")}
                          </span>
                        )}
                        {(lib.is_pinned || pinnedIds.includes(lib.id)) && (
                          <span className="pm-settings-hw-default-pill" title={t("settings.pinnedForMeetings")}>
                            {t("common.pin")}
                          </span>
                        )}
                      </div>
                      <div className="pm-meta">{t("settings.nWords", { n: lib.word_count })}</div>
                    </div>
                    {!lib.is_system && (
                      <SlideConfirmDeleteButton
                        className="shrink-0 ml-1"
                        title={t("settings.deleteLibrary")}
                        onConfirm={() => { void handleDelete(lib.id) }}
                      />
                    )}
                  </div>
                ))}
                {libraries.length === 0 && (
                  <p className="pm-meta p-2 text-center">
                    {t("settings.noLibrariesYet")}
                  </p>
                )}
              </div>
            </ScrollArea>
          </div>

          {/* Main pane — soft fade on library switch */}
          <div className="pm-settings-hw-main">
            <div
              className={cn(
                "pm-settings-hw-main-body",
                mainIn && "is-in",
              )}
            >
              {selectedLib ? (
                <>
                  {/* Library meta — soft inset card */}
                  <section className="pm-settings-hw-meta" aria-label={t("settings.libraryDetails")}>
                    <div className="pm-settings-hw-meta-top">
                      <div className="pm-settings-hw-field pm-settings-hw-field--name">
                        <FieldLabel className="pm-settings-hw-field-label">{t("common.name")}</FieldLabel>
                        <Input
                          value={selectedLib.name}
                          onChange={(e) => updateField("name", e.target.value)}
                          className="pm-settings-hw-input"
                          placeholder={t("settings.libraryName")}
                          readOnly={!!selectedLib.is_system}
                          disabled={!!selectedLib.is_system}
                        />
                      </div>
                      <div className="pm-settings-hw-meta-actions">
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className={cn(
                            "pm-settings-hw-action-btn",
                            pinnedIds.includes(selectedId ?? "") && "is-default",
                          )}
                          disabled={settingPin || !selectedId}
                          title={
                            pinnedIds.includes(selectedId ?? "")
                              ? t("settings.unpinNewMeetings")
                              : t("settings.pinNewMeetings")
                          }
                          onClick={() => { void handleTogglePin() }}
                        >
                          <Pin
                            className="h-3.5 w-3.5"
                            strokeWidth={1.75}
                            fill={pinnedIds.includes(selectedId ?? "") ? "currentColor" : "none"}
                          />
                          {pinnedIds.includes(selectedId ?? "") ? t("common.pinned") : t("common.pin")}
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="pm-settings-hw-action-btn"
                          disabled={!selectedId}
                          title={t("settings.exportExcel")}
                          onClick={handleExport}
                        >
                          <FileUp className="h-3.5 w-3.5" strokeWidth={2} />
                          {t("common.export")}
                        </Button>
                      </div>
                    </div>
                    <div className="pm-settings-hw-field">
                      <FieldLabel className="pm-settings-hw-field-label">{t("common.description")}</FieldLabel>
                      <Textarea
                        value={selectedLib.description}
                        onChange={(e) => updateField("description", e.target.value)}
                        className="pm-settings-hw-textarea"
                        placeholder={t("settings.optionalLibraryNote")}
                        readOnly={!!selectedLib.is_system}
                        disabled={!!selectedLib.is_system}
                      />
                    </div>
                  </section>

                  {/* Word list — dense soft tray */}
                  <section className="pm-settings-hw-words" aria-label={t("settings.hotWords")}>
                    <div className="pm-settings-hw-words-head">
                      <div className="pm-settings-hw-words-title">
                        <span className="pm-settings-hw-words-label">{t("common.words")}</span>
                        <span className="pm-settings-hw-count">
                          {wordRows.length}
                        </span>
                        <span
                          className={cn(
                            "pm-settings-hw-autosave",
                            isSaving && "is-saving",
                            isDirty && !isSaving && "is-pending",
                          )}
                          aria-live="polite"
                        >
                          {isSaving
                            ? t("common.saving")
                            : isDirty
                              ? t("common.editing")
                              : t("common.saved")}
                        </span>
                      </div>
                      {!selectedLib.is_system && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="xs"
                          className="pm-settings-hw-add"
                          onClick={addWord}
                        >
                          <Plus className="h-3 w-3" strokeWidth={1.75} />
                          {t("common.add")}
                        </Button>
                      )}
                    </div>
                    <ScrollArea className="pm-settings-hw-words-scroll">
                      <div ref={wordListRef} className="pm-settings-hw-words-list">
                        {wordRows.map((word) => (
                          <div
                            key={word.uid}
                            data-word-uid={word.uid}
                            className={cn(
                              "pm-settings-hw-word-slot",
                              leavingUid === word.uid && "is-leaving",
                            )}
                          >
                            <div className="pm-settings-hw-word-row">
                              <Input
                                value={word.text}
                                onChange={(e) => updateWord(word.uid, "text", e.target.value)}
                                placeholder={t("settings.hotWord")}
                                className="pm-settings-hw-word-text"
                                readOnly={!!selectedLib.is_system}
                                disabled={!!selectedLib.is_system}
                              />
                              <div className="pm-settings-hw-weight" title={t("settings.weightRange")}>
                                <span className="pm-settings-hw-weight-label">W</span>
                                <Input
                                  type="number"
                                  min={1}
                                  max={10}
                                  value={isNaN(word.weight) ? "" : word.weight}
                                  onChange={(e) => {
                                    const v = e.target.value
                                    if (v === "") {
                                      updateWord(word.uid, "weight", NaN)
                                      return
                                    }
                                    const n = parseInt(v)
                                    if (!isNaN(n)) {
                                      updateWord(word.uid, "weight", Math.max(1, Math.min(10, n)))
                                    }
                                  }}
                                  onBlur={() => {
                                    if (isNaN(word.weight)) updateWord(word.uid, "weight", 4)
                                  }}
                                  className="pm-settings-hw-weight-input"
                                />
                              </div>
                              <DropdownSelect
                                size="sm"
                                value={word.lang || ""}
                                onChange={(v) => updateWord(word.uid, "lang", v)}
                                options={
                                  langOptions.some((o) => o.value === (word.lang || ""))
                                    ? langOptions
                                    : [
                                        {
                                          value: word.lang || "",
                                          label: word.lang || t("common.any"),
                                        },
                                        ...langOptions,
                                      ]
                                }
                                placeholder={t("common.lang")}
                                className="pm-settings-hw-lang"
                              />
                              {!selectedLib.is_system && (
                              <button
                                type="button"
                                className="pm-settings-hw-word-del"
                                title={t("settings.removeWord")}
                                aria-label={t("settings.removeWord")}
                                disabled={leavingUid === word.uid}
                                onClick={() => { void removeWord(word.uid) }}
                              >
                                <Trash2 className="h-3 w-3" strokeWidth={1.75} />
                              </button>
                              )}
                            </div>
                          </div>
                        ))}
                        {wordRows.length === 0 && (
                          <div className="pm-settings-hw-words-empty">
                            <p className="pm-settings-hw-words-empty-title">{t("settings.noWordsYet")}</p>
                            <p className="pm-settings-hw-words-empty-hint">
                              {t("settings.addTermsBoost")}
                            </p>
                          </div>
                        )}
                      </div>
                    </ScrollArea>
                  </section>
                </>
              ) : (
                <div className="pm-settings-hw-empty">
                  <div className="pm-settings-hw-empty-icon" aria-hidden>
                    <BookOpen className="h-6 w-6" strokeWidth={1.5} />
                  </div>
                  <p className="pm-settings-hw-empty-title">{t("settings.pickLibrary")}</p>
                  <p className="pm-settings-hw-empty-hint">
                    {t("settings.pickLibraryHint")}
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
