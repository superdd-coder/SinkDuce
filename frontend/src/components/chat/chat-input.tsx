import { useReportReview } from "@/hooks/use-report-review"
import { canSendReview } from "@/lib/report-review"
import { ReportReviewChip } from "./report-review-chip"
import { useState, useRef, useEffect, type KeyboardEvent } from "react"
import { createPortal } from "react-dom"
import { useShallow } from "zustand/react/shallow"
import { ArrowUp, ChevronDown, ChevronRight, Globe } from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import { useStreamChat } from "@/hooks/use-stream"
import { useChatAttachments } from "@/hooks/use-chat-attachments"
import { ChatAttachmentDrafts, ChatAttachmentPicker } from "./chat-attachments"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { createImeEnterGuard } from "@/lib/ime"
import { chatModelLabel } from "@/lib/chat-model-label"
import { useT } from "@/i18n/use-t"
import {
  clearWebSearchAlwaysAllow,
  setWebSearchConfirmAnchor,
  getWebSearchConfirmAnchor,
} from "@/lib/web-search-confirm"

function getComposerStillOurs(el: HTMLDivElement | null) {
  return !!el && getWebSearchConfirmAnchor() === el
}

/** Models the composer may target: the provider's curated list, narrowed to
    function-calling models when that subset is flagged in Settings. */
function providerChatModels(p: {
  model?: string
  selected_models?: string[]
  function_call_model_ids?: string[]
}): string[] {
  const all =
    p.selected_models && p.selected_models.length > 0
      ? p.selected_models
      : p.model
        ? [p.model]
        : []
  const fc = p.function_call_model_ids ?? []
  return fc.length === 0 ? all : all.filter((m) => fc.includes(m))
}

/** Full-open cascade width (provider col + models col + chrome). A fixed
    popup anchored by `left` never reflows to fit the viewport, so the
    positioning below reserves this up front. */
const PROVIDER_POP_OPEN_W = 400

