import { useState, useEffect, useMemo } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogKicker, DialogTitle } from "@/components/ui/dialog"
import { FieldLabel } from "@/components/ui/field-label"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { Loader2, Eye, EyeOff, RefreshCw } from "lucide-react"
import {
  getLLMProviders, updateLLMProvider, createLLMProvider,
  getEmbeddingProviders, updateEmbeddingProvider, createEmbeddingProvider,
  getRerankProviders, updateRerankProvider, createRerankProvider,
  updateConfig,
} from "@/api/client"
import { toast } from "sonner"
import { cn } from "@/lib/utils"

interface OneShotOpenRouterDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}

const BASE_URL = "https://openrouter.ai/api/v1"

const RERANK_MODELS: ORModel[] = [
  { id: "cohere/rerank-v3.5", name: "Cohere: Rerank 3.5", pricing: { prompt: "0.001", completion: "0" }, context_length: 0 },
  { id: "cohere/rerank-3-english", name: "Cohere: Rerank 3 (English)", pricing: { prompt: "0.001", completion: "0" }, context_length: 0 },
  { id: "cohere/rerank-3-multilingual", name: "Cohere: Rerank 3 (Multilingual)", pricing: { prompt: "0.001", completion: "0" }, context_length: 0 },
  { id: "nvidia/llama-nemotron-rerank-vl-1b-v2-free", name: "NVIDIA: Nemotron Rerank (Free)", pricing: { prompt: "0", completion: "0" }, context_length: 0, architecture: { modality: "text->text" } },
]

interface ORModel {
  id: string
  name: string
  pricing: { prompt: string; completion: string }
  context_length: number
  architecture?: { modality?: string; tokenizer?: string; input_modalities?: string[]; output_modalities?: string[] }
  supported_parameters?: string[]
}

function isFree(m: ORModel): boolean {
  return (parseFloat(m.pricing?.prompt || "0") + parseFloat(m.pricing?.completion || "0")) === 0
}

function hasVision(m: ORModel): boolean {
  const arch = m.architecture
  if (!arch) return false
  const mod = arch.modality || ""
  if (mod.includes("image")) return true
  const inputs = arch.input_modalities as string[] | undefined
  if (inputs && inputs.some((i: string) => i.includes("image"))) return true
  return false
}

function isEmbedding(m: ORModel): boolean {
  const id = m.id.toLowerCase(); const name = m.name.toLowerCase()
  return id.includes("embed") || name.includes("embed")
}

function hasToolCalling(m: ORModel): boolean {
  return m.supported_parameters?.includes("tools") ?? false
}

