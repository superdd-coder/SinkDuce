import {
  useState,
  useEffect,
  useRef,
  useCallback,
  type ReactNode,
} from "react"
import { createPortal } from "react-dom"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkBreaks from "remark-breaks"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsIndicator,
  TabsContent,
} from "@/components/ui/tabs"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { FieldLabel } from "@/components/ui/field-label"
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Loader2,
  FlaskConical,
  Trash2,
  Wand2,
  Play,
  RotateCw,
  CheckCircle,
  XCircle,
  Clock,
  ChevronDown,
  ChevronRight,
  Bot,
  Sparkles,
  Search,
  Info,
} from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import {
  recallSearch,
  type RecallResult,
  getEvalCases,
  deleteEvalCase,
  generateEvalCases,
  runEval,
  getEvalHistory,
  getChunkContent,
  type EvalTestCase,
  type EvalReport,
  type ChunkContent,
  getFiles,
  type FileListItem,
} from "@/api/client"
import { toast } from "sonner"
import { ResultList } from "./result-list"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"

// ── Helpers ────────────────────────────────────────────────

function formatSigned(n: number, digits = 2): string {
  if (n > 0) return `+${n.toFixed(digits)}`
  if (n < 0) return n.toFixed(digits)
  return "0.00"
}

/** Quality tint — only --pm-* greens / muted / danger */
function qualityClass(n: number): string {
  if (n >= 0.7) return "text-[var(--pm-green)]"
  if (n > 0.2) return "text-[var(--pm-green)]/80"
  if (n > -0.2) return "text-[var(--pm-muted)]"
  if (n > -0.6) return "text-[var(--pm-danger)]/80"
  return "text-[var(--pm-danger)]"
}

function FieldTip({ label, tooltip }: { label: string; tooltip: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <FieldLabel className="!mb-0 cursor-default">{label}</FieldLabel>
      <Tooltip>
        <TooltipTrigger className="cursor-help inline-flex text-[var(--pm-faint)] hover:text-[var(--pm-muted)]">
          <Info className="h-3 w-3" />
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs">
          {tooltip}
        </TooltipContent>
      </Tooltip>
    </span>
  )
}

/** Dual-label chip text — both words mounted; crossfade via .is-active (no hard cut). */
function ChipSwap({
  a,
  b,
  showB,
  mode,
}: {
  a: string
  b: string
  showB: boolean
  /** Wider min for Dense/Hybrid */
  mode?: boolean
}) {
  return (
    <span className={cn("pm-recall-chip-swap", mode && "pm-recall-chip-swap--mode")}>
      <span className={cn(!showB && "is-active")}>{a}</span>
      <span className={cn(showB && "is-active")}>{b}</span>
    </span>
  )
}

/** Horizontal soft-open slot for toolbar params (Rerank K / Min). */
function ParamSlot({
  open,
  children,
}: {
  open: boolean
  children: ReactNode
}) {
  return (
    <div className={cn("pm-recall-param-slot", open && "is-open")}>
      <div className="pm-recall-param-slot-clip">
        <div className="pm-recall-param-slot-inner">{children}</div>
      </div>
    </div>
  )
}

// ── Search Tab ─────────────────────────────────────────────

