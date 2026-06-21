import { useState, useRef, useEffect, type KeyboardEvent } from "react"
import { Layers, Bot, Settings } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { useAppStore } from "@/stores/app-store"
import { useStreamChat } from "@/hooks/use-stream"
import { uploadFiles } from "@/api/client"
import { toast } from "sonner"

function persisted<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(`chat_${key}`)
    if (v === null) return fallback
    return JSON.parse(v) as T
  } catch {
    return fallback
  }
}

export function ChatInput() {
  const [input, setInput] = useState("")
  const [showCollections, setShowCollections] = useState(false)
  const [useAgent, setUseAgent] = useState(() => persisted("useAgent", true))
  const [searchMode, setSearchMode] = useState(() => persisted("searchMode", "dense"))
  const [topK, setTopK] = useState(() => persisted("topK", 5))
  const [useReranker, setUseReranker] = useState(() => persisted("useReranker", true))
  const [maxIterations, setMaxIterations] = useState(() => persisted("maxIterations", 3))
  const [rerankTopK, setRerankTopK] = useState(() => persisted("rerankTopK", 5))
  const [minScore, setMinScore] = useState(() => persisted("minScore", 0))
  const {
    isStreaming,
    activeCollection,
    collections,
    fetchCollections,
    selectedCollections,
    toggleCollection,
    activeProvider,
    activeModel,
    setActiveModel,
    providers,
  } = useAppStore()
  const { sendMessage } = useStreamChat()
  const fileRef = useRef<HTMLInputElement>(null)
  const collectionMenuRef = useRef<HTMLDivElement>(null)

  useEffect(() => { fetchCollections() }, [fetchCollections])
  useEffect(() => { localStorage.setItem("chat_useAgent", JSON.stringify(useAgent)) }, [useAgent])
  useEffect(() => { localStorage.setItem("chat_searchMode", JSON.stringify(searchMode)) }, [searchMode])
  useEffect(() => { if (!isNaN(topK)) localStorage.setItem("chat_topK", JSON.stringify(topK)) }, [topK])
  useEffect(() => { localStorage.setItem("chat_useReranker", JSON.stringify(useReranker)) }, [useReranker])
  useEffect(() => { if (!isNaN(maxIterations)) localStorage.setItem("chat_maxIterations", JSON.stringify(maxIterations)) }, [maxIterations])
  useEffect(() => { if (!isNaN(rerankTopK)) localStorage.setItem("chat_rerankTopK", JSON.stringify(rerankTopK)) }, [rerankTopK])
  useEffect(() => { localStorage.setItem("chat_minScore", JSON.stringify(minScore)) }, [minScore])

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (collectionMenuRef.current && !collectionMenuRef.current.contains(e.target as Node)) {
        setShowCollections(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  const readyProviders = providers.filter((p) => p.status === "ready" || !p.status)
  const currentProvider = activeProvider
    ? readyProviders.find((p) => p.id === activeProvider)
    : readyProviders.find((p) => p.is_default) || readyProviders[0]
  const availableModels = currentProvider?.selected_models && currentProvider.selected_models.length > 0
    ? currentProvider.selected_models
    : currentProvider?.model ? [currentProvider.model] : []

  const handleSend = async () => {
    const text = input.trim()
    if (!text || isStreaming) return
    setInput("")
    const cols = selectedCollections.length > 0
      ? selectedCollections
      : collections.map(c => c.id)
    await sendMessage(text, cols, activeProvider, activeModel, useAgent, searchMode, {
      top_k: isNaN(topK) ? 5 : topK,
      use_reranker: useReranker,
      max_iterations: isNaN(maxIterations) ? 3 : maxIterations,
      min_score: minScore,
      rerank_top_k: isNaN(rerankTopK) ? 5 : rerankTopK,
    })
  }

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  const handleFileAttach = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files?.length) return
    try {
      const res = await uploadFiles(files, activeCollection)
      toast.success(res.message)
    } catch (err) {
      toast.error(`Upload failed: ${err instanceof Error ? err.message : String(err)}`)
    }
    if (fileRef.current) fileRef.current.value = ""
  }

  const collectionLabel = selectedCollections.length === 0
    ? "All collections"
    : `${selectedCollections.length} collection${selectedCollections.length !== 1 ? "s" : ""}`

  return (
    <div className="border-t border-border p-3 px-12">
      <div className="max-w-3xl mx-auto space-y-2.5">
        {/* Toolbar */}
        <div className="flex items-center gap-4 flex-wrap text-[10px] font-medium uppercase tracking-[0.1em]">
          {/* Collection selector */}
          <div className="relative" ref={collectionMenuRef}>
            <button
              type="button"
              className={`flex items-center gap-1.5 cursor-pointer bg-transparent border-none font-sans transition-colors ${selectedCollections.length > 0 ? "text-primary" : "text-muted-foreground hover:text-primary"}`}
              style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase" }}
              onClick={() => setShowCollections(!showCollections)}
            >
              <Layers className="h-3 w-3" />
              {collectionLabel}
            </button>
            {showCollections && (
              <div className="absolute z-50 bottom-full left-0 mb-1 w-56 rounded-md p-2 space-y-1 max-h-60 overflow-y-auto bg-popover border border-border shadow-md">
                {collections.map((col) => (
                  <label key={col.id} className="flex items-center gap-2 text-sm cursor-pointer px-2 py-1 rounded hover:bg-accent text-foreground">
                    <input
                      type="checkbox"
                      checked={selectedCollections.includes(col.id)}
                      onChange={() => toggleCollection(col.id)}
                      className="rounded"
                    />
                    {col.name}
                  </label>
                ))}
              </div>
            )}
          </div>

          <div className="w-px h-3 bg-border" />

          {/* Agent toggle — solid dark green when ON */}
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

          {/* Reranker — solid dark green when ON */}
          <button
            type="button"
            className={`cursor-pointer border-none font-sans transition-all ${
              useAgent
                ? "bg-primary text-primary-foreground cursor-not-allowed"
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

          <div className="w-px h-3 bg-border" />

          {/* Provider/Model selects */}
          {readyProviders.length > 0 && (
            <>
              <select
                className="text-[10px] font-medium uppercase tracking-[0.08em] px-2 py-1 bg-transparent border border-border rounded-sm text-muted-foreground cursor-pointer font-sans outline-none hover:text-primary focus:border-border"
                value={activeProvider ?? ""}
                onChange={(e) => {
                  const val = e.target.value || null
                  useAppStore.getState().setActiveProvider(val)
                  const prov = readyProviders.find((p) => p.id === val)
                  const defaultM = prov?.default_model || prov?.selected_models?.[0] || prov?.model || null
                  setActiveModel(defaultM)
                }}
              >
                <option value="">Default provider</option>
                {readyProviders.map((p) => (
                  <option key={p.id} value={p.id}>{p.name || p.model}</option>
                ))}
              </select>

              {activeProvider && availableModels.length >= 1 && (
                <select
                  className="text-[10px] font-medium uppercase tracking-[0.08em] px-2 py-1 bg-transparent border border-border rounded-sm text-muted-foreground cursor-pointer font-sans outline-none hover:text-primary focus:border-border"
                  value={activeModel ?? ""}
                  onChange={(e) => setActiveModel(e.target.value || null)}
                >
                  {availableModels.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              )}
            </>
          )}

          {/* Settings */}
          <div className="ml-auto">
            <Sheet>
              <SheetTrigger render={<Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-primary" />}>
                <Settings className="h-3.5 w-3.5" />
              </SheetTrigger>
              <SheetContent side="right" className="sm:max-w-sm">
                <SheetHeader>
                  <SheetTitle>Chat Settings</SheetTitle>
                </SheetHeader>
                <div className="px-4 pb-4 space-y-4 overflow-y-auto flex-1">
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium">Search Mode</label>
                    <select className="w-full h-8 rounded-md border border-input bg-background px-2 text-xs" value={searchMode} onChange={(e) => setSearchMode(e.target.value)}>
                      <option value="dense">Dense — vector similarity</option>
                      <option value="hybrid">Hybrid — vector + BM25 keyword</option>
                    </select>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium">Top K — chunks to retrieve</label>
                    <input type="number" min={1} max={50} value={isNaN(topK) ? "" : topK}
                      onChange={(e) => { const v = e.target.value; if (v === "") { setTopK(NaN); return } const n = parseInt(v); if (!isNaN(n)) setTopK(Math.max(1, Math.min(50, n))) }}
                      onBlur={() => { if (isNaN(topK)) setTopK(5) }}
                      className="w-full h-8 rounded-md border border-input bg-background px-2 text-xs"
                    />
                  </div>
                  {useReranker && (
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium">Rerank Top K</label>
                      <input type="number" min={1} max={50} value={isNaN(rerankTopK) ? "" : rerankTopK}
                        onChange={(e) => { const v = e.target.value; if (v === "") { setRerankTopK(NaN); return } const n = parseInt(v); if (!isNaN(n)) setRerankTopK(Math.max(1, Math.min(50, n))) }}
                        onBlur={() => { if (isNaN(rerankTopK)) setRerankTopK(5) }}
                        className="w-full h-8 rounded-md border border-input bg-background px-2 text-xs"
                      />
                    </div>
                  )}
                  {searchMode !== "hybrid" && (
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium">Similarity Threshold — {minScore.toFixed(2)}</label>
                      <input type="range" min={0} max={1} step={0.05} value={minScore} onChange={(e) => setMinScore(parseFloat(e.target.value))} className="w-full" />
                      <div className="flex justify-between text-[10px] text-muted-foreground">
                        <span>0.00 (all results)</span>
                        <span>1.00 (exact match)</span>
                      </div>
                    </div>
                  )}
                  {useAgent && (
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium">Max Iterations</label>
                      <input type="number" min={1} max={10} value={isNaN(maxIterations) ? "" : maxIterations}
                        onChange={(e) => { const v = e.target.value; if (v === "") { setMaxIterations(NaN); return } const n = parseInt(v); if (!isNaN(n)) setMaxIterations(Math.max(1, Math.min(10, n))) }}
                        onBlur={() => { if (isNaN(maxIterations)) setMaxIterations(3) }}
                        className="w-full h-8 rounded-md border border-input bg-background px-2 text-xs"
                      />
                    </div>
                  )}
                </div>
              </SheetContent>
            </Sheet>
          </div>
        </div>

        {/* Input area */}
        <div className="flex items-end gap-3">
          <input ref={fileRef} type="file" multiple accept=".pdf,.txt,.md,.docx,.xlsx,.pptx" className="hidden" onChange={handleFileAttach} />

          <textarea
            className="flex-1 resize-none border-0 border-b border-border px-0 py-2.5 text-sm min-h-[40px] max-h-[120px] outline-none bg-transparent leading-[1.7] focus:border-primary"
            style={{ fontFamily: "var(--font-serif)", fontStyle: "italic", color: "var(--ze-text)", borderRadius: 0 }}
            placeholder="Ask about your documents…"
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isStreaming}
          />

          <button
            type="button"
            className="shrink-0 flex items-center gap-1.5 cursor-pointer transition-opacity border-none text-white font-sans"
            style={{
              background: "var(--ze-green)",
              fontSize: "10px", fontWeight: 600,
              textTransform: "uppercase", letterSpacing: "0.12em",
              padding: "8px 16px", borderRadius: "2px",
              opacity: !input.trim() || isStreaming ? 0.3 : 1,
            }}
            onClick={handleSend}
            disabled={!input.trim() || isStreaming}
          >
            Send
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="5" y1="12" x2="19" y2="12" /><polyline points="12 5 19 12 12 19" /></svg>
          </button>
        </div>
      </div>
    </div>
  )
}
