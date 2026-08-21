/**
 * Collection Settings — Premium surface (nested white cards on float stage).
 * Layout language matches Group form / Overview cards:
 * soft float · FieldLabel · DropdownSelect · Input · Button · no hard Separators.
 */
import { useState, useEffect, useRef, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { FieldLabel } from "@/components/ui/field-label"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { Info, Lock, RefreshCw } from "lucide-react"
import {
  getCollectionConfig,
  updateCollectionConfig,
  triggerSparseRecalc,
  getConfig,
  getEmbeddingProviders,
  type EmbeddingProvider,
} from "@/api/client"
import { useAppStore } from "@/stores/app-store"
import { toast } from "sonner"
import { useT } from "@/i18n/use-t"
import { formatApiError } from "@/api/http"

interface CollectionConfigProps {
  collection: string
}

/** Field label + optional info tooltip — type role = label (Geist). */
function ConfigLabel({
  children,
  tooltip,
  htmlFor,
}: {
  children: ReactNode
  tooltip?: string
  htmlFor?: string
}) {
  const t = useT()
  return (
    <div className="pm-config-label-row">
      <FieldLabel htmlFor={htmlFor} className="pm-config-field-label">
        {children}
      </FieldLabel>
      {tooltip ? (
        <Tooltip>
          <TooltipTrigger
            type="button"
            className="pm-config-info"
            aria-label={t("library.moreInfo")}
          >
            <Info className="h-3 w-3" strokeWidth={1.75} />
          </TooltipTrigger>
          <TooltipContent side="top" className="max-w-xs">
            {tooltip}
          </TooltipContent>
        </Tooltip>
      ) : null}
    </div>
  )
}

function ConfigSwitch({
  checked,
  onCheckedChange,
  label,
  id,
}: {
  checked: boolean
  onCheckedChange: (next: boolean) => void
  label: string
  id: string
}) {
  return (
    <button
      type="button"
      id={id}
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={cn("pm-config-switch", checked && "is-on")}
      onClick={() => onCheckedChange(!checked)}
    >
      <span className="pm-config-switch-thumb" aria-hidden />
    </button>
  )
}

export function CollectionConfig({ collection }: CollectionConfigProps) {
  const t = useT()
  const { providers } = useAppStore()
  const [chunkMode, setChunkMode] = useState("normal")
  const [chunkSize, setChunkSize] = useState("")
  const [chunkOverlap, setChunkOverlap] = useState("")
  const [bufferRatio, setBufferRatio] = useState("")
  const [parentStrategy, setParentStrategy] = useState("paragraph")
  const [parentChunkSize, setParentChunkSize] = useState("")
  const [parentChunkOverlap, setParentChunkOverlap] = useState("")
  const [childChunkSize, setChildChunkSize] = useState("")
  const [childChunkOverlap, setChildChunkOverlap] = useState("")
  const [contextualEnabled, setContextualEnabled] = useState(true)
  const [contextualWindow, setContextualWindow] = useState("1")
  const [embeddingDimensions, setEmbeddingDimensions] = useState("")
  const [embeddingModel, setEmbeddingModel] = useState("")
  const [globalEmbModel, setGlobalEmbModel] = useState("")
  const [embeddingProviderId, setEmbeddingProviderId] = useState("")
  const [embeddingProviders, setEmbeddingProviders] = useState<
    EmbeddingProvider[]
  >([])
  const [allowedTypes, setAllowedTypes] = useState<string[]>([])
  /** Skip autosave until first load for this collection finishes. */
  const [hydrated, setHydrated] = useState(false)
  const [saveStatus, setSaveStatus] = useState<
    "idle" | "pending" | "saving" | "saved" | "error"
  >("idle")
  const saveGenRef = useRef(0)
  const savedClearTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** After hydrate, first effect pass is load baseline — do not POST. */
  const skipAutosaveOnceRef = useRef(true)

  const FILE_TYPES = [
    { ext: "pdf", label: "PDF" },
    { ext: "txt", label: "TXT" },
    { ext: "md", label: "Markdown" },
    { ext: "docx", label: "Word" },
    { ext: "xlsx", label: "Excel" },
    { ext: "pptx", label: "PowerPoint" },
    { ext: "csv", label: "CSV" },
  ]

  const [enrichingLlmProvider, setEnrichingLlmProvider] = useState("")
  const [enrichingLlmModel, setEnrichingLlmModel] = useState("")

  const [cloudParsing, setCloudParsing] = useState(true)
  const [mineruGloballyEnabled, setMineruGloballyEnabled] = useState(false)

  const [sparseRecalcThreshold, setSparseRecalcThreshold] = useState("5000")
  const [sparseRecalcCounter, setSparseRecalcCounter] = useState(0)
  const [recalcRunning, setRecalcRunning] = useState(false)

  const readyProviders = providers.filter(
    (p) => p.status === "ready" || !p.status
  )
  const enrichingProvider = enrichingLlmProvider
    ? readyProviders.find((p) => p.id === enrichingLlmProvider)
    : null
  const enrichingModels =
    enrichingProvider?.selected_models &&
    enrichingProvider.selected_models.length > 0
      ? enrichingProvider.selected_models
      : enrichingProvider?.model
        ? [enrichingProvider.model]
        : []

  useEffect(() => {
    let cancelled = false
    setHydrated(false)
    setSaveStatus("idle")
    saveGenRef.current += 1
    skipAutosaveOnceRef.current = true

    const load = async () => {
      try {
        const cfg = (await getCollectionConfig(collection)) as Record<
          string,
          unknown
        >
        if (cancelled || cfg.error) return

        try {
          const globalCfg = await getConfig()
          const emb = globalCfg.embedding as Record<string, unknown> | undefined
          if (emb?.model) setGlobalEmbModel(String(emb.model))
          const mineru = globalCfg.mineru as Record<string, unknown> | undefined
          setMineruGloballyEnabled(!!mineru?.enabled)
        } catch {
          /* ignore */
        }

        try {
          const list = await getEmbeddingProviders()
          if (!cancelled) setEmbeddingProviders(list)
        } catch {
          /* ignore */
        }

        if (cancelled) return

        setEmbeddingDimensions(String(cfg.dimensions ?? "1536"))
        setChunkMode(String(cfg.chunk_mode ?? "normal"))
        setChunkSize(String(cfg.chunk_size ?? ""))
        setChunkOverlap(String(cfg.chunk_overlap ?? ""))
        setBufferRatio(String(cfg.buffer_ratio ?? "0.5"))
        setParentStrategy(String(cfg.parent_strategy ?? "paragraph"))
        setParentChunkSize(String(cfg.parent_chunk_size ?? ""))
        setParentChunkOverlap(String(cfg.parent_chunk_overlap ?? ""))
        setChildChunkSize(String(cfg.child_chunk_size ?? ""))
        setChildChunkOverlap(String(cfg.child_chunk_overlap ?? ""))

        setContextualEnabled(Boolean(cfg.contextual_enabled ?? true))
        setContextualWindow(String(cfg.contextual_window ?? 1))
        setEmbeddingModel(String(cfg.embedding_model ?? ""))
        setEmbeddingProviderId(String(cfg.embedding_provider_id ?? ""))

        const aft = cfg.allowed_file_types
        setAllowedTypes(Array.isArray(aft) ? aft.map(String) : [])

        setEnrichingLlmProvider(String(cfg.enriching_llm_provider ?? ""))
        setEnrichingLlmModel(String(cfg.enriching_llm_model ?? ""))

        setCloudParsing(Boolean(cfg.cloud_parsing ?? true))

        setSparseRecalcThreshold(String(cfg.sparse_recalc_threshold ?? "5000"))
        setSparseRecalcCounter(Number(cfg.sparse_recalc_counter ?? 0))
      } catch {
        // ignore
      } finally {
        if (!cancelled) {
          // Next paint: ignore the load-driven state write in autosave deps
          requestAnimationFrame(() => {
            if (!cancelled) setHydrated(true)
          })
        }
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [collection])

  /**
   * Autosave — debounce field edits; no Save button.
   * Quiet status in header; toast only on failure (avoid spam).
   */
  useEffect(() => {
    if (!hydrated) return
    if (skipAutosaveOnceRef.current) {
      skipAutosaveOnceRef.current = false
      return
    }

    setSaveStatus("pending")
    if (savedClearTimerRef.current) {
      clearTimeout(savedClearTimerRef.current)
      savedClearTimerRef.current = null
    }

    const timer = window.setTimeout(() => {
      const gen = ++saveGenRef.current
      setSaveStatus("saving")

      const config: Record<string, unknown> = {}
      if (bufferRatio) config.buffer_ratio = parseFloat(bufferRatio)
      if (chunkMode === "normal") {
        if (chunkSize) config.chunk_size = parseInt(chunkSize, 10)
        if (chunkOverlap) config.chunk_overlap = parseInt(chunkOverlap, 10)
      } else {
        config.parent_strategy = parentStrategy
        if (parentChunkSize)
          config.parent_chunk_size = parseInt(parentChunkSize, 10)
        if (parentChunkOverlap)
          config.parent_chunk_overlap = parseInt(parentChunkOverlap, 10)
        if (childChunkSize)
          config.child_chunk_size = parseInt(childChunkSize, 10)
        if (childChunkOverlap)
          config.child_chunk_overlap = parseInt(childChunkOverlap, 10)
      }
      config.contextual_enabled = contextualEnabled
      if (contextualWindow)
        config.contextual_window = parseInt(contextualWindow, 10)
      if (embeddingModel) config.embedding_model = embeddingModel
      config.embedding_provider_id = embeddingProviderId || null
      config.allowed_file_types = allowedTypes
      config.enriching_llm_provider = enrichingLlmProvider || null
      config.enriching_llm_model = enrichingLlmModel || null
      config.cloud_parsing = cloudParsing
      if (sparseRecalcThreshold)
        config.sparse_recalc_threshold = parseInt(sparseRecalcThreshold, 10)

      void updateCollectionConfig(collection, config)
        .then((res) => {
          if (gen !== saveGenRef.current) return
          if (res.error) {
            setSaveStatus("error")
            toast.error(res.error)
            return
          }
          setSaveStatus("saved")
          savedClearTimerRef.current = setTimeout(() => {
            if (gen === saveGenRef.current) setSaveStatus("idle")
          }, 1600)
        })
        .catch((err) => {
          if (gen !== saveGenRef.current) return
          setSaveStatus("error")
          toast.error(
            t("common.failedWithError", { error: formatApiError(err, t) })
          )
        })
    }, 480)

    return () => window.clearTimeout(timer)
  }, [
    hydrated,
    collection,
    chunkMode,
    chunkSize,
    chunkOverlap,
    bufferRatio,
    parentStrategy,
    parentChunkSize,
    parentChunkOverlap,
    childChunkSize,
    childChunkOverlap,
    contextualEnabled,
    contextualWindow,
    embeddingModel,
    embeddingProviderId,
    allowedTypes,
    enrichingLlmProvider,
    enrichingLlmModel,
    cloudParsing,
    sparseRecalcThreshold,
  ])

  useEffect(() => {
    return () => {
      if (savedClearTimerRef.current) clearTimeout(savedClearTimerRef.current)
    }
  }, [])

  const handleRecalc = async () => {
    setRecalcRunning(true)
    try {
      const res = await triggerSparseRecalc(collection)
      if (res.error) {
        toast.error(res.error)
      } else {
        toast.success(res.message || t("library.sparseRecalcTriggered"))
        window.setTimeout(async () => {
          try {
            const cfg = (await getCollectionConfig(collection)) as Record<
              string,
              unknown
            >
            if (!cfg.error)
              setSparseRecalcCounter(Number(cfg.sparse_recalc_counter ?? 0))
          } catch {
            /* ignore */
          }
        }, 2000)
      }
    } catch (err) {
      toast.error(t("common.failedWithError", { error: formatApiError(err, t) }))
    } finally {
      setRecalcRunning(false)
    }
  }

  const thresholdN = parseInt(sparseRecalcThreshold || "5000", 10) || 5000
  const thresholdReached = sparseRecalcCounter >= thresholdN

  return (
    <div className="pm-collection-config">
      <header className="pm-config-head">
        <div className="pm-config-head-row">
          <h2 className="pm-config-title">{t("library.collectionSettings")}</h2>
          <span
            className={cn(
              "pm-meta pm-config-save-status",
              saveStatus === "saved" && "is-saved",
              saveStatus === "error" && "is-error",
              (saveStatus === "pending" || saveStatus === "saving") &&
                "is-busy"
            )}
            aria-live="polite"
          >
            {saveStatus === "pending" || saveStatus === "saving"
              ? t("library.saving")
              : saveStatus === "saved"
                ? t("library.saved")
                : saveStatus === "error"
                  ? t("library.saveFailed")
                  : t("library.autosaveOn")}
          </span>
        </div>
        <p className="pm-meta text-[var(--pm-faint)]">
          {t("library.processingHint")}
        </p>
      </header>

      <div className="pm-config-stack">
        {/* ── Dimensions & Mode ── */}
        <section className="pm-config-card">
          <header className="pm-config-card-head">
            <span className="pm-config-card-kicker">{t("library.dimensionsMode")}</span>
            <span className="pm-meta text-[var(--pm-faint)]">{t("library.lockedAtCreate")}</span>
          </header>
          <div className="pm-config-grid">
            <div className="pm-config-field">
              <ConfigLabel tooltip={t("library.dimsTooltip")}>
                {t("library.dimensions")}
              </ConfigLabel>
              <div className="pm-config-locked">
                <Input
                  value={embeddingDimensions}
                  disabled
                  className="pm-config-input"
                />
                <Lock
                  className="pm-config-lock-icon"
                  strokeWidth={1.75}
                  aria-hidden
                />
              </div>
            </div>
            <div className="pm-config-field">
              <ConfigLabel tooltip={t("library.lockedTooltip")}>
                {t("library.chunkMode")}
              </ConfigLabel>
              <div className="pm-config-locked">
                <Input
                  value={
                    chunkMode === "parent_child"
                      ? t("library.chunkModeParentChild")
                      : t("library.chunkModeNormal")
                  }
                  disabled
                  className="pm-config-input"
                />
                <Lock
                  className="pm-config-lock-icon"
                  strokeWidth={1.75}
                  aria-hidden
                />
              </div>
            </div>
          </div>
        </section>

        {/* ── Chunking ── */}
        <section className="pm-config-card">
          <header className="pm-config-card-head">
            <span className="pm-config-card-kicker">{t("library.chunking")}</span>
          </header>
          <div className="pm-config-grid">
            <div className="pm-config-field">
              <ConfigLabel tooltip={t("library.bufferRatioTooltip")}>
                {t("library.bufferRatio")}
              </ConfigLabel>
              <Input
                value={bufferRatio}
                onChange={(e) => setBufferRatio(e.target.value)}
                placeholder="0.5"
                className="pm-config-input"
              />
            </div>
            {chunkMode === "parent_child" && (
              <div className="pm-config-field">
                <ConfigLabel tooltip={t("library.parentStrategyTooltip")}>
                  {t("library.parentStrategy")}
                </ConfigLabel>
                <DropdownSelect
                  size="sm"
                  value={parentStrategy}
                  onChange={setParentStrategy}
                  options={[
                    { value: "paragraph", label: t("library.strategyParagraph") },
                    { value: "fixed_token", label: t("library.strategyFixedToken") },
                    { value: "heading", label: t("library.strategyHeading") },
                  ]}
                />
              </div>
            )}
          </div>
          {chunkMode === "normal" ? (
            <div className="pm-config-grid">
              <div className="pm-config-field">
                <ConfigLabel tooltip={t("library.chunkSizeTooltip")}>{t("library.chunkSize")}</ConfigLabel>
                <Input
                  value={chunkSize}
                  onChange={(e) => setChunkSize(e.target.value)}
                  placeholder="512"
                  className="pm-config-input"
                />
              </div>
              <div className="pm-config-field">
                <ConfigLabel tooltip={t("library.chunkOverlapTooltip")}>
                  {t("library.chunkOverlap")}
                </ConfigLabel>
                <Input
                  value={chunkOverlap}
                  onChange={(e) => setChunkOverlap(e.target.value)}
                  placeholder="64"
                  className="pm-config-input"
                />
              </div>
            </div>
          ) : (
            <>
              <div className="pm-config-grid">
                <div className="pm-config-field">
                  <ConfigLabel tooltip={t("library.parentSizeTooltip")}>
                    {t("library.parentChunkSize")}
                  </ConfigLabel>
                  <Input
                    value={parentChunkSize}
                    onChange={(e) => setParentChunkSize(e.target.value)}
                    placeholder="1024"
                    className="pm-config-input"
                  />
                </div>
                <div className="pm-config-field">
                  <ConfigLabel tooltip={t("library.parentOverlapTooltip")}>
                    {t("library.parentChunkOverlap")}
                  </ConfigLabel>
                  <Input
                    value={parentChunkOverlap}
                    onChange={(e) => setParentChunkOverlap(e.target.value)}
                    placeholder="128"
                    className="pm-config-input"
                  />
                </div>
              </div>
              <div className="pm-config-grid">
                <div className="pm-config-field">
                  <ConfigLabel tooltip={t("library.childSizeTooltip")}>
                    {t("library.childChunkSize")}
                  </ConfigLabel>
                  <Input
                    value={childChunkSize}
                    onChange={(e) => setChildChunkSize(e.target.value)}
                    placeholder="128"
                    className="pm-config-input"
                  />
                </div>
                <div className="pm-config-field">
                  <ConfigLabel tooltip={t("library.childOverlapTooltip")}>
                    {t("library.childChunkOverlap")}
                  </ConfigLabel>
                  <Input
                    value={childChunkOverlap}
                    onChange={(e) => setChildChunkOverlap(e.target.value)}
                    placeholder="32"
                    className="pm-config-input"
                  />
                </div>
              </div>
            </>
          )}
        </section>

        {/* ── Embedding Model ── */}
        <section className="pm-config-card">
          <header className="pm-config-card-head">
            <span className="pm-config-card-kicker">{t("library.embeddingModel")}</span>
          </header>
          <div className="pm-config-field">
            <ConfigLabel tooltip={t("library.providerTooltip")}>
              {t("library.provider")}
            </ConfigLabel>
            <DropdownSelect
              size="sm"
              value={embeddingProviderId}
              onChange={setEmbeddingProviderId}
              placeholder={`${t("library.globalDefault")}${globalEmbModel ? ` (${globalEmbModel})` : ""}`}
              options={[
                {
                  value: "",
                  label: `${t("library.globalDefault")}${globalEmbModel ? ` (${globalEmbModel})` : ""}`,
                },
                ...embeddingProviders.map((p) => ({
                  value: p.id,
                  label: p.name || p.model || p.id,
                })),
              ]}
            />
          </div>
          {embeddingModel ? (
            <div className="pm-config-field">
              <ConfigLabel tooltip={t("library.modelLegacyTooltip")}>{t("library.modelLegacy")}</ConfigLabel>
              <Input
                value={embeddingModel}
                onChange={(e) => setEmbeddingModel(e.target.value)}
                placeholder="text-embedding-3-small"
                className="pm-config-input"
              />
            </div>
          ) : null}
        </section>

        {/* ── Allowed File Types ── */}
        <section className="pm-config-card">
          <header className="pm-config-card-head">
            <span className="pm-config-card-kicker">{t("library.allowedTypes")}</span>
            <span className="pm-meta text-[var(--pm-faint)]">
              {t("library.emptyAllAllowed")}
            </span>
          </header>
          <p className="pm-meta pm-config-card-desc">
            {t("library.restrictFileTypes")}
          </p>
          <div className="pm-config-chips" role="group" aria-label={t("library.fileTypes")}>
            {FILE_TYPES.map((ft) => {
              const on = allowedTypes.includes(ft.ext)
              return (
                <button
                  key={ft.ext}
                  type="button"
                  className={cn("pm-field-chip", on && "is-on")}
                  aria-pressed={on}
                  onClick={() =>
                    setAllowedTypes((prev) =>
                      prev.includes(ft.ext)
                        ? prev.filter((t) => t !== ft.ext)
                        : [...prev, ft.ext]
                    )
                  }
                >
                  {ft.label}
                </button>
              )
            })}
          </div>
        </section>

        {/* ── Contextual Enrichment ── */}
        <section className="pm-config-card">
          <header className="pm-config-card-head">
            <span className="pm-config-card-kicker">{t("library.contextualEnrichment")}</span>
            <ConfigSwitch
              id="pm-config-contextual"
              label={t("library.enableContextual")}
              checked={contextualEnabled}
              onCheckedChange={setContextualEnabled}
            />
          </header>
          <p className="pm-meta pm-config-card-desc">
            {t("library.contextualDesc")}
          </p>
          <div
            className={cn(
              "pm-config-fold",
              contextualEnabled && "is-open"
            )}
          >
            <div className="pm-config-fold-inner">
              <div className="pm-config-field">
                <ConfigLabel tooltip={t("library.contextWindowTooltip")}>
                  {t("library.contextWindow")}
                </ConfigLabel>
                <Input
                  value={contextualWindow}
                  onChange={(e) => setContextualWindow(e.target.value)}
                  placeholder="1"
                  className="pm-config-input"
                  disabled={!contextualEnabled}
                />
              </div>
            </div>
          </div>
        </section>

        {/* ── Library LLM ── */}
        <section className="pm-config-card">
          <header className="pm-config-card-head">
            <span className="pm-config-card-kicker">{t("settings.libraryLlm")}</span>
          </header>
          <p className="pm-meta pm-config-card-desc">
            {t("library.libraryLlmOverride")}
          </p>
          <div className="pm-config-grid">
            <div className="pm-config-field">
              <FieldLabel className="pm-config-field-label">{t("library.provider")}</FieldLabel>
              <DropdownSelect
                size="sm"
                value={enrichingLlmProvider}
                onChange={(id) => {
                  setEnrichingLlmProvider(id)
                  const prov = readyProviders.find((p) => p.id === id)
                  const defaultM =
                    prov?.default_model ||
                    prov?.selected_models?.[0] ||
                    prov?.model ||
                    ""
                  setEnrichingLlmModel(defaultM)
                }}
                placeholder={t("library.globalDefault")}
                options={[
                  { value: "", label: t("library.globalDefault") },
                  ...readyProviders.map((p) => ({
                    value: p.id,
                    label: p.name || p.model || p.id,
                  })),
                ]}
              />
            </div>
            <div className="pm-config-field">
              <FieldLabel className="pm-config-field-label">{t("library.model")}</FieldLabel>
              <DropdownSelect
                size="sm"
                value={enrichingLlmModel}
                onChange={setEnrichingLlmModel}
                disabled={!enrichingLlmProvider}
                placeholder={t("library.selectModel")}
                options={[
                  { value: "", label: t("library.selectModel") },
                  ...enrichingModels.map((m) => ({ value: m, label: m })),
                ]}
              />
            </div>
          </div>
        </section>

        {/* ── Cloud Parsing ── */}
        {mineruGloballyEnabled ? (
          <section className="pm-config-card">
            <header className="pm-config-card-head">
              <span className="pm-config-card-kicker">
                {t("library.cloudParsingMineru")}
              </span>
              <ConfigSwitch
                id="pm-config-cloud-parsing"
                label={t("library.enableCloudParsing")}
                checked={cloudParsing}
                onCheckedChange={setCloudParsing}
              />
            </header>
            <p className="pm-meta pm-config-card-desc">
              {t("library.cloudParsingDesc")}
            </p>
          </section>
        ) : null}

        {/* ── Sparse Vocabulary ── */}
        <section className="pm-config-card">
          <header className="pm-config-card-head">
            <span className="pm-config-card-kicker">{t("library.sparseVocab")}</span>
          </header>
          <p className="pm-meta pm-config-card-desc">
            {t("library.sparseDesc")}
          </p>
          <div className="pm-config-grid">
            <div className="pm-config-field">
              <ConfigLabel tooltip={t("library.recalcThresholdTooltip")}>
                {t("library.recalcThreshold")}
              </ConfigLabel>
              <Input
                value={sparseRecalcThreshold}
                onChange={(e) => setSparseRecalcThreshold(e.target.value)}
                placeholder="5000"
                className="pm-config-input"
              />
            </div>
            <div className="pm-config-field">
              <ConfigLabel tooltip={t("library.changeCounterTooltip")}>
                {t("library.changeCounter")}
              </ConfigLabel>
              <Input value={String(sparseRecalcCounter)} disabled />
            </div>
          </div>
          <div className="pm-config-action-row">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={recalcRunning}
              onClick={() => void handleRecalc()}
            >
              <RefreshCw
                className={cn("h-3.5 w-3.5", recalcRunning && "animate-spin")}
                strokeWidth={1.75}
              />
              {recalcRunning ? t("library.running") : t("library.recalculateNow")}
            </Button>
            <span
              className={cn(
                "pm-meta",
                thresholdReached
                  ? "text-[var(--pm-green)]"
                  : "text-[var(--pm-faint)]"
              )}
            >
              {thresholdReached
                ? t("library.thresholdReached")
                : t("library.nChanges", {
                    n: sparseRecalcCounter,
                    threshold: sparseRecalcThreshold || "5000",
                  })}
            </span>
          </div>
        </section>
      </div>
    </div>
  )
}
