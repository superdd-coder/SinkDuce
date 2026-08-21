import { useState, useRef, useEffect, type KeyboardEvent } from "react"
import { createPortal } from "react-dom"
import { useShallow } from "zustand/react/shallow"
import { Globe, Sparkles } from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import { useStreamChat } from "@/hooks/use-stream"
import { uploadFiles } from "@/api/client"
import { Button } from "@/components/ui/button"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { createImeEnterGuard } from "@/lib/ime"
import { useT } from "@/i18n/use-t"
import { formatApiError } from "@/api/http"
import {
  loadWebSearchForSession,
  setSessionWebSearch,
} from "@/lib/session-web-search"
import {
  clearWebSearchAlwaysAllow,
  setWebSearchConfirmAnchor,
  getWebSearchConfirmAnchor,
} from "@/lib/web-search-confirm"

function getComposerStillOurs(el: HTMLDivElement | null) {
  return !!el && getWebSearchConfirmAnchor() === el
}

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
  const t = useT()
  const [input, setInput] = useState("")
  const [showCollections, setShowCollections] = useState(false)
  const [thinking, setThinking] = useState(() => persisted("thinking", true))
  const sessionId = useAppStore((s) => s.sessionId)
  const [webSearch, setWebSearch] = useState(() => loadWebSearchForSession(null))
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
  } = useAppStore(
    useShallow((s) => ({
      isStreaming: s.isStreaming,
      activeCollection: s.activeCollection,
      collections: s.collections,
      fetchCollections: s.fetchCollections,
      selectedCollections: s.selectedCollections,
      toggleCollection: s.toggleCollection,
      activeProvider: s.activeProvider,
      activeModel: s.activeModel,
      setActiveModel: s.setActiveModel,
      providers: s.providers,
    }))
  )
  const { sendMessage, stopGeneration } = useStreamChat()
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const imeGuardRef = useRef(createImeEnterGuard())
  const fileRef = useRef<HTMLInputElement>(null)
  const collectionMenuRef = useRef<HTMLDivElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const [showProviderMenu, setShowProviderMenu] = useState(false)
  const [hoveredProvider, setHoveredProvider] = useState<string | null>(null)
  const providerMenuRef = useRef<HTMLDivElement>(null)
  const providerDropdownRef = useRef<HTMLDivElement>(null)
  const providerButtonRef = useRef<HTMLButtonElement>(null)
  /** Force re-measure on open (layout can shift). */
  const [, setMenuTick] = useState(0)

  useEffect(() => { fetchCollections() }, [fetchCollections])
  useEffect(() => { localStorage.setItem("chat_thinking", JSON.stringify(thinking)) }, [thinking])

  useEffect(() => {
    setWebSearch(loadWebSearchForSession(sessionId))
  }, [sessionId])

  const toggleWebSearch = () => {
    setWebSearch((prev) => {
      const next = !prev
      const sid = useAppStore.getState().sessionId
      setSessionWebSearch(sid, next)
      if (!next) clearWebSearchAlwaysAllow(sid)
      return next
    })
  }

  useEffect(() => {
    const el = textareaRef.current
    if (el) {
      el.style.height = "auto"
      /* Cap growth so the floating card stays compact */
      el.style.height = Math.min(el.scrollHeight, 96) + "px"
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

  // Reposition open menus on scroll/resize
  useEffect(() => {
    if (!showCollections && !showProviderMenu) return
    const bump = () => setMenuTick((t) => t + 1)
    window.addEventListener("resize", bump)
    window.addEventListener("scroll", bump, true)
    return () => {
      window.removeEventListener("resize", bump)
      window.removeEventListener("scroll", bump, true)
    }
  }, [showCollections, showProviderMenu])

  const readyProviders = providers.filter((p) => (p.status === "ready" || p.status === "unknown" || !p.status))
  const providerList = readyProviders.length > 0 ? readyProviders : providers
  const currentProvider = activeProvider
    ? providerList.find((p) => p.id === activeProvider) || providers.find((p) => p.id === activeProvider)
    : providerList.find((p) => p.is_default) || providerList[0]

  const handleSend = async () => {
    const text = input.trim()
    if (!text || isStreaming) return
    setInput("")
    await sendMessage(text, thinking, webSearch)
  }

  const handleKeyDown = (e: KeyboardEvent) => {
    if (!imeGuardRef.current.isSubmitEnter(e)) return
    e.preventDefault()
    void handleSend()
  }

  const handleFileAttach = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files?.length) return
    try {
      const res = await uploadFiles(files, activeCollection)
      toast.success(res.message)
    } catch (err) {
      toast.error(t("chat.uploadFailed", { error: formatApiError(err, t) }))
    }
    if (fileRef.current) fileRef.current.value = ""
  }

  const collectionLabel = selectedCollections.length === 0
    ? t("chat.allCollections")
    : selectedCollections.length === 1
      ? t("chat.nCollection", { n: 1 })
      : t("chat.nCollections", { n: selectedCollections.length })

  const composerRef = useRef<HTMLDivElement>(null)
  const sidebarView = useAppStore((s) => s.sidebarView)
  useEffect(() => {
    if (sidebarView !== "chat") return
    const el = composerRef.current
    if (el) setWebSearchConfirmAnchor(el, sessionId)
    return () => {
      if (getComposerStillOurs(el)) setWebSearchConfirmAnchor(null)
    }
  }, [sidebarView, sessionId])

  const modelLabel =
    activeModel ||
    currentProvider?.default_model ||
    currentProvider?.model ||
    currentProvider?.name ||
    t("chat.defaultProvider")

  const colBtnRect = buttonRef.current?.getBoundingClientRect()
  const colHostRect = collectionMenuRef.current?.getBoundingClientRect()
  const provHostRect = providerMenuRef.current?.getBoundingClientRect()

  return (
    <div
      ref={composerRef}
      className={cn(
        "pm-chat-composer sk-input-frame",
        isStreaming && "is-streaming",
      )}
    >
      {/* Toolbar — collection · Think · Web · model */}
      <div className="pm-chat-composer-tools">
        {/* Collection selector — original diamond + green wipe, rounded shell */}
        <div className="relative shrink-0" ref={collectionMenuRef}>
          <button
            type="button"
            ref={buttonRef}
            onClick={() => {
              setShowCollections((v) => !v)
              setMenuTick((t) => t + 1)
            }}
            className={cn(
              "pm-chat-tool-chip pm-chat-tool-chip--wipe pm-chat-tool-chip--collections",
              selectedCollections.length > 0 && "is-on",
              showCollections && "is-menu-open",
            )}
          >
            {/* Sizer locks width to "All collections" so N collections doesn't resize the chip */}
            <span className="pm-chat-tool-chip-label pm-chat-tool-chip-label--fixed">
              <span className="pm-chat-tool-chip-label-sizer" aria-hidden>
                {t("chat.allCollections")}
              </span>
              <span className="pm-chat-tool-chip-label-text">{collectionLabel}</span>
            </span>
            <span className="pm-chat-tool-chip-wipe" aria-hidden />
          </button>
          {createPortal(
            <div
              ref={dropdownRef}
              className={cn(
                "pm-chat-pop pm-chat-pop--collections",
                showCollections && "is-open",
              )}
              style={{
                width: colBtnRect ? Math.max(colBtnRect.width, 180) : 180,
                minWidth: 155,
                bottom: colHostRect
                  ? Math.max(8, window.innerHeight - colHostRect.top + 6)
                  : 0,
                left: colHostRect ? colHostRect.left : 0,
              }}
            >
              {collections.length === 0 ? (
                <div className="pm-chat-pop-empty">{t("chat.noCollections")}</div>
              ) : (
                <div className="pm-chat-pop-scroll">
                  {collections.map((col) => (
                    <label
                      key={col.id}
                      onClick={() => toggleCollection(col.id)}
                      className="pm-chat-pop-item group"
                    >
                      <span className="pm-chat-pop-item-label is-wrap">
                        <span
                          className={cn(
                            "sk-diamond",
                            selectedCollections.includes(col.id) && "on",
                          )}
                          aria-hidden
                        />
                        <span className="pm-chat-pop-item-name">{col.name}</span>
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

        <span className="pm-chat-tool-sep" aria-hidden />

        {/* Think — flow layers always mounted; .is-on fades in (no hard cut) */}
        <button
          type="button"
          className={cn(
            "pm-chat-tool-chip pm-chat-tool-chip--flow",
            thinking && "is-on",
          )}
          onClick={() => setThinking(!thinking)}
          title={
            thinking
              ? t("chat.thinkOn")
              : t("chat.thinkOff")
          }
        >
          <Sparkles className="size-3" />
          {t("chat.think")}
        </button>

        <span className="pm-chat-tool-sep" aria-hidden />

        {/* Web — same enter/exit flow fade as Think */}
        <button
          type="button"
          className={cn(
            "pm-chat-tool-chip pm-chat-tool-chip--flow",
            webSearch && "is-on",
          )}
          onClick={toggleWebSearch}
          title={
            webSearch
              ? t("chat.webOn")
              : t("chat.webOff")
          }
        >
          <Globe className="size-3" />
          {t("chat.web")}
        </button>

        <span className="pm-chat-tool-sep" aria-hidden />

        {/* Provider / model cascade — original wipe + slide-in models, rounded premium */}
        <div className="relative shrink-0" ref={providerMenuRef}>
          <button
            type="button"
            ref={providerButtonRef}
            onClick={() => {
              setShowProviderMenu((v) => !v)
              setHoveredProvider(null)
              setMenuTick((t) => t + 1)
            }}
            className={cn(
              "pm-chat-tool-chip pm-chat-tool-chip--wipe",
              activeProvider && "is-on",
              showProviderMenu && "is-menu-open",
            )}
          >
            <span className="pm-chat-tool-chip-label truncate max-w-[9rem]">
              {modelLabel}
            </span>
            <span className="pm-chat-tool-chip-wipe" aria-hidden />
          </button>
          {createPortal(
            <div
              ref={providerDropdownRef}
              className={cn(
                "pm-chat-pop pm-chat-pop--cascade",
                showProviderMenu && "is-open",
              )}
              style={{
                bottom: provHostRect
                  ? Math.max(8, window.innerHeight - provHostRect.top + 6)
                  : 0,
                left: provHostRect ? provHostRect.left : 0,
              }}
            >
              <div
                className={cn(
                  "pm-chat-pop-col",
                  hoveredProvider && "is-split",
                )}
              >
                <button
                  type="button"
                  className={cn(
                    "pm-chat-pop-item group",
                    !activeProvider && "is-active",
                  )}
                  onMouseEnter={() => setHoveredProvider(null)}
                  onClick={() => {
                    useAppStore.getState().setActiveProvider(null)
                    setActiveModel(null)
                    setShowProviderMenu(false)
                  }}
                >
                  <span className="pm-chat-pop-item-label">Default</span>
                  <span className="pm-chat-pop-item-wipe" aria-hidden />
                </button>
                {(readyProviders.length > 0 ? readyProviders : providers).map((p) => {
                  const provModels =
                    p.selected_models && p.selected_models.length > 0
                      ? p.selected_models
                      : p.model
                        ? [p.model]
                        : []
                  const isActive = activeProvider === p.id && !hoveredProvider
                  const isHover = hoveredProvider === p.id
                  return (
                    <button
                      key={p.id}
                      type="button"
                      className={cn(
                        "pm-chat-pop-item group",
                        isActive && "is-active",
                        isHover && "is-hover",
                      )}
                      onMouseEnter={() => setHoveredProvider(p.id)}
                    >
                      <span className="pm-chat-pop-item-label">
                        <span className="pm-chat-pop-item-name truncate">
                          {p.name || p.model}
                        </span>
                      </span>
                      {provModels.length > 0 && (
                        <span className="pm-chat-pop-item-arrow">→</span>
                      )}
                      <span className="pm-chat-pop-item-wipe" aria-hidden />
                    </button>
                  )
                })}
                {providers.length === 0 && (
                  <div className="pm-chat-pop-empty">
                    No providers — configure in Settings
                  </div>
                )}
              </div>

              <div
                className={cn(
                  "pm-chat-pop-models",
                  hoveredProvider && "is-open",
                )}
              >
                <div className="pm-chat-pop-models-inner">
                  {(() => {
                    const list = readyProviders.length > 0 ? readyProviders : providers
                    const hp = hoveredProvider
                      ? list.find((p) => p.id === hoveredProvider)
                      : null
                    const allModels = hp
                      ? hp.selected_models && hp.selected_models.length > 0
                        ? hp.selected_models
                        : hp.model
                          ? [hp.model]
                          : []
                      : []
                    const fcIds =
                      (hp as { function_call_model_ids?: string[] } | null)
                        ?.function_call_model_ids ?? []
                    const models = allModels.filter(
                      (m: string) => fcIds.length === 0 || fcIds.includes(m),
                    )
                    if (models.length === 0) return null
                    return models.map((m) => (
                      <button
                        key={m}
                        type="button"
                        className={cn(
                          "pm-chat-pop-item group",
                          activeProvider === hoveredProvider &&
                            activeModel === m &&
                            "is-active",
                        )}
                        onClick={() => {
                          if (hoveredProvider) {
                            useAppStore.getState().setActiveProvider(hoveredProvider)
                            setActiveModel(m)
                          }
                          setShowProviderMenu(false)
                        }}
                      >
                        <span className="pm-chat-pop-item-label">
                          <span className="pm-chat-pop-item-name truncate">{m}</span>
                        </span>
                        <span className="pm-chat-pop-item-wipe" aria-hidden />
                      </button>
                    ))
                  })()}
                </div>
              </div>
            </div>,
            document.body,
          )}
        </div>
      </div>

      {/* Input + send on one row — keeps card short (QC-like density) */}
      <div className="pm-chat-composer-main">
        <input
          ref={fileRef}
          type="file"
          multiple
          accept=".pdf,.txt,.md,.docx,.xlsx,.pptx"
          className="hidden"
          onChange={handleFileAttach}
        />
        <div className="pm-chat-composer-field">
          <textarea
            ref={textareaRef}
            className="pm-chat-composer-textarea"
            placeholder={t("chat.askPlaceholder")}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onCompositionStart={() => imeGuardRef.current.onCompositionStart()}
            onCompositionEnd={() => {
              imeGuardRef.current.onCompositionEnd()
              requestAnimationFrame(() => imeGuardRef.current.clearJustEnded())
            }}
            disabled={isStreaming}
          />
          {isStreaming && (
            <span className="sk-stream-cursor absolute right-1 bottom-2" aria-hidden />
          )}
        </div>
        {isStreaming ? (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            className="pm-chat-composer-send shrink-0"
            onClick={stopGeneration}
          >
            {t("common.cancel")}
          </Button>
        ) : (
          <Button
            type="button"
            variant="default"
            size="xs"
            className="pm-chat-composer-send shrink-0"
            onClick={handleSend}
            disabled={!input.trim()}
          >
            {t("common.send")}
            <svg
              width="11"
              height="11"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              aria-hidden
            >
              <line x1="5" y1="12" x2="19" y2="12" />
              <polyline points="12 5 19 12 12 19" />
            </svg>
          </Button>
        )}
      </div>

      <p className="pm-chat-composer-disclaimer">
        {t("chat.disclaimer")}
      </p>
    </div>
  )
}