function SearchTab() {
  const t = useT()
  const {
    recallCollections,
    toggleRecallCollection,
    collections,
    fetchCollections,
  } = useAppStore()
  const [query, setQuery] = useState("")
  const [topK, setTopK] = useState("10")
  const [rerankTopK, setRerankTopK] = useState("5")
  const [searchMode, setSearchMode] = useState("dense")
  const [sparseLlmTokenize, setSparseLlmTokenize] = useState(true)
  const [useReranker, setUseReranker] = useState(false)
  const [useAgent, setUseAgent] = useState(false)
  const [minScore, setMinScore] = useState(0)
  const [results, setResults] = useState<RecallResult[]>([])
  const [timeMs, setTimeMs] = useState(0)
  const [searchParams, setSearchParams] = useState<Record<string, unknown>>({})
  const [searchContext, setSearchContext] = useState("")
  const [showContext, setShowContext] = useState(false)
  const [searching, setSearching] = useState(false)
  /** True after a completed search — distinguishes idle from “0 results”. */
  const [hasSearched, setHasSearched] = useState(false)
  const [showCollections, setShowCollections] = useState(false)
  /** Tick forces re-read of button rect when opening the chat-style pop */
  const [colMenuTick, setColMenuTick] = useState(0)
  const [filesMap, setFilesMap] = useState<Record<string, string>>({})
  const collectionAnchorRef = useRef<HTMLDivElement>(null)
  const collectionBtnRef = useRef<HTMLButtonElement>(null)
  const collectionPopRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetchCollections()
  }, [fetchCollections])

  // Build source→display map for every collection that can appear in search
  // (selected set, or all collections when none selected).
  useEffect(() => {
    const cols =
      recallCollections.length > 0
        ? recallCollections
        : collections.map((c) => c.id)
    if (cols.length === 0) {
      setFilesMap({})
      return
    }
    let cancelled = false
    Promise.all(
      cols.map((c) =>
        getFiles(c).catch(() => ({ files: [] as FileListItem[] })),
      ),
    )
      .then((resList) => {
        if (cancelled) return
        const map: Record<string, string> = {}
        for (const r of resList) {
          for (const f of r.files) {
            const name = (f.display_name || "").trim()
            if (!name) continue
            // Skip useless placeholders from a bad list_files path
            const low = name.toLowerCase()
            if (
              low === "document" ||
              low === "meeting" ||
              low === "note" ||
              low.startsWith("document (")
            ) {
              continue
            }
            if (f.source) map[f.source] = name
            if (f.file_id) {
              map[f.file_id] = name
              map[`__file__:${f.file_id}`] = name
              map[`file:${f.file_id}`] = name
            }
          }
        }
        setFilesMap(map)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [recallCollections.join(","), collections.map((c) => c.id).join(",")])

  useEffect(() => {
    if (!showCollections) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (collectionAnchorRef.current?.contains(t)) return
      if (collectionPopRef.current?.contains(t)) return
      setShowCollections(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setShowCollections(false)
    }
    document.addEventListener("mousedown", onDown)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onDown)
      document.removeEventListener("keydown", onKey)
    }
  }, [showCollections])

  const collectionLabel =
    recallCollections.length === 0
      ? t("chat.allCollections")
      : recallCollections.length === 1
        ? t("chat.nCollection", { n: 1 })
        : t("chat.nCollections", { n: recallCollections.length })

  // Chat-style pop: open below the chip (Search toolbar is near top, not bottom)
  const colBtnRect = collectionBtnRef.current?.getBoundingClientRect()
  const colHostRect = collectionAnchorRef.current?.getBoundingClientRect()
  void colMenuTick

  const handleSearch = async () => {
    if (!query.trim()) return
    const cols =
      recallCollections.length > 0
        ? recallCollections
        : collections.map((c) => c.id)
    setResults([])
    setSearchContext("")
    setShowContext(false)
    setSearching(true)
    try {
      const res = await recallSearch({
        query: query.trim(),
        collections: cols,
        search_mode: searchMode,
        top_k: parseInt(topK) || 10,
        rerank_top_k: parseInt(rerankTopK) || 5,
        use_reranker: useReranker,
        use_agent: useAgent,
        min_score: minScore,
        sparse_llm_tokenize:
          searchMode === "hybrid" ? sparseLlmTokenize : undefined,
      })
      setResults(res.results || [])
      setTimeMs(res.time_ms)
      setSearchParams(res.search_params || {})
      setSearchContext(res.context || "")
      setHasSearched(true)
    } catch (err) {
      toast.error(
        t("recall.failed", {
          error: err instanceof Error ? err.message : String(err),
        }),
      )
      setHasSearched(true)
    } finally {
      setSearching(false)
    }
  }

  const showIdleEmpty = !hasSearched && !searching && results.length === 0
  // Always open results panel after a completed search (incl. 0 hits)
  const showResultsPanel = hasSearched && !searching

  return (
    <div className="pm-recall-stack">
      <div className="pm-recall-card pm-recall-card--composer">
        <div className="pm-recall-query-row">
          <Textarea
            className="pm-recall-query min-h-0 py-0"
            placeholder={t("recall.enterQuery")}
            rows={1}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault()
                handleSearch()
              }
            }}
            disabled={searching}
          />
          <Button
            variant="default"
            size="sm"
            onClick={handleSearch}
            disabled={!query.trim() || searching}
          >
            {searching ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Search className="h-3 w-3" />
            )}
            {searching ? t("recall.searching") : t("recall.search")}
          </Button>
        </div>

        <div className="pm-recall-toolbar">
          <div className="relative shrink-0" ref={collectionAnchorRef}>
            <button
              type="button"
              ref={collectionBtnRef}
              className={cn(
                "pm-chat-tool-chip pm-chat-tool-chip--wipe",
                recallCollections.length > 0 && "is-on",
                showCollections && "is-menu-open",
              )}
              onClick={() => {
                setShowCollections((v) => !v)
                setColMenuTick((t) => t + 1)
              }}
              aria-expanded={showCollections}
              aria-haspopup="listbox"
            >
              <span className="pm-chat-tool-chip-label whitespace-nowrap text-center">
                {collectionLabel}
              </span>
              <span className="pm-chat-tool-chip-wipe" aria-hidden />
            </button>
            {createPortal(
              <div
                ref={collectionPopRef}
                className={cn(
                  "pm-chat-pop pm-chat-pop--collections pm-recall-col-pop",
                  showCollections && "is-open",
                )}
                style={{
                  width: colBtnRect ? Math.max(colBtnRect.width, 180) : 180,
                  minWidth: 155,
                  top: colHostRect
                    ? colHostRect.bottom + 6
                    : 0,
                  left: colHostRect ? colHostRect.left : 0,
                }}
                role="listbox"
                aria-label={t("common.collections")}
              >
                {collections.length === 0 ? (
                  <div className="pm-chat-pop-empty">{t("chat.noCollections")}</div>
                ) : (
                  <div className="pm-chat-pop-scroll">
                    {collections.map((col) => (
                      <label
                        key={col.id}
                        onClick={() => toggleRecallCollection(col.id)}
                        className="pm-chat-pop-item group"
                      >
                        <span className="pm-chat-pop-item-label is-wrap">
                          <span
                            className={cn(
                              "sk-diamond",
                              recallCollections.includes(col.id) && "on",
                            )}
                            aria-hidden
                          />
                          <span className="pm-chat-pop-item-name">
                            {col.name}
                          </span>
                        </span>
                        <span className="pm-chat-pop-item-wipe" aria-hidden />
                      </label>
                    ))}
                  </div>
                )}
              </div>,
              document.body,
            )}
          </div>

          <span className="pm-recall-toolbar-sep" aria-hidden />

          <button
            type="button"
            className={cn(
              "pm-chat-tool-chip pm-chat-tool-chip--flow",
              useAgent && "is-on",
            )}
            onClick={() => {
              const next = !useAgent
              setUseAgent(next)
              if (next) setUseReranker(true)
            }}
            title={
              useAgent
                ? t("recall.agenticOn")
                : t("recall.agenticOff")
            }
          >
            <Bot className="size-3" />
            <ChipSwap a={t("recall.direct")} b={t("recall.agent")} showB={useAgent} />
          </button>

          <button
            type="button"
            className={cn(
              "pm-chat-tool-chip pm-chat-tool-chip--flow",
              useReranker && "is-on",
            )}
            disabled={useAgent}
            onClick={() => {
              if (!useAgent) setUseReranker(!useReranker)
            }}
            title={
              useAgent
                ? t("recall.rerankerRequired")
                : t("recall.toggleReranker")
            }
          >
            {t("recall.rerank")}
          </button>

          <div
            className={cn(
              "pm-recall-hybrid",
              searchMode === "hybrid" && "is-open",
            )}
          >
            <button
              type="button"
              className={cn(
                "pm-chat-tool-chip pm-chat-tool-chip--flow",
                searchMode === "hybrid" && "is-on",
              )}
              onClick={() =>
                setSearchMode(searchMode === "hybrid" ? "dense" : "hybrid")
              }
              title={
                searchMode === "hybrid"
                  ? t("recall.hybridTitle")
                  : t("recall.denseHybrid")
              }
            >
              <ChipSwap
                a={t("recall.dense")}
                b={t("recall.hybrid")}
                showB={searchMode === "hybrid"}
                mode
              />
            </button>
            <div className="pm-recall-hybrid-extra">
              <button
                type="button"
                className={cn(
                  "pm-chat-tool-chip pm-chat-tool-chip--flow",
                  sparseLlmTokenize && "is-on",
                )}
                disabled={useAgent}
                onClick={() => {
                  if (!useAgent) setSparseLlmTokenize(!sparseLlmTokenize)
                }}
                title={
                  useAgent
                    ? t("recall.alwaysOnAgentic")
                    : sparseLlmTokenize
                      ? t("recall.llmKeywordOn")
                      : t("recall.llmKeywordOff")
                }
              >
                <Sparkles className="size-3" />
              </button>
            </div>
          </div>

          <span className="pm-recall-toolbar-sep" aria-hidden />

          <div className="pm-recall-field">
            <FieldTip
              label={t("recall.topK")}
              tooltip={t("recall.topKTip")}
            />
            <Input
              value={topK}
              onChange={(e) => setTopK(e.target.value)}
              inputMode="numeric"
            />
          </div>

          <ParamSlot open={useReranker}>
            <div className="pm-recall-field">
              <FieldTip
                label={t("recall.rerankK")}
                tooltip={t("recall.rerankKTip")}
              />
              <Input
                value={rerankTopK}
                onChange={(e) => setRerankTopK(e.target.value)}
                inputMode="numeric"
              />
            </div>
          </ParamSlot>

          <ParamSlot open={searchMode !== "hybrid"}>
            <div className="pm-recall-field">
              <FieldTip
                label={t("recall.min")}
                tooltip={t("recall.minTip")}
              />
              <Input
                inputMode="numeric"
                value={Math.round(minScore * 100)}
                onChange={(e) => {
                  const raw = e.target.value
                  if (raw === "") {
                    setMinScore(0)
                    return
                  }
                  const v = parseInt(raw)
                  if (!isNaN(v))
                    setMinScore(Math.max(0, Math.min(99, v)) / 100)
                }}
              />
              <span className="pm-meta">%</span>
            </div>
          </ParamSlot>
        </div>
      </div>

      {/* Idle empty — only before the first completed search */}
      <div className={cn("pm-recall-fold", showIdleEmpty && "is-open")}>
        <div className="pm-recall-fold-clip">
          <div className="pm-recall-fold-body">
            <div className="pm-recall-card">
              <div className="pm-recall-empty">
                <Search
                  className="h-8 w-8 pm-recall-empty-icon"
                  strokeWidth={1.25}
                />
                <p className="pm-recall-empty-title">{t("recall.readyToSearch")}</p>
                <p className="pm-recall-empty-sub">
                  {t("recall.runQueryHint")}
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Results panel — open after any completed search (incl. 0 hits) */}
      <div className={cn("pm-recall-fold", showResultsPanel && "is-open")}>
        <div className="pm-recall-fold-clip">
          <div className="pm-recall-fold-body">
            <div className="pm-recall-card">
              <div className="pm-recall-card-head">
                <div className="pm-recall-card-head-text">
                  <h3 className="pm-recall-section-title">
                    {t("recall.nResults", { n: results.length })}
                  </h3>
                  <p className="pm-recall-section-desc">
                    {t("recall.retrievedIn")} {timeMs}ms
                  </p>
                </div>
                <div className="pm-recall-results-meta">
                  <Badge variant="outline">
                    {String(searchParams.search_mode || searchMode)}
                  </Badge>
                  {!!searchParams.use_reranker && (
                    <Badge variant="default">{t("recall.reranked")}</Badge>
                  )}
                  {!!searchParams.use_agent && (
                    <Badge variant="default">{t("recall.agentic")}</Badge>
                  )}
                  {searchContext && (
                    <Button
                      type="button"
                      variant={showContext ? "secondary" : "ghost"}
                      size="sm"
                      onClick={() => setShowContext(!showContext)}
                    >
                      {showContext ? t("recall.hideContext") : t("recall.viewContext")}
                    </Button>
                  )}
                </div>
              </div>
              <div className="pm-recall-crossfade">
                <div data-active={!(showContext && searchContext)}>
                  {results.length > 0 ? (
                    <ResultList results={results} filesMap={filesMap} />
                  ) : (
                    <div className="pm-recall-empty pm-recall-empty--inline">
                      <p className="pm-recall-empty-title">{t("recall.noChunks")}</p>
                      <p className="pm-recall-empty-sub">
                        {searchParams.use_agent
                          ? t("recall.agentEmpty")
                          : t("recall.tryAnother")}
                      </p>
                    </div>
                  )}
                </div>
                {searchContext ? (
                  <div data-active={!!(showContext && searchContext)}>
                    <div className="pm-recall-context prose prose-neutral max-w-none">
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm, remarkBreaks]}
                      >
                        {searchContext}
                      </ReactMarkdown>
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Evaluate Tab ───────────────────────────────────────────

