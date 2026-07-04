import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { Lock, RefreshCw } from "lucide-react"
import { getCollectionConfig, updateCollectionConfig, triggerSparseRecalc, getConfig, getEmbeddingProviders, type EmbeddingProvider } from "@/api/client"
import { useAppStore } from "@/stores/app-store"
import { toast } from "sonner"
import { TooltipLabel } from "@/components/shared/tooltip-label"

interface CollectionConfigProps {
  collection: string
}

export function CollectionConfig({ collection }: CollectionConfigProps) {
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
  const [embeddingProviders, setEmbeddingProviders] = useState<EmbeddingProvider[]>([])
  const [allowedTypes, setAllowedTypes] = useState<string[]>([])
  const [saving, setSaving] = useState(false)

  const FILE_TYPES = [
    { ext: "pdf", label: "PDF" },
    { ext: "txt", label: "TXT" },
    { ext: "md", label: "Markdown" },
    { ext: "docx", label: "Word" },
    { ext: "xlsx", label: "Excel" },
    { ext: "pptx", label: "PowerPoint" },
    { ext: "csv", label: "CSV" },
  ]

  // Enriching LLM config (stores provider ID)
  const [enrichingLlmProvider, setEnrichingLlmProvider] = useState("")
  const [enrichingLlmModel, setEnrichingLlmModel] = useState("")

  // Cloud parsing (MinerU)
  const [cloudParsing, setCloudParsing] = useState(true)
  const [mineruGloballyEnabled, setMineruGloballyEnabled] = useState(false)

  // Sparse vocabulary
  const [sparseRecalcThreshold, setSparseRecalcThreshold] = useState("5000")
  const [sparseRecalcCounter, setSparseRecalcCounter] = useState(0)
  const [recalcRunning, setRecalcRunning] = useState(false)

  const readyProviders = providers.filter((p) => p.status === "ready" || !p.status)
  const enrichingProvider = enrichingLlmProvider
    ? readyProviders.find((p) => p.id === enrichingLlmProvider)
    : null
  const enrichingModels = enrichingProvider?.selected_models && enrichingProvider.selected_models.length > 0
    ? enrichingProvider.selected_models
    : enrichingProvider?.model ? [enrichingProvider.model] : []

  useEffect(() => {
    const load = async () => {
      try {
        const cfg = await getCollectionConfig(collection) as Record<string, unknown>
        if (cfg.error) return

        // Fetch global embedding model for dropdown
        try {
          const globalCfg = await getConfig()
          const emb = globalCfg.embedding as Record<string, unknown> | undefined
          if (emb?.model) setGlobalEmbModel(String(emb.model))
          // Check if MinerU is globally enabled
          const mineru = globalCfg.mineru as Record<string, unknown> | undefined
          setMineruGloballyEnabled(!!mineru?.enabled)
        } catch { /* ignore */ }

        // Fetch embedding providers for selector
        try {
          const providers = await getEmbeddingProviders()
          setEmbeddingProviders(providers)
        } catch { /* ignore */ }

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

        // Allowed file types
        const aft = cfg.allowed_file_types
        setAllowedTypes(Array.isArray(aft) ? aft.map(String) : [])

        // Enriching LLM config
        setEnrichingLlmProvider(String(cfg.enriching_llm_provider ?? ""))
        setEnrichingLlmModel(String(cfg.enriching_llm_model ?? ""))

        // Cloud parsing
        setCloudParsing(Boolean(cfg.cloud_parsing ?? true))

        // Sparse vocabulary
        setSparseRecalcThreshold(String(cfg.sparse_recalc_threshold ?? "5000"))
        setSparseRecalcCounter(Number(cfg.sparse_recalc_counter ?? 0))
      } catch {
        // ignore
      }
    }
    load()
  }, [collection])

  const handleSave = async () => {
    setSaving(true)
    try {
      const config: Record<string, unknown> = {}
      if (bufferRatio) config.buffer_ratio = parseFloat(bufferRatio)
      if (chunkMode === "normal") {
        if (chunkSize) config.chunk_size = parseInt(chunkSize)
        if (chunkOverlap) config.chunk_overlap = parseInt(chunkOverlap)
      } else {
        config.parent_strategy = parentStrategy
        if (parentChunkSize) config.parent_chunk_size = parseInt(parentChunkSize)
        if (parentChunkOverlap) config.parent_chunk_overlap = parseInt(parentChunkOverlap)
        if (childChunkSize) config.child_chunk_size = parseInt(childChunkSize)
        if (childChunkOverlap) config.child_chunk_overlap = parseInt(childChunkOverlap)
      }
      config.contextual_enabled = contextualEnabled
      if (contextualWindow) config.contextual_window = parseInt(contextualWindow)
      if (embeddingModel) config.embedding_model = embeddingModel
      config.embedding_provider_id = embeddingProviderId || null

      // Allowed file types (empty array = allow all)
      config.allowed_file_types = allowedTypes

      // Enriching LLM config (always send to allow clearing)
      config.enriching_llm_provider = enrichingLlmProvider || null
      config.enriching_llm_model = enrichingLlmModel || null

      // Cloud parsing
      config.cloud_parsing = cloudParsing

      // Sparse vocabulary
      if (sparseRecalcThreshold) config.sparse_recalc_threshold = parseInt(sparseRecalcThreshold)

      const res = await updateCollectionConfig(collection, config)
      if (res.error) toast.error(res.error)
      else toast.success(res.message || "Config updated")
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* ── Dimensions & Mode ── */}
      <div className="space-y-3">
        <h3 className="text-base font-semibold">Dimensions & Mode</h3>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <TooltipLabel label="Dimensions" tooltip="Vector dimensions for embeddings. Locked at creation time." />
            <div className="flex items-center gap-2">
              <Input value={embeddingDimensions} disabled className="flex-1" />
              <Lock className="h-4 w-4 text-muted-foreground" />
            </div>
          </div>
          <div className="space-y-1.5">
            <TooltipLabel label="Chunk Mode" tooltip="Locked at creation time." />
            <div className="flex items-center gap-2">
              <Input value={chunkMode === "parent_child" ? "Parent-Child" : "Normal"} disabled className="flex-1" />
              <Lock className="h-4 w-4 text-muted-foreground" />
            </div>
          </div>
        </div>
      </div>

      <Separator />

      {/* ── Chunking ── */}
      <div className="space-y-3">
        <h3 className="text-base font-semibold">Chunking</h3>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <TooltipLabel label="Buffer Ratio" tooltip="Controls how aggressively paragraphs are merged. 0.5 = merge until 50% of max_tokens." />
            <Input value={bufferRatio} onChange={(e) => setBufferRatio(e.target.value)} placeholder="0.5" />
          </div>
          {chunkMode === "parent_child" && (
            <div className="space-y-1.5">
              <TooltipLabel label="Parent Strategy" tooltip="How parent chunks are created." />
              <select
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={parentStrategy}
                onChange={(e) => setParentStrategy(e.target.value)}
              >
                <option value="paragraph">Paragraph</option>
                <option value="fixed_token">Fixed Token</option>
                <option value="heading">Heading</option>
              </select>
            </div>
          )}
        </div>
        {chunkMode === "normal" ? (
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <TooltipLabel label="Chunk Size" tooltip="Tokens per chunk." />
              <Input value={chunkSize} onChange={(e) => setChunkSize(e.target.value)} placeholder="512" />
            </div>
            <div className="space-y-1.5">
              <TooltipLabel label="Chunk Overlap" tooltip="Overlapping tokens between adjacent chunks." />
              <Input value={chunkOverlap} onChange={(e) => setChunkOverlap(e.target.value)} placeholder="64" />
            </div>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <TooltipLabel label="Parent Chunk Size" tooltip="Size of parent chunks." />
                <Input value={parentChunkSize} onChange={(e) => setParentChunkSize(e.target.value)} placeholder="1024" />
              </div>
              <div className="space-y-1.5">
                <TooltipLabel label="Parent Chunk Overlap" tooltip="Overlap between parent chunks." />
                <Input value={parentChunkOverlap} onChange={(e) => setParentChunkOverlap(e.target.value)} placeholder="128" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <TooltipLabel label="Child Chunk Size" tooltip="Size of child chunks used for matching." />
                <Input value={childChunkSize} onChange={(e) => setChildChunkSize(e.target.value)} placeholder="128" />
              </div>
              <div className="space-y-1.5">
                <TooltipLabel label="Child Chunk Overlap" tooltip="Overlap between child chunks." />
                <Input value={childChunkOverlap} onChange={(e) => setChildChunkOverlap(e.target.value)} placeholder="32" />
              </div>
            </div>
          </>
        )}
      </div>

      <Separator />

      {/* ── Embedding Model ── */}
      <div className="space-y-3">
        <h3 className="text-base font-semibold">Embedding Model</h3>
        <div className="space-y-1.5">
          <TooltipLabel label="Provider" tooltip="Select embedding provider for this collection." />
          <select
            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            value={embeddingProviderId}
            onChange={(e) => setEmbeddingProviderId(e.target.value)}
          >
            <option value="">Global default{globalEmbModel ? ` (${globalEmbModel})` : ""}</option>
            {embeddingProviders.map((p) => (
              <option key={p.id} value={p.id}>{p.name || p.model}</option>
            ))}
          </select>
        </div>
        {embeddingModel && (
          <div className="space-y-1.5">
            <TooltipLabel label="Model (legacy)" tooltip="Legacy field." />
            <Input value={embeddingModel} onChange={(e) => setEmbeddingModel(e.target.value)} placeholder="text-embedding-3-small" />
          </div>
        )}
      </div>

      <Separator />

      {/* ── Allowed File Types ── */}
      <div className="space-y-3">
        <h3 className="text-base font-semibold">Allowed File Types</h3>
        <p className="font-normal text-[12px] text-muted-foreground/80 leading-relaxed">Restrict which file types can be uploaded. Leave empty to allow all.</p>
        <div className="flex flex-wrap gap-2">
          {FILE_TYPES.map((ft) => (
            <label
              key={ft.ext}
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border text-xs cursor-pointer transition-colors ${
                allowedTypes.includes(ft.ext) ? "bg-primary text-primary-foreground border-primary" : "bg-background border-input hover:bg-accent"
              }`}
            >
              <input
                type="checkbox"
                className="sr-only"
                checked={allowedTypes.includes(ft.ext)}
                onChange={() =>
                  setAllowedTypes((prev) =>
                    prev.includes(ft.ext) ? prev.filter((t) => t !== ft.ext) : [...prev, ft.ext]
                  )
                }
              />
              {ft.label}
            </label>
          ))}
        </div>
      </div>

      <Separator />

      {/* ── Contextual Enrichment ── */}
      <div className="space-y-3">
        <h3 className="text-base font-semibold">Contextual Enrichment</h3>
        <label className="flex items-center gap-2 text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground cursor-pointer">
          <input type="checkbox" checked={contextualEnabled} onChange={(e) => setContextualEnabled(e.target.checked)} className="rounded" />
          Enable Contextual Enrichment
        </label>
        {contextualEnabled && (
          <>
            <div className="space-y-1.5">
              <TooltipLabel label="Context Window" tooltip="Surrounding chunks on each side used for context." />
              <Input value={contextualWindow} onChange={(e) => setContextualWindow(e.target.value)} placeholder="1" />
            </div>
            <p className="font-normal text-[12px] text-muted-foreground/80 leading-relaxed">
              Contextual enrichment uses an LLM to generate background information for each chunk, improving retrieval quality.
            </p>
          </>
        )}
      </div>

      <Separator />

      {/* ── Enriching LLM ── */}
      <div className="space-y-3">
        <h3 className="text-base font-semibold">Enriching LLM</h3>
        <p className="font-normal text-[12px] text-muted-foreground/80 leading-relaxed">
          LLM used for contextual enrichment during document ingestion. Leave empty to use the global default.
        </p>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <label className="text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground">Provider</label>
            <select
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={enrichingLlmProvider}
              onChange={(e) => {
                setEnrichingLlmProvider(e.target.value)
                const prov = readyProviders.find((p) => p.id === e.target.value)
                const defaultM = prov?.default_model || prov?.selected_models?.[0] || prov?.model || ""
                setEnrichingLlmModel(defaultM)
              }}
            >
              <option value="">Global default</option>
              {readyProviders.map((p) => (
                <option key={p.id} value={p.id}>{p.name || p.model}</option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <label className="text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground">Model</label>
            <select
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={enrichingLlmModel}
              onChange={(e) => setEnrichingLlmModel(e.target.value)}
              disabled={!enrichingLlmProvider}
            >
              <option value="">Select model</option>
              {enrichingModels.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>
        </div>
      </div>

      <Separator />

      {/* ── Cloud Parsing (MinerU) ── */}
      {mineruGloballyEnabled && (
        <>
          <div className="space-y-3">
            <h3 className="text-base font-semibold">Cloud Parsing (MinerU)</h3>
            <p className="font-normal text-[12px] text-muted-foreground/80 leading-relaxed">
              Use MinerU cloud API for document parsing. Produces higher quality Markdown output with better table, formula, and layout preservation.
            </p>
            <label className="flex items-center gap-2 text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground cursor-pointer">
              <input type="checkbox" checked={cloudParsing} onChange={(e) => setCloudParsing(e.target.checked)} className="rounded" />
              Enable Cloud Parsing for this Collection
            </label>
            {cloudParsing && (
              <p className="font-normal text-[12px] text-muted-foreground/80 leading-relaxed">
                When enabled, uploaded documents will be parsed by MinerU's cloud API and chunked using a Markdown-aware strategy.
              </p>
            )}
          </div>
          <Separator />
        </>
      )}

      {/* ── Sparse Vocabulary ── */}
      <div className="space-y-3">
        <h3 className="text-base font-semibold">Sparse Vocabulary (BM25)</h3>
        <p className="font-normal text-[12px] text-muted-foreground/80 leading-relaxed">
          BM25 statistics drift as documents are added or removed. The vocabulary is rebuilt automatically when changes reach the threshold.
        </p>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <TooltipLabel label="Recalc Threshold" tooltip="Chunk changes before auto-rebuilding. 5000 ≈ 1000 files." />
            <Input value={sparseRecalcThreshold} onChange={(e) => setSparseRecalcThreshold(e.target.value)} placeholder="5000" />
          </div>
          <div className="space-y-1.5">
            <TooltipLabel label="Change Counter" tooltip="Chunk changes since last rebuild." />
            <Input value={String(sparseRecalcCounter)} disabled />
          </div>
        </div>
        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            disabled={recalcRunning}
            onClick={async () => {
              setRecalcRunning(true)
              try {
                const res = await triggerSparseRecalc(collection)
                if (res.error) {
                  toast.error(res.error)
                } else {
                  toast.success(res.message || "Sparse recalculation triggered")
                  setTimeout(async () => {
                    try {
                      const cfg = await getCollectionConfig(collection) as Record<string, unknown>
                      if (!cfg.error) setSparseRecalcCounter(Number(cfg.sparse_recalc_counter ?? 0))
                    } catch { /* ignore */ }
                  }, 2000)
                }
              } catch (err) {
                toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
              } finally {
                setRecalcRunning(false)
              }
            }}
          >
            <RefreshCw className={`h-4 w-4 mr-1.5 ${recalcRunning ? "animate-spin" : ""}`} />
            {recalcRunning ? "Running..." : "Recalculate Now"}
          </Button>
          <span className="text-[12px] text-muted-foreground">
            {sparseRecalcCounter >= parseInt(sparseRecalcThreshold || "5000")
              ? "Threshold reached — auto-rebuild pending."
              : `${sparseRecalcCounter} / ${sparseRecalcThreshold || "5000"} changes`}
          </span>
        </div>
      </div>

      <Separator />

      <div className="flex justify-end">
        <Button onClick={handleSave} disabled={saving}>
          {saving ? "Saving..." : "Save Config"}
        </Button>
      </div>
    </div>
  )
}
