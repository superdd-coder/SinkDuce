import { useState, useRef, useEffect, type KeyboardEvent } from "react"
import { createPortal } from "react-dom"
import { Sparkles } from "lucide-react"
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
  const [thinking, setThinking] = useState(() => persisted("thinking", true))
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
  const { sendMessage, stopGeneration } = useStreamChat()
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
  useEffect(() => { localStorage.setItem("chat_thinking", JSON.stringify(thinking)) }, [thinking])

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
    await sendMessage(text, thinking)
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

          {/* Thinking toggle — solid when ON */}
          <button
            type="button"
            className={`flex items-center gap-1.5 cursor-pointer border-none font-sans transition-all ${thinking ? "bg-primary text-primary-foreground" : "bg-transparent text-muted-foreground hover:text-primary"}`}
            style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", padding: thinking ? "3px 8px" : "0", borderRadius: "2px" }}
            onClick={() => setThinking(!thinking)}
            title={thinking ? "Deep thinking ON — slower, more thorough" : "Deep thinking OFF — faster responses"}
          >
            <Sparkles className="h-3 w-3" />
            Think
          </button>

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

          {isStreaming ? (
            <button
              type="button"
              className="shrink-0 flex items-center gap-1.5 cursor-pointer border-none text-white font-sans"
              style={{
                background: "oklch(0.55 0.18 20)",
                fontSize: "10px", fontWeight: 600,
                textTransform: "uppercase", letterSpacing: "0.12em",
                padding: "8px 16px", borderRadius: "2px",
              }}
              onClick={stopGeneration}
            >
              Stop
              <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2" /></svg>
            </button>
          ) : (
            <button
              type="button"
              className="shrink-0 flex items-center gap-1.5 cursor-pointer transition-opacity border-none text-white font-sans"
              style={{
                background: "var(--ze-green)",
                fontSize: "10px", fontWeight: 600,
                textTransform: "uppercase", letterSpacing: "0.12em",
                padding: "8px 16px", borderRadius: "2px",
                opacity: !input.trim() ? 0.3 : 1,
              }}
              onClick={handleSend}
              disabled={!input.trim()}
            >
              Send
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="5" y1="12" x2="19" y2="12" /><polyline points="12 5 19 12 12 19" /></svg>
            </button>
          )}
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
