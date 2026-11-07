import { useEffect, useId, useRef, useState } from "react"
import { ArrowDown, ArrowRight, ArrowUp, BookOpen, ChevronDown, Command, CornerDownLeft, Eraser, FileText, Folder, LoaderCircle, Mic, RotateCcw, Search, StickyNote, X } from "lucide-react"
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { useAppStore } from "@/stores/app-store"
import { sourceScopedKey } from "@/api/source-scope"
import { SEARCH_CATEGORIES, searchWorkspace, type SearchCategory, type WorkspaceSearchHit, type WorkspaceSearchResponse } from "@/api/workspace-search"
import { appendSearchPage, highlightSearchText, searchHitKey } from "@/lib/workspace-search"
import { useT } from "@/i18n/use-t"

const icons = { projects: Folder, files: FileText, meetings: Mic, minutes: BookOpen, notes: StickyNote }
function Highlight({ text, query }: { text: string; query: string }) {
  return <>{highlightSearchText(text, query).map((part, i) => part.match ? <mark key={i}>{part.text}</mark> : part.text)}</>
}

export function GlobalSearch() {
  const identity = useAppStore(s => s.identity)
  const scope = JSON.stringify([identity?.actor_id, identity?.role_id, identity?.modules, identity?.domains])
  return <GlobalSearchContent key={sourceScopedKey(`workspace-search/${scope}`)} />
}

