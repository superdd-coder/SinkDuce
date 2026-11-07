import { useReportReview } from "@/hooks/use-report-review"
import { canSendReview } from "@/lib/report-review"
import { ReportReviewChip } from "./report-review-chip"
import { useState, useRef, useEffect, type KeyboardEvent } from "react"
import { useShallow } from "zustand/react/shallow"
import { ArrowUp, ChevronDown, ChevronRight, Cpu, Globe, ListOrdered, Play, Square, X } from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import { useStreamChat } from "@/hooks/use-stream"
import { useChatAttachments } from "@/hooks/use-chat-attachments"
import { ChatAttachmentDrafts, ChatAttachmentPicker } from "./chat-attachments"
import { Button } from "@/components/ui/button"
import { SoftMenu, MenuItem, MenuCheck } from "@/components/ui/menu"
import { MultiSelectMenu } from "@/components/ui/multi-select-menu"
import { cn } from "@/lib/utils"
import { createImeEnterGuard } from "@/lib/ime"
import { chatModelLabel } from "@/lib/chat-model-label"
import { reservedChatInputs } from "@/lib/chat-queue"
import { useT } from "@/i18n/use-t"
import { formatApiError } from "@/api/http"
import { toast } from "sonner"
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
    setSelectedCollections,
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
      setSelectedCollections: s.setSelectedCollections,
      activeProvider: s.activeProvider,
      activeModel: s.activeModel,
      setActiveModel: s.setActiveModel,
      providers: s.providers,
    }))
  )
  const { sendMessage, stopGeneration, resumeQueue } = useStreamChat()
  const queue = useAppStore(state => sessionId ? state.chatQueues[sessionId] : undefined)
  const attachments = useChatAttachments(sessionId, () => useAppStore.getState().ensureSessionHydrated(), isStreaming)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const imeGuardRef = useRef(createImeEnterGuard())
  const collectionMenuRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const [showProviderMenu, setShowProviderMenu] = useState(false)
  const [hoveredProvider, setHoveredProvider] = useState<string | null>(null)
  const providerMenuRef = useRef<HTMLDivElement>(null)
  const providerButtonRef = useRef<HTMLButtonElement>(null)

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

  const readyProviders = providers.filter((p) => (p.status === "ready" || p.status === "unknown" || !p.status))
  const providerList = readyProviders.length > 0 ? readyProviders : providers

  const review = useReportReview(sessionId, isStreaming)
  const reserved = reservedChatInputs(queue)
  const drafts = attachments.drafts.filter(draft => !draft.attachment || !reserved.attachments.has(draft.attachment.id))
  const ready = attachments.ready.filter(file => !reserved.attachments.has(file.id))
  const comments = review.comments.filter(comment => !reserved.comments.has(comment.id))
  const canSend = canSendReview(input, drafts, comments.length > 0)
  const generating = isStreaming || Boolean(queue?.running)
  const showStop = generating && !input.trim() && !canSend
  const actionLabel = t(showStop ? "chat.stopGenerating"
    : generating || queue?.pending.length ? "chat.queue.send" : "common.send")

  const showSendError = (error: unknown) => toast.error(t("chat.failed", { error: formatApiError(error, t) }))

  const handleSend = async () => {
    if (!canSend || showStop) return
    const text = input.trim()
    setInput("")
    textareaRef.current?.focus({ preventScroll: true })
    try {
      const accepted = await sendMessage(text, ready)
      if (!accepted && useAppStore.getState().sessionId === sessionId) setInput(draft => draft || text)
    } catch (error) {
      showSendError(error)
    }
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

  return (
    <div
      ref={composerRef}
      {...attachments.dropProps}
      className={cn(
        "pm-chat-composer",
        generating && "is-streaming",
      )}
    >
      {sessionId && !!queue?.pending.length && (
        <div className="pm-chat-queue">
          <div className="pm-chat-queue-head">
            <ListOrdered size={14} aria-hidden="true" />
            <span role="status">{t(queue.paused ? "chat.queue.paused" : "chat.queue.waiting", { count: queue.pending.length })}</span>
            {queue.paused && <Button variant="ghost" size="icon-xs" aria-label={t("chat.queue.resume")} title={t("chat.queue.resume")}
              onClick={() => void resumeQueue(sessionId).catch(showSendError)}><Play aria-hidden="true" /></Button>}
          </div>
          <ol className="pm-chat-queue-list" aria-label={t("chat.queue.title")}>
            {queue.pending.map((turn, index) => {
              const label = turn.content || turn.attachments.map(file => file.filename).join(", ") || t("chat.queue.review")
              return <li key={turn.id}>
                <span className="pm-chat-queue-number" aria-hidden="true">{index + 1}</span>
                <span className="pm-chat-queue-message" title={label}>{label}</span>
                <Button variant="ghost" size="icon-xs" aria-label={t("chat.queue.remove", { number: index + 1 })} title={t("common.remove")}
                  onClick={() => useAppStore.getState().setChatQueue(sessionId, current => current && {
                    ...current, pending: current.pending.filter(item => item.id !== turn.id),
                  })}><X aria-hidden="true" /></Button>
              </li>
            })}
          </ol>
        </div>
      )}
      <ReportReviewChip comments={comments} disabled={isStreaming} />
      <ChatAttachmentDrafts manager={{ ...attachments, drafts }} />
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
        />
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

        {/* Project scope — searchable multi-select with one selection summary. */}
        <div className="pm-chat-composer-collections relative min-w-0" ref={collectionMenuRef}>
          <button
            type="button"
            ref={buttonRef}
            aria-haspopup="dialog" aria-expanded={showCollections}
            onClick={() => {
              setShowCollections((v) => !v)
              setShowProviderMenu(false)
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
          <MultiSelectMenu open={showCollections} onClose={() => setShowCollections(false)} anchorRef={buttonRef}
            value={selectedCollections} onChange={setSelectedCollections} label={t("common.collections")}
            options={collections.map(col => ({ value: col.id, label: col.name, description: col.archived ? t("library.archivedTag") : undefined }))} />
        </div>

        <span className="flex-1" />

        {/* Provider and model share one adaptive connected surface. */}
        <div className="pm-chat-composer-model relative shrink-0" ref={providerMenuRef}>
          <button
            type="button"
            ref={providerButtonRef}
            aria-haspopup="menu" aria-expanded={showProviderMenu}
            onClick={() => {
              setShowProviderMenu((v) => !v)
              setShowCollections(false)
              setHoveredProvider(activeProvider || providerList[0]?.id || null)
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
          <SoftMenu open={showProviderMenu} portal connected anchorRef={providerButtonRef} align="end"
            onClose={() => setShowProviderMenu(false)} onSubmenuBack={() => setHoveredProvider(null)} autoFocus
            className="sd-provider-menu" rootWidth={220} childWidth={290} connectedPanelKey={hoveredProvider}
            connectedPanel={hoveredProvider && hoveredModels.length > 0 ? <>
              <div className="sd-menu-title">{t("menus.models")}</div>
              {hoveredModels.map(m => <MenuItem key={m} role="menuitemradio"
                aria-checked={activeProvider === hoveredProvider && activeModel === m}
                active={activeProvider === hoveredProvider && activeModel === m} title={m}
                onClick={() => {
                  useAppStore.getState().setActiveProvider(hoveredProvider)
                  setActiveModel(m); setShowProviderMenu(false)
                  providerButtonRef.current?.focus()
                }}><span className="truncate">{m}</span><MenuCheck checked={activeProvider === hoveredProvider && activeModel === m} /></MenuItem>)}
            </> : undefined}>
            <div className="sd-menu-title">{t("menus.providers")}</div>
            <MenuItem role="menuitemradio" aria-checked={!activeProvider} active={!activeProvider}
              onMouseEnter={() => setHoveredProvider(null)} onClick={() => {
                useAppStore.getState().setActiveProvider(null); setActiveModel(null); setShowProviderMenu(false)
                providerButtonRef.current?.focus()
              }}><Cpu className="sd-menu-icon" /><span className="flex-1">{t("chat.defaultProvider")}</span><MenuCheck checked={!activeProvider} /></MenuItem>
            {providerList.map(p => {
              const chatModels = providerChatModels(p)
              return <MenuItem key={p.id} data-submenu-trigger={chatModels.length ? "" : undefined}
                aria-haspopup={chatModels.length ? "menu" : undefined} aria-expanded={chatModels.length ? hoveredProvider === p.id : undefined}
                active={activeProvider === p.id} title={p.name || p.model}
                onMouseEnter={() => setHoveredProvider(p.id)} onClick={() => {
                  if (hoveredProvider !== p.id && chatModels.length) { setHoveredProvider(p.id); return }
                  useAppStore.getState().setActiveProvider(p.id)
                  setActiveModel(p.default_model && chatModels.includes(p.default_model) ? p.default_model : chatModels[0] ?? p.model ?? null)
                  setShowProviderMenu(false); providerButtonRef.current?.focus()
                }}><Cpu className="sd-menu-icon" /><span className="flex-1 truncate">{p.name || p.model}</span>
                {chatModels.length > 0 ? <ChevronRight size={13} /> : <MenuCheck checked={activeProvider === p.id} />}
              </MenuItem>
            })}
            {providers.length === 0 && <div className="sd-menu-empty">{t("common.noOptions")}</div>}
          </SoftMenu>
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
        <Button
          type="button"
          className="pm-chat-composer-send shrink-0"
          onClick={showStop ? stopGeneration : handleSend}
          disabled={!showStop && !canSend}
          aria-label={actionLabel}
          title={actionLabel}
        >
          {showStop ? <Square className="size-3" fill="currentColor" aria-hidden="true" /> : <ArrowUp className="size-4" strokeWidth={2} aria-hidden="true" />}
        </Button>
      </div>
    </div>
  )
}