const EVAL_RUNNING_PREFIX = "eval_running_"
const GEN_RUNNING_PREFIX = "gen_running_"

function EvaluateTab() {
  const t = useT()
  const {
    recallCollections,
    setRecallCollections,
    collections,
    fetchCollections,
  } = useAppStore()
  const collection = recallCollections[0] || ""
  const [cases, setCases] = useState<EvalTestCase[]>([])
  const [loading, setLoading] = useState(false)
  const [evalTopK, setEvalTopK] = useState("10")
  const [evalSearchMode, setEvalSearchMode] = useState("dense")
  const [evalSparseLlmTokenize, setEvalSparseLlmTokenize] = useState(true)
  const [evalUseReranker, setEvalUseReranker] = useState(false)
  const [evalRerankTopK, setEvalRerankTopK] = useState("5")
  const [evalMinScore, setEvalMinScore] = useState(0)
  const [running, setRunning] = useState(false)
  const [report, setReport] = useState<EvalReport | null>(null)
  const [dashboardVisible, setDashboardVisible] = useState(false)
  const [metricsVisible, setMetricsVisible] = useState(false)
  const [history, setHistory] = useState<EvalReport[]>([])
  const [historyExpanded, setHistoryExpanded] = useState(false)
  const [expandedQuery, setExpandedQuery] = useState<string | null>(null)
  const [expandedCaseId, setExpandedCaseId] = useState<string | null>(null)
  const [expandedChunk, setExpandedChunk] = useState<ChunkContent | null>(null)
  const [chunkLoading, setChunkLoading] = useState(false)
  const [expandedChunkKey, setExpandedChunkKey] = useState<string | null>(null)
  const [evalFilesMap, setEvalFilesMap] = useState<Record<string, string>>({})
  const [regenConfirmOpen, setRegenConfirmOpen] = useState(false)
  /** collectionId → test case count (for dropdown markers) */
  const [caseCounts, setCaseCounts] = useState<Record<string, number>>({})
  const autoRecovered = useRef<Set<string>>(new Set())

  useEffect(() => {
    fetchCollections()
  }, [fetchCollections])

  // Mark collections that already have eval cases in the dropdown
  useEffect(() => {
    if (collections.length === 0) {
      setCaseCounts({})
      return
    }
    let cancelled = false
    Promise.all(
      collections.map((c) =>
        getEvalCases(c.id)
          .then((r) => [c.id, r.cases?.length ?? 0] as const)
          .catch(() => [c.id, 0] as const),
      ),
    ).then((entries) => {
      if (!cancelled) setCaseCounts(Object.fromEntries(entries))
    })
    return () => {
      cancelled = true
    }
  }, [collections])

  // Refresh case count after generate/delete for current collection
  useEffect(() => {
    if (!collection) return
    setCaseCounts((prev) => ({
      ...prev,
      [collection]: cases.length,
    }))
  }, [collection, cases.length])

  useEffect(() => {
    if (!collection) return
    getFiles(collection)
      .then((r) => {
        const map: Record<string, string> = {}
        for (const f of r.files) {
          if (f.display_name) map[f.source] = f.display_name
        }
        setEvalFilesMap(map)
      })
      .catch(() => {})
  }, [collection])

  useEffect(() => {
    if (report && !historyExpanded) {
      setMetricsVisible(false)
      setDashboardVisible(false)
      const t1 = setTimeout(() => setDashboardVisible(true), 40)
      const t2 = setTimeout(() => setMetricsVisible(true), 120)
      return () => {
        clearTimeout(t1)
        clearTimeout(t2)
      }
    }
    setDashboardVisible(false)
    setMetricsVisible(false)
  }, [report, historyExpanded])

  // Auto-recover in-flight eval after refresh / tab switch
  useEffect(() => {
    if (!collection || autoRecovered.current.has(collection)) return
    const key = EVAL_RUNNING_PREFIX + collection
    const saved = localStorage.getItem(key)
    if (!saved) return
    try {
      const data = JSON.parse(saved)
      if (data.running && data.params) {
        autoRecovered.current.add(collection)
        if (data.params.top_k) setEvalTopK(String(data.params.top_k))
        if (data.params.search_mode) setEvalSearchMode(data.params.search_mode)
        if (data.params.use_reranker !== undefined)
          setEvalUseReranker(data.params.use_reranker)
        if (data.params.rerank_top_k)
          setEvalRerankTopK(String(data.params.rerank_top_k))
        if (data.params.min_score !== undefined)
          setEvalMinScore(data.params.min_score)
        setRunning(true)
        setReport(null)
        runEval(collection, data.params)
          .then((res) => {
            setReport(res)
            loadHistory()
          })
          .catch((err) =>
            toast.error(
              t("recall.evalFailed", {
                error: err instanceof Error ? err.message : String(err),
              }),
            ),
          )
          .finally(() => {
            setRunning(false)
            localStorage.removeItem(key)
          })
      }
    } catch {
      localStorage.removeItem(key)
    }
  }, [collection])

  useEffect(() => {
    if (!collection || autoRecovered.current.has(`gen_${collection}`)) return
    const key = GEN_RUNNING_PREFIX + collection
    const saved = localStorage.getItem(key)
    if (!saved) return
    try {
      const data = JSON.parse(saved)
      if (data.running) {
        autoRecovered.current.add(`gen_${collection}`)
        setLoading(true)
        generateEvalCases(collection, data.regenerate ?? false)
          .then((res) => {
            toast.success(res.message)
            loadCases()
          })
          .catch((err) =>
            toast.error(
              t("recall.failed", {
                error: err instanceof Error ? err.message : String(err),
              }),
            ),
          )
          .finally(() => {
            setLoading(false)
            localStorage.removeItem(key)
          })
      }
    } catch {
      localStorage.removeItem(key)
    }
  }, [collection])

  const loadCases = useCallback(async () => {
    if (!collection) return
    try {
      const res = await getEvalCases(collection)
      setCases(res.cases)
    } catch {
      /* ignore */
    }
  }, [collection])

  const loadHistory = useCallback(async () => {
    if (!collection) return
    try {
      const res = await getEvalHistory(collection)
      setHistory([...res.history].reverse())
    } catch {
      /* ignore */
    }
  }, [collection])

  useEffect(() => {
    loadCases()
    loadHistory()
  }, [loadCases, loadHistory])

  const handleDeleteCase = async (id: string) => {
    if (!collection) return
    try {
      await deleteEvalCase(collection, id)
      loadCases()
    } catch {
      /* ignore */
    }
  }

  const handleGenerate = async (regenerate = false) => {
    if (!collection) return
    if (regenerate) {
      setRegenConfirmOpen(true)
      return
    }
    await runGenerate(false)
  }

  const runGenerate = async (regenerate: boolean) => {
    if (!collection) return
    const key = GEN_RUNNING_PREFIX + collection
    localStorage.setItem(
      key,
      JSON.stringify({ running: true, regenerate, ts: Date.now() }),
    )
    setLoading(true)
    try {
      const res = await generateEvalCases(collection, regenerate)
      toast.success(res.message)
      loadCases()
    } catch (err) {
      toast.error(
        t("recall.failed", {
          error: err instanceof Error ? err.message : String(err),
        }),
      )
    } finally {
      setLoading(false)
      localStorage.removeItem(key)
    }
  }

  const handleRun = async () => {
    if (!collection || cases.length === 0) return
    const params = {
      top_k: parseInt(evalTopK) || 10,
      search_mode: evalSearchMode,
      use_reranker: evalUseReranker,
      sparse_llm_tokenize:
        evalSearchMode === "hybrid" ? evalSparseLlmTokenize : undefined,
      rerank_top_k: parseInt(evalRerankTopK) || 5,
      min_score: evalMinScore,
    }
    const key = EVAL_RUNNING_PREFIX + collection
    localStorage.setItem(
      key,
      JSON.stringify({ running: true, params, ts: Date.now() }),
    )
    setRunning(true)
    setReport(null)
    try {
      const res = await runEval(collection, params)
      setReport(res)
      loadHistory()
    } catch (err) {
      toast.error(
        t("recall.evalFailed", {
          error: err instanceof Error ? err.message : String(err),
        }),
      )
    } finally {
      setRunning(false)
      localStorage.removeItem(key)
    }
  }

  const collectionOptions = collections.map((c) => ({
    value: c.id,
    label: c.name,
    /** Green diamond only — no “N cases” text */
    indicator: (caseCounts[c.id] ?? 0) > 0,
  }))

  if (!collection) {
    return (
      <div className="pm-recall-card">
        <div className="pm-recall-empty">
          <FlaskConical
            className="h-9 w-9 pm-recall-empty-icon"
            strokeWidth={1.25}
          />
          <p className="pm-recall-empty-title">{t("recall.chooseCollection")}</p>
          <p className="pm-recall-empty-sub">
            {t("recall.emptySub")}
          </p>
          <div className="w-full max-w-xs mt-2">
            <DropdownSelect
              value=""
              onChange={(id) => {
                if (id) setRecallCollections([id])
              }}
              options={collectionOptions}
              placeholder={t("recall.chooseCollectionPh")}
            />
          </div>
        </div>
      </div>
    )
  }

  const hSelIdx = report
    ? history.findIndex((h) => h.timestamp === report.timestamp)
    : -1
  const hSelected = hSelIdx >= 0 ? history[hSelIdx] : history[0]
  const moreCount = history.length - 1

  return (
    <div className="pm-recall-stack">
      {/* Config */}
      <div className="pm-recall-card pm-recall-card--composer">
        <div className="pm-recall-card-head">
          <div className="pm-recall-card-head-text">
            <h3 className="pm-recall-section-title">{t("recall.runConfig")}</h3>
            <p className="pm-recall-section-desc">
              {t("recall.runConfigDesc")}
            </p>
          </div>
        </div>
        <div className="pm-recall-toolbar">
          <div className="min-w-[11rem] max-w-[16rem]">
            <DropdownSelect
              value={collection}
              onChange={(id) => {
                if (!id) setRecallCollections([])
                else setRecallCollections([id])
              }}
              options={collectionOptions}
              placeholder={t("recall.chooseCollectionPh")}
              size="sm"
            />
          </div>

          <span className="pm-recall-toolbar-sep" aria-hidden />

          <button
            type="button"
            className={cn(
              "pm-chat-tool-chip pm-chat-tool-chip--flow",
              evalUseReranker && "is-on",
            )}
            onClick={() => setEvalUseReranker(!evalUseReranker)}
            title={t("recall.toggleReranker")}
          >
            {t("recall.rerank")}
          </button>

          <div
            className={cn(
              "pm-recall-hybrid",
              evalSearchMode === "hybrid" && "is-open",
            )}
          >
            <button
              type="button"
              className={cn(
                "pm-chat-tool-chip pm-chat-tool-chip--flow",
                evalSearchMode === "hybrid" && "is-on",
              )}
              onClick={() =>
                setEvalSearchMode(
                  evalSearchMode === "hybrid" ? "dense" : "hybrid",
                )
              }
              title={
                evalSearchMode === "hybrid"
                  ? t("recall.hybridTitle")
                  : t("recall.denseHybrid")
              }
            >
              <ChipSwap
                a={t("recall.dense")}
                b={t("recall.hybrid")}
                showB={evalSearchMode === "hybrid"}
                mode
              />
            </button>
            <div className="pm-recall-hybrid-extra">
              <button
                type="button"
                className={cn(
                  "pm-chat-tool-chip pm-chat-tool-chip--flow",
                  evalSparseLlmTokenize && "is-on",
                )}
                onClick={() =>
                  setEvalSparseLlmTokenize(!evalSparseLlmTokenize)
                }
                title={
                  evalSparseLlmTokenize
                    ? t("recall.llmKeywordOn")
                    : t("recall.llmKeywordOff")
                }
              >
                <Sparkles className="size-3" />
              </button>
            </div>
          </div>

          <span className="pm-recall-toolbar-sep" aria-hidden />

          <div className="pm-recall-field">
            <FieldTip label={t("recall.topK")} tooltip={t("recall.topKTip")} />
            <Input
              value={evalTopK}
              onChange={(e) => setEvalTopK(e.target.value)}
              inputMode="numeric"
            />
          </div>

          <ParamSlot open={evalUseReranker}>
            <div className="pm-recall-field">
              <FieldTip
                label={t("recall.rerankK")}
                tooltip={t("recall.rerankKTip")}
              />
              <Input
                value={evalRerankTopK}
                onChange={(e) => setEvalRerankTopK(e.target.value)}
                inputMode="numeric"
              />
            </div>
          </ParamSlot>

          <ParamSlot open={evalSearchMode !== "hybrid"}>
            <div className="pm-recall-field">
              <FieldTip
                label={t("recall.min")}
                tooltip={t("recall.minTip")}
              />
              <Input
                inputMode="numeric"
                value={Math.round(evalMinScore * 100)}
                onChange={(e) => {
                  const raw = e.target.value
                  if (raw === "") {
                    setEvalMinScore(0)
                    return
                  }
                  const v = parseInt(raw)
                  if (!isNaN(v))
                    setEvalMinScore(Math.max(0, Math.min(99, v)) / 100)
                }}
              />
              <span className="pm-meta">%</span>
            </div>
          </ParamSlot>
        </div>

        <div className="pm-recall-actions">
          <Button
            variant="default"
            onClick={handleRun}
            disabled={running || cases.length === 0}
          >
            {running ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            {t("recall.runEval", { n: cases.length })}
          </Button>
        </div>
      </div>

      {/* Test Cases */}
      <div className="pm-recall-card">
        <div className="pm-recall-card-head">
          <div className="pm-recall-card-head-text">
            <h3 className="pm-recall-section-title">
              {t("recall.testCases")}
            </h3>
            <p className="pm-recall-section-desc">
              {t("recall.caseCount", { n: cases.length })}
            </p>
          </div>
          <div className="flex gap-2 shrink-0">
            {cases.length === 0 ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => handleGenerate(false)}
                disabled={loading}
              >
                {loading ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <Wand2 className="h-3 w-3" />
                )}
                {t("recall.autoGenerate")}
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => handleGenerate(true)}
                disabled={loading}
              >
                {loading ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <RotateCw className="h-3 w-3" />
                )}
                {t("recall.regenerate")}
              </Button>
            )}
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-10">
            <Loader2 className="h-4 w-4 animate-spin text-[var(--pm-muted)]" />
            <span className="pm-meta">{t("recall.generating")}</span>
          </div>
        ) : cases.length === 0 ? (
          <div className="pm-recall-empty !py-10">
            <p className="pm-recall-empty-title">{t("recall.noCases")}</p>
            <p className="pm-recall-empty-sub">
              {t("recall.autoGenerateHint")}
            </p>
          </div>
        ) : (
          <div className="pm-recall-list">
            {cases.map((c) => {
              const open = expandedCaseId === c.id
              return (
                <div
                  key={c.id}
                  className={cn("pm-recall-row", open && "is-open")}
                >
                  <div
                    role="button"
                    tabIndex={0}
                    className="pm-recall-row-main group"
                    onClick={async () => {
                      if (open) {
                        setExpandedCaseId(null)
                        setExpandedChunk(null)
                        return
                      }
                      setExpandedCaseId(c.id)
                      setExpandedChunk(null)
                      if (c.target_chunk_id) {
                        setChunkLoading(true)
                        try {
                          const chunk = await getChunkContent(
                            collection,
                            c.target_chunk_id,
                          )
                          setExpandedChunk(chunk)
                        } catch {
                          /* chunk not found */
                        }
                        setChunkLoading(false)
                      }
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault()
                        ;(e.currentTarget as HTMLElement).click()
                      }
                    }}
                  >
                    {open ? (
                      <ChevronDown className="h-3.5 w-3.5 shrink-0 text-[var(--pm-muted)]" />
                    ) : (
                      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-[var(--pm-muted)]" />
                    )}
                    <span className="pm-recall-row-query">{c.query}</span>
                    <span
                      className="pm-meta shrink-0 truncate max-w-[180px]"
                      title={c.target_source}
                    >
                      {evalFilesMap[c.target_source] ||
                        c.target_source?.split("/").pop() ||
                        c.target_source}
                    </span>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      className="opacity-0 group-hover:opacity-100 transition-opacity"
                      onClick={(e) => {
                        e.stopPropagation()
                        handleDeleteCase(c.id)
                      }}
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                  <div className={cn("pm-recall-fold", open && "is-open")}>
                    <div className="pm-recall-fold-clip">
                      <div className="pm-recall-fold-body pm-recall-row-detail">
                        <div>
                          <div className="pm-label mb-1">{t("recall.query")}</div>
                          <div className="text-[var(--pm-text)] whitespace-pre-wrap leading-relaxed font-[family-name:var(--pm-ff)] font-[300]">
                            {c.query}
                          </div>
                        </div>
                        <div className="flex gap-4 flex-wrap pm-meta">
                          <span>
                            {t("recall.targetChunk")}:{" "}
                            <span className="t-mono-family text-[var(--pm-text)]">
                              {c.target_chunk_id}
                            </span>
                          </span>
                          <span>
                            {t("recall.source")}:{" "}
                            <span className="text-[var(--pm-text)]">
                              {evalFilesMap[c.target_source] ||
                                c.target_source?.split("/").pop() ||
                                c.target_source}
                            </span>
                          </span>
                        </div>
                        <div>
                          <div className="pm-label mb-1">
                            {t("recall.targetChunkContent")}
                          </div>
                          {chunkLoading ? (
                            <div className="flex items-center gap-2 pm-meta py-2">
                              <Loader2 className="h-3 w-3 animate-spin" />
                              {t("common.loading")}
                            </div>
                          ) : expandedChunk ? (
                            <div className="pm-recall-chunk">
                              {expandedChunk.text}
                            </div>
                          ) : (
                            <div className="pm-meta italic">
                              {t("recall.chunkNotFound")}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* History */}
      {history.length > 0 && (
        <div className="pm-recall-card">
          <div className="pm-recall-card-head">
            <div className="pm-recall-card-head-text">
              <h3 className="pm-recall-section-title">{t("recall.history")}</h3>
              <p className="pm-recall-section-desc">
                {t("recall.historyDesc")}
              </p>
            </div>
            {moreCount > 0 && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setHistoryExpanded(!historyExpanded)}
              >
                {t("recall.nRecords", { n: history.length })}
              </Button>
            )}
          </div>

          <div
            role="button"
            tabIndex={0}
            className="pm-recall-row-main rounded-[var(--pm-r-sm)] hover:bg-[color-mix(in_srgb,var(--pm-ink)_3%,transparent)]"
            onClick={() => {
              if (moreCount > 0) setHistoryExpanded(!historyExpanded)
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault()
                if (moreCount > 0) setHistoryExpanded(!historyExpanded)
              }
            }}
          >
            {moreCount > 0 ? (
              historyExpanded ? (
                <ChevronDown className="h-3 w-3 text-[var(--pm-muted)] shrink-0" />
              ) : (
                <ChevronRight className="h-3 w-3 text-[var(--pm-muted)] shrink-0" />
              )
            ) : (
              <Clock className="h-3 w-3 text-[var(--pm-muted)] shrink-0" />
            )}
            {hSelIdx >= 0 && hSelected ? (
              <>
                <span className="pm-meta">
                  {hSelected.timestamp
                    ? new Date(hSelected.timestamp).toLocaleString()
                    : t("recall.runN", { n: 1 })}
                </span>
                <Badge variant="outline">
                  {t("recall.nCases", { n: hSelected.total_cases })}
                </Badge>
                <span className="text-[var(--pm-text)] font-[family-name:var(--pm-ff)] font-[300]">
                  {t("recall.recallMetric")}:{" "}
                  {(
                    (hSelected.avg_recall ?? hSelected.avg_hard_recall ?? 0) *
                    100
                  ).toFixed(0)}
                  %
                </span>
                <span
                  className={cn(
                    "t-mono-family font-[family-name:var(--pm-ff)] font-[300]",
                    qualityClass(hSelected.avg_quality_score ?? 0),
                  )}
                >
                  Q: {formatSigned(hSelected.avg_quality_score ?? 0)}
                </span>
                <span className="pm-meta">
                  {(hSelected.avg_time_ms ?? 0).toFixed(0)}ms
                </span>
              </>
            ) : (
              <span className="pm-meta">{t("recall.browseRecords")}</span>
            )}
          </div>

          <div
            className={cn("pm-recall-fold", historyExpanded && "is-open")}
          >
            <div className="pm-recall-fold-clip">
              <div className="pm-recall-fold-body">
                <div
                  className="pm-recall-list pt-1"
                  style={{
                    maxHeight: `${Math.min(history.length * 40, 220)}px`,
                    overflowY: "auto",
                  }}
                >
                  {history.map((h, i) => (
                    <button
                      type="button"
                      key={i}
                      className={cn(
                        "pm-recall-row-main rounded-[var(--pm-r-sm)]",
                        h.timestamp === hSelected?.timestamp &&
                          "bg-[var(--pm-green-wash)]",
                      )}
                      onClick={() => {
                        setReport(h)
                        setHistoryExpanded(false)
                      }}
                    >
                      <Clock className="h-3 w-3 text-[var(--pm-muted)] shrink-0" />
                      <span className="pm-meta">
                        {h.timestamp
                          ? new Date(h.timestamp).toLocaleString()
                          : t("recall.runN", { n: i + 1 })}
                      </span>
                      <Badge variant="outline">{t("recall.nCases", { n: h.total_cases })}</Badge>
                      <span className="text-[var(--pm-text)] font-[family-name:var(--pm-ff)] font-[300]">
                        {t("recall.recallMetric")}:{" "}
                        {(
                          (h.avg_recall ?? h.avg_hard_recall ?? 0) * 100
                        ).toFixed(0)}
                        %
                      </span>
                      <span
                        className={cn(
                          "t-mono-family font-[family-name:var(--pm-ff)] font-[300]",
                          qualityClass(h.avg_quality_score ?? 0),
                        )}
                      >
                        Q: {formatSigned(h.avg_quality_score ?? 0)}
                      </span>
                      <span className="pm-meta">
                        {(h.avg_time_ms ?? 0).toFixed(0)}ms
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      <Dialog open={regenConfirmOpen} onOpenChange={setRegenConfirmOpen}>
        <DialogContent className="pm-dialog sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("recall.regenerateAllQ")}</DialogTitle>
            <DialogDescription>
              {t("recall.regenBody")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setRegenConfirmOpen(false)}
            >
              {t("common.cancel")}
            </Button>
            <Button
              variant="default"
              size="sm"
              onClick={() => {
                setRegenConfirmOpen(false)
                void runGenerate(true)
              }}
            >
              {t("recall.regenerate")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Results Dashboard */}
      <div className={cn("pm-recall-fold", dashboardVisible && "is-open")}>
        <div className="pm-recall-fold-clip">
          <div className="pm-recall-fold-body">
            {report && (
              <div className="pm-recall-card space-y-6">
                <div className="pm-recall-card-head">
                  <div className="pm-recall-card-head-text">
                    <h3 className="pm-recall-section-title">{t("recall.scoreboard")}</h3>
                    <p className="pm-recall-section-desc">
                      {t("recall.scoreboardDesc")}
                    </p>
                  </div>
                </div>
                <div className="pm-recall-metrics">
                  {(
                    [
                      {
                        label: t("recall.recallMetric"),
                        value: `${((report.avg_recall ?? 0) * 100).toFixed(1)}%`,
                        good: (report.avg_recall ?? 0) >= 0.7,
                        delay: 0,
                      },
                      {
                        label: t("recall.hardRecall"),
                        value: `${((report.avg_hard_recall ?? 0) * 100).toFixed(1)}%`,
                        good: (report.avg_hard_recall ?? 0) >= 0.7,
                        delay: 70,
                      },
                      {
                        label: t("recall.quality"),
                        value: formatSigned(report.avg_quality_score ?? 0),
                        good: (report.avg_quality_score ?? 0) >= 0.5,
                        delay: 140,
                      },
                      {
                        label: t("recall.mrr"),
                        value: (report.avg_mrr ?? 0).toFixed(3),
                        good: (report.avg_mrr ?? 0) >= 0.5,
                        delay: 210,
                      },
                    ] as const
                  ).map((m) => (
                    <div
                      key={m.label}
                      className={cn(
                        "pm-recall-metric",
                        metricsVisible && "is-visible",
                      )}
                      style={{
                        transitionDelay: metricsVisible
                          ? `${m.delay}ms`
                          : "0ms",
                      }}
                    >
                      <span
                        className={cn(
                          "pm-recall-metric-value",
                          m.good && "is-good",
                        )}
                      >
                        {m.value}
                      </span>
                      <span className="pm-recall-metric-label">{m.label}</span>
                    </div>
                  ))}
                </div>

                <div>
                  <div className="pm-recall-card-head mb-3">
                    <div className="pm-recall-card-head-text">
                      <h3 className="pm-recall-section-title">
                        {t("recall.perQuery")}
                      </h3>
                      <p className="pm-recall-section-desc">
                        {t("recall.perQueryDesc")}
                      </p>
                    </div>
                  </div>
                  <div className="pm-recall-list">
                    {report.per_query.map((r) => {
                      const open = expandedQuery === r.test_case_id
                      return (
                        <div
                          key={r.test_case_id}
                          className={cn("pm-recall-row", open && "is-open")}
                        >
                          <div
                            role="button"
                            tabIndex={0}
                            className="pm-recall-row-main"
                            onClick={() =>
                              setExpandedQuery(open ? null : r.test_case_id)
                            }
                            onKeyDown={(e) => {
                              if (e.key === "Enter" || e.key === " ") {
                                e.preventDefault()
                                setExpandedQuery(open ? null : r.test_case_id)
                              }
                            }}
                          >
                            {open ? (
                              <ChevronDown className="h-3.5 w-3.5 shrink-0 text-[var(--pm-muted)]" />
                            ) : (
                              <ChevronRight className="h-3.5 w-3.5 shrink-0 text-[var(--pm-muted)]" />
                            )}
                            {r.recalled ? (
                              <CheckCircle className="h-3.5 w-3.5 shrink-0 text-[var(--pm-green)]" />
                            ) : (
                              <XCircle className="h-3.5 w-3.5 shrink-0 text-[var(--pm-danger)]" />
                            )}
                            <span className="pm-recall-row-query">{r.query}</span>
                            {r.hard_recall ? (
                              <Badge variant="default">{t("recall.target")}</Badge>
                            ) : r.holistic_can_answer ? (
                              <Badge variant="secondary">{t("recall.holistic")}</Badge>
                            ) : (
                              <Badge variant="destructive">{t("recall.miss")}</Badge>
                            )}
                            <span
                              className={cn(
                                "w-14 text-right t-mono-family pm-meta",
                                qualityClass(r.quality_score ?? 0),
                              )}
                            >
                              Q:{formatSigned(r.quality_score ?? 0)}
                            </span>
                            <span className="pm-meta w-12 text-right">
                              {r.time_ms}ms
                            </span>
                          </div>

                          <div
                            className={cn("pm-recall-fold", open && "is-open")}
                          >
                            <div className="pm-recall-fold-clip">
                              <div className="pm-recall-fold-body pm-recall-row-detail">
                                <div className="flex gap-3 flex-wrap pm-meta">
                                  <span>
                                    {t("recall.recallMetric")}:{" "}
                                    <span
                                      className={
                                        r.recalled
                                          ? "text-[var(--pm-green)]"
                                          : "text-[var(--pm-danger)]"
                                      }
                                    >
                                      {r.recalled ? "✓" : "✗"}
                                    </span>
                                  </span>
                                  <span>
                                    {t("recall.hardRecall")}:{" "}
                                    <span
                                      className={
                                        r.hard_recall
                                          ? "text-[var(--pm-green)]"
                                          : "text-[var(--pm-danger)]"
                                      }
                                    >
                                      {r.hard_recall ? "✓" : "✗"}
                                    </span>
                                  </span>
                                  <span>
                                    {t("recall.holistic")}:{" "}
                                    <span
                                      className={
                                        r.holistic_can_answer
                                          ? "text-[var(--pm-green)]"
                                          : "text-[var(--pm-muted)]"
                                      }
                                    >
                                      {r.holistic_can_answer ? "✓" : "✗"}
                                    </span>
                                  </span>
                                  <span>
                                    {t("recall.quality")}:{" "}
                                    <span
                                      className={cn(
                                        "t-mono-family",
                                        qualityClass(r.quality_score ?? 0),
                                      )}
                                    >
                                      {formatSigned(r.quality_score ?? 0)}
                                    </span>{" "}
                                    <span className="pm-meta">[-1, 1]</span>
                                  </span>
                                  <span>{t("recall.mrr")}: {(r.mrr ?? 0).toFixed(3)}</span>
                                  {(r.target_position ?? 0) > 0 && (
                                    <span className="text-[var(--pm-green)]">
                                      {t("recall.targetAt", { n: r.target_position ?? 0 })}
                                    </span>
                                  )}
                                </div>

                                {r.holistic_reason && (
                                  <div className="pm-recall-callout">
                                    <strong>{t("recall.holistic")}: </strong>
                                    &ldquo;{r.holistic_reason}&rdquo;
                                  </div>
                                )}

                                {(r.chunk_judgments || []).length > 0 ? (
                                  <div className="space-y-2">
                                    <div className="pm-label">
                                      {t("recall.retrievedChunks")}
                                    </div>
                                    {r.chunk_judgments.map((j, i) => {
                                      const jKey = String(j.judgment)
                                      const tone =
                                        jKey === "1"
                                          ? "is-pos"
                                          : jKey === "-1"
                                            ? "is-neg"
                                            : ""
                                      const label =
                                        jKey === "1"
                                          ? "+1"
                                          : jKey === "-1"
                                            ? "-1"
                                            : "0"
                                      const text =
                                        r.retrieved_chunks?.[i]?.text || ""
                                      const ck = `${r.test_case_id}-${j.id || i}`
                                      const show = expandedChunkKey === ck
                                      return (
                                        <div
                                          key={j.id || i}
                                          className={cn(
                                            "pm-recall-judgment",
                                            tone,
                                          )}
                                        >
                                          <div className="flex items-center gap-2 flex-wrap">
                                            <span className="t-mono-family pm-meta">
                                              #{i + 1}
                                            </span>
                                            <Badge
                                              variant={
                                                jKey === "1"
                                                  ? "default"
                                                  : jKey === "-1"
                                                    ? "destructive"
                                                    : "secondary"
                                              }
                                            >
                                              {label}
                                            </Badge>
                                            {j.is_target && (
                                              <Badge variant="default">
                                                {t("recall.target")}
                                              </Badge>
                                            )}
                                            <span className="pm-meta">
                                              ret_score=
                                              {j.score?.toFixed(3)}
                                            </span>
                                            <span className="pm-meta">
                                              idx={j.chunk_index}
                                            </span>
                                            <span className="pm-meta truncate">
                                              {j.source?.split("/").pop()}
                                            </span>
                                          </div>
                                          {j.reason && (
                                            <p className="mt-1 pm-meta italic text-[var(--pm-text)]">
                                              &ldquo;{j.reason}&rdquo;
                                            </p>
                                          )}
                                          {text ? (
                                            <div className="mt-1.5">
                                              <Button
                                                variant="link"
                                                size="sm"
                                                className="h-auto px-0"
                                                onClick={() =>
                                                  setExpandedChunkKey(
                                                    show ? null : ck,
                                                  )
                                                }
                                              >
                                                {show
                                                  ? t("recall.hideChunk")
                                                  : j.is_target
                                                    ? t("recall.showTargetChunk")
                                                    : t("recall.showChunk")}
                                              </Button>
                                              <div
                                                className={cn(
                                                  "pm-recall-fold",
                                                  show && "is-open",
                                                )}
                                              >
                                                <div className="pm-recall-fold-clip">
                                                  <div className="pm-recall-fold-body">
                                                    <div className="pm-recall-chunk mt-1">
                                                      {text}
                                                    </div>
                                                  </div>
                                                </div>
                                              </div>
                                            </div>
                                          ) : (
                                            <p className="mt-1 pm-meta italic">
                                              {t("recall.noChunkText")}
                                            </p>
                                          )}
                                        </div>
                                      )
                                    })}
                                  </div>
                                ) : (
                                  <p className="pm-meta italic">
                                    {t("recall.olderRun")}
                                  </p>
                                )}
                              </div>
                            </div>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Main View ──────────────────────────────────────────────

export function RecallView() {
  const t = useT()
  return (
    /* Same page chrome as Settings: canvas scroll · mast · soft white cards (no float stage) */
    <div className="pm-settings pm-recall">
      <div className="pm-settings-inner">
        <header className="pm-settings-mast">
          <h1 className="pm-settings-page-title">{t("nav.recall")}</h1>
          <p className="pm-settings-page-desc">
            {t("recall.pageDesc")}
          </p>
        </header>

        <Tabs defaultValue="search" className="pm-recall-tabs">
          <TabsList>
            <TabsIndicator
              className="pm-tabs-indicator"
              renderBeforeHydration
            />
            <TabsTrigger value="search">{t("recall.search")}</TabsTrigger>
            <TabsTrigger value="evaluate">{t("recall.evaluate")}</TabsTrigger>
          </TabsList>
          <TabsContent
            key="search"
            value="search"
            className="pm-recall-tab-panel outline-none"
          >
            <SearchTab />
          </TabsContent>
          <TabsContent
            key="evaluate"
            value="evaluate"
            className="pm-recall-tab-panel outline-none"
          >
            <EvaluateTab />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
