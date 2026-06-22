import { useState, useRef, useEffect, type KeyboardEvent } from "react"
import { createPortal } from "react-dom"
import { Bot, Sparkles, Settings } from "lucide-react"
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
  const [sparseLlmTokenize, setSparseLlmTokenize] = useState(() => persisted("sparseLlmTokenize", true))
  const getDefaultTopK = (rerankerOn: boolean) => rerankerOn ? 15 : 5
  const [useReranker, setUseReranker] = useState(() => persisted("useReranker", true))
  const [topK, setTopK] = useState(() => persisted("topK", getDefaultTopK(persisted("useReranker", true))))
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
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const collectionMenuRef = useRef<HTMLDivElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const [showProviderMenu, setShowProviderMenu] = useState(false)
  const [hoveredProvider, setHoveredProvider] = useState<string | null>(null)
  const providerMenuRef = useRef<HTMLDivElement>(null)
  const providerDropdownRef = useRef<HTMLDivElement>(null)
  const providerButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => { fetchCollections() }, [fetchCollections])
  useEffect(() => { localStorage.setItem("chat_useAgent", JSON.stringify(useAgent)) }, [useAgent])
  useEffect(() => { localStorage.setItem("chat_searchMode", JSON.stringify(searchMode)) }, [searchMode])
  useEffect(() => { localStorage.setItem("chat_sparseLlmTokenize", JSON.stringify(sparseLlmTokenize)) }, [sparseLlmTokenize])
  useEffect(() => { if (!isNaN(topK)) localStorage.setItem("chat_topK", JSON.stringify(topK)) }, [topK])
  useEffect(() => { localStorage.setItem("chat_useReranker", JSON.stringify(useReranker)) }, [useReranker])
  useEffect(() => { if (!isNaN(maxIterations)) localStorage.setItem("chat_maxIterations", JSON.stringify(maxIterations)) }, [maxIterations])
  useEffect(() => { if (!isNaN(rerankTopK)) localStorage.setItem("chat_rerankTopK", JSON.stringify(rerankTopK)) }, [rerankTopK])
  useEffect(() => { localStorage.setItem("chat_minScore", JSON.stringify(minScore)) }, [minScore])

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current
    if (el) {
      el.style.height = "auto"
      el.style.height = Math.min(el.scrollHeight, 160) + "px"
    }
  }, [input])

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
    const handleClickOutside = (e: MouseEvent) => {
      if (
        providerMenuRef.current && !providerMenuRef.current.contains(e.target as Node) &&
        providerDropdownRef.current && !providerDropdownRef.current.contains(e.target as Node)
      ) {
        setShowProviderMenu(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  const readyProviders = providers.filter((p) => p.status === "ready" || !p.status)
  const currentProvider = activeProvider
    ? readyProviders.find((p) => p.id === activeProvider)
    : readyProviders.find((p) => p.is_default) || readyProviders[0]
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
      sparse_llm_tokenize: sparseLlmTokenize,
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
    <div className="px-12 pb-5 pt-1">
      <div
        className="max-w-3xl mx-auto space-y-2.5 bg-background/60 backdrop-blur-md border px-5 py-3"
        style={{
          borderRadius: "4px",
          borderColor: "oklch(0.38 0.08 160 / 0.25)",
          boxShadow: "0 -8px 30px -4px rgba(0,0,0,0.06), 0 2px 8px -2px rgba(0,0,0,0.03), 0 0 18px -4px oklch(0.38 0.08 160 / 0.12)",
        }}
      >
        {/* Toolbar */}
        <div className="flex items-center gap-4 overflow-hidden text-[10px] font-medium uppercase tracking-[0.1em]">
          {/* Collection selector */}
          <div className="relative" ref={collectionMenuRef}>
            <button
              type="button"
              ref={buttonRef}
              onClick={() => setShowCollections(!showCollections)}
              className="group relative flex items-center justify-center overflow-hidden rounded px-3 py-2 font-sans transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)]"
              style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", minWidth: "155px", color: showCollections ? "var(--color-primary-foreground)" : selectedCollections.length > 0 ? "var(--color-primary)" : "var(--color-muted-foreground)" }}
            >
              <span className="relative z-10 whitespace-nowrap text-center">
                {collectionLabel}
              </span>
              <span
                className="absolute inset-0 z-0 transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] bg-primary"
                style={{
                  transform: showCollections ? "scaleX(1)" : "scaleX(0)",
                  transformOrigin: showCollections ? "right" : "left",
                }}
              />
            </button>
            {createPortal(
              <div
                ref={dropdownRef}
                className={`fixed z-[100] flex-col items-center overflow-hidden rounded border border-primary/30 bg-popover/60 backdrop-blur-md shadow-lg transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] ${
                  showCollections
                    ? "opacity-100 visible translate-y-0 pointer-events-auto"
                    : "opacity-0 invisible translate-y-3 pointer-events-none"
                }`}
                style={{
                  width: buttonRef.current ? buttonRef.current.getBoundingClientRect().width : "auto",
                  bottom: collectionMenuRef.current ? window.innerHeight - collectionMenuRef.current.getBoundingClientRect().top + 4 : 0,
                  left: collectionMenuRef.current ? collectionMenuRef.current.getBoundingClientRect().left : 0,
                }}
              >
                {collections.map((col) => (
                  <label
                    key={col.id}
                    onClick={() => toggleCollection(col.id)}
                    className="relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group"
                  >
                    <span className="relative z-10 flex items-center gap-2 px-2 py-2 w-full text-[10px]">
                      {selectedCollections.includes(col.id) ? (
                        <span className="w-1.5 h-1.5 bg-primary group-hover:bg-primary-foreground rotate-45 shrink-0 transition-colors duration-500" />
                      ) : (
                        <span className="w-1.5 h-1.5 shrink-0" />
                      )}
                      <span className="whitespace-normal break-words min-w-0 leading-snug">{col.name}</span>
                    </span>
                    <span className="absolute inset-0 z-0 bg-primary transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
                  </label>
                ))}
              </div>,
              document.body
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
                ? "bg-primary/50 text-primary-foreground/60 cursor-not-allowed"
                : useReranker
                  ? "bg-primary text-primary-foreground"
                  : "bg-transparent text-muted-foreground hover:text-primary"
            }`}
            style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", padding: (useAgent || useReranker) ? "3px 8px" : "0", borderRadius: "2px" }}
            disabled={useAgent}
            onClick={() => {
              if (!useAgent) {
                const next = !useReranker
                setUseReranker(next)
                // Auto-adjust Top K when toggling Reranker:
                // ON → switch from 5 to 15 (more candidates for rerank)
                // OFF → switch from 15 to 5 (less candidates needed)
                setTopK(prev => {
                  const n = isNaN(prev) ? 0 : prev
                  if (next && n <= 5) return 15
                  if (!next && n >= 15) return 5
                  return n
                })
              }
            }}
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

          <div className="w-px h-3 bg-border hidden lg:block" />

          {/* Provider/Model cascading menu */}
          {readyProviders.length > 0 && (
            <div className="relative hidden lg:block" ref={providerMenuRef}>
              <button
                type="button"
                ref={providerButtonRef}
                onClick={() => { setShowProviderMenu(!showProviderMenu); setHoveredProvider(null) }}
                className="group relative flex items-center overflow-hidden rounded px-3 py-2 font-sans transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)]"
                style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", minWidth: "160px", color: showProviderMenu ? "var(--color-primary-foreground)" : activeProvider ? "var(--color-primary)" : "var(--color-muted-foreground)" }}
              >
                <span className="relative z-10 whitespace-nowrap">
                  {activeModel || currentProvider?.default_model || currentProvider?.model || currentProvider?.name || "Default provider"}
                </span>
                <span
                  className="absolute inset-0 z-0 transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] bg-primary"
                  style={{ transform: showProviderMenu ? "scaleX(1)" : "scaleX(0)", transformOrigin: showProviderMenu ? "right" : "left" }}
                />
              </button>
              {createPortal(
                <div
                  ref={providerDropdownRef}
                  className={`fixed z-[100] flex overflow-hidden rounded border border-primary/30 bg-popover/60 backdrop-blur-md shadow-lg transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] ${
                    showProviderMenu ? "opacity-100 visible translate-y-0 pointer-events-auto" : "opacity-0 invisible translate-y-3 pointer-events-none"
                  }`}
                  style={{
                    bottom: providerMenuRef.current ? window.innerHeight - providerMenuRef.current.getBoundingClientRect().top + 4 : 0,
                    left: providerMenuRef.current ? providerMenuRef.current.getBoundingClientRect().left : 0,
                  }}
                >
                  {/* Left: provider list */}
                  <div className={`flex flex-col flex-1 min-w-[160px] ${hoveredProvider ? "border-r border-primary/20" : ""}`}>
                    <button
                      type="button"
                      className="group relative flex items-center overflow-hidden px-3 py-2 text-[10px] font-medium uppercase tracking-[0.08em] text-left whitespace-nowrap w-full"
                      onMouseEnter={() => setHoveredProvider(null)}
                      onClick={() => {
                        useAppStore.getState().setActiveProvider(null)
                        setActiveModel(null)
                        setShowProviderMenu(false)
                      }}
                    >
                      <span className="relative z-10 text-muted-foreground group-hover:text-primary-foreground transition-colors duration-500">Default</span>
                      <span className="absolute inset-0 z-0 bg-primary transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100" />
                    </button>
                    {readyProviders.map((p) => {
                      const provModels = p.selected_models && p.selected_models.length > 0
                        ? p.selected_models
                        : p.model ? [p.model] : []
                      const isActive = activeProvider === p.id && !hoveredProvider
                      return (
                        <button
                          key={p.id}
                          type="button"
                          className="group relative flex items-center justify-between overflow-hidden px-3 py-2 text-[10px] font-medium uppercase tracking-[0.08em] text-left whitespace-nowrap w-full"
                          onMouseEnter={() => setHoveredProvider(p.id)}
                        >
                          <span className={`relative z-10 transition-colors duration-500 ${isActive ? "text-primary" : hoveredProvider === p.id ? "text-primary-foreground" : "text-muted-foreground group-hover:text-primary-foreground"}`}>
                            {p.name || p.model}
                          </span>
                          {provModels.length > 0 && <span className={`relative z-10 text-[8px] transition-opacity duration-500 ${hoveredProvider === p.id ? "opacity-60" : "opacity-40 group-hover:opacity-60"}`}>→</span>}
                          <span className={`absolute inset-0 z-0 bg-primary transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] origin-left ${hoveredProvider === p.id ? "scale-x-100" : "scale-x-0 group-hover:scale-x-100"}`} />
                        </button>
                      )
                    })}
                  </div>
                  {/* Right: model list — slides in on hover */}
                  <div
                    className={`flex flex-col overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] ${
                      hoveredProvider
                        ? "max-w-[200px] opacity-100"
                        : "max-w-0 opacity-0"
                    }`}
                  >
                    <div className="min-w-[140px]">
                      {(() => {
                        const hp = hoveredProvider ? readyProviders.find(p => p.id === hoveredProvider) : null
                        const models = hp
                          ? (hp.selected_models && hp.selected_models.length > 0 ? hp.selected_models : hp.model ? [hp.model] : [])
                          : []
                        if (models.length === 0) return null
                        return models.map((m) => (
                          <button
                            key={m}
                            type="button"
                            className="group relative block w-full overflow-hidden px-3 py-2 text-[10px] font-medium uppercase tracking-[0.08em] text-left whitespace-nowrap"
                            onClick={() => {
                              if (hoveredProvider) {
                                useAppStore.getState().setActiveProvider(hoveredProvider)
                                setActiveModel(m)
                              }
                              setShowProviderMenu(false)
                            }}
                          >
                            <span className={`relative z-10 transition-colors duration-500 ${activeProvider === hoveredProvider && activeModel === m ? "text-primary group-hover:text-primary-foreground" : "text-muted-foreground group-hover:text-primary-foreground"}`}>
                              {m}
                            </span>
                            <span className="absolute inset-0 z-0 bg-primary transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100" />
                          </button>
                        ))
                      })()}
                    </div>
                  </div>
                </div>,
                document.body
              )}
            </div>
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
                    <label className="text-xs font-medium">Top K — chunks to retrieve</label>
                    <input type="number" min={1} max={50} value={isNaN(topK) ? "" : topK}
                      onChange={(e) => { const v = e.target.value; if (v === "") { setTopK(NaN); return } const n = parseInt(v); if (!isNaN(n)) setTopK(Math.max(1, Math.min(50, n))) }}
                      onBlur={() => { if (isNaN(topK)) setTopK(getDefaultTopK(useReranker)) }}
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
            ref={textareaRef}
            className="flex-1 resize-none border-0 border-b border-border px-0 py-2.5 text-sm min-h-[40px] max-h-[160px] outline-none bg-transparent leading-[1.7] focus:border-primary"
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

        {/* Disclaimer */}
        <p
          className="text-center select-none"
          style={{
            fontSize: "10px",
            fontWeight: 400,
            color: "oklch(0.38 0.07 160 / 0.85)",
            fontFamily: "var(--font-serif)",
            fontStyle: "italic",
          }}
        >
          AI-generated answers may contain errors. Please verify critical information.
        </p>
      </div>
    </div>
  )
}