export function OneShotOpenRouterDialog({ open, onOpenChange, onSaved }: OneShotOpenRouterDialogProps) {
  const [apiKey, setApiKey] = useState("")
  const [llmModel, setLlmModel] = useState("deepseek/deepseek-v4-flash")
  const [chatModel, setChatModel] = useState("deepseek/deepseek-v4-pro")
  const [visualModel, setVisualModel] = useState("xiaomi/mimo-v2.5")
  const [embModel, setEmbModel] = useState("qwen/qwen3-embedding-4b")
  const [rerankerModel, setRerankerModel] = useState("cohere/rerank-v3.5")
  const [showApiKey, setShowApiKey] = useState(false)
  const [saving, setSaving] = useState(false)

  const [models, setModels] = useState<ORModel[]>([])
  const [fetching, setFetching] = useState(false)
  const [fetchError, setFetchError] = useState("")


  const fetchModels = async () => {
    setFetching(true); setFetchError("")
    try {
      const res = await fetch("/api/proxy/openrouter-models", {
        headers: { "Accept": "application/json" },
        cache: "no-store",
      })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        const preview = text.slice(0, 200)
        throw new Error(`HTTP ${res.status}: ${preview}`)
      }
      // Verify content-type before parsing
      const ct = res.headers.get("content-type") || ""
      if (!ct.includes("application/json")) {
        const text = await res.text().catch(() => "")
        throw new Error(`Expected JSON but got ${ct}: ${text.slice(0, 200)}`)
      }
      const data = await res.json()
      if (data.error) throw new Error(data.error)
      setModels([...(data.llm || []), ...(data.embedding || [])])
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : String(err))
    } finally { setFetching(false) }
  }

  useEffect(() => { if (open) fetchModels() }, [open])

  const classifiedModels = useMemo(() => {
    const r = { llm: [] as ORModel[], vision: [] as ORModel[], embedding: [] as ORModel[], rerank: [...RERANK_MODELS] as ORModel[] }
    for (const m of models) {
      if (isEmbedding(m)) r.embedding.push(m)
      else {
        r.llm.push(m)
        if (hasVision(m)) r.vision.push(m)
      }
    }
    const sorter = (a: ORModel, b: ORModel) => {
      const aF = isFree(a) ? 0 : 1; const bF = isFree(b) ? 0 : 1
      return aF !== bF ? aF - bF : a.id.localeCompare(b.id)
    }
    for (const k of Object.keys(r) as (keyof typeof r)[]) r[k].sort(sorter)
    return r
  }, [models])

  // Chat models must support tool/function calling
  const chatModels = useMemo(
    () => classifiedModels.llm.filter(hasToolCalling),
    [classifiedModels.llm],
  )

  // Auto-select first free model in each category
  useEffect(() => {
    const pick = (list: ORModel[]) => { const f = list.find(isFree); return f?.id || list[0]?.id || "" }
    if (!llmModel && classifiedModels.llm.length > 0) setLlmModel(pick(classifiedModels.llm))
    if (!chatModel && chatModels.length > 0) setChatModel(pick(chatModels))
    if (!visualModel && classifiedModels.vision.length > 0) setVisualModel(pick(classifiedModels.vision))
    if (!embModel && classifiedModels.embedding.length > 0) setEmbModel(pick(classifiedModels.embedding))
    if (!rerankerModel && classifiedModels.rerank.length > 0) setRerankerModel(pick(classifiedModels.rerank))
  }, [classifiedModels.llm.length, classifiedModels.vision.length, classifiedModels.embedding.length])

  const handleSave = async () => {
    if (!apiKey.trim() || !llmModel.trim() || !chatModel.trim() || !embModel.trim() || !rerankerModel.trim()) {
      toast.error("API Key, LLM, Chat, Embedding, and Reranker are required")
      return
    }
    setSaving(true)
    try {
      const [llmList, embList, rerankList] = await Promise.all([
        getLLMProviders(), getEmbeddingProviders(), getRerankProviders(),
      ] as const)
      // Unset defaults one by one (avoid concurrent async_reload_services races)
      for (const p of llmList.filter((p: any) => p.is_default)) {
        await updateLLMProvider(p.id, { ...p, is_default: false })
      }
      for (const p of embList.filter((p: any) => p.is_default)) {
        await updateEmbeddingProvider(p.id, { ...p, is_default: false })
      }
      for (const p of rerankList.filter((p: any) => p.is_default)) {
        await updateRerankProvider(p.id, { ...p, is_default: false })
      }
      const selected = [...new Set([llmModel, chatModel, visualModel].filter(Boolean))]
      await createLLMProvider({ name: "OpenRouter", provider: "openai_compatible", model: llmModel.trim(), base_url: BASE_URL, api_key: apiKey.trim(), is_default: true, selected_models: selected as any, default_model: llmModel.trim(), visual_model_ids: visualModel.trim() ? [visualModel.trim()] : [], function_call_model_ids: [chatModel.trim()] } as any)
      await createEmbeddingProvider({ name: "OpenRouter", provider: "openai_compatible", model: embModel.trim(), base_url: BASE_URL, api_key: apiKey.trim(), dimensions: 1536, batch_size: 10, is_default: true } as any)
      await createRerankProvider({ name: "OpenRouter", provider: "openai_compatible", model: rerankerModel.trim(), base_url: BASE_URL, api_key: apiKey.trim(), is_default: true } as any)
      await updateConfig("default_chat_model", { default_chat_model: chatModel.trim() })
      if (visualModel.trim()) await updateConfig("visual_model_id", { visual_model_id: visualModel.trim() })
      toast.success("OpenRouter configured")
      onSaved(); onOpenChange(false); setApiKey("")
    } catch (err) { toast.error(`Setup failed: ${err instanceof Error ? err.message : String(err)}`) }
    finally { setSaving(false) }
  }

  const toOpts = (models: ORModel[], emptyLabel?: string) => {
    const opts = models.map((m) => ({
      value: m.id,
      label: isFree(m) ? `${m.id} · free` : m.id,
    }))
    return emptyLabel ? [{ value: "", label: emptyLabel }, ...opts] : opts
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "pm-dialog pm-dialog--silk pm-settings-dlg",
          "sm:max-w-lg",
          "!animate-none data-open:!animate-none data-closed:!animate-none",
        )}
        overlayClassName="pm-dialog-overlay--silk"
      >
        <DialogHeader>
          <DialogKicker>Settings</DialogKicker>
          <DialogTitle>OneShot OpenRouter</DialogTitle>
          <DialogDescription>
            Fetch the model catalog, then pick LLM, chat, vision, embedding, and rerank.
          </DialogDescription>
        </DialogHeader>

        <div className="pm-settings-dlg-scroll">
          <div className="pm-dialog-body pm-settings-dlg-body">
            <section className="pm-settings-dlg-card">
              <span className="pm-settings-dlg-card-kicker">API key</span>
              <div className="pm-settings-dlg-field">
                <FieldLabel>OpenRouter key</FieldLabel>
                <div className="pm-settings-dlg-secret">
                  <Input
                    type={showApiKey ? "text" : "password"}
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="sk-or-v1-..."
                  />
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    className="pm-settings-dlg-secret-btn"
                    onClick={() => setShowApiKey(!showApiKey)}
                  >
                    {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </Button>
                </div>
              </div>
              {fetching && (
                <div className="flex items-center gap-2 pm-meta">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  Fetching models…
                </div>
              )}
              {fetchError && (
                <div className="pm-settings-row-between gap-2">
                  <span className="pm-meta text-[var(--pm-danger)] min-w-0 truncate">
                    Failed: {fetchError}
                  </span>
                  <Button variant="ghost" size="xs" onClick={fetchModels}>
                    <RefreshCw className="w-3 h-3" />
                    Retry
                  </Button>
                </div>
              )}
            </section>

            {!fetching && !fetchError && models.length > 0 && (
              <section className="pm-settings-dlg-card">
                <span className="pm-settings-dlg-card-kicker">Models</span>
                <div className="pm-settings-dlg-fields">
                  <div className="pm-settings-dlg-field">
                    <FieldLabel>LLM</FieldLabel>
                    <DropdownSelect
                      value={llmModel}
                      onChange={setLlmModel}
                      options={toOpts(classifiedModels.llm)}
                      placeholder="Select LLM model…"
                    />
                  </div>
                  <div className="pm-settings-dlg-field">
                    <FieldLabel>Chat (tools)</FieldLabel>
                    <DropdownSelect
                      value={chatModel}
                      onChange={setChatModel}
                      options={toOpts(chatModels)}
                      placeholder="Select chat model…"
                    />
                    <p className="pm-settings-dlg-card-hint mt-1.5">
                      Only models that support tool / function calling.
                    </p>
                  </div>
                  <div className="pm-settings-dlg-field">
                    <FieldLabel>Visual</FieldLabel>
                    <DropdownSelect
                      value={visualModel}
                      onChange={setVisualModel}
                      options={toOpts(classifiedModels.vision, "None (skip)")}
                      placeholder="None (skip)"
                    />
                  </div>
                  <div className="pm-settings-dlg-grid">
                    <div className="pm-settings-dlg-field">
                      <FieldLabel>Embedding</FieldLabel>
                      <DropdownSelect
                        value={embModel}
                        onChange={setEmbModel}
                        options={toOpts(classifiedModels.embedding)}
                        placeholder="Select embedding…"
                      />
                    </div>
                    <div className="pm-settings-dlg-field">
                      <FieldLabel>Reranker</FieldLabel>
                      <DropdownSelect
                        value={rerankerModel}
                        onChange={setRerankerModel}
                        options={toOpts(classifiedModels.rerank)}
                        placeholder="Select reranker…"
                      />
                    </div>
                  </div>
                </div>
              </section>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="default" onClick={handleSave} disabled={saving || fetching}>
            {saving ? <><Loader2 className="h-4 w-4 animate-spin" />Setting up…</> : "Apply"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )

}
