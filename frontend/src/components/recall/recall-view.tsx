import { useState, useEffect, useRef, useCallback } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkBreaks from "remark-breaks"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger, TabsIndicator, TabsContent } from "@/components/ui/tabs"
import {
  Loader2, FlaskConical, Trash2, Wand2, Play, RotateCw,
  CheckCircle, XCircle, Clock, ChevronDown, ChevronRight,
  Bot, Sparkles,
} from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import {
  recallSearch, type RecallResult,
  getEvalCases, deleteEvalCase, generateEvalCases,
  runEval, getEvalHistory, getChunkContent,
  type EvalTestCase, type EvalReport, type ChunkContent,
} from "@/api/client"
import { toast } from "sonner"
import { ResultList } from "./result-list"
import { TooltipLabel } from "@/components/shared/tooltip-label"


// ── Search Tab (existing) ──────────────────────────────────

function SearchTab() {
  const { selectedCollections, setSelectedCollections, collections, fetchCollections } = useAppStore()
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
  const [showCollections, setShowCollections] = useState(false)
  const collectionMenuRef = useRef<HTMLDivElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    fetchCollections()
  }, [fetchCollections])

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        collectionMenuRef.current && !collectionMenuRef.current.contains(e.target as Node) &&
        dropdownRef.current && !dropdownRef.current.contains(e.target as Node)
      ) {
        setShowCollections(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = Math.min(el.scrollHeight, 300) + "px"
  }, [query])

  const toggleCollection = (id: string) => {
    const exists = selectedCollections.includes(id)
    const next = exists ? selectedCollections.filter((c) => c !== id) : [...selectedCollections, id]
    setSelectedCollections(next)
  }

  const handleSearch = async () => {
    if (!query.trim()) return
    // Use IDs for API calls
    const cols = selectedCollections.length > 0 ? selectedCollections : collections.map(c => c.id)
    setResults([])
    setSearching(true)
    try {
      const res = await recallSearch({
        query: query.trim(), collections: cols, search_mode: searchMode,
        top_k: parseInt(topK) || 10, rerank_top_k: parseInt(rerankTopK) || 5,
        use_reranker: useReranker, use_agent: useAgent,
        min_score: minScore,
        sparse_llm_tokenize: searchMode === "hybrid" ? sparseLlmTokenize : undefined,
      })
      setResults(res.results)
      setTimeMs(res.time_ms)
      setSearchParams(res.search_params || {})
      setSearchContext(res.context || "")
    } catch (err) {
      toast.error(`Search failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setSearching(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="pb-6 mb-6 border-b border-primary/30 space-y-4">
          <div className="flex items-end gap-3">
            <textarea
              ref={textareaRef}
              className="flex-1 resize-none border-0 border-b border-border px-0 py-2.5 text-sm min-h-[40px] max-h-[300px] outline-none bg-transparent leading-[1.7] focus:border-primary"
              style={{ fontFamily: "var(--font-serif)", fontStyle: "italic", color: "var(--ze-text)", borderRadius: 0 }}
              placeholder="Search query…"
              rows={1}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              disabled={searching}
            />
            <button
              type="button"
              className="shrink-0 flex items-center gap-1.5 cursor-pointer transition-opacity border-none text-white font-sans"
              style={{
                background: "var(--ze-green)",
                fontSize: "10px", fontWeight: 600,
                textTransform: "uppercase", letterSpacing: "0.12em",
                padding: "8px 16px", borderRadius: "2px",
                opacity: !query.trim() || searching ? 0.3 : 1,
              }}
              onClick={handleSearch}
              disabled={!query.trim() || searching}
            >
              {searching ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="5" y1="12" x2="19" y2="12" /><polyline points="12 5 19 12 12 19" /></svg>
              )}
              <span>{searching ? "Searching" : "Search"}</span>
            </button>
          </div>

          <div className="flex items-center gap-4 flex-wrap">
            {/* Collection dropdown — animated menu style */}
            <div className="relative" ref={collectionMenuRef}>
              <button
                type="button"
                ref={buttonRef}
                onClick={() => setShowCollections(!showCollections)}
                className="group relative flex items-center justify-center overflow-hidden rounded px-3 py-2 font-sans transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)]"
                style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", minWidth: "140px", color: showCollections ? "var(--color-primary-foreground)" : selectedCollections.length > 0 ? "var(--color-primary)" : "var(--color-muted-foreground)" }}
              >
                <span className="relative z-10 whitespace-nowrap text-center">
                  Collections ({selectedCollections.length})
                </span>
                <span
                  className="absolute inset-0 z-0 transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] bg-primary"
                  style={{
                    transform: showCollections ? "scaleX(1)" : "scaleX(0)",
                    transformOrigin: showCollections ? "right" : "left",
                  }}
                />
              </button>
              <div
                ref={dropdownRef}
                className={`fixed z-[100] mt-1 flex-col items-center overflow-hidden rounded border border-primary/40 bg-popover shadow-md transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
                  showCollections
                    ? "opacity-100 visible translate-y-0 pointer-events-auto"
                    : "opacity-0 invisible -translate-y-3 pointer-events-none"
                }`}
                style={{
                  width: buttonRef.current ? buttonRef.current.getBoundingClientRect().width : "auto",
                  top: collectionMenuRef.current ? collectionMenuRef.current.getBoundingClientRect().bottom + 4 : 0,
                  left: collectionMenuRef.current ? collectionMenuRef.current.getBoundingClientRect().left : 0,
                }}
              >
                {collections.map((col) => (
                  <label
                    key={col.id}
                    onClick={() => toggleCollection(col.id)}
                    className="relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group"
                  >
                    <span className="relative z-10 flex items-center gap-2 px-2 py-2 w-full text-[10px]">
                      {selectedCollections.includes(col.id) ? (
                        <span className="w-1.5 h-1.5 bg-primary group-hover:bg-primary-foreground rotate-45 shrink-0 transition-colors duration-700" />
                      ) : (
                        <span className="w-1.5 h-1.5 shrink-0" />
                      )}
                      <span className="whitespace-normal break-words min-w-0 leading-snug">{col.name}</span>
                    </span>
                    <span className="absolute inset-0 z-0 bg-primary transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
                  </label>
                ))}
              </div>
            </div>

            <div className="w-px h-3 bg-border self-center" />

            {/* Agent toggle */}
            <button
              type="button"
              className={`flex items-center gap-1.5 cursor-pointer border-none font-sans transition-all ${useAgent ? "bg-primary text-primary-foreground" : "bg-transparent text-muted-foreground hover:text-primary"}`}
              style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", padding: useAgent ? "3px 8px" : "0", borderRadius: "2px" }}
              onClick={() => {
                const next = !useAgent
                setUseAgent(next)
                if (next) setUseReranker(true)
              }}
              title={useAgent ? "Agentic RAG ON" : "Agentic RAG OFF — direct retrieval"}
            >
              <Bot className="h-3 w-3" />
              {useAgent ? "Agent" : "Direct"}
            </button>

            {/* Reranker toggle */}
            <button
              type="button"
              className={`cursor-pointer border-none font-sans transition-all ${
                useAgent
                  ? "bg-primary/50 text-primary-foreground/60 cursor-not-allowed"
                  : useReranker
                    ? "bg-primary text-primary-foreground"
                    : "bg-transparent text-muted-foreground hover:text-primary"
              }`}
              style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", padding: (useAgent || useReranker) ? "3px 8px" : "0", borderRadius: "2px" }}
              disabled={useAgent}
              onClick={() => { if (!useAgent) setUseReranker(!useReranker) }}
              title={useAgent ? "Reranker is required for Agentic RAG" : "Toggle reranker"}
            >
              Rerank
            </button>

            {/* Search Mode + optional LLM — grouped when hybrid, with animation */}
            <div
              className={`flex items-center gap-2 transition-all duration-300 ease-in-out ${
                searchMode === "hybrid"
                  ? "rounded border border-primary/30 py-1 pl-1 pr-2 max-w-[200px] opacity-100"
                  : "max-w-[60px] border-transparent opacity-100"
              }`}
            >
              <button
                type="button"
                className={`flex items-center gap-1.5 cursor-pointer border-none font-sans transition-all ${
                  searchMode === "hybrid"
                    ? "bg-primary text-primary-foreground"
                    : "bg-transparent text-muted-foreground hover:text-primary"
                }`}
                style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", padding: searchMode === "hybrid" ? "3px 8px" : "0", borderRadius: "2px" }}
                onClick={() => setSearchMode(searchMode === "hybrid" ? "dense" : "hybrid")}
                title={searchMode === "hybrid" ? "Hybrid — Dense + BM25" : "Dense — vector similarity"}
              >
                {searchMode === "hybrid" ? "Hybrid" : "Dense"}
              </button>
              <div
                className={`flex items-center gap-2 transition-all duration-300 ease-in-out overflow-hidden ${
                  searchMode === "hybrid" ? "opacity-100 max-w-[100px]" : "opacity-0 max-w-0"
                }`}
              >
                <span className="text-[10px] text-muted-foreground/60 select-none">·</span>
                <button
                  type="button"
                  className={`cursor-pointer border-none font-sans transition-all ${
                    useAgent
                      ? "bg-primary/50 text-primary-foreground/60 cursor-not-allowed"
                      : sparseLlmTokenize
                        ? "bg-primary text-primary-foreground"
                        : "bg-transparent text-muted-foreground hover:text-primary"
                  }`}
                  style={{ fontSize: "10px", padding: (useAgent || sparseLlmTokenize) ? "3px 5px" : "0", borderRadius: "2px", lineHeight: 1 }}
                  disabled={useAgent}
                  onClick={() => { if (!useAgent) setSparseLlmTokenize(!sparseLlmTokenize) }}
                  title={useAgent ? "Always on in Agentic mode" : sparseLlmTokenize ? "LLM keyword extraction ON" : "LLM keyword extraction OFF — raw tokenization"}
                >
                  <Sparkles className="h-3 w-3" />
                </button>
              </div>
            </div>

            <div className="w-px h-3 bg-border self-center" />

            <div className="flex items-center gap-2">
              <TooltipLabel label="Top K" tooltip="Number of top results to retrieve." />
              <Input className="w-10 h-7 border-0 border-b border-primary/40 bg-transparent rounded-none px-0 text-xs text-center focus:border-primary" value={topK} onChange={(e) => setTopK(e.target.value)} />
            </div>

            {useReranker && (
              <div className="flex items-center gap-2">
                <TooltipLabel label="Rerank Top K" tooltip="Number of results after reranking." />
                <Input className="w-10 h-7 border-0 border-b border-primary/40 bg-transparent rounded-none px-0 text-xs text-center focus:border-primary" value={rerankTopK} onChange={(e) => setRerankTopK(e.target.value)} />
              </div>
            )}

            {searchMode !== "hybrid" && (
              <div className="flex items-center gap-1">
                <TooltipLabel label="Threshold" tooltip="Minimum similarity score (0-1). Results below this are filtered out." />
                <input
                  type="text" inputMode="numeric"
                  value={Math.round(minScore * 100)}
                  onChange={(e) => { const raw = e.target.value; if (raw === "") { setMinScore(0); return } const v = parseInt(raw); if (!isNaN(v)) setMinScore(Math.max(0, Math.min(99, v)) / 100) }}
                  className="w-10 h-7 border-0 border-b border-primary/40 bg-transparent rounded-none px-0 text-xs text-center focus:border-primary"
                />
                <span className="text-[10px] text-muted-foreground">%</span>
              </div>
            )}
          </div>
      </div>

      <div
        className={`grid transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
          results.length > 0
            ? "grid-rows-[1fr] opacity-100"
            : "grid-rows-[0fr] opacity-0"
        }`}
      >
        <div className="overflow-hidden">
          <div
            className={`transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
              results.length > 0
                ? "translate-y-0 opacity-100"
                : "-translate-y-4 opacity-0"
            }`}
          >
            <div className="flex items-center gap-3 text-sm text-muted-foreground mb-3">
              <span>{results.length} results</span>
              <span>in {timeMs}ms</span>
              <Badge variant="outline">{String(searchParams.search_mode || searchMode)}</Badge>
              {!!searchParams.use_reranker && <Badge variant="secondary">Reranked</Badge>}
              {!!searchParams.use_agent && <Badge variant="secondary">Agentic</Badge>}
              {searchContext && (
                <button
                  type="button"
                  onClick={() => setShowContext(!showContext)}
                  className={`cursor-pointer border-none font-sans transition-all ${showContext ? "bg-primary text-primary-foreground" : "bg-transparent text-muted-foreground hover:text-primary"}`}
                  style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", padding: showContext ? "3px 8px" : "0", borderRadius: "2px" }}
                >
                  VIEW CONTEXT
                </button>
              )}
            </div>
            {showContext && searchContext ? (
              <div className="text-xs text-muted-foreground bg-muted/30 p-4 rounded-lg max-h-[600px] overflow-auto prose prose-sm prose-neutral dark:prose-invert max-w-none [&_h2]:text-sm [&_h3]:text-xs [&_p]:text-xs [&_pre]:text-[11px] [&_code]:text-[11px] [&_li]:text-xs">
                <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{searchContext}</ReactMarkdown>
              </div>
            ) : (
              <ResultList results={results} />
            )}
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
  const { selectedCollections, setSelectedCollections, collections, fetchCollections } = useAppStore()
  const collection = selectedCollections[0] || ""
  const [cases, setCases] = useState<EvalTestCase[]>([])
  const [loading, setLoading] = useState(false)
  const [evalTopK, setEvalTopK] = useState("10")
  const [evalSearchMode, setEvalSearchMode] = useState("dense")
  const [evalSparseLlmTokenize, setEvalSparseLlmTokenize] = useState(true)
  const [evalUseReranker, setEvalUseReranker] = useState(false)
  const [evalRerankTopK, setEvalRerankTopK] = useState("5")
  const [evalMinScore, setEvalMinScore] = useState(0)
  const [running, setRunning] = useState(false)
  const [evalShowCollections, setEvalShowCollections] = useState(false)
  const evalCollectionMenuRef = useRef<HTMLDivElement>(null)
  const evalDropdownRef = useRef<HTMLDivElement>(null)
  const evalButtonRef = useRef<HTMLButtonElement>(null)
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
  const autoRecovered = useRef<Set<string>>(new Set())

  useEffect(() => {
    fetchCollections()
  }, [fetchCollections])

  // Animate dashboard section when report changes
  useEffect(() => {
    if (report && !historyExpanded) {
      setMetricsVisible(false)
      setDashboardVisible(false)
      const t1 = setTimeout(() => setDashboardVisible(true), 50)
      const t2 = setTimeout(() => setMetricsVisible(true), 600)
      return () => { clearTimeout(t1); clearTimeout(t2) }
    } else {
      setDashboardVisible(false)
      setMetricsVisible(false)
    }
  }, [report, historyExpanded])

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        evalCollectionMenuRef.current && !evalCollectionMenuRef.current.contains(e.target as Node) &&
        evalDropdownRef.current && !evalDropdownRef.current.contains(e.target as Node)
      ) {
        setEvalShowCollections(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  const selectEvalCollection = (id: string) => {
    if (collection === id) {
      setSelectedCollections([])
    } else {
      setSelectedCollections([id])
    }
    setEvalShowCollections(false)
  }

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
        // Restore params
        if (data.params.top_k) setEvalTopK(String(data.params.top_k))
        if (data.params.search_mode) setEvalSearchMode(data.params.search_mode)
        if (data.params.use_reranker !== undefined) setEvalUseReranker(data.params.use_reranker)
        if (data.params.rerank_top_k) setEvalRerankTopK(String(data.params.rerank_top_k))
        if (data.params.min_score !== undefined) setEvalMinScore(data.params.min_score)
        setRunning(true)
        setReport(null)
        runEval(collection, data.params)
          .then(res => { setReport(res); loadHistory() })
          .catch(err => toast.error(`Evaluation failed: ${err instanceof Error ? err.message : String(err)}`))
          .finally(() => { setRunning(false); localStorage.removeItem(key) })
      }
    } catch { localStorage.removeItem(key) }
  }, [collection])

  // Auto-recover in-flight case generation after refresh / tab switch
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
          .then(res => { toast.success(res.message); loadCases() })
          .catch(err => toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`))
          .finally(() => { setLoading(false); localStorage.removeItem(key) })
      }
    } catch { localStorage.removeItem(key) }
  }, [collection])

  const loadCases = useCallback(async () => {
    if (!collection) return
    try {
      const res = await getEvalCases(collection)
      setCases(res.cases)
    } catch { /* ignore */ }
  }, [collection])

  const loadHistory = useCallback(async () => {
    if (!collection) return
    try {
      const res = await getEvalHistory(collection)
      setHistory([...res.history].reverse())
    } catch { /* ignore */ }
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
    } catch { /* ignore */ }
  }

  const handleGenerate = async (regenerate = false) => {
    if (!collection) return
    if (regenerate && !confirm("This will delete all existing test cases and generate fresh ones. Continue?")) return
    const key = GEN_RUNNING_PREFIX + collection
    localStorage.setItem(key, JSON.stringify({ running: true, regenerate, ts: Date.now() }))
    setLoading(true)
    try {
      const res = await generateEvalCases(collection, regenerate)
      toast.success(res.message)
      loadCases()
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
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
      sparse_llm_tokenize: evalSearchMode === "hybrid" ? evalSparseLlmTokenize : undefined,
      rerank_top_k: parseInt(evalRerankTopK) || 5,
      min_score: evalMinScore,
    }
    const key = EVAL_RUNNING_PREFIX + collection
    localStorage.setItem(key, JSON.stringify({ running: true, params, ts: Date.now() }))
    setRunning(true)
    setReport(null)
    try {
      const res = await runEval(collection, params)
      setReport(res)
      loadHistory()
    } catch (err) {
      toast.error(`Evaluation failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setRunning(false)
      localStorage.removeItem(key)
    }
  }

  if (!collection) {
    return (
      <div className="py-12 text-center text-muted-foreground space-y-4">
        <FlaskConical className="h-12 w-12 mx-auto mb-3 opacity-30" />
        <p>Select a collection to evaluate</p>
        <div className="flex justify-center">
          <div className="relative" ref={evalCollectionMenuRef}>
            <button
              type="button"
              ref={evalButtonRef}
              onClick={() => setEvalShowCollections(!evalShowCollections)}
              className="group relative flex items-center justify-center overflow-hidden rounded px-3 py-2 font-sans transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)]"
              style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", minWidth: "140px", color: evalShowCollections ? "var(--color-primary-foreground)" : "var(--color-muted-foreground)" }}
            >
              <span className="relative z-10 whitespace-nowrap text-center">Choose a collection...</span>
              <span
                className="absolute inset-0 z-0 transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] bg-primary"
                style={{
                  transform: evalShowCollections ? "scaleX(1)" : "scaleX(0)",
                  transformOrigin: evalShowCollections ? "right" : "left",
                }}
              />
            </button>
            <div
              ref={evalDropdownRef}
              className={`fixed z-[100] mt-1 flex-col items-center overflow-hidden rounded border border-primary/40 bg-popover shadow-md transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
                evalShowCollections
                  ? "opacity-100 visible translate-y-0 pointer-events-auto"
                  : "opacity-0 invisible -translate-y-3 pointer-events-none"
              }`}
              style={{
                width: evalButtonRef.current ? evalButtonRef.current.getBoundingClientRect().width : "auto",
                top: evalCollectionMenuRef.current ? evalCollectionMenuRef.current.getBoundingClientRect().bottom + 4 : 0,
                left: evalCollectionMenuRef.current ? evalCollectionMenuRef.current.getBoundingClientRect().left : 0,
              }}
            >
              {collections.map((col) => (
                <label
                  key={col.id}
                  onClick={() => selectEvalCollection(col.id)}
                  className="relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group"
                >
                  <span className="relative z-10 flex items-center gap-2 px-2 py-2 w-full text-[10px]">
                    {collection === col.id ? (
                      <span className="w-1.5 h-1.5 bg-primary group-hover:bg-primary-foreground rotate-45 shrink-0 transition-colors duration-700" />
                    ) : (
                      <span className="w-1.5 h-1.5 shrink-0" />
                    )}
                    <span className="whitespace-normal break-words min-w-0 leading-snug">{col.name}</span>
                  </span>
                  <span className="absolute inset-0 z-0 bg-primary transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
                </label>
              ))}
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Config bar */}
      <div className="pb-6 mb-6 border-b border-primary/30">
          <div className="flex items-center gap-4 flex-wrap">
            {/* Collection dropdown — single select */}
            <div className="relative" ref={evalCollectionMenuRef}>
              <button
                type="button"
                ref={evalButtonRef}
                onClick={() => setEvalShowCollections(!evalShowCollections)}
                className="group relative flex items-center justify-center overflow-hidden rounded px-3 py-2 font-sans transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)]"
                style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", minWidth: "140px", color: evalShowCollections ? "var(--color-primary-foreground)" : collection ? "var(--color-primary)" : "var(--color-muted-foreground)" }}
              >
                <span className="relative z-10 whitespace-nowrap text-center">
                  {collection ? collections.find(c => c.id === collection)?.name || collection : "Choose a collection..."}
                </span>
                <span
                  className="absolute inset-0 z-0 transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] bg-primary"
                  style={{
                    transform: evalShowCollections ? "scaleX(1)" : "scaleX(0)",
                    transformOrigin: evalShowCollections ? "right" : "left",
                  }}
                />
              </button>
              <div
                ref={evalDropdownRef}
                className={`fixed z-[100] mt-1 flex-col items-center overflow-hidden rounded border border-primary/40 bg-popover shadow-md transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
                  evalShowCollections
                    ? "opacity-100 visible translate-y-0 pointer-events-auto"
                    : "opacity-0 invisible -translate-y-3 pointer-events-none"
                }`}
                style={{
                  width: evalButtonRef.current ? evalButtonRef.current.getBoundingClientRect().width : "auto",
                  top: evalCollectionMenuRef.current ? evalCollectionMenuRef.current.getBoundingClientRect().bottom + 4 : 0,
                  left: evalCollectionMenuRef.current ? evalCollectionMenuRef.current.getBoundingClientRect().left : 0,
                }}
              >
                {collections.map((col) => (
                  <label
                    key={col.id}
                    onClick={() => selectEvalCollection(col.id)}
                    className="relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group"
                  >
                    <span className="relative z-10 flex items-center gap-2 px-2 py-2 w-full text-[10px]">
                      {collection === col.id ? (
                        <span className="w-1.5 h-1.5 bg-primary group-hover:bg-primary-foreground rotate-45 shrink-0 transition-colors duration-700" />
                      ) : (
                        <span className="w-1.5 h-1.5 shrink-0" />
                      )}
                      <span className="whitespace-normal break-words min-w-0 leading-snug">{col.name}</span>
                    </span>
                    <span className="absolute inset-0 z-0 bg-primary transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
                  </label>
                ))}
              </div>
            </div>
            <div className="w-px h-3 bg-border self-center" />

            {/* Reranker toggle */}
            <button
              type="button"
              className={`cursor-pointer border-none font-sans transition-all ${
                evalUseReranker
                  ? "bg-primary text-primary-foreground"
                  : "bg-transparent text-muted-foreground hover:text-primary"
              }`}
              style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", padding: evalUseReranker ? "3px 8px" : "0", borderRadius: "2px" }}
              onClick={() => setEvalUseReranker(!evalUseReranker)}
              title="Toggle reranker"
            >
              Rerank
            </button>

            {/* Search Mode + optional LLM — grouped when hybrid, with animation */}
            <div
              className={`flex items-center gap-2 transition-all duration-300 ease-in-out ${
                evalSearchMode === "hybrid"
                  ? "rounded border border-primary/30 py-1 pl-1 pr-2 max-w-[200px] opacity-100"
                  : "max-w-[60px] border-transparent opacity-100"
              }`}
            >
              <button
                type="button"
                className={`flex items-center gap-1.5 cursor-pointer border-none font-sans transition-all ${
                  evalSearchMode === "hybrid"
                    ? "bg-primary text-primary-foreground"
                    : "bg-transparent text-muted-foreground hover:text-primary"
                }`}
                style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", padding: evalSearchMode === "hybrid" ? "3px 8px" : "0", borderRadius: "2px" }}
                onClick={() => setEvalSearchMode(evalSearchMode === "hybrid" ? "dense" : "hybrid")}
                title={evalSearchMode === "hybrid" ? "Hybrid — Dense + BM25" : "Dense — vector similarity"}
              >
                {evalSearchMode === "hybrid" ? "Hybrid" : "Dense"}
              </button>
              <div
                className={`flex items-center gap-2 transition-all duration-300 ease-in-out overflow-hidden ${
                  evalSearchMode === "hybrid" ? "opacity-100 max-w-[100px]" : "opacity-0 max-w-0"
                }`}
              >
                <span className="text-[10px] text-muted-foreground/60 select-none">·</span>
                <button
                  type="button"
                  className={`cursor-pointer border-none font-sans transition-all ${
                    evalSparseLlmTokenize
                      ? "bg-primary text-primary-foreground"
                      : "bg-transparent text-muted-foreground hover:text-primary"
                  }`}
                  style={{ fontSize: "10px", padding: evalSparseLlmTokenize ? "3px 5px" : "0", borderRadius: "2px", lineHeight: 1 }}
                  onClick={() => setEvalSparseLlmTokenize(!evalSparseLlmTokenize)}
                  title={evalSparseLlmTokenize ? "LLM keyword extraction ON" : "LLM keyword extraction OFF — raw tokenization"}
                >
                  <Sparkles className="h-3 w-3" />
                </button>
              </div>
            </div>

            <div className="w-px h-3 bg-border self-center" />
            <div className="flex items-center gap-2">
              <TooltipLabel label="Top K" tooltip="Results to retrieve per query" />
              <Input className="w-10 h-7 border-0 border-b border-primary/40 bg-transparent rounded-none px-0 text-xs text-center focus:border-primary" value={evalTopK} onChange={(e) => setEvalTopK(e.target.value)} />
            </div>
            {evalUseReranker && (
              <div className="flex items-center gap-2">
                <TooltipLabel label="Rerank Top K" tooltip="Number of results after reranking." />
                <Input className="w-10 h-7 border-0 border-b border-primary/40 bg-transparent rounded-none px-0 text-xs text-center focus:border-primary" value={evalRerankTopK} onChange={(e) => setEvalRerankTopK(e.target.value)} />
              </div>
            )}
            {evalSearchMode !== "hybrid" && (
              <div className="flex items-center gap-1">
                <TooltipLabel label="Threshold" tooltip="Minimum similarity score (0-1). Filter retrieved chunks below this. Same as the slider in Search tab." />
                <input
                  type="text" inputMode="numeric"
                  value={Math.round(evalMinScore * 100)}
                  onChange={(e) => { const raw = e.target.value; if (raw === "") { setEvalMinScore(0); return } const v = parseInt(raw); if (!isNaN(v)) setEvalMinScore(Math.max(0, Math.min(99, v)) / 100) }}
                  className="w-10 h-7 border-0 border-b border-primary/40 bg-transparent rounded-none px-0 text-xs text-center focus:border-primary"
                />
                <span className="text-[10px] text-muted-foreground">%</span>
              </div>
            )}
          </div>
          <div className="mt-4">
            <Button onClick={handleRun} disabled={running || cases.length === 0}>
              {running ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Play className="h-4 w-4 mr-2" />}
              Run Evaluation ({cases.length} cases)
            </Button>
          </div>
      </div>

      {/* Test Cases */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <span className="text-[11px] font-normal uppercase tracking-[0.12em] text-muted-foreground/80">Test Cases ({cases.length})</span>
          <div className="flex gap-2">
            {cases.length === 0 ? (
              <button
                className="cursor-pointer border-none font-sans text-muted-foreground hover:text-primary transition-colors"
                style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", background: "transparent" }}
                onClick={() => handleGenerate(false)} disabled={loading}
              >
                {loading ? <Loader2 className="h-3 w-3 mr-1 inline animate-spin" /> : <Wand2 className="h-3 w-3 mr-1 inline" />}
                Auto-generate
              </button>
            ) : (
              <button
                className="cursor-pointer border-none font-sans text-muted-foreground hover:text-primary transition-colors"
                style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", background: "transparent" }}
                onClick={() => handleGenerate(true)} disabled={loading}
              >
                {loading ? <Loader2 className="h-3 w-3 mr-1 inline animate-spin" /> : <RotateCw className="h-3 w-3 mr-1 inline" />}
                Regenerate All
              </button>
            )}
          </div>
        </div>
        <div className="space-y-2">
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Generating test cases...
            </div>
          ) : cases.length === 0 ? (
            <p className="text-xs text-muted-foreground py-4 text-center">
              No test cases. Click "Auto-generate" to create cases from indexed files.
            </p>
          ) : (
            <div className="space-y-1">
              {cases.map((c) => (
                <div key={c.id}>
                  <div
                    className="flex items-center gap-2 text-xs py-1.5 px-2 rounded hover:bg-accent group cursor-pointer"
                    onClick={async () => {
                      if (expandedCaseId === c.id) {
                        setExpandedCaseId(null)
                        setExpandedChunk(null)
                        return
                      }
                      setExpandedCaseId(c.id)
                      setExpandedChunk(null)
                      if (c.target_chunk_id) {
                        setChunkLoading(true)
                        try {
                          const chunk = await getChunkContent(collection, c.target_chunk_id)
                          setExpandedChunk(chunk)
                        } catch { /* chunk not found */ }
                        setChunkLoading(false)
                      }
                    }}
                  >
                    {expandedCaseId === c.id ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />}
                    <span className="flex-1 truncate">{c.query}</span>
                    <span className="text-muted-foreground shrink-0 truncate max-w-[200px]" title={c.target_source}>
                      → {c.target_source?.split("/").pop()}
                    </span>
                    <Button variant="ghost" size="sm" className="h-5 w-5 p-0 opacity-0 group-hover:opacity-100" onClick={(e) => { e.stopPropagation(); handleDeleteCase(c.id) }}>
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                  <div className={`grid transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
                    expandedCaseId === c.id ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
                  }`}>
                    <div className="overflow-hidden">
                      <div className={`pl-8 pb-3 space-y-2 text-xs border-l-2 border-muted ml-1 mt-1 transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
                        expandedCaseId === c.id ? "translate-y-0 opacity-100" : "-translate-y-2 opacity-0"
                      }`}>
                        <div>
                          <div className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide mb-1">Query</div>
                          <div className="text-foreground/90 whitespace-pre-wrap">{c.query}</div>
                        </div>
                        <div className="flex gap-4 text-[10px] text-muted-foreground">
                          <span>Target chunk: <span className="font-mono text-foreground/70">{c.target_chunk_id}</span></span>
                          <span>Source: <span className="text-foreground/70">{c.target_source?.split("/").pop()}</span></span>
                        </div>
                        <div>
                          <div className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide mb-1">Target Chunk Content</div>
                          {chunkLoading ? (
                            <div className="flex items-center gap-2 text-muted-foreground py-2">
                              <Loader2 className="h-3 w-3 animate-spin" />
                              Loading...
                            </div>
                          ) : expandedChunk ? (
                            <div className="text-foreground/80 text-[11px] whitespace-pre-wrap bg-muted/30 p-2 rounded max-h-48 overflow-y-auto">
                              {expandedChunk.text}
                            </div>
                          ) : (
                            <div className="text-muted-foreground italic text-[11px]">Chunk not found (may have been deleted)</div>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* History */}
      {history.length > 0 && (() => {
        const hSelIdx = report
          ? history.findIndex(h => h.timestamp === report.timestamp)
          : -1
        const hSelected = hSelIdx >= 0 ? history[hSelIdx] : history[0]
        const moreCount = history.length - 1
        return (
        <div>
          <div className="mb-2">
            <span className="text-[11px] font-normal uppercase tracking-[0.12em] text-muted-foreground/80">Evaluation History</span>
          </div>
          {/* Always-visible selected row */}
          <div
            className="flex items-center gap-3 text-xs py-1.5 px-2 rounded hover:bg-accent cursor-pointer"
            onClick={() => { if (moreCount > 0) setHistoryExpanded(!historyExpanded) }}
          >
            {moreCount > 0 ? (
              historyExpanded ? <ChevronDown className="h-3 w-3 text-muted-foreground shrink-0" /> : <ChevronRight className="h-3 w-3 text-muted-foreground shrink-0" />
            ) : (
              <Clock className="h-3 w-3 text-muted-foreground shrink-0" />
            )}
            {hSelIdx >= 0 ? (
              <>
                <span className="text-muted-foreground">
                  {hSelected.timestamp
                    ? new Date(hSelected.timestamp).toLocaleString()
                    : "Run 1"}
                </span>
                <Badge variant="outline" className="text-[10px]">{hSelected.total_cases} cases</Badge>
                <span>Recall: {((hSelected.avg_recall ?? hSelected.avg_hard_recall ?? 0) * 100).toFixed(0)}%</span>
                <span className={`font-mono ${qualityColor(hSelected.avg_quality_score ?? 0)}`}>Q: {formatSigned(hSelected.avg_quality_score ?? 0)}</span>
                <span className="text-muted-foreground">{(hSelected.avg_time_ms ?? 0).toFixed(0)}ms</span>
              </>
            ) : (
              <span className="text-muted-foreground">Browse Evaluation Records</span>
            )}
            {moreCount > 0 && (
              <span
                className="ml-auto cursor-pointer select-none"
                style={{ fontSize: "10px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-primary)" }}
                onClick={(e) => { e.stopPropagation(); setHistoryExpanded(!historyExpanded) }}
              >
                {history.length} Record{history.length !== 1 ? "s" : ""}
              </span>
            )}
          </div>
          {/* Expandable list */}
          <div className={`grid transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
            historyExpanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
          }`}>
            <div className="overflow-hidden">
              <div className={`transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
                historyExpanded ? "translate-y-0 opacity-100" : "-translate-y-2 opacity-0"
              }`}>
                <div className="space-y-1 pt-1"
                  style={{ maxHeight: `${Math.min(history.length * 36, 200)}px`, overflowY: "auto" }}
                >
                  {history.map((h, i) => (
                    <div
                      key={i}
                      className={`flex items-center gap-3 text-xs py-1.5 px-2 rounded hover:bg-accent cursor-pointer ${h.timestamp === hSelected.timestamp ? "bg-accent/50" : ""}`}
                      onClick={() => { setReport(h); setHistoryExpanded(false) }}
                    >
                      <Clock className="h-3 w-3 text-muted-foreground shrink-0" />
                      <span className="text-muted-foreground">
                        {h.timestamp
                          ? new Date(h.timestamp).toLocaleString()
                          : `Run ${i + 1}`}
                      </span>
                      <Badge variant="outline" className="text-[10px]">{h.total_cases} cases</Badge>
                      <span>Recall: {((h.avg_recall ?? h.avg_hard_recall ?? 0) * 100).toFixed(0)}%</span>
                      <span className={`font-mono ${qualityColor(h.avg_quality_score ?? 0)}`}>Q: {formatSigned(h.avg_quality_score ?? 0)}</span>
                      <span className="text-muted-foreground">{(h.avg_time_ms ?? 0).toFixed(0)}ms</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
        )
      })()}

      {/* Results Dashboard */}
      <div className={`grid transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
        dashboardVisible ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
      }`}>
        <div className="overflow-hidden">
          <div className={`transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
            dashboardVisible ? "translate-y-0 opacity-100" : "-translate-y-4 opacity-0"
          }`}>
            {report && <>
            <div className="flex justify-between pb-5 border-b border-dashed border-border">
            <div className={`flex flex-col items-center transition-opacity duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
              metricsVisible ? "opacity-100" : "opacity-0"
            }`} style={{ transitionDelay: metricsVisible ? "0ms" : "0ms" }}>
              <span className="text-[28px] font-light leading-none text-foreground" style={{ fontFamily: "var(--font-serif)" }}>{((report.avg_recall ?? 0) * 100).toFixed(1)}%</span>
              <span className="text-[11px] font-normal uppercase tracking-[0.12em] text-muted-foreground/80 text-muted-foreground mt-1.5">Recall</span>
            </div>
            <div className={`flex flex-col items-center transition-opacity duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
              metricsVisible ? "opacity-100" : "opacity-0"
            }`} style={{ transitionDelay: metricsVisible ? "300ms" : "0ms" }}>
              <span className="text-[28px] font-light leading-none text-foreground" style={{ fontFamily: "var(--font-serif)" }}>{((report.avg_hard_recall ?? 0) * 100).toFixed(1)}%</span>
              <span className="text-[11px] font-normal uppercase tracking-[0.12em] text-muted-foreground/80 text-muted-foreground mt-1.5">Hard Recall</span>
            </div>
            <div className={`flex flex-col items-center transition-opacity duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
              metricsVisible ? "opacity-100" : "opacity-0"
            }`} style={{ transitionDelay: metricsVisible ? "600ms" : "0ms" }}>
              <span className="text-[28px] font-light leading-none text-foreground" style={{ fontFamily: "var(--font-serif)" }}>{formatSigned(report.avg_quality_score ?? 0)}</span>
              <span className="text-[11px] font-normal uppercase tracking-[0.12em] text-muted-foreground/80 text-muted-foreground mt-1.5">Quality</span>
            </div>
            <div className={`flex flex-col items-center transition-opacity duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
              metricsVisible ? "opacity-100" : "opacity-0"
            }`} style={{ transitionDelay: metricsVisible ? "900ms" : "0ms" }}>
              <span className="text-[28px] font-light leading-none text-foreground" style={{ fontFamily: "var(--font-serif)" }}>{(report.avg_mrr ?? 0).toFixed(3)}</span>
              <span className="text-[11px] font-normal uppercase tracking-[0.12em] text-muted-foreground/80 text-muted-foreground mt-1.5">MRR</span>
            </div>
          </div>

          <div>
            <div className="mb-2">
              <span className="text-[11px] font-normal uppercase tracking-[0.12em] text-muted-foreground/80">Per-Query Results</span>
            </div>
            <div className="space-y-1">
              {report.per_query.map((r) => (
                <div key={r.test_case_id}>
                  <div
                    className="flex items-center gap-2 text-xs py-2 px-2 rounded hover:bg-accent cursor-pointer"
                    onClick={() => setExpandedQuery(expandedQuery === r.test_case_id ? null : r.test_case_id)}
                  >
                    {expandedQuery === r.test_case_id ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />}
                    {r.recalled ? <CheckCircle className="h-3 w-3 text-emerald-500 shrink-0" /> : <XCircle className="h-3 w-3 text-orange-500 shrink-0" />}
                    <span className="flex-1 truncate">{r.query}</span>
                    {r.hard_recall ? (
                      <Badge className="text-[10px] px-1 bg-emerald-600">target</Badge>
                    ) : r.holistic_can_answer ? (
                      <Badge variant="secondary" className="text-[10px] px-1">holistic</Badge>
                    ) : (
                      <Badge className="text-[10px] px-1 bg-orange-600">miss</Badge>
                    )}
                    <span className={`w-14 text-right font-mono ${qualityColor(r.quality_score ?? 0)}`}>
                      Q:{formatSigned(r.quality_score ?? 0)}
                    </span>
                    <span className="text-muted-foreground w-12 text-right">{r.time_ms}ms</span>
                  </div>
                  <div className={`grid transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
                    expandedQuery === r.test_case_id ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
                  }`}>
                    <div className="overflow-hidden">
                      <div className={`pl-8 pb-3 space-y-2 text-xs border-l-2 border-muted ml-1 transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
                        expandedQuery === r.test_case_id ? "translate-y-0 opacity-100" : "-translate-y-2 opacity-0"
                      }`}>
                        <div className="flex gap-3 text-muted-foreground flex-wrap">
                          <span>Recall: <span className={r.recalled ? "text-emerald-500 font-medium" : "text-orange-500"}>{r.recalled ? "✓" : "✗"}</span></span>
                          <span>Hard: <span className={r.hard_recall ? "text-emerald-500 font-medium" : "text-orange-500"}>{r.hard_recall ? "✓" : "✗"}</span></span>
                          <span>Holistic: <span className={r.holistic_can_answer ? "text-emerald-500 font-medium" : "text-muted-foreground"}>{r.holistic_can_answer ? "✓" : "✗"}</span></span>
                          <span>Quality: <span className={`font-mono ${qualityColor(r.quality_score ?? 0)}`}>{formatSigned(r.quality_score ?? 0)}</span> <span className="text-[10px]">[-1, 1]</span></span>
                          <span>MRR: {(r.mrr ?? 0).toFixed(3)}</span>
                          {(r.target_position ?? 0) > 0 && <span className="text-emerald-500 font-medium">target @ #{r.target_position}</span>}
                        </div>
                        {r.holistic_reason && (
                          <div className="text-foreground/80 italic text-[11px] bg-indigo-50/40 dark:bg-indigo-950/15 px-2 py-1.5 rounded border-l-2 border-indigo-300/50">
                            <span className="font-medium text-indigo-500 not-italic">Holistic: </span>
                            "{r.holistic_reason}"
                          </div>
                        )}
                        {(r.chunk_judgments || []).length > 0 ? (
                          <div className="mt-2 space-y-1.5">
                            <div className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
                              Retrieved Chunks — LLM Judgment
                            </div>
                            {r.chunk_judgments.map((j, i) => {
                              const judgmentStyles: Record<string, { border: string; bg: string; badge: string; label: string }> = {
                                "1": { border: "border-l-emerald-500", bg: "bg-emerald-50 dark:bg-emerald-950/30", badge: "bg-emerald-600", label: "+1" },
                                "0": { border: "border-l-muted-foreground/30", bg: "", badge: "bg-muted-foreground", label: "0" },
                                "-1": { border: "border-l-orange-500", bg: "bg-orange-50 dark:bg-orange-950/20", badge: "bg-orange-600", label: "-1" },
                              }
                              const judgmentColors = judgmentStyles[String(j.judgment)] || judgmentStyles["0"]
                              const text = r.retrieved_chunks?.[i]?.text || ""
                              return (
                                <div key={j.id || i} className={`border-l-4 ${judgmentColors.border} ${judgmentColors.bg} pl-2 py-1.5`}>
                                  <div className="flex items-center gap-2 text-[10px] flex-wrap">
                                    <span className="font-mono text-muted-foreground">#{i + 1}</span>
                                    <span className={`px-1.5 py-0.5 rounded text-white font-bold ${judgmentColors.badge}`}>
                                      {judgmentColors.label}
                                    </span>
                                    {j.is_target ? (
                                      <span className="px-1.5 py-0.5 rounded bg-emerald-600 text-white font-medium">
                                        TARGET
                                      </span>
                                    ) : null}
                                    <span className="text-muted-foreground">ret_score={j.score?.toFixed(3)}</span>
                                    <span className="text-muted-foreground">idx={j.chunk_index}</span>
                                    <span className="text-foreground/70 truncate">
                                      {j.source?.split("/").pop()}
                                    </span>
                                  </div>
                                  {j.reason && (
                                    <div className="mt-1 text-foreground/80 italic text-[11px]">
                                      "{j.reason}"
                                    </div>
                                  )}
                                  {text ? (() => {
                                    const ck = `${r.test_case_id}-${j.id || i}`
                                    const show = expandedChunkKey === ck
                                    return (
                                    <div className="mt-1">
                                      <button
                                        className="text-[10px] text-muted-foreground cursor-pointer hover:text-foreground select-none border-none bg-transparent p-0"
                                        onClick={() => setExpandedChunkKey(show ? null : ck)}
                                      >
                                        {show ? "Hide chunk text" : (j.is_target ? "Show target chunk text" : "Show chunk text")}
                                      </button>
                                      <div className={`grid transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
                                        show ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
                                      }`}>
                                        <div className="overflow-hidden">
                                          <div className="mt-1 text-foreground/70 text-[11px] whitespace-pre-wrap bg-muted/30 p-2 rounded max-h-64 overflow-y-auto">
                                            {text}
                                          </div>
                                        </div>
                                      </div>
                                    </div>
                                    )
                                  })() : (
                                    <div className="mt-1 text-[10px] text-muted-foreground italic">
                                      (no chunk text available)
                                    </div>
                                  )}
                                </div>
                              )
                            })}
                          </div>
                        ) : (
                          <div className="text-muted-foreground italic text-[11px]">
                            (Older run — no per-chunk data. Re-run evaluation to see chunk-level judgments.)
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
          </>}
          </div>
        </div>
      </div>
    </div>
  )
}

function formatSigned(n: number, digits = 2): string {
  if (n > 0) return `+${n.toFixed(digits)}`
  if (n < 0) return n.toFixed(digits)
  return "0.00"
}

function qualityColor(n: number): string {
  if (n >= 0.7) return "text-emerald-500 dark:text-emerald-400"
  if (n > 0.2) return "text-emerald-600 dark:text-emerald-400/80"
  if (n > -0.2) return "text-muted-foreground"
  if (n > -0.6) return "text-orange-500/80"
  return "text-orange-600 dark:text-orange-400"
}

// ── Main View ──────────────────────────────────────────────

export function RecallView() {
  return (
    <div className="h-full overflow-auto p-6">
      <div className="max-w-4xl mx-auto space-y-6">
        <div className="mb-5">
          <span className="text-[18px] font-[350] tracking-tight uppercase">
            Recall
          </span>
          <p className="text-[10px] text-muted-foreground mt-0.5">
            Search &amp; evaluate retrieval quality
          </p>
        </div>

        <Tabs defaultValue="search">
          <TabsList className="relative" variant="line">
            <TabsIndicator renderBeforeHydration />
            <TabsTrigger value="search" className="font-light uppercase tracking-wider after:!opacity-0">Search</TabsTrigger>
            <TabsTrigger value="evaluate" className="font-light uppercase tracking-wider after:!opacity-0">Evaluate</TabsTrigger>
          </TabsList>
          <TabsContent key="search" value="search" className="mt-4 animate-tab-in">
            <SearchTab />
          </TabsContent>
          <TabsContent key="evaluate" value="evaluate" className="mt-4 animate-tab-in">
            <EvaluateTab />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