export function ChatInput() {
  const t = useT()
  const [input, setInput] = useState("")
  const [showCollections, setShowCollections] = useState(false)
  // 这两项住在 store：与 provider/model 一致，并随消息存进会话 config
  const thinking = useAppStore((s) => s.thinking)
  const setThinking = useAppStore((s) => s.setThinking)
  const webSearch = useAppStore((s) => s.webSearch)
  const setWebSearch = useAppStore((s) => s.setWebSearch)
  const sessionId = useAppStore((s) => s.sessionId)
  const {
    isStreaming,
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
  const attachments = useChatAttachments(sessionId, () => useAppStore.getState().ensureSessionHydrated(), isStreaming)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const imeGuardRef = useRef(createImeEnterGuard())
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

  /* The provider/model selection persists in localStorage but the config it
     points into changes underneath it (retired model snapshots, edited
     provider cards). Re-validate whenever providers refresh so the chip and
     the send path never reference a model the config no longer has. */
  useEffect(() => {
    if (providers.length === 0) return
    const st = useAppStore.getState()
    const prov = st.activeProvider
      ? st.providers.find((p) => p.id === st.activeProvider)
      : null
    if (st.activeProvider && !prov) {
      /* Provider deleted — drop the whole selection; defaults re-seed on load. */
      st.setActiveProvider(null)
      st.setActiveModel(null)
      return
    }
    if (prov) {
      const models = providerChatModels(prov)
      if (st.activeModel && models.length > 0 && !models.includes(st.activeModel)) {
        st.setActiveModel(
          prov.default_model && models.includes(prov.default_model)
            ? prov.default_model
            : models[0],
        )
      }
    } else if (st.activeModel) {
      /* A pinned model without a provider is dead weight — send ignores it. */
      st.setActiveModel(null)
    }
  }, [providers])

  const toggleWebSearch = () => {
    const next = !useAppStore.getState().webSearch
    setWebSearch(next)
    // 关掉 Web 搜索时，顺手清掉该会话的"始终允许"记录
    if (!next) clearWebSearchAlwaysAllow(useAppStore.getState().sessionId)
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
    const bump = () => {
      if (showProviderMenu && providerMenuRef.current?.getClientRects().length === 0) {
        setShowProviderMenu(false)
        return
      }
      // Chat pins the thread while streaming; capture-phase scroll would
      // setState every token and hit React error #185 (max update depth).
      if (useAppStore.getState().isStreaming) return
      setMenuTick((t) => t + 1)
    }
    window.addEventListener("resize", bump)
    window.addEventListener("scroll", bump, true)
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(bump)
    if (composerRef.current) observer?.observe(composerRef.current)
    return () => {
      window.removeEventListener("resize", bump)
      window.removeEventListener("scroll", bump, true)
      observer?.disconnect()
    }
  }, [showCollections, showProviderMenu])

  const readyProviders = providers.filter((p) => (p.status === "ready" || p.status === "unknown" || !p.status))
  const providerList = readyProviders.length > 0 ? readyProviders : providers

  const review = useReportReview(sessionId, isStreaming)

  const handleSend = async () => {
    if (!canSendReview(input, attachments.drafts, review.hasComments) || isStreaming) return
    const text = input.trim()
    setInput("")
    await sendMessage(text, thinking, webSearch, attachments.ready)
  }

  const handleKeyDown = (e: KeyboardEvent) => {
    if (!imeGuardRef.current.isSubmitEnter(e)) return
    e.preventDefault()
    void handleSend()
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

  const modelLabel = chatModelLabel(activeProvider, activeModel, t("chat.defaultProvider"))

  const hoveredProviderObj = hoveredProvider
    ? providerList.find((p) => p.id === hoveredProvider)
    : null
  const hoveredModels = hoveredProviderObj ? providerChatModels(hoveredProviderObj) : []

  const colBtnRect = buttonRef.current?.getBoundingClientRect()
  const colHostRect = collectionMenuRef.current?.getBoundingClientRect()
  const provHostRect = providerMenuRef.current?.getBoundingClientRect()

  return (
    <div
      ref={composerRef}
      {...attachments.dropProps}
      className={cn(
        "pm-chat-composer",
        isStreaming && "is-streaming",
      )}
    >
      <ReportReviewChip comments={review.comments} disabled={isStreaming} />
      <ChatAttachmentDrafts manager={attachments} />
      {/* Input — single quiet line on top, tools docked below (ChatGPT-style) */}
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

      {/* Tool row — web toggle · collections · spacer · model · thinking · send */}
      <div className="pm-chat-composer-tools">
        <ChatAttachmentPicker manager={attachments} />
        {/* Web search — icon-only toggle, lights up green when on */}
        <button
          type="button"
          className={cn("pm-chat-tool-icon", webSearch && "is-on")}
          onClick={toggleWebSearch}
          title={webSearch ? t("chat.webOn") : t("chat.webOff")}
          aria-pressed={webSearch}
        >
          <Globe className="size-4" strokeWidth={1.75} />
        </button>

        {/* Collections — ghost pill, portal menu unchanged */}
        <div className="pm-chat-composer-collections relative min-w-0" ref={collectionMenuRef}>
          <button
            type="button"
            ref={buttonRef}
            onClick={() => {
              setShowCollections((v) => !v)
              setMenuTick((t) => t + 1)
            }}
            className={cn(
              "pm-chat-tool-chip",
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
            <ChevronDown className="size-3 opacity-60" strokeWidth={1.75} />
          </button>
          {createPortal(
            <div
              ref={dropdownRef}
              className={cn(
                "pm-chat-pop pm-chat-pop--collections",
                showCollections && "is-open",
              )}
              style={{
                // Wide enough for name + archived tag on collection items
                width: colBtnRect ? Math.max(colBtnRect.width, 230) : 230,
                minWidth: 210,
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
                      className={cn("pm-chat-pop-item group", col.archived && "is-archived")}
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
                        {col.archived && (
                          <span className="pm-chat-pop-archived-tag">
                            {t("library.archivedTag")}
                          </span>
                        )}
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

        <span className="flex-1" />

        {/* Provider / model cascade — two-level menu unchanged */}
        <div className="pm-chat-composer-model relative shrink-0" ref={providerMenuRef}>
          <button
            type="button"
            ref={providerButtonRef}
            onClick={() => {
              setShowProviderMenu((v) => !v)
              setHoveredProvider(null)
              setMenuTick((t) => t + 1)
            }}
            className={cn(
              "pm-chat-tool-chip",
              activeProvider && "is-on",
              showProviderMenu && "is-menu-open",
            )}
          >
            <span className="pm-chat-tool-chip-label truncate max-w-[9rem]">
              {modelLabel}
            </span>
            <ChevronDown className="size-3 opacity-60" strokeWidth={1.75} />
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
                /* The chip sits at the right edge of the tool row; anchoring
                   at its left edge would push the models column off-window.
                   Shift the pop left whenever the full-open cascade (models
                   column expanded) wouldn't fit. */
                left: provHostRect
                  ? Math.max(
                      8,
                      Math.min(
                        provHostRect.left,
                        window.innerWidth - PROVIDER_POP_OPEN_W - 8,
                      ),
                    )
                  : 0,
              }}
            >
              <div className="pm-chat-pop-col">
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
                {providerList.map((p) => {
                  const chatModels = providerChatModels(p)
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
                      onClick={() => {
                        /* WKWebView (desktop app) never delivers mouseenter,
                           so hover cannot be load-bearing: the first click pins
                           the submenu open; clicking the pinned row (or a model
                           in it) commits the provider at its default model. */
                        if (hoveredProvider !== p.id) {
                          setHoveredProvider(p.id)
                          return
                        }
                        useAppStore.getState().setActiveProvider(p.id)
                        setActiveModel(
                          p.default_model && chatModels.includes(p.default_model)
                            ? p.default_model
                            : chatModels[0] ?? p.model ?? null,
                        )
                        setShowProviderMenu(false)
                      }}
                    >
                      <span className="pm-chat-pop-item-label">
                        <span className="pm-chat-pop-item-name truncate">
                          {p.name || p.model}
                        </span>
                      </span>
                      {chatModels.length > 0 && (
                        <span className="pm-chat-pop-item-arrow" aria-hidden>
                          <ChevronRight className="size-3" strokeWidth={1.75} />
                        </span>
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

              {/* Mounted only while a provider is pinned — no width transition:
                  WKWebView freezes same-frame class-toggle expansions, so the
                  column must appear by mounting, not by animating open. */}
              {hoveredProvider && hoveredModels.length > 0 && (
                <div className="pm-chat-pop-models">
                  <div className="pm-chat-pop-models-inner">
                    {hoveredModels.map((m) => (
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
                    ))}
                  </div>
                </div>
              )}
            </div>,
            document.body,
          )}
        </div>

        {/* Thinking — simple click toggle */}
        <button
          type="button"
          className={cn("pm-chat-tool-chip pm-chat-composer-thinking", thinking && "is-on")}
          onClick={() => setThinking(!thinking)}
          title={thinking ? t("chat.thinkOn") : t("chat.thinkOff")}
          aria-pressed={thinking}
        >
          <span className="pm-chat-tool-chip-label">{t("chat.think")}</span>
        </button>

        {/* Connectivity probes are advisory; send reports failures, like Enter. */}
        {isStreaming ? (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            className="pm-chat-composer-cancel shrink-0"
            onClick={stopGeneration}
          >
            {t("common.cancel")}
          </Button>
        ) : (
          <Button
            type="button"
            variant="default"
            className="pm-chat-composer-send shrink-0"
            onClick={handleSend}
            disabled={!canSendReview(input, attachments.drafts, review.hasComments)}
            aria-label={t("common.send")}
          >
            <ArrowUp className="size-4" strokeWidth={2} />
          </Button>
        )}
      </div>
    </div>
  )
}