function GlobalSearchContent() {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [category, setCategory] = useState<SearchCategory | "all">("all")
  const [active, setActive] = useState(0)
  const [retry, setRetry] = useState(0)
  const [result, setResult] = useState<{ key: string; data?: WorkspaceSearchResponse; error?: boolean } | null>(null)
  const [paging, setPaging] = useState<SearchCategory | null>(null)
  const [pageError, setPageError] = useState<SearchCategory | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const controller = useRef<AbortController | null>(null)
  const id = useId()
  const trimmed = query.trim()
  const key = `${trimmed}\n${retry}`
  const current = result?.key === key ? result : null
  const loading = !!trimmed && !current
  const groups = current?.data?.groups.filter(group => category === "all" || group.category === category) || []
  const hits = groups.flatMap(group => group.items)
  const activeHit = hits[Math.min(active, Math.max(0, hits.length - 1))]
  const total = groups.reduce((n, group) => n + group.total, 0)
  const identity = useAppStore(s => s.identity)
  const libraryVisible = identity?.modules?.database !== false
  const meetingVisible = identity?.domains?.meeting !== false
  const categories = SEARCH_CATEGORIES.filter(cat => ["meetings", "minutes"].includes(cat) ? meetingVisible : libraryVisible)

  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if (event.isComposing || event.repeat || event.altKey || event.shiftKey || !(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "k") return
      if (!open && document.querySelector('[role="dialog"][data-open]')) return
      event.preventDefault()
      setOpen(value => !value)
      if (!open) { setRetry(n => n + 1); setActive(0); setPaging(null); setPageError(null) }
    }
    window.addEventListener("keydown", listener)
    return () => window.removeEventListener("keydown", listener)
  }, [open])

  useEffect(() => {
    if (!open || !trimmed) return
    const abort = new AbortController()
    controller.current = abort
    const timer = window.setTimeout(() => {
      searchWorkspace(trimmed, abort.signal).then(data => {
        if (!abort.signal.aborted) setResult({ key, data })
      }).catch(() => {
        if (!abort.signal.aborted) setResult({ key, error: true })
      })
    }, 180)
    return () => { window.clearTimeout(timer); abort.abort() }
  }, [open, trimmed, key])

  useEffect(() => {
    if (open && activeHit) document.getElementById(`${id}-${searchHitKey(activeHit)}`)?.scrollIntoView({ block: "nearest" })
  }, [open, activeHit, id])

  function changeQuery(value: string) {
    setQuery(value); setActive(0); setPaging(null); setPageError(null)
  }
  function changeOpen(value: boolean) {
    setOpen(value)
    if (value) { setRetry(n => n + 1); setActive(0); setPaging(null); setPageError(null) }
  }
  function navigate(hit: WorkspaceSearchHit) {
    const store = useAppStore.getState()
    const view = hit.meeting_id ? "meeting" : "database"
    store.setSidebarView(view)
    // Honor the app's existing recording / unsaved-work navigation guard.
    if (useAppStore.getState().sidebarView !== view) return
    if (hit.meeting_id) store.setActiveMeeting(hit.meeting_id)
    else if (hit.collection_id) store.setActiveCollection(hit.collection_id)
    useAppStore.setState({ pendingWorkspaceSearch: hit })
    setOpen(false)
  }
  async function more(cat: SearchCategory) {
    const group = current?.data?.groups.find(item => item.category === cat)
    const abort = controller.current
    if (!group || !abort || abort.signal.aborted || paging) return
    setPaging(cat); setPageError(null)
    try {
      const page = await searchWorkspace(trimmed, abort.signal, cat, group.next_offset)
      if (abort.signal.aborted) return
      setResult(previous => previous?.key === key && previous.data ? { ...previous, data: { ...previous.data,
        groups: previous.data.groups.map(item => item.category === cat && page.groups[0] ? appendSearchPage(item, page.groups[0]) : item),
        unavailable_categories: [...new Set([...previous.data.unavailable_categories, ...page.unavailable_categories])],
      } } : previous)
    } catch { if (!abort.signal.aborted) setPageError(cat) }
    finally { if (!abort.signal.aborted) setPaging(null) }
  }

  return <>
    <button type="button" className="sd-nav-item sd-nav-search" aria-label={t("search.title")} title={`${t("search.title")} (⌘ / Ctrl K)`} onClick={() => changeOpen(true)}>
      <Search size={18} strokeWidth={1.65} aria-hidden /><span className="sd-nav-label">{t("search.short")}</span><kbd className="sd-nav-label">⌘ K</kbd>
    </button>
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogContent className="sd-search-dialog" showCloseButton={false} initialFocus={input}>
        <DialogTitle className="sr-only">{t("search.title")}</DialogTitle>
        <div className="sd-search-input-row">
          <Search size={21} strokeWidth={1.7} aria-hidden />
          <input ref={input} value={query} maxLength={200} onChange={event => changeQuery(event.target.value)} aria-label={t("search.title")} placeholder={t("search.placeholder")}
            role="combobox" aria-autocomplete="list" aria-expanded={true} aria-controls={`${id}-results`} aria-activedescendant={activeHit ? `${id}-${searchHitKey(activeHit)}` : undefined}
            onKeyDown={event => {
              if (event.nativeEvent.isComposing) return
              if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault()
                setActive(value => Math.max(0, Math.min(hits.length - 1, value + (event.key === "ArrowDown" ? 1 : -1))))
              }
              if (event.key === "Enter" && activeHit) { event.preventDefault(); navigate(activeHit) }
            }} />
          {query && <Button variant="ghost" size="icon" aria-label={t("search.clear")} title={t("search.clear")} onClick={() => { changeQuery(""); input.current?.focus() }}><Eraser /></Button>}
          <Button variant="secondary" size="icon" aria-label={t("common.close")} title={t("common.close")} onClick={() => setOpen(false)}><X /></Button>
        </div>
        <div className="sd-search-categories" role="group" aria-label={t("search.categories")}>
          <Button variant="ghost" size="sm" aria-pressed={category === "all"} onClick={() => { setCategory("all"); setActive(0) }}>{t("search.all")}</Button>
          {categories.map(cat => { const Icon = icons[cat]; const count = current?.data?.groups.find(group => group.category === cat)?.total; return <Button key={cat} variant="ghost" size="sm" aria-pressed={category === cat} onClick={() => { setCategory(cat); setActive(0) }}><Icon />{t(`search.${cat}`)}{!!count && <span className="sd-search-category-count">{count}</span>}</Button> })}
        </div>
        <div className="sd-search-status" role="status" aria-live="polite">
          {loading ? <><LoaderCircle size={14} className="animate-spin" />{t("search.loading")}</> : current?.error ? t("search.failed") : trimmed ? t("search.count", { count: total }) : t("search.scope")}
          {(current?.error || !!current?.data?.unavailable_categories.length) && <Button size="icon-sm" variant="ghost" title={t("search.retry")} aria-label={t("search.retry")} onClick={() => { setRetry(n => n + 1); input.current?.focus() }}><RotateCcw /></Button>}
        </div>
        {!!current?.data?.unavailable_categories.length && <p className="sd-search-warning">{t("search.partial", { categories: current.data.unavailable_categories.map(cat => t(`search.${cat}`)).join("、") })}</p>}
        <div className="sd-search-results" id={`${id}-results`} role="listbox" aria-label={t("search.results")} aria-busy={loading}>
          {!trimmed ? <div className="sd-search-empty" role="presentation"><Search size={28} strokeWidth={1.4} /><p>{t("search.hint")}</p><span>{t("search.example")}</span></div>
            : !loading && !current?.error && !current?.data?.unavailable_categories.length && total === 0 ? <div className="sd-search-empty" role="presentation"><Search size={28} strokeWidth={1.4} /><p>{t("search.empty")}</p><span>{t("search.emptyHint")}</span></div>
            : groups.filter(group => group.total).map(group => <div className="sd-search-group" key={group.category} role="group" aria-labelledby={`${id}-group-${group.category}`}>
              <div className="sd-search-group-heading" id={`${id}-group-${group.category}`}><span>{t(`search.${group.category}`)}</span><span>{group.total}</span></div>
              {group.items.map(hit => { const Icon = icons[hit.category]; const selected = activeHit === hit; return <button key={searchHitKey(hit)} type="button" role="option" aria-selected={selected} tabIndex={-1} id={`${id}-${searchHitKey(hit)}`} className="sd-search-result" onMouseEnter={() => setActive(hits.indexOf(hit))} onClick={() => navigate(hit)}>
                <span className="sd-search-result-icon"><Icon size={18} strokeWidth={1.65} /></span><span className="sd-search-result-copy"><strong><Highlight text={hit.title} query={trimmed} /></strong>{hit.context && <small title={hit.context}>{hit.context}</small>}{hit.snippet && <span className="sd-search-snippet"><Highlight text={hit.snippet} query={trimmed} /></span>}</span><ArrowRight size={15} className="sd-search-result-arrow" />
              </button> })}
              {group.has_more && <div className="sd-search-more" role="presentation"><Button variant="ghost" size="icon-sm" aria-disabled={!!paging} onClick={() => void more(group.category)} aria-label={t("search.more", { category: t(`search.${group.category}`) })} title={t("search.more", { category: t(`search.${group.category}`) })}>{paging === group.category ? <LoaderCircle className="animate-spin" /> : pageError === group.category ? <RotateCcw /> : <ChevronDown />}</Button><span>{pageError === group.category ? t("search.failed") : `${group.items.length} / ${group.total}`}</span></div>}
            </div>)}
        </div>
        <footer className="sd-search-footer"><span><ArrowUp size={12} /><ArrowDown size={12} />{t("search.move")}</span><span><CornerDownLeft size={13} />{t("search.open")}</span><span><Command size={12} /> K <span>/ Ctrl K</span></span></footer>
      </DialogContent>
    </Dialog>
  </>
}
