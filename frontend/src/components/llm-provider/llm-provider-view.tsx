import { useEffect, useRef, useState, type CSSProperties } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Combobox } from "@/components/ui/combobox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { FieldLabel } from "@/components/ui/field-label"
import { Plus, Star, Pencil, Trash2, Plug, Loader2, Eye, EyeOff, Zap, Download, RefreshCw, Sparkles, MessageSquare, ChevronRight } from "lucide-react"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { cn } from "@/lib/utils"
import { useAppStore } from "@/stores/app-store"
import {
  getLLMProviders, type LLMProvider,
  getEmbeddingProviders, createEmbeddingProvider, updateEmbeddingProvider,
  deleteEmbeddingProvider, testEmbeddingProvider, setDefaultEmbeddingProvider,
  type EmbeddingProvider,
  getRerankProviders, createRerankProvider, updateRerankProvider,
  deleteRerankProvider, testRerankProvider, setDefaultRerankProvider,
  type RerankProvider,
  getFileTranscriptionProviders, createFileTranscriptionProvider, updateFileTranscriptionProvider,
  deleteFileTranscriptionProvider, setActiveFileTranscriptionProvider, testFileTranscriptionProvider,
  getRealtimeTranscriptionProviders, createRealtimeTranscriptionProvider, updateRealtimeTranscriptionProvider,
  deleteRealtimeTranscriptionProvider, setActiveRealtimeTranscriptionProvider,
  testRealtimeTranscriptionProvider,
  type TranscriptionProvider, type LanguageHintOption,
  getConfig, updateConfig, toggleModelLoad, getModelState, getModelStatus, getAvailableModels,
  deleteLocalModels,
  type ModelStatus,
} from "@/api/client"
import { useProviderTypes } from "@/hooks/use-provider-types"
import { toast } from "sonner"
import { ProviderCard } from "./provider-card"
import type { OneshotSlotSnapshot } from "./oneshot-slots"
import { AddProviderDialog } from "./add-provider-dialog"
import { LocalModelCard } from "./local-model-card"
import { ConnectionInfoCard } from "./connection-info-card"
import type { LoadDetail, LoadState } from "./local-model-card"
import { ModelDownloadDialog } from "@/components/model-download-dialog"
import { HotWordsManager } from "./hot-words-manager"
import { PeopleManager } from "./people-manager"
import { OneShotDashscopeDialog } from "./oneshot-dashscope-dialog"
import { OneShotOpenRouterDialog } from "./oneshot-openrouter-dialog"

const LOCAL_MODEL_BUNDLES = [
  {
    id: "file",
    label: "File transcription pack",
    description: "SenseVoice + VAD + speaker + punctuation",
    modelIds: ["transcription", "vad", "speaker", "punc"] as const,
  },
  {
    id: "realtime",
    label: "Realtime transcription",
    description: "Paraformer streaming",
    modelIds: ["realtime"] as const,
  },
] as const

const LOCAL_MODEL_IDS = LOCAL_MODEL_BUNDLES.flatMap((b) => [...b.modelIds])

// OpenRouter transcription models suitable for long audio (file transcription)
const OPENROUTER_TRANSCRIPTION_MODELS = [
  { value: "openai/whisper-large-v3-turbo", label: "OpenAI: Whisper Large V3 Turbo" },
  { value: "openai/whisper-large-v3", label: "OpenAI: Whisper Large V3" },
  { value: "openai/whisper-1", label: "OpenAI: Whisper V1" },
  { value: "openai/gpt-4o-transcribe", label: "OpenAI: GPT-4o Transcribe" },
  { value: "openai/gpt-4o-mini-transcribe", label: "OpenAI: GPT-4o Mini Transcribe" },
  { value: "google/chirp-3", label: "Google: Chirp 3" },
  { value: "nvidia/parakeet-tdt-0.6b-v3", label: "NVIDIA: Parakeet TDT 0.6B V3" },
  { value: "microsoft/mai-transcribe-1.5", label: "Microsoft: MAI Transcribe 1.5" },
  { value: "mistralai/voxtral-mini-transcribe", label: "Mistral: Voxtral Mini Transcribe" },
]

// ── Generic provider card for embedding/rerank ──

interface SimpleProviderCardProps<T extends { id: string; name: string; provider: string; model: string; base_url: string; is_default: boolean }> {
  provider: T
  subtitle?: string
  onEdit: (p: T) => void
  onRefresh: () => void
  onTest: (id: string) => Promise<{ success: boolean; error?: string }>
  onDelete: (id: string) => Promise<{ message?: string; error?: string }>
  onSetDefault: (id: string) => Promise<{ message?: string; error?: string }>
}

function SimpleProviderCard<T extends { id: string; name: string; provider: string; model: string; base_url: string; is_default: boolean }>({
  provider, subtitle, onEdit, onRefresh, onTest, onDelete, onSetDefault,
}: SimpleProviderCardProps<T>) {
  const [testing, setTesting] = useState(false)
  const [status, setStatus] = useState<"unknown" | "ready" | "error">("unknown")

  const statusClass =
    status === "ready" ? "is-ready" : status === "error" ? "is-error" : ""

  const handleTest = async () => {
    setTesting(true)
    try {
      const res = await onTest(provider.id)
      setStatus(res.success ? "ready" : "error")
      if (res.success) toast.success(`${provider.name}: connection OK`)
      else toast.error(`${provider.name}: ${res.error || "connection failed"}`)
    } catch {
      setStatus("error")
      toast.error("Test failed")
    } finally {
      setTesting(false)
    }
  }

  const handleDelete = async () => {
    try {
      const res = await onDelete(provider.id)
      if (res.error) toast.error(res.error)
      else {
        toast.success(
          res.message || `Provider '${provider.name || "Unnamed"}' deleted`,
        )
        onRefresh()
      }
    } catch { toast.error("Delete failed") }
  }

  const handleSetDefault = async () => {
    try {
      const res = await onSetDefault(provider.id)
      if (res.error) toast.error(res.error)
      else {
        toast.success(
          res.message || `Provider '${provider.name || "Unnamed"}' set as default`,
        )
        onRefresh()
      }
    } catch { toast.error("Failed to set default") }
  }

  return (
    <div className="pm-settings-provider-card">
      <div className="pm-settings-provider-top">
        <div className="pm-settings-provider-name-row">
          <span className="pm-settings-provider-name">{provider.name || "Unnamed"}</span>
          <span className={cn("pm-settings-status-dot", statusClass)} aria-hidden />
        </div>
        {provider.is_default && (
          <Badge variant="default" className="shrink-0">
            Default
          </Badge>
        )}
      </div>
      <p className="pm-settings-provider-meta">{provider.model || ""}</p>
      {subtitle ? <p className="pm-settings-provider-meta">{subtitle}</p> : null}
      <p className="pm-settings-provider-meta" title={provider.base_url || undefined}>
        {provider.base_url || ""}
      </p>
      <div className="pm-settings-provider-actions">
        <Button variant="ghost" size="sm" onClick={handleTest} disabled={testing}>
          {testing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plug className="h-3 w-3" />}
          Test
        </Button>
        <Button variant="ghost" size="sm" onClick={handleSetDefault} disabled={provider.is_default}>
          <Star className="h-3 w-3" />
          Default
        </Button>
        <Button variant="secondary" size="sm" onClick={() => onEdit(provider)}>
          <Pencil className="h-3 w-3" />
          Edit
        </Button>
        <Button variant="destructive" size="sm" onClick={handleDelete}>
          <Trash2 className="h-3 w-3" />
          Delete
        </Button>
      </div>
    </div>
  )
}

function SettingsSwitch({
  checked,
  onCheckedChange,
  label,
  id,
  disabled,
}: {
  checked: boolean
  onCheckedChange: (next: boolean) => void
  label: string
  id: string
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      id={id}
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={cn("pm-config-switch", checked && "is-on")}
      onClick={() => !disabled && onCheckedChange(!checked)}
    >
      <span className="pm-config-switch-thumb" aria-hidden />
    </button>
  )
}

// ── Generic provider dialog for embedding/rerank ──

interface FieldDef {
  key: string
  label: string
  type?: string
  placeholder?: string
  options?: { value: string; label: string }[]
}

interface SimpleProviderDialogProps<T extends { id: string }> {
  open: boolean
  provider: T | null
  title: string
  /** Green dialog kicker — model type: LLM · Embedding · Rerank · Transcription … */
  kicker?: string
  fields: FieldDef[]
  getTransFields?: (form: Record<string, string>) => FieldDef[]
  defaults: Record<string, unknown>
  onOpenChange: (open: boolean) => void
  onSaved: () => void
  onCreate: (data: Record<string, unknown>) => Promise<T>
  onUpdate: (id: string, data: Record<string, unknown>) => Promise<T>
  checkboxField?: string
  checkboxLabel?: string
  modelFetchSection?: string  // "embedding" or "rerank" — enables fetch+dropdown for model field
  renderExtra?: (form: Record<string, string>, set: (k: string, v: string) => void) => React.ReactNode
}

function SimpleProviderDialog<T extends { id: string }>({
  open, provider, title, kicker, fields, getTransFields, defaults, onOpenChange, onSaved, onCreate, onUpdate,
  checkboxField = "is_default", checkboxLabel = "Set as default",
  modelFetchSection, renderExtra,
}: SimpleProviderDialogProps<T>) {
  const [form, setForm] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [showApiKey, setShowApiKey] = useState(false)
  const [fetchingModels, setFetchingModels] = useState(false)
  const [availableModels, setAvailableModels] = useState<string[]>([])

  // Resolve fields dynamically if getTransFields is provided
  const resolvedFields = getTransFields ? getTransFields(form) : fields

  // Keep form in sync when resolved fields change (e.g. adapter switch)
  useEffect(() => {
    if (!getTransFields) return
    setForm((prev) => {
      const next = { ...prev }
      for (const f of resolvedFields) {
        if (next[f.key] === undefined) next[f.key] = String(defaults[f.key] ?? "")
      }
      return next
    })
  }, [JSON.stringify(resolvedFields.map((f) => f.key))])

  useEffect(() => {
    if (provider) {
      const init: Record<string, string> = {}
      for (const f of resolvedFields) {
        init[f.key] = String((provider as Record<string, unknown>)[f.key] ?? defaults[f.key] ?? "")
      }
      setForm(init)
    } else {
      const init: Record<string, string> = {}
      for (const f of resolvedFields) {
        init[f.key] = String(defaults[f.key] ?? "")
      }
      setForm(init)
    }
    setShowApiKey(false)
    setAvailableModels([])
  }, [provider, open])

  const set = (key: string, value: string) => setForm((prev) => ({ ...prev, [key]: value }))

  const fetchModels = async () => {
    if (!form.base_url?.trim()) {
      toast.error("Enter a base URL first")
      return
    }
    if (!modelFetchSection) return
    setFetchingModels(true)
    try {
      const res = await getAvailableModels(modelFetchSection, {
        base_url: form.base_url,
        api_key: form.api_key || undefined,
        provider: form.provider || undefined,
      })
      if (res.error) {
        toast.error(res.error)
      } else {
        setAvailableModels(res.models || [])
        if (res.models?.length) {
          toast.success(`Found ${res.models.length} models`)
        } else {
          toast.info("No models returned")
        }
      }
    } catch (err) {
      toast.error(`Failed to fetch models: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setFetchingModels(false)
    }
  }

  const handleSave = async () => {
    if (!form.name?.trim()) { toast.error("Name is required"); return }
    setSaving(true)
    try {
      const data: Record<string, unknown> = {}
      for (const f of resolvedFields) {
        const v = form[f.key]
        if (f.type === "number") data[f.key] = parseInt(v) || 0
        else data[f.key] = v
      }
      data[checkboxField] = form[checkboxField] === "true"
      if (provider) await onUpdate(provider.id, data)
      else await onCreate(data)
      toast.success(provider ? "Updated" : "Created")
      onSaved()
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally { setSaving(false) }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "pm-dialog pm-dialog--silk pm-settings-dlg",
          "sm:max-w-md",
          "!animate-none data-open:!animate-none data-closed:!animate-none",
        )}
        overlayClassName="pm-dialog-overlay--silk"
      >
        <DialogHeader>
          <DialogKicker>{kicker || title}</DialogKicker>
          <DialogTitle>{provider ? "Edit provider" : "Add provider"}</DialogTitle>
          <DialogDescription>
            {provider
              ? `Update connection details for this ${title.toLowerCase()}.`
              : `Add a ${title.toLowerCase()} with endpoint and credentials.`}
          </DialogDescription>
        </DialogHeader>
        <div className="pm-settings-dlg-scroll">
          <div className="pm-dialog-body pm-settings-dlg-body">
            <section className="pm-settings-dlg-card">
              <span className="pm-settings-dlg-card-kicker">Connection</span>
              <div className="pm-settings-dlg-fields">
                {resolvedFields.map((f) => (
                  <div key={f.key} className="pm-settings-dlg-field">
                    <FieldLabel>{f.label}</FieldLabel>
                    {f.key === "model" && modelFetchSection && !f.options?.length ? (
                      <>
                        <div className="flex gap-2">
                          <Combobox
                            value={form.model || ""}
                            onChange={(v) => set("model", v)}
                            options={availableModels}
                            placeholder={f.placeholder}
                            className="flex-1"
                          />
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            onClick={fetchModels}
                            disabled={fetchingModels || !form.base_url?.trim()}
                            title="Fetch models"
                          >
                            {fetchingModels ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <RefreshCw className="h-4 w-4" />
                            )}
                          </Button>
                        </div>
                        {availableModels.length === 0 && !fetchingModels && form.base_url?.trim() && (
                          <p className="pm-settings-dlg-card-hint mt-1.5">
                            Refresh to fetch models from the base URL.
                          </p>
                        )}
                      </>
                    ) : f.options ? (
                      <DropdownSelect
                        value={form[f.key] || ""}
                        onChange={(v) => set(f.key, v)}
                        options={f.options}
                      />
                    ) : f.key === "api_key" ? (
                      <div className="pm-settings-dlg-secret">
                        <Input
                          type={showApiKey ? "text" : "password"}
                          value={form[f.key] || ""}
                          onChange={(e) => set(f.key, e.target.value)}
                          placeholder={f.placeholder}
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
                    ) : (
                      <Input
                        type={f.type || "text"}
                        value={form[f.key] || ""}
                        onChange={(e) => set(f.key, e.target.value)}
                        placeholder={f.placeholder}
                      />
                    )}
                  </div>
                ))}
                {renderExtra?.(form, set)}
              </div>
              <div className="pm-settings-dlg-pref">
                <p className="pm-settings-dlg-pref-label">
                  Prefer this provider when multiple are available
                </p>
                <button
                  type="button"
                  className={cn("pm-field-chip", form[checkboxField] === "true" && "is-on")}
                  aria-pressed={form[checkboxField] === "true"}
                  onClick={() =>
                    set(checkboxField, form[checkboxField] === "true" ? "false" : "true")
                  }
                >
                  <Star className="h-3 w-3" strokeWidth={1.75} />
                  {form[checkboxField] === "true" ? "Default" : checkboxLabel}
                </button>
              </div>
            </section>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="default" onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : provider ? "Update" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── Transcription provider card ──

interface TranscriptionProviderCardProps {
  provider: TranscriptionProvider
  kind: "file" | "realtime"
  onEdit: (p: TranscriptionProvider) => void
  onRefresh: () => void
  onDelete: (id: string) => Promise<{ message?: string; error?: string }>
  onSetActive: (id: string) => Promise<{ message?: string; error?: string }>
  onTest: (id: string) => Promise<{ success: boolean; message?: string; error?: string }>
}

function TranscriptionProviderCard({ provider, onEdit, onRefresh, onDelete, onSetActive, onTest }: TranscriptionProviderCardProps) {
  const modelLabel = provider.model || provider.adapter

  const [status, setStatus] = useState<"unknown" | "ready" | "error">("unknown")
  const [testing, setTesting] = useState(false)
  const statusClass =
    status === "ready" ? "is-ready" : status === "error" ? "is-error" : ""

  const handleTest = async () => {
    setTesting(true)
    try {
      const res = await onTest(provider.id)
      if (res.success) {
        setStatus("ready")
        toast.success(res.message || "Test passed")
      } else {
        setStatus("error")
        toast.error(res.error || "Test failed")
      }
    } catch {
      setStatus("error")
      toast.error("Test failed")
    } finally {
      setTesting(false)
    }
  }

  const handleDelete = async () => {
    try {
      const res = await onDelete(provider.id)
      if (res.error) toast.error(res.error)
      else {
        toast.success(
          res.message || `Provider '${provider.name || "Unnamed"}' deleted`,
        )
        onRefresh()
      }
    } catch { toast.error("Delete failed") }
  }

  const handleSetActive = async () => {
    try {
      const res = await onSetActive(provider.id)
      if (res.error) toast.error(res.error)
      else {
        toast.success(
          res.message || `Provider '${provider.name || "Unnamed"}' set as default`,
        )
        onRefresh()
      }
    } catch { toast.error("Failed to set default") }
  }

  return (
    <div className="pm-settings-provider-card">
      <div className="pm-settings-provider-top">
        <div className="pm-settings-provider-name-row">
          <span className="pm-settings-provider-name">{provider.name || "Unnamed"}</span>
          <span className={cn("pm-settings-status-dot", statusClass)} aria-hidden />
        </div>
        {provider.is_active && (
          <Badge variant="default" className="shrink-0">
            Default
          </Badge>
        )}
      </div>
      <p className="pm-settings-provider-meta">{modelLabel}</p>
      <div className="min-h-[1rem]" />
      <div className="pm-settings-provider-actions">
        <Button variant="ghost" size="sm" onClick={handleSetActive} disabled={provider.is_active}>
          <Star className="h-3 w-3" />
          Default
        </Button>
        <Button variant="ghost" size="sm" onClick={handleTest} disabled={testing}>
          {testing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Zap className="h-3 w-3" />}
          Test
        </Button>
        <Button variant="secondary" size="sm" onClick={() => onEdit(provider)}>
          <Pencil className="h-3 w-3" />
          Edit
        </Button>
        <Button variant="destructive" size="sm" onClick={handleDelete}>
          <Trash2 className="h-3 w-3" />
          Delete
        </Button>
      </div>
    </div>
  )
}

// ── Main View ──

export function LLMProviderView() {
  const { providers, setProviders, developerMode, toggleDeveloperMode } = useAppStore()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingProvider, setEditingProvider] = useState<LLMProvider | null>(null)
  const [modelDownloadOpen, setModelDownloadOpen] = useState(false)

  // Embedding providers
  const [embProviders, setEmbProviders] = useState<EmbeddingProvider[]>([])
  const [embDialogOpen, setEmbDialogOpen] = useState(false)
  const [editingEmb, setEditingEmb] = useState<EmbeddingProvider | null>(null)

  // Rerank providers
  const [rerankProviders, setRerankProviders] = useState<RerankProvider[]>([])
  const [rerankDialogOpen, setRerankDialogOpen] = useState(false)
  const [editingRerank, setEditingRerank] = useState<RerankProvider | null>(null)

  // File transcription providers
  const [fileTransProviders, setFileTransProviders] = useState<TranscriptionProvider[]>([])
  const [fileTransDialogOpen, setFileTransDialogOpen] = useState(false)
  const [editingFileTrans, setEditingFileTrans] = useState<TranscriptionProvider | null>(null)

  // Realtime transcription providers
  const [rtTransProviders, setRtTransProviders] = useState<TranscriptionProvider[]>([])
  const [rtTransDialogOpen, setRtTransDialogOpen] = useState(false)
  const [editingRtTrans, setEditingRtTrans] = useState<TranscriptionProvider | null>(null)

  // Hot words manager
  const [hotWordsManagerOpen, setHotWordsManagerOpen] = useState(false)
  const [peopleManagerOpen, setPeopleManagerOpen] = useState(false)

  // OneShot Dashscope dialog
  const [oneshotDialogOpen, setOneshotDialogOpen] = useState(false)
const [openrouterDialogOpen, setOpenrouterDialogOpen] = useState(false)

  // Language hints config editor state for file transcription openai_compatible adapter
  const [fileTransLangHints, setFileTransLangHints] = useState<LanguageHintOption[]>([])

  // Local model catalog (download management)
  const [localModelCatalog, setLocalModelCatalog] = useState<ModelStatus[]>([])
  const [deletingModelIds, setDeletingModelIds] = useState<Set<string>>(new Set())
  const [deleteLocalModelsOpen, setDeleteLocalModelsOpen] = useState(false)

  // Visual Model selection
  const [visualModelId, setVisualModelId] = useState<string>("")

  // Chat Model selection
  const [chatModelId, setChatModelId] = useState<string>("")

  // MinerU cloud parsing options
  const MINERU_MODEL_OPTIONS = [
    { value: "pipeline", label: "Pipeline (Default)" },
    { value: "vlm", label: "VLM (Recommended)" },
    { value: "MinerU-HTML", label: "MinerU HTML" },
  ]
  const MINERU_LANGUAGE_OPTIONS = [
    { value: "ch", label: "Chinese + English + Traditional Chinese" },
    { value: "ch_server", label: "Chinese + Japanese (server)" },
    { value: "en", label: "English" },
    { value: "japan", label: "Japanese" },
    { value: "korean", label: "Korean" },
    { value: "chinese_cht", label: "Traditional Chinese" },
    { value: "ta", label: "Tamil" },
    { value: "te", label: "Telugu" },
    { value: "ka", label: "Kannada" },
    { value: "el", label: "Greek" },
    { value: "th", label: "Thai" },
    { value: "latin", label: "Latin (40+ languages)" },
    { value: "arabic", label: "Arabic" },
    { value: "cyrillic", label: "Cyrillic (30+ languages)" },
    { value: "east_slavic", label: "East Slavic (RU/UA/BY)" },
    { value: "devanagari", label: "Devanagari (Hindi/Marathi/Nepali)" },
  ]

  const [ragTopK,setRagTopK]=useState("20");const [ragRerankTopK,setRagRerankTopK]=useState("5");const [ragMaxParallel,setRagMaxParallel]=useState("10");const [ragMaxIter,setRagMaxIter]=useState("8");const [ragSearchMode,setRagSearchMode]=useState("hybrid");const [ragMinScore,setRagMinScore]=useState("25")
  const [dirTopK,setDirTopK]=useState("20");const [dirRerankTopK,setDirRerankTopK]=useState("5");const [dirSearchMode,setDirSearchMode]=useState("hybrid");const [dirRerankEnabled,setDirRerankEnabled]=useState(true);const [dirMinScore,setDirMinScore]=useState("25")
  const [enrichMaxParallel,setEnrichMaxParallel]=useState("50");const [enrichModel,setEnrichModel]=useState("")
  const [meetingModel,setMeetingModel]=useState("")
  const [agenticQueryModel,setAgenticQueryModel]=useState("")
  const [noteDistillModel,setNoteDistillModel]=useState("")
  const [showAdvanced,setShowAdvanced]=useState(false)
  const [showModelConfig,setShowModelConfig]=useState(false)
  const _saveRag=(mode?:string)=>updateConfig("rag",{top_k:parseInt(ragTopK)||20,rerank_top_k:parseInt(ragRerankTopK)||5,max_parallel_queries:parseInt(ragMaxParallel)||10,max_iterations:parseInt(ragMaxIter)||8,default_search_mode:mode??ragSearchMode,min_score:(parseInt(ragMinScore)||0)/100}).catch(()=>{})
  const _saveDir=(mode?:string)=>updateConfig("direct_rag",{top_k:parseInt(dirTopK)||20,rerank_top_k:parseInt(dirRerankTopK)||5,use_reranker:dirRerankEnabled,default_search_mode:mode??dirSearchMode,min_score:(parseInt(dirMinScore)||0)/100}).catch(()=>{})
  // MinerU cloud parsing settings
  const [mineruEnabled, setMineruEnabled] = useState(false)
  const [mineruToken, setMineruToken] = useState("")
  const [mineruModel, setMineruModel] = useState("pipeline")
  const [mineruOcr, setMineruOcr] = useState(false)
  const [mineruFormula, setMineruFormula] = useState(true)
  const [mineruTable, setMineruTable] = useState(true)
  const [mineruLanguage, setMineruLanguage] = useState("ch")
  const [showMineruToken, setShowMineruToken] = useState(false)

  // Web search (Tavily) — Settings only stores API key; Chat UI owns the toggle
  const [webSearchApiKey, setWebSearchApiKey] = useState("")
  const [showWebSearchKey, setShowWebSearchKey] = useState(false)

  // Runtime load states from backend
  const [loadStates, setLoadStates] = useState<Record<string, LoadState>>({})
  const [loadDetails, setLoadDetails] = useState<Record<string, LoadDetail>>({})
  const loadStatesRef = useRef(loadStates)
  loadStatesRef.current = loadStates
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const asLoadState = (v: string | undefined): LoadState => {
    if (v === "loading" || v === "loaded" || v === "error" || v === "unloaded") return v
    return "unloaded"
  }

  const asLoadDetail = (
    d?: { state?: string; message?: string; error?: string; started_at?: number; load_s?: number },
  ): LoadDetail | undefined => {
    if (!d) return undefined
    return {
      state: d.state ? asLoadState(d.state) : undefined,
      message: d.message,
      error: d.error,
      started_at: d.started_at,
      load_s: d.load_s,
    }
  }

  // Model download status (id → downloaded)
  const [modelDownloaded, setModelDownloaded] = useState<Record<string, boolean>>({})

  const refreshModelDownloaded = async () => {
    try {
      const status = await getModelStatus()
      setLocalModelCatalog(status)
      const map: Record<string, boolean> = {}
      for (const m of status) {
        map[m.id] = m.downloaded
      }
      // builtin-local-file needs transcription + vad + speaker + punc
      // builtin-local-rt needs realtime
      const fileTransReady = (map.transcription && map.vad && map.speaker && map.punc)
      setModelDownloaded({
        "builtin-local-file": !!fileTransReady,
        "builtin-local-rt": map.realtime || false,
      })
    } catch { /* ignore */ }
  }

  // Fetch runtime load states, poll while any model is loading/downloading
  const refreshLoadStates = async () => {
    try {
      const state = await getModelState()
      const server = state.load_states || {}
      const prev = loadStatesRef.current
      // Merge: keep local "loading" until server reports a real terminal/loading state
      const next: Record<string, LoadState> = { ...prev }
      for (const [id, st] of Object.entries(server)) {
        next[id] = asLoadState(st)
      }
      // Toast only on real transitions from loading → loaded/error
      for (const [id, st] of Object.entries(next)) {
        const was = prev[id]
        if (was === "loading" && st === "loaded") {
          const detail = state.load_details?.[id]
          const took =
            typeof detail?.load_s === "number" ? ` in ${detail.load_s}s` : ""
          toast.success(
            id === "builtin-local-rt"
              ? `Realtime model ready${took}`
              : `File transcription model ready${took}`,
          )
        }
        if (was === "loading" && st === "error") {
          const detail = state.load_details?.[id]
          toast.error(detail?.error || detail?.message || "Model load failed")
        }
      }
      setLoadStates(next)
      setLoadDetails((prevDetails) => {
        const merged: Record<string, LoadDetail> = { ...prevDetails }
        for (const [id, d] of Object.entries(state.load_details || {})) {
          const parsed = asLoadDetail(d)
          if (parsed) merged[id] = parsed
        }
        return merged
      })
      return next
    } catch {
      return loadStatesRef.current
    }
  }

  const startPolling = (immediate = true) => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current)
      pollTimerRef.current = null
    }
    const poll = async () => {
      const states = await refreshLoadStates()
      // Avoid hammering full catalog during pure load; still refresh lightly
      const stillLoading = Object.values(states).some((v) => v === "loading")
      let stillDownloading = false
      try {
        const ms = await getModelStatus()
        setLocalModelCatalog(ms)
        stillDownloading = ms.some(
          (m) => m.status === "downloading" || m.status === "extracting"
        )
        const map: Record<string, boolean> = {}
        for (const m of ms) map[m.id] = m.downloaded
        setModelDownloaded({
          "builtin-local-file": !!(map.transcription && map.vad && map.speaker && map.punc),
          "builtin-local-rt": map.realtime || false,
        })
      } catch { /* ignore */ }
      if (stillLoading || stillDownloading) {
        // Fast poll while loading so UI feels responsive
        pollTimerRef.current = setTimeout(poll, stillLoading ? 500 : 1500)
      } else {
        pollTimerRef.current = null
      }
    }
    if (immediate) void poll()
    else pollTimerRef.current = setTimeout(poll, 500)
  }

  // Auto-poll on mount if anything is in progress
  useEffect(() => {
    const init = async () => {
      await refreshLoadStates()
      await refreshModelDownloaded()
      try {
        const ms = await getModelStatus()
        const states = await getModelState()
        const isLoading = Object.values(states.load_states || {}).some((v) => v === "loading")
        const isDownloading = ms.some(
          (m) => m.status === "downloading" || m.status === "extracting"
        )
        if (isLoading || isDownloading) {
          startPolling(true)
        }
      } catch { /* ignore */ }
    }
    init()
    return () => {
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
    }
  }, [])

  // Extract built-in providers from each list
  const builtinFileTrans = fileTransProviders.find((p) => p.id === "builtin-local-file") ?? null
  const builtinRtTrans = rtTransProviders.find((p) => p.id === "builtin-local-rt") ?? null

  // Filter out built-in local providers — those are shown in Local Models section
  const cloudFileProviders = fileTransProviders.filter((p) => !p.id.startsWith("builtin-"))
  const cloudRtProviders = rtTransProviders.filter((p) => !p.id.startsWith("builtin-"))

  // ── LLM ──
  const fetchProviders = async () => {
    try {
      const list = await getLLMProviders()
      setProviders(list.map((p) => ({ ...p, status: "unknown" as const })))
      // Sync default_chat_model: if no fc-capable default exists, clear it
      const fcDefaults = list.filter(p => p.is_default && (p.function_call_model_ids ?? []).length > 0)
      if (fcDefaults.length === 0) {
        setChatModelId("")
        updateConfig("default_chat_model", { default_chat_model: null }).catch(() => {})
      } else {
        // Re-fetch config to refresh chatModelId in case backend auto-set it
        getConfig().then(c => {
          if (c.default_chat_model && typeof c.default_chat_model === "string") setChatModelId(c.default_chat_model)
        }).catch(() => {})
      }
    } catch { toast.error("Failed to load providers") }
  }

  // ── Embedding ──
  const fetchEmbProviders = async () => {
    try { setEmbProviders(await getEmbeddingProviders()) } catch { /* ignore */ }
  }

  // ── Rerank ──
  const fetchRerankProviders = async () => {
    try { setRerankProviders(await getRerankProviders()) } catch { /* ignore */ }
  }

  // ── File Transcription ──
  const fetchFileTransProviders = async () => {
    try { setFileTransProviders(await getFileTranscriptionProviders()) } catch { /* ignore */ }
  }

  // ── Realtime Transcription ──
  const fetchRtTransProviders = async () => {
    try { setRtTransProviders(await getRealtimeTranscriptionProviders()) } catch { /* ignore */ }
  }

  const confirmDeleteLocalModels = async () => {
    const ids = [...LOCAL_MODEL_IDS]
    const hasFiles = ids.some((id) => {
      const m = localModelCatalog.find((x) => x.id === id)
      return m?.downloaded || m?.status === "error"
    })
    if (!hasFiles) {
      setDeleteLocalModelsOpen(false)
      toast.message("Nothing to delete")
      return
    }
    setDeletingModelIds((prev) => new Set([...prev, ...ids]))
    try {
      const res = await deleteLocalModels(ids)
      if (!res.success) {
        toast.error(res.error || "Delete failed")
      } else {
        const freed = res.freed_mb ? ` (~${res.freed_mb} MB)` : ""
        toast.success(`Local models deleted${freed}`)
        setDeleteLocalModelsOpen(false)
      }
      await refreshModelDownloaded()
      await refreshLoadStates()
      fetchFileTransProviders()
      fetchRtTransProviders()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setDeletingModelIds((prev) => {
        const next = new Set(prev)
        ids.forEach((id) => next.delete(id))
        return next
      })
    }
  }

  const applyLlmSlotConfig = (c: Awaited<ReturnType<typeof getConfig>>) => {
    setVisualModelId(typeof c.visual_model_id === "string" ? c.visual_model_id : "")
    setChatModelId(typeof c.default_chat_model === "string" ? c.default_chat_model : "")
    const e = c.enrichment
    if (!e) return
    if (typeof e.max_parallel_context === "number") setEnrichMaxParallel(String(e.max_parallel_context))
    if (typeof e.enrichment_model === "string") setEnrichModel(e.enrichment_model)
    if (typeof e.meeting_model === "string") setMeetingModel(e.meeting_model)
    if (typeof e.agentic_query_model === "string") setAgenticQueryModel(e.agentic_query_model)
    if (typeof e.note_distill_model === "string") setNoteDistillModel(e.note_distill_model)
  }

  const applyOneshotSlots = (slots?: OneshotSlotSnapshot) => {
    if (!slots) return
    setVisualModelId(slots.visual_model_id)
    setChatModelId(slots.default_chat_model)
    setEnrichModel(slots.enrichment_model)
    setMeetingModel(slots.meeting_model)
    setAgenticQueryModel(slots.agentic_query_model)
    setNoteDistillModel(slots.note_distill_model)
  }

  const refreshAfterOneshot = (slots?: OneshotSlotSnapshot) => {
    applyOneshotSlots(slots)
    fetchProviders()
    fetchEmbProviders()
    fetchRerankProviders()
    fetchFileTransProviders()
    fetchRtTransProviders()
    getConfig()
      .then((c) => {
        if (!slots) {
          applyLlmSlotConfig(c)
          return
        }
        if (typeof c.visual_model_id === "string" && c.visual_model_id) setVisualModelId(c.visual_model_id)
        if (typeof c.default_chat_model === "string" && c.default_chat_model) setChatModelId(c.default_chat_model)
        const e = c.enrichment
        if (!e) return
        if (typeof e.enrichment_model === "string" && e.enrichment_model) setEnrichModel(e.enrichment_model)
        if (typeof e.meeting_model === "string" && e.meeting_model) setMeetingModel(e.meeting_model)
        if (typeof e.agentic_query_model === "string" && e.agentic_query_model) setAgenticQueryModel(e.agentic_query_model)
        if (typeof e.note_distill_model === "string" && e.note_distill_model) setNoteDistillModel(e.note_distill_model)
      })
      .catch(() => {})
  }

  useEffect(() => {
    fetchProviders()
    fetchEmbProviders()
    fetchRerankProviders()
    fetchFileTransProviders()
    fetchRtTransProviders()
    refreshModelDownloaded()
    getConfig().then((c) => {
      applyLlmSlotConfig(c)
      if(c.rag){if(typeof c.rag.top_k==="number")setRagTopK(String(c.rag.top_k));if(typeof c.rag.rerank_top_k==="number")setRagRerankTopK(String(c.rag.rerank_top_k));if(typeof c.rag.max_parallel_queries==="number")setRagMaxParallel(String(c.rag.max_parallel_queries));if(typeof c.rag.max_iterations==="number")setRagMaxIter(String(c.rag.max_iterations));if(typeof c.rag.default_search_mode==="string")setRagSearchMode(c.rag.default_search_mode);if(typeof c.rag.min_score==="number")setRagMinScore(String(c.rag.min_score))}
      if(c.direct_rag){if(typeof c.direct_rag.top_k==="number")setDirTopK(String(c.direct_rag.top_k));if(typeof c.direct_rag.rerank_top_k==="number")setDirRerankTopK(String(c.direct_rag.rerank_top_k));if(typeof c.direct_rag.default_search_mode==="string")setDirSearchMode(c.direct_rag.default_search_mode);setDirRerankEnabled(c.direct_rag.use_reranker!==false);if(typeof c.direct_rag.min_score==="number")setDirMinScore(String(c.direct_rag.min_score))}
      // Load MinerU config
      if (c.mineru) {
        setMineruEnabled(!!c.mineru.enabled)
        setMineruToken(typeof c.mineru.api_token === "string" ? c.mineru.api_token : "")
        setMineruModel(typeof c.mineru.model_version === "string" ? c.mineru.model_version : "pipeline")
        setMineruOcr(!!c.mineru.is_ocr)
        setMineruFormula(c.mineru.enable_formula !== false)
        setMineruTable(c.mineru.enable_table !== false)
        setMineruLanguage(typeof c.mineru.language === "string" ? c.mineru.language : "ch")
      }
      // Load Web Search (Tavily) API key only
      if (c.web_search) {
        setWebSearchApiKey(typeof c.web_search.api_key === "string" ? c.web_search.api_key : "")
      }
    }).catch(() => {})
  }, [])

  const handleAdd = () => { setEditingProvider(null); setDialogOpen(true) }
  const handleEdit = (provider: LLMProvider) => { setEditingProvider(provider); setDialogOpen(true) }
  const handleSaved = () => { setDialogOpen(false); setEditingProvider(null); fetchProviders() }

  // Dynamic provider type lists from backend registry
  const providerTypes = useProviderTypes()
  const embOptions = providerTypes.embedding.map((p) => ({ value: p.name, label: p.display_name }))
  const rerankOptions = providerTypes.reranker.map((p) => ({ value: p.name, label: p.display_name }))
  const ftAdapterOpts = providerTypes.file_transcription.map((p) => ({ value: p.name, label: p.display_name }))
  const rtAdapterOpts = providerTypes.realtime_transcription.map((p) => ({ value: p.name, label: p.display_name }))

  const embFields: FieldDef[] = [
    { key: "name", label: "Name", placeholder: "My Embedding" },
    { key: "provider", label: "Provider", options: embOptions },
    { key: "model", label: "Model", placeholder: "text-embedding-3-small" },
    { key: "base_url", label: "Base URL", placeholder: "https://api.openai.com/v1" },
    { key: "api_key", label: "API Key", type: "password", placeholder: "sk-..." },
    { key: "batch_size", label: "Batch Size", type: "number", placeholder: "10" },
  ]

  const rerankFields: FieldDef[] = [
    { key: "name", label: "Name", placeholder: "My Reranker" },
    { key: "provider", label: "Provider", options: rerankOptions },
    { key: "model", label: "Model", placeholder: "rerank-multilingual-v3.0" },
    { key: "base_url", label: "Base URL", placeholder: "https://api.cohere.com/v1" },
    { key: "api_key", label: "API Key", type: "password", placeholder: "sk-..." },
  ]

  const fileTransFields: FieldDef[] = [
    { key: "name", label: "Name", placeholder: "My File Transcription" },
    { key: "adapter", label: "Adapter", options: ftAdapterOpts },
  ]

  const getFileTransFields = (form: Record<string, string>): FieldDef[] => {
    const adapter = form.adapter || ""
    // Local FunASR is ONNX-only (CPU via onnxruntime); no torch device picker.
    if (adapter === "openai_compatible") {
      return [
        ...fileTransFields,
        { key: "base_url", label: "Base URL", placeholder: "https://api.openai.com/v1" },
        { key: "model", label: "Model", placeholder: "whisper-1" },
        { key: "api_key", label: "API Key", type: "password", placeholder: "sk-..." },
      ]
    }
    if (adapter === "openrouter") {
      return [
        ...fileTransFields,
        { key: "base_url", label: "Base URL", placeholder: "https://openrouter.ai/api/v1" },
        { key: "model", label: "Model", options: OPENROUTER_TRANSCRIPTION_MODELS },
        { key: "api_key", label: "API Key", type: "password", placeholder: "sk-or-v1-..." },
      ]
    }
    if (adapter === "dashscope_funasr") {
      return [
        ...fileTransFields,
        {
          key: "model",
          label: "Model",
          options: [
            { value: "fun-asr", label: "fun-asr (FunASR cloud)" },
            { value: "qwen-audio-3.0-asr-flash-filetrans", label: "qwen-audio-3.0-asr-flash-filetrans" },
          ],
        },
        { key: "api_key", label: "API Key", type: "password", placeholder: "sk-..." },
      ]
    }
    // Other remote adapters: only api_key
    return [
      ...fileTransFields,
      { key: "api_key", label: "API Key", type: "password", placeholder: "sk-..." },
    ]
  }

  const rtTransFields: FieldDef[] = [
    { key: "name", label: "Name", placeholder: "My Realtime Transcription" },
    { key: "adapter", label: "Adapter", options: rtAdapterOpts },
  ]

  const getRtTransFields = (form: Record<string, string>): FieldDef[] => {
    const adapter = form.adapter || ""
    // Local FunASR realtime is ONNX-only (CPU); no torch device picker.
    if (adapter === "openai_compatible") {
      return [
        ...rtTransFields,
        { key: "base_url", label: "Base URL", placeholder: "https://api.openai.com/v1" },
        { key: "model", label: "Model", placeholder: "gpt-4o-realtime-preview" },
        { key: "api_key", label: "API Key", type: "password", placeholder: "sk-..." },
      ]
    }
    if (adapter === "dashscope_funasr_realtime") {
      return [
        ...rtTransFields,
        {
          key: "model",
          label: "Model",
          options: [
            { value: "fun-asr-realtime", label: "fun-asr-realtime (FunASR cloud)" },
            { value: "qwen-audio-3.0-asr-flash-streaming", label: "qwen-audio-3.0-asr-flash-streaming" },
          ],
        },
        { key: "api_key", label: "API Key", type: "password", placeholder: "sk-..." },
      ]
    }
    return [
      ...rtTransFields,
      { key: "api_key", label: "API Key", type: "password", placeholder: "sk-..." },
    ]
  }


  const visualModelOptions = (() => {
    const visualModels = providers.flatMap((p) =>
      (p.visual_model_ids || []).map((m) => ({
        value: `${p.id}|${m}`,
        label: `${p.name || p.id} / ${m}`,
      })),
    )
    return [{ value: "", label: "None (disabled)" }, ...visualModels]
  })()

  const chatModelOptions = (() => {
    const chatModels = providers
      .flatMap((p) =>
        (p.selected_models || (p.model ? [p.model] : [])).map((m) => ({
          value: `${p.id}|${m}`,
          label: `${p.name || p.id} / ${m}`,
          isFunctionCall: ((p as { function_call_model_ids?: string[] }).function_call_model_ids || []).includes(m),
        })),
      )
      .filter((cm) => cm.isFunctionCall)
    return [{ value: "", label: "Default" }, ...chatModels.map(({ value, label }) => ({ value, label }))]
  })()

  const coerceSlotValue = (stored: string, options: { value: string }[]) => {
    if (!stored) return ""
    if (options.some((o) => o.value === stored)) return stored
    const hit = options.find((o) => o.value.endsWith(`|${stored}`))
    return hit?.value ?? stored
  }

  const meetingModelOptions = (() => {
    const meetingModels = providers.flatMap((p) =>
      (p.selected_models || (p.model ? [p.model] : [])).map((m) => ({
        value: `${p.id}|${m}`,
        label: `${p.name || p.id} / ${m}`,
      })),
    )
    return [{ value: "", label: "Default" }, ...meetingModels]
  })()

  const enrichModelOptions = (() => {
    const models = providers.flatMap((p) =>
      (p.selected_models || (p.model ? [p.model] : [])).map((m) => ({
        value: `${p.id}|${m}`,
        label: `${p.name || p.id} / ${m}`,
      })),
    )
    // Keep a bare provider-id value selectable so older configs still display.
    if (enrichModel && !enrichModel.includes("|") && !models.some((o) => o.value === enrichModel)) {
      const p = providers.find((x) => x.id === enrichModel)
      models.unshift({
        value: enrichModel,
        label: p ? `${p.name || p.id} (provider default)` : enrichModel,
      })
    }
    return [{ value: "", label: "Default" }, ...models]
  })()

  const saveMineru = async (patch: Record<string, unknown>) => {
    await updateConfig("mineru", {
      enabled: mineruEnabled,
      api_token: mineruToken,
      base_url: "https://mineru.net/api/v4",
      model_version: mineruModel,
      is_ocr: mineruOcr,
      enable_formula: mineruFormula,
      enable_table: mineruTable,
      language: mineruLanguage,
      ...patch,
    })
  }

  return (
    <div className="pm-settings">
      <div className="pm-settings-inner">
        <header className="pm-settings-mast">
          <h1 className="pm-settings-page-title">Settings</h1>
          <p className="pm-settings-page-desc">
            Configure language models, retrieval, transcription, and system defaults.
          </p>
        </header>

        {/* Quick setup */}
        <section className="pm-settings-section">
          <div className="pm-settings-card">
            <div className="pm-settings-card-head">
              <div className="pm-settings-card-head-text min-w-0">
                <h2 className="pm-settings-card-kicker">Quick setup</h2>
                <p className="pm-settings-card-desc">
                  Configure a full provider stack with a single API key.
                </p>
              </div>
              <div className="flex flex-wrap gap-2 shrink-0">
                <Button variant="secondary" size="sm" onClick={() => setOneshotDialogOpen(true)}>
                  <Zap className="h-3.5 w-3.5" />
                  Dashscope
                </Button>
                <Button variant="secondary" size="sm" onClick={() => setOpenrouterDialogOpen(true)}>
                  <Zap className="h-3.5 w-3.5" />
                  OpenRouter
                </Button>
              </div>
            </div>
          </div>
        </section>

        {/* Models — one section soft-float card */}
        <section className="pm-settings-section">
          <div className="pm-settings-card">
            <div className="pm-settings-card-head">
              <div className="pm-settings-card-head-text">
                <h2 className="pm-settings-card-kicker">Models</h2>
                <p className="pm-settings-card-desc">LLM providers for chat, tools, and vision.</p>
              </div>
              <Button variant="default" size="sm" onClick={handleAdd}>Add</Button>
            </div>
            <div className="pm-settings-card-body">
              <div className="pm-settings-provider-grid">
                {providers.filter((p) => !p.id.startsWith("builtin-")).map((p) => (
                  <ProviderCard key={p.id} provider={p} onEdit={handleEdit} onRefresh={fetchProviders} />
                ))}
              </div>

              <div className="pm-settings-fold-card">
                <button
                  type="button"
                  className={cn("pm-settings-fold-trigger", showModelConfig && "is-open")}
                  onClick={() => setShowModelConfig(!showModelConfig)}
                  aria-expanded={showModelConfig}
                >
                  <span className="pm-settings-subhead">More · image · chat · meeting · library · query · distill</span>
                  <ChevronRight className="pm-settings-fold-chev" strokeWidth={1.75} />
                </button>
                <div className={cn("pm-config-fold", showModelConfig && "is-open")}>
                  <div className="pm-config-fold-inner">
                    <div className="pm-settings-fold-body">
                      <div className="pm-settings-model-fields">
                        {/* Image description */}
                        <div className="pm-settings-model-field">
                          <FieldLabel>Image description</FieldLabel>
                          {visualModelOptions.length <= 1 ? (
                            <div className="pm-settings-empty">
                              <Sparkles className="h-5 w-5" />
                              <p className="pm-meta">No visual-capable models configured.</p>
                            </div>
                          ) : (
                            <DropdownSelect
                              value={coerceSlotValue(visualModelId, visualModelOptions)}
                              onChange={async (v) => {
                                setVisualModelId(v)
                                try {
                                  await updateConfig("visual_model_id", { visual_model_id: v || null })
                                  toast.success("Image description model updated")
                                } catch {
                                  toast.error("Failed to update image description model")
                                }
                              }}
                              options={visualModelOptions}
                              placeholder="None (disabled)"
                            />
                          )}
                        </div>

                        {/* Chat model */}
                        <div className="pm-settings-model-field">
                          <FieldLabel>Chat model</FieldLabel>
                          {chatModelOptions.length <= 1 ? (
                            <div className="pm-settings-empty">
                              <MessageSquare className="h-5 w-5" />
                              <p className="pm-meta">No chat-capable models configured.</p>
                            </div>
                          ) : (
                            <DropdownSelect
                              value={coerceSlotValue(chatModelId, chatModelOptions)}
                              onChange={async (v) => {
                                setChatModelId(v)
                                try {
                                  await updateConfig("default_chat_model", { default_chat_model: v || null })
                                  toast.success("Chat model updated")
                                } catch {
                                  toast.error("Failed to update chat model")
                                }
                              }}
                              options={chatModelOptions}
                              placeholder="Default"
                            />
                          )}
                        </div>

                        <div className="pm-settings-model-field">
                          <FieldLabel>Meeting summary model</FieldLabel>
                          {meetingModelOptions.length <= 1 ? (
                            <div className="pm-settings-empty">
                              <p className="pm-meta">No LLM providers configured.</p>
                            </div>
                          ) : (
                            <DropdownSelect
                              value={coerceSlotValue(meetingModel, meetingModelOptions)}
                              onChange={async (v) => {
                                setMeetingModel(v)
                                try {
                                  await updateConfig("enrichment", {
                                    meeting_model: v,
                                  })
                                  toast.success("Meeting model updated")
                                } catch {
                                  toast.error("Failed to update")
                                }
                              }}
                              options={meetingModelOptions}
                              placeholder="Default"
                            />
                          )}
                        </div>

                        <div className="pm-settings-model-field">
                          <FieldLabel>Library LLM</FieldLabel>
                          {enrichModelOptions.length <= 1 ? (
                            <div className="pm-settings-empty">
                              <p className="pm-meta">No LLM providers configured.</p>
                            </div>
                          ) : (
                            <DropdownSelect
                              value={coerceSlotValue(enrichModel, enrichModelOptions)}
                              onChange={async (v) => {
                                setEnrichModel(v)
                                try {
                                  await updateConfig("enrichment", {
                                    enrichment_model: v,
                                  })
                                  toast.success("Library LLM updated")
                                } catch {
                                  toast.error("Failed to update")
                                }
                              }}
                              options={enrichModelOptions}
                              placeholder="Default"
                            />
                          )}
                        </div>

                        <div className="pm-settings-model-field">
                          <FieldLabel>Agentic query</FieldLabel>
                          {enrichModelOptions.length <= 1 ? (
                            <div className="pm-settings-empty">
                              <p className="pm-meta">No LLM providers configured.</p>
                            </div>
                          ) : (
                            <DropdownSelect
                              value={coerceSlotValue(agenticQueryModel, enrichModelOptions)}
                              onChange={async (v) => {
                                setAgenticQueryModel(v)
                                try {
                                  await updateConfig("enrichment", {
                                    agentic_query_model: v,
                                  })
                                  toast.success("Agentic query model updated")
                                } catch {
                                  toast.error("Failed to update")
                                }
                              }}
                              options={enrichModelOptions}
                              placeholder="Default"
                            />
                          )}
                        </div>

                        <div className="pm-settings-model-field">
                          <FieldLabel>Note distill</FieldLabel>
                          {enrichModelOptions.length <= 1 ? (
                            <div className="pm-settings-empty">
                              <p className="pm-meta">No LLM providers configured.</p>
                            </div>
                          ) : (
                            <DropdownSelect
                              value={coerceSlotValue(noteDistillModel, enrichModelOptions)}
                              onChange={async (v) => {
                                setNoteDistillModel(v)
                                try {
                                  await updateConfig("enrichment", {
                                    note_distill_model: v,
                                  })
                                  toast.success("Note distill model updated")
                                } catch {
                                  toast.error("Failed to update")
                                }
                              }}
                              options={enrichModelOptions}
                              placeholder="Default"
                            />
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Embedding */}
        <section className="pm-settings-section">
          <div className="pm-settings-card">
            <div className="pm-settings-card-head">
              <div className="pm-settings-card-head-text">
                <h2 className="pm-settings-card-kicker">Embedding models</h2>
                <p className="pm-settings-card-desc">Vector encoders for retrieval.</p>
              </div>
              <Button
                variant="default"
                size="sm"
                onClick={() => { setEditingEmb(null); setEmbDialogOpen(true) }}
              >
                Add
              </Button>
            </div>
            <div className="pm-settings-card-body">
              <div className="pm-settings-provider-grid">
                {embProviders.filter((p) => !p.id.startsWith("builtin-")).map((p) => (
                  <SimpleProviderCard
                    key={p.id}
                    provider={p}
                    onEdit={(p) => { setEditingEmb(p); setEmbDialogOpen(true) }}
                    onRefresh={fetchEmbProviders}
                    onTest={testEmbeddingProvider}
                    onDelete={deleteEmbeddingProvider}
                    onSetDefault={setDefaultEmbeddingProvider}
                  />
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* Rerank */}
        <section className="pm-settings-section">
          <div className="pm-settings-card">
            <div className="pm-settings-card-head">
              <div className="pm-settings-card-head-text">
                <h2 className="pm-settings-card-kicker">Rerank models</h2>
                <p className="pm-settings-card-desc">Cross-encoder reranking for final ranking.</p>
              </div>
              <Button
                variant="default"
                size="sm"
                onClick={() => { setEditingRerank(null); setRerankDialogOpen(true) }}
              >
                Add
              </Button>
            </div>
            <div className="pm-settings-card-body">
              <div className="pm-settings-provider-grid">
                {rerankProviders.filter((p) => !p.id.startsWith("builtin-")).map((p) => (
                  <SimpleProviderCard
                    key={p.id}
                    provider={p}
                    onEdit={(p) => { setEditingRerank(p); setRerankDialogOpen(true) }}
                    onRefresh={fetchRerankProviders}
                    onTest={testRerankProvider}
                    onDelete={deleteRerankProvider}
                    onSetDefault={setDefaultRerankProvider}
                  />
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* Transcription */}
        <section className="pm-settings-section">
          <div className="pm-settings-card">
            <div className="pm-settings-card-head">
              <div className="pm-settings-card-head-text">
                <h2 className="pm-settings-card-kicker">Transcription</h2>
                <p className="pm-settings-card-desc">File batch and realtime speech providers.</p>
              </div>
            </div>
            <div className="pm-settings-card-body">
              <div>
                <div className="pm-settings-card-head mb-2">
                  <h3 className="pm-settings-subhead">File transcription</h3>
                  <Button
                    variant="default"
                    size="sm"
                    onClick={() => {
                      setEditingFileTrans(null)
                      setFileTransLangHints([])
                      setFileTransDialogOpen(true)
                    }}
                  >
                    Add
                  </Button>
                </div>
                <div className="pm-settings-provider-grid">
                  {builtinFileTrans && (
                    <LocalModelCard
                      id={builtinFileTrans.id}
                      name={builtinFileTrans.name}
                      model={builtinFileTrans.model || builtinFileTrans.adapter}
                      isDefault={builtinFileTrans?.is_active ?? false}
                      loadState={loadStates[builtinFileTrans.id] || "unloaded"}
                      loadDetail={loadDetails[builtinFileTrans.id]}
                      isDownloaded={modelDownloaded["builtin-local-file"] ?? false}
                      onTest={async () => {
                        const r = await testFileTranscriptionProvider(builtinFileTrans.id)
                        return {
                          success: !!r.success,
                          message: r.message,
                          error: r.error,
                          code: (r as { code?: string }).code,
                        }
                      }}
                      onSetDefault={async () => {
                        const res = await setActiveFileTranscriptionProvider(builtinFileTrans.id)
                        fetchFileTransProviders()
                        startPolling(true)
                        const adapter = (res as { adapter?: string })?.adapter
                        toast.success(
                          adapter
                            ? `Default file transcription → ${adapter}`
                            : "Set as default file transcription",
                        )
                      }}
                      onToggleLoad={async (action) => {
                        const id = builtinFileTrans.id
                        if (action === "load") {
                          // Optimistic loading only — never optimistic "loaded"
                          setLoadStates((s) => ({ ...s, [id]: "loading" }))
                          setLoadDetails((d) => ({
                            ...d,
                            [id]: {
                              state: "loading",
                              message: "Loading file transcription pack into memory…",
                              started_at: Date.now() / 1000,
                            },
                          }))
                        }
                        const res = await toggleModelLoad(id, action)
                        if (!res.success) {
                          // Revert optimistic loading
                          await refreshLoadStates()
                          return res
                        }
                        // Apply only the real status from server
                        if (res.status === "loading") {
                          setLoadStates((s) => ({ ...s, [id]: "loading" }))
                          setLoadDetails((d) => ({
                            ...d,
                            [id]: {
                              ...(d[id] || {}),
                              state: "loading",
                              message: res.message || d[id]?.message,
                              started_at: d[id]?.started_at ?? Date.now() / 1000,
                            },
                          }))
                          startPolling(true)
                        } else if (res.status === "loaded") {
                          setLoadStates((s) => ({ ...s, [id]: "loaded" }))
                          setLoadDetails((d) => ({
                            ...d,
                            [id]: { state: "loaded", message: res.message || "Ready" },
                          }))
                        } else if (res.status === "unloaded") {
                          setLoadStates((s) => ({ ...s, [id]: "unloaded" }))
                          setLoadDetails((d) => ({
                            ...d,
                            [id]: { state: "unloaded", message: res.message || "Unloaded" },
                          }))
                        } else if (action === "load") {
                          // Unknown status while loading — keep loading + poll
                          setLoadStates((s) => ({ ...s, [id]: "loading" }))
                          startPolling(true)
                        }
                        fetchFileTransProviders()
                        return res
                      }}
                      onDownload={() => setModelDownloadOpen(true)}
                    />
                  )}
                  {cloudFileProviders.map((p) => (
                    <TranscriptionProviderCard
                      key={p.id}
                      provider={p}
                      kind="file"
                      onEdit={(p) => {
                        setEditingFileTrans(p)
                        setFileTransLangHints(p.language_hints_config || [])
                        setFileTransDialogOpen(true)
                      }}
                      onRefresh={fetchFileTransProviders}
                      onDelete={deleteFileTranscriptionProvider}
                      onSetActive={setActiveFileTranscriptionProvider}
                      onTest={testFileTranscriptionProvider}
                    />
                  ))}
                </div>
              </div>

              <div>
                <div className="pm-settings-card-head mb-2">
                  <h3 className="pm-settings-subhead">Realtime transcription</h3>
                  <Button
                    variant="default"
                    size="sm"
                    onClick={() => {
                      setEditingRtTrans(null)
                      setRtTransDialogOpen(true)
                    }}
                  >
                    Add
                  </Button>
                </div>
                <div className="pm-settings-provider-grid">
                  {builtinRtTrans && (
                    <LocalModelCard
                      id={builtinRtTrans.id}
                      name={builtinRtTrans.name}
                      model={builtinRtTrans.model || builtinRtTrans.adapter}
                      isDefault={builtinRtTrans?.is_active ?? false}
                      loadState={loadStates[builtinRtTrans.id] || "unloaded"}
                      loadDetail={loadDetails[builtinRtTrans.id]}
                      isDownloaded={modelDownloaded["builtin-local-rt"] ?? false}
                      onTest={async () => {
                        const r = await testRealtimeTranscriptionProvider(builtinRtTrans.id)
                        return {
                          success: !!r.success,
                          message: r.message,
                          error: r.error,
                          code: (r as { code?: string }).code,
                        }
                      }}
                      onSetDefault={async () => {
                        const res = await setActiveRealtimeTranscriptionProvider(builtinRtTrans.id)
                        fetchRtTransProviders()
                        startPolling(true)
                        const adapter = (res as { adapter?: string })?.adapter
                        toast.success(
                          adapter
                            ? `Default realtime transcription → ${adapter}`
                            : "Set as default realtime transcription",
                        )
                      }}
                      onToggleLoad={async (action) => {
                        const id = builtinRtTrans.id
                        if (action === "load") {
                          setLoadStates((s) => ({ ...s, [id]: "loading" }))
                          setLoadDetails((d) => ({
                            ...d,
                            [id]: {
                              state: "loading",
                              message: "Loading realtime model into memory…",
                              started_at: Date.now() / 1000,
                            },
                          }))
                        }
                        const res = await toggleModelLoad(id, action)
                        if (!res.success) {
                          await refreshLoadStates()
                          return res
                        }
                        if (res.status === "loading") {
                          setLoadStates((s) => ({ ...s, [id]: "loading" }))
                          setLoadDetails((d) => ({
                            ...d,
                            [id]: {
                              ...(d[id] || {}),
                              state: "loading",
                              message: res.message || d[id]?.message,
                              started_at: d[id]?.started_at ?? Date.now() / 1000,
                            },
                          }))
                          startPolling(true)
                        } else if (res.status === "loaded") {
                          setLoadStates((s) => ({ ...s, [id]: "loaded" }))
                          setLoadDetails((d) => ({
                            ...d,
                            [id]: { state: "loaded", message: res.message || "Ready" },
                          }))
                        } else if (res.status === "unloaded") {
                          setLoadStates((s) => ({ ...s, [id]: "unloaded" }))
                          setLoadDetails((d) => ({
                            ...d,
                            [id]: { state: "unloaded", message: res.message || "Unloaded" },
                          }))
                        } else if (action === "load") {
                          setLoadStates((s) => ({ ...s, [id]: "loading" }))
                          startPolling(true)
                        }
                        fetchRtTransProviders()
                        return res
                      }}
                      onDownload={() => setModelDownloadOpen(true)}
                    />
                  )}
                  {cloudRtProviders.map((p) => (
                    <TranscriptionProviderCard
                      key={p.id}
                      provider={p}
                      kind="realtime"
                      onEdit={(p) => {
                        setEditingRtTrans(p)
                        setRtTransDialogOpen(true)
                      }}
                      onRefresh={fetchRtTransProviders}
                      onDelete={deleteRealtimeTranscriptionProvider}
                      onSetActive={setActiveRealtimeTranscriptionProvider}
                      onTest={testRealtimeTranscriptionProvider}
                    />
                  ))}
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Hot words */}
        <section className="pm-settings-section">
          <div className="pm-settings-card">
            <div className="pm-settings-card-head">
              <div className="pm-settings-card-head-text min-w-0">
                <h2 className="pm-settings-card-kicker">Hot words</h2>
                <p className="pm-settings-card-desc">
                  Libraries for domain terms — names, acronyms, jargon — to improve transcription.
                </p>
              </div>
              <Button variant="default" size="sm" onClick={() => setHotWordsManagerOpen(true)}>
                Manage
              </Button>
            </div>
          </div>
        </section>

        <section className="pm-settings-section">
          <div className="pm-settings-card">
            <div className="pm-settings-card-head">
              <div className="pm-settings-card-head-text min-w-0">
                <h2 className="pm-settings-card-kicker">People</h2>
                <p className="pm-settings-card-desc">
                  Voiceprint directory from Meetings — rename, listen, see which meetings they are in.
                </p>
              </div>
              <Button variant="default" size="sm" onClick={() => setPeopleManagerOpen(true)}>
                Manage
              </Button>
            </div>
          </div>
        </section>

        {/* Web search */}
        <section className="pm-settings-section">
        <div className="pm-settings-card">
          <div className="pm-settings-card-head-text">
            <h2 className="pm-settings-card-kicker">Web search (Tavily)</h2>
          </div>
          <p className="pm-settings-card-desc">
            Store your Tavily API key here. Turn web search on/off from the{" "}
            <strong className="text-[var(--pm-ink)] font-normal">Chat</strong> toolbar (Globe / Web).
            Even when on, every search still asks for confirmation, and results are labeled WEB
            (not knowledge base). Meeting chat never uses web search. Get a key at{" "}
            <a
              href="https://tavily.com"
              target="_blank"
              rel="noopener noreferrer"
              className="pm-settings-link"
            >
              tavily.com
            </a>
            .
          </p>
          <div className="pm-config-field">
            <FieldLabel>Tavily API key</FieldLabel>
            <div className="relative">
              <Input
                type={showWebSearchKey ? "text" : "password"}
                value={webSearchApiKey}
                onChange={(e) => setWebSearchApiKey(e.target.value)}
                onBlur={async () => {
                  try {
                    await updateConfig("web_search", {
                      provider: "tavily",
                      api_key: webSearchApiKey,
                    })
                    toast.success(
                      webSearchApiKey.trim()
                        ? "Tavily API key saved"
                        : "Tavily API key cleared",
                    )
                  } catch {
                    toast.error("Failed to save Tavily API key")
                  }
                }}
                placeholder="tvly-..."
                className="pr-10"
              />
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                className="absolute right-1 top-1/2 -translate-y-1/2"
                onClick={() => setShowWebSearchKey(!showWebSearchKey)}
              >
                {showWebSearchKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </Button>
            </div>
            <p className="pm-meta mt-1.5">Without a key, the Chat “Web” toggle will have no effect.</p>
          </div>
        </div>
        </section>

        {/* MinerU */}
        <section className="pm-settings-section">
        <div className="pm-settings-card">
          <div className="pm-settings-row-between">
            <div className="min-w-0">
              <h2 className="pm-settings-card-kicker">MinerU cloud parsing</h2>
              <p className="pm-meta pm-settings-card-desc mt-1">
                High-quality document parsing with better table, formula, and layout preservation.
                Get a token at{" "}
                <a
                  href="https://mineru.net/apiManage/token"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="pm-settings-link"
                >
                  mineru.net
                </a>
                . Enable per-collection in Collection Settings when ready.
              </p>
            </div>
            <SettingsSwitch
              id="pm-settings-mineru"
              label="Enable MinerU"
              checked={mineruEnabled}
              onCheckedChange={async (next) => {
                setMineruEnabled(next)
                try {
                  await saveMineru({ enabled: next })
                  toast.success(next ? "MinerU enabled" : "MinerU disabled")
                } catch {
                  toast.error("Failed to update MinerU setting")
                  setMineruEnabled(!next)
                }
              }}
            />
          </div>

          <div className={cn("pm-config-fold", mineruEnabled && "is-open")}>
            <div className="pm-config-fold-inner">
              <div className="space-y-4 pt-1">
                <div className="pm-config-field">
                  <FieldLabel>API token</FieldLabel>
                  <div className="relative">
                    <Input
                      type={showMineruToken ? "text" : "password"}
                      value={mineruToken}
                      onChange={(e) => setMineruToken(e.target.value)}
                      onBlur={async () => {
                        try {
                          await saveMineru({ api_token: mineruToken })
                        } catch { /* ignore */ }
                      }}
                      placeholder="Enter your MinerU API token"
                      disabled={!mineruEnabled}
                      className="pr-10"
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      className="absolute right-1 top-1/2 -translate-y-1/2"
                      onClick={() => setShowMineruToken(!showMineruToken)}
                      disabled={!mineruEnabled}
                    >
                      {showMineruToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </Button>
                  </div>
                </div>

                <div className="pm-config-grid">
                  <div className="pm-config-field">
                    <FieldLabel>Model version</FieldLabel>
                    <DropdownSelect
                      value={mineruModel}
                      onChange={async (v) => {
                        setMineruModel(v)
                        try {
                          await saveMineru({ model_version: v })
                        } catch { /* ignore */ }
                      }}
                      options={MINERU_MODEL_OPTIONS}
                      disabled={!mineruEnabled}
                    />
                  </div>
                  <div className="pm-config-field">
                    <FieldLabel>Language</FieldLabel>
                    <DropdownSelect
                      value={mineruLanguage}
                      onChange={async (v) => {
                        setMineruLanguage(v)
                        try {
                          await saveMineru({ language: v })
                        } catch { /* ignore */ }
                      }}
                      options={MINERU_LANGUAGE_OPTIONS}
                      disabled={!mineruEnabled}
                    />
                  </div>
                </div>

                <div>
                  <FieldLabel>Parsing options</FieldLabel>
                  <div className="space-y-3 mt-2">
                    {(
                      [
                        {
                          key: "ocr",
                          label: "Force OCR",
                          desc: "Force OCR on all pages. When off, MinerU auto-detects scanned or image pages.",
                          checked: mineruOcr,
                          set: setMineruOcr,
                          patch: (v: boolean) => ({ is_ocr: v }),
                        },
                        {
                          key: "formula",
                          label: "Formula recognition",
                          desc: "Recognize mathematical formulas and convert to LaTeX.",
                          checked: mineruFormula,
                          set: setMineruFormula,
                          patch: (v: boolean) => ({ enable_formula: v }),
                        },
                        {
                          key: "table",
                          label: "Table recognition",
                          desc: "Detect and extract tables as structured Markdown.",
                          checked: mineruTable,
                          set: setMineruTable,
                          patch: (v: boolean) => ({ enable_table: v }),
                        },
                      ] as const
                    ).map((opt) => (
                      <div key={opt.key} className="pm-settings-row-between">
                        <div className="min-w-0">
                          <p className="pm-label text-[var(--pm-ink)]">{opt.label}</p>
                          <p className="pm-meta mt-0.5">{opt.desc}</p>
                        </div>
                        <SettingsSwitch
                          id={`pm-settings-mineru-${opt.key}`}
                          label={opt.label}
                          checked={opt.checked}
                          disabled={!mineruEnabled}
                          onCheckedChange={async (next) => {
                            opt.set(next)
                            try {
                              await saveMineru(opt.patch(next))
                            } catch {
                              opt.set(!next)
                            }
                          }}
                        />
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        </section>

        <ConnectionInfoCard />

        {/* Advanced */}
        <section className="pm-settings-section">
          <div className="pm-settings-card pm-settings-fold-card">
          <button
            type="button"
            className={cn("pm-settings-fold-trigger", showAdvanced && "is-open")}
            onClick={() => {
              setShowAdvanced(!showAdvanced)
              if (!showAdvanced) {
                setTimeout(
                  () =>
                    document
                      .getElementById("pm-settings-advanced")
                      ?.scrollIntoView({ behavior: "smooth", block: "start" }),
                  100,
                )
              }
            }}
            aria-expanded={showAdvanced}
          >
            <span className="pm-settings-card-kicker">Advanced</span>
            <ChevronRight className="pm-settings-fold-chev" strokeWidth={1.75} />
          </button>

          <div
            id="pm-settings-advanced"
            className={cn("pm-config-fold", showAdvanced && "is-open")}
          >
            <div className="pm-config-fold-inner">
              <div className="pm-settings-fold-body">
                <div className="pm-settings-adv-stack">
                  {/* Enrichment */}
                  <div className="pm-settings-adv-block">
                    <div className="pm-settings-adv-head">
                      <h3 className="pm-settings-subhead">Enrichment</h3>
                      <p className="pm-settings-adv-desc">
                        Global cap on concurrent Summary, Context, and image
                        description requests across all files (default 50).
                        The Library LLM is set in the model list above.
                      </p>
                    </div>
                    <div className="pm-settings-adv-grid">
                      <div className="pm-settings-adv-field">
                        <FieldLabel>Parallel</FieldLabel>
                        <Input
                          inputMode="numeric"
                          value={enrichMaxParallel}
                          onChange={(e) => setEnrichMaxParallel(e.target.value)}
                          onBlur={() => {
                            const v = parseInt(enrichMaxParallel) || 50
                            setEnrichMaxParallel(String(Math.max(1, Math.min(100, v))))
                            updateConfig("enrichment", {
                              max_parallel_context: v,
                              batch_poll_interval: 30,
                              enrichment_model: enrichModel,
                            }).catch(() => {})
                          }}
                        />
                      </div>
                    </div>
                  </div>

                  {/* Agentic RAG */}
                  <div className="pm-settings-adv-block">
                    <div className="pm-settings-adv-head">
                      <h3 className="pm-settings-subhead">Agentic RAG defaults</h3>
                      <p className="pm-settings-adv-desc">
                        Applies to decompose, rewrite loop, and aggregate.
                      </p>
                    </div>
                    <div className="pm-settings-adv-toolbar">
                      <button
                        type="button"
                        className={cn("pm-field-chip", ragSearchMode === "hybrid" && "is-on")}
                        onClick={() => {
                          const m = ragSearchMode === "hybrid" ? "dense" : "hybrid"
                          setRagSearchMode(m)
                          _saveRag(m)
                        }}
                      >
                        {ragSearchMode === "hybrid" ? "Hybrid" : "Dense"}
                      </button>
                    </div>
                    <div className="pm-settings-adv-fields-stack">
                      <div className="pm-settings-adv-grid">
                        {(
                          [
                            {
                              label: "Top K",
                              value: ragTopK,
                              set: setRagTopK,
                              min: 1,
                              max: 100,
                              fallback: 20,
                            },
                            {
                              label: "Rerank Top K",
                              value: ragRerankTopK,
                              set: setRagRerankTopK,
                              min: 1,
                              max: 50,
                              fallback: 5,
                            },
                            {
                              label: "Parallel",
                              value: ragMaxParallel,
                              set: setRagMaxParallel,
                              min: 1,
                              max: 32,
                              fallback: 10,
                            },
                            {
                              label: "Iter",
                              value: ragMaxIter,
                              set: setRagMaxIter,
                              min: 1,
                              max: 20,
                              fallback: 8,
                            },
                          ] as const
                        ).map((field) => (
                          <div key={field.label} className="pm-settings-adv-field">
                            <FieldLabel>{field.label}</FieldLabel>
                            <Input
                              inputMode="numeric"
                              value={field.value}
                              onChange={(e) => field.set(e.target.value)}
                              onBlur={() => {
                                const v = parseInt(field.value) || field.fallback
                                field.set(String(Math.max(field.min, Math.min(field.max, v))))
                                _saveRag()
                              }}
                            />
                          </div>
                        ))}
                      </div>
                      {/* Dense-only: Min score — silk fold, no hard cut */}
                      <div
                        className={cn(
                          "pm-settings-adv-fold",
                          ragSearchMode === "dense" && "is-open",
                        )}
                      >
                        <div className="pm-settings-adv-fold-inner">
                          <div className="pm-settings-adv-grid pm-settings-adv-fold-pad">
                            <div className="pm-settings-adv-field">
                              <FieldLabel>Min score</FieldLabel>
                              <Input
                                inputMode="numeric"
                                value={ragMinScore}
                                onChange={(e) => setRagMinScore(e.target.value)}
                                onBlur={() => {
                                  const v = parseInt(ragMinScore) || 25
                                  setRagMinScore(String(Math.max(0, Math.min(100, v))))
                                  _saveRag()
                                }}
                              />
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Direct RAG */}
                  <div className="pm-settings-adv-block">
                    <div className="pm-settings-adv-head">
                      <h3 className="pm-settings-subhead">Direct RAG defaults</h3>
                      <p className="pm-settings-adv-desc">
                        Used when Agentic mode is disabled. Direct retrieval with optional rerank.
                      </p>
                    </div>
                    <div className="pm-settings-adv-toolbar">
                      <button
                        type="button"
                        className={cn("pm-field-chip", dirRerankEnabled && "is-on")}
                        onClick={() => {
                          const n = !dirRerankEnabled
                          setDirRerankEnabled(n)
                          setDirTopK(n ? "20" : "10")
                          updateConfig("direct_rag", { use_reranker: n }).catch(() => {})
                        }}
                      >
                        Rerank {dirRerankEnabled ? "on" : "off"}
                      </button>
                      <button
                        type="button"
                        className={cn("pm-field-chip", dirSearchMode === "hybrid" && "is-on")}
                        onClick={() => {
                          const m = dirSearchMode === "hybrid" ? "dense" : "hybrid"
                          setDirSearchMode(m)
                          _saveDir(m)
                        }}
                      >
                        {dirSearchMode === "hybrid" ? "Hybrid" : "Dense"}
                      </button>
                    </div>
                    <div className="pm-settings-adv-fields-stack">
                      <div className="pm-settings-adv-grid">
                        <div className="pm-settings-adv-field">
                          <FieldLabel>Top K</FieldLabel>
                          <Input
                            inputMode="numeric"
                            value={dirTopK}
                            onChange={(e) => setDirTopK(e.target.value)}
                            onBlur={() => {
                              const v = parseInt(dirTopK) || (dirRerankEnabled ? 20 : 10)
                              setDirTopK(String(Math.max(1, Math.min(100, v))))
                              _saveDir()
                            }}
                          />
                        </div>
                      </div>
                      {/* Rerank on: Rerank Top K */}
                      <div
                        className={cn(
                          "pm-settings-adv-fold",
                          dirRerankEnabled && "is-open",
                        )}
                      >
                        <div className="pm-settings-adv-fold-inner">
                          <div className="pm-settings-adv-grid pm-settings-adv-fold-pad">
                            <div className="pm-settings-adv-field">
                              <FieldLabel>Rerank Top K</FieldLabel>
                              <Input
                                inputMode="numeric"
                                value={dirRerankTopK}
                                onChange={(e) => setDirRerankTopK(e.target.value)}
                                onBlur={() => {
                                  const v = parseInt(dirRerankTopK) || 5
                                  setDirRerankTopK(String(Math.max(1, Math.min(50, v))))
                                  _saveDir()
                                }}
                              />
                            </div>
                          </div>
                        </div>
                      </div>
                      {/* Dense: Min score */}
                      <div
                        className={cn(
                          "pm-settings-adv-fold",
                          dirSearchMode === "dense" && "is-open",
                        )}
                      >
                        <div className="pm-settings-adv-fold-inner">
                          <div className="pm-settings-adv-grid pm-settings-adv-fold-pad">
                            <div className="pm-settings-adv-field">
                              <FieldLabel>Min score</FieldLabel>
                              <Input
                                inputMode="numeric"
                                value={dirMinScore}
                                onChange={(e) => setDirMinScore(e.target.value)}
                                onBlur={() => {
                                  const v = parseInt(dirMinScore) || 25
                                  setDirMinScore(String(Math.max(0, Math.min(100, v))))
                                  _saveDir()
                                }}
                              />
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Local model download management */}
                  <div className="pm-settings-adv-block">
                    {(() => {
                      const LOCAL_BUNDLES = LOCAL_MODEL_BUNDLES
                      const allIds = LOCAL_MODEL_IDS
                      const allMembers = allIds
                        .map((id) => localModelCatalog.find((m) => m.id === id))
                        .filter(Boolean) as ModelStatus[]
                      const catalogKnown = allMembers.length > 0
                      const allDone =
                        catalogKnown && allMembers.every((m) => m.downloaded)
                      const anyDownloading = allMembers.some(
                        (m) =>
                          m.status === "downloading" ||
                          m.status === "extracting"
                      )
                      const anyExtracting = allMembers.some(
                        (m) => m.status === "extracting"
                      )
                      const downloadProgress = Math.max(
                        0,
                        ...allMembers
                          .filter(
                            (m) =>
                              m.status === "downloading" ||
                              m.status === "extracting"
                          )
                          .map((m) => Math.round(m.progress || 0)),
                        0
                      )
                      const deletingAll = allIds.some((id) =>
                        deletingModelIds.has(id)
                      )
                      const primaryActionLabel = anyExtracting
                        ? "Extracting…"
                        : anyDownloading
                          ? `Downloading ${downloadProgress}%`
                          : "Download"
                      // Only crossfade on semantic phase change — not every % tick
                      const primaryActionPhase = anyExtracting
                        ? "extracting"
                        : anyDownloading
                          ? "downloading"
                          : "idle"

                      return (
                        <>
                    <div className="pm-settings-adv-head">
                      <div>
                        <h3 className="pm-settings-subhead">Local model downloads</h3>
                        <p className="pm-settings-adv-desc">
                          Download FunASR models for offline file and realtime transcription (CPU).
                        </p>
                      </div>
                      <div className="pm-settings-adv-toolbar">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            refreshModelDownloaded()
                            toast.success("Status refreshed")
                          }}
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                          Refresh
                        </Button>
                        {allDone ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setDeleteLocalModelsOpen(true)}
                            disabled={deletingAll || anyDownloading}
                          >
                            {deletingAll ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <Trash2 className="h-3.5 w-3.5" />
                            )}
                            Delete
                          </Button>
                        ) : (
                          <Button
                            variant="default"
                            size="sm"
                            className={cn(
                              "pm-settings-dl-btn",
                              anyDownloading && "is-busy"
                            )}
                            style={
                              {
                                ["--pm-dl-pct" as string]: `${
                                  anyDownloading
                                    ? Math.min(
                                        100,
                                        Math.max(0, downloadProgress)
                                      )
                                    : 0
                                }%`,
                              } as CSSProperties
                            }
                            onClick={() => {
                              if (anyDownloading || deletingAll) return
                              setModelDownloadOpen(true)
                            }}
                            disabled={deletingAll}
                            aria-busy={anyDownloading || undefined}
                            title={
                              anyExtracting
                                ? "Extracting ONNX packs…"
                                : anyDownloading
                                  ? `Downloading… ${downloadProgress}%`
                                  : "Download local models"
                            }
                          >
                            <span className="pm-settings-dl-btn-inner">
                              {anyDownloading ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <Download className="h-3.5 w-3.5" />
                              )}
                              <span
                                className="pm-settings-dl-btn-text"
                                key={primaryActionPhase}
                              >
                                {primaryActionLabel}
                              </span>
                            </span>
                          </Button>
                        )}
                      </div>
                    </div>
                    <div className="pm-settings-local-model-stack">
                      {LOCAL_BUNDLES.map((bundle) => {
                        const members = bundle.modelIds
                          .map((id) => localModelCatalog.find((m) => m.id === id))
                          .filter(Boolean) as ModelStatus[]
                        const known = members.length > 0
                        const packDone =
                          known && members.every((m) => m.downloaded)
                        const anyPackDownloaded = members.some((m) => m.downloaded)
                        const anyPackDownloading = members.some(
                          (m) =>
                            m.status === "downloading" ||
                            m.status === "extracting"
                        )
                        const anyError = members.some((m) => m.status === "error")
                        const totalMb = members.reduce(
                          (s, m) => s + (m.size_mb || 0),
                          0
                        )
                        // Progress only on toolbar Download button — cards stay calm
                        const statusLabel = !known
                          ? "Unknown"
                          : anyPackDownloading
                            ? "In progress"
                            : anyError
                              ? "Error"
                              : packDone
                                ? "Ready"
                                : anyPackDownloaded
                                  ? "Partial"
                                  : "Not downloaded"
                        const badgeVariant = anyError
                          ? "destructive"
                          : packDone
                            ? "default"
                            : anyPackDownloading
                              ? "secondary"
                              : "outline"
                        return (
                          <div key={bundle.id} className="pm-settings-provider-card">
                            <div className="pm-settings-provider-top">
                              <div className="pm-settings-provider-name-row">
                                <span className="pm-settings-provider-name">
                                  {bundle.label}
                                </span>
                              </div>
                              <div className="flex items-center gap-1.5 shrink-0">
                                <Badge
                                  variant={
                                    badgeVariant as
                                      | "default"
                                      | "secondary"
                                      | "destructive"
                                      | "outline"
                                  }
                                >
                                  {statusLabel}
                                </Badge>
                              </div>
                            </div>

                            <p className="pm-settings-provider-meta">
                              {bundle.description}
                              {totalMb > 0 ? ` · ~${totalMb} MB` : ""}
                            </p>

                            {members.length > 0 && (
                              <ul className="pm-settings-local-model-parts">
                                {members.map((m) => (
                                  <li key={m.id}>
                                    <span>{m.display_name || m.id}</span>
                                    <span className="pm-settings-local-model-part-status">
                                      {m.downloaded
                                        ? "Ready"
                                        : m.status === "error"
                                          ? "Error"
                                          : anyPackDownloading
                                            ? "…"
                                            : "—"}
                                    </span>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        )
                      })}
                      {localModelCatalog.length === 0 && (
                        <p className="pm-settings-adv-desc" style={{ margin: 0 }}>
                          Loading model status… click Refresh if this stays empty.
                        </p>
                      )}
                    </div>
                        </>
                      )
                    })()}
                  </div>

                  {/* Developer mode */}
                  <div className="pm-settings-adv-row">
                    <div className="pm-settings-adv-row-text">
                      <h3 className="pm-settings-subhead">Developer mode</h3>
                      <p className="pm-settings-adv-desc">
                        Backend logs and the file-detail Ingest run timeline.
                      </p>
                    </div>
                    <div className="pm-settings-adv-row-actions">
                      <SettingsSwitch
                        id="pm-settings-dev"
                        label="Developer mode"
                        checked={developerMode}
                        onCheckedChange={() => toggleDeveloperMode()}
                      />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
          </div>
        </section>

        {/* Dialogs */}
        <AddProviderDialog
          open={dialogOpen}
          provider={editingProvider}
          onOpenChange={setDialogOpen}
          onSaved={handleSaved}
        />

        <SimpleProviderDialog
          open={embDialogOpen}
          provider={editingEmb}
          title="Embedding Provider"
          kicker="Embedding"
          fields={embFields}
          defaults={{ provider: "openai_compatible", batch_size: "10", is_default: "false" }}
          onOpenChange={setEmbDialogOpen}
          onSaved={() => {
            setEmbDialogOpen(false)
            setEditingEmb(null)
            fetchEmbProviders()
          }}
          onCreate={(data) => createEmbeddingProvider(data as Partial<EmbeddingProvider>)}
          onUpdate={(id, data) => updateEmbeddingProvider(id, data as Partial<EmbeddingProvider>)}
          modelFetchSection="embedding"
        />

        <SimpleProviderDialog
          open={rerankDialogOpen}
          provider={editingRerank}
          title="Rerank Provider"
          kicker="Rerank"
          fields={rerankFields}
          defaults={{ provider: "openai_compatible", is_default: "false" }}
          onOpenChange={setRerankDialogOpen}
          onSaved={() => {
            setRerankDialogOpen(false)
            setEditingRerank(null)
            fetchRerankProviders()
          }}
          onCreate={(data) => createRerankProvider(data as Partial<RerankProvider>)}
          onUpdate={(id, data) => updateRerankProvider(id, data as Partial<RerankProvider>)}
          modelFetchSection="rerank"
        />

        <SimpleProviderDialog
          open={fileTransDialogOpen}
          provider={editingFileTrans}
          title="File Transcription Provider"
          kicker="File transcription"
          fields={fileTransFields}
          getTransFields={getFileTransFields}
          defaults={{ adapter: ftAdapterOpts[0]?.value ?? "", is_active: "false", device: "auto" }}
          onOpenChange={(open) => {
            setFileTransDialogOpen(open)
            if (!open) setFileTransLangHints([])
          }}
          onSaved={() => {
            setFileTransDialogOpen(false)
            setEditingFileTrans(null)
            setFileTransLangHints([])
            fetchFileTransProviders()
          }}
          onCreate={async (data) => {
            const payload = { ...data }
            if (fileTransLangHints.length > 0) payload.language_hints_config = fileTransLangHints
            return createFileTranscriptionProvider(payload as Partial<TranscriptionProvider>)
          }}
          onUpdate={async (id, data) => {
            const payload = { ...data }
            payload.language_hints_config = fileTransLangHints
            return updateFileTranscriptionProvider(id, payload as Partial<TranscriptionProvider>)
          }}
          checkboxField="is_active"
          checkboxLabel="Set as active"
          modelFetchSection="transcription"
          renderExtra={(form) => {
            if (form.adapter !== "openai_compatible") return null
            const add = () => setFileTransLangHints((prev) => [...prev, { code: "", label: "" }])
            const remove = (idx: number) =>
              setFileTransLangHints((prev) => prev.filter((_, i) => i !== idx))
            const update = (idx: number, field: string, value: string) =>
              setFileTransLangHints((prev) =>
                prev.map((item, i) => (i === idx ? { ...item, [field]: value } : item)),
              )
            return (
              <div className="space-y-2">
                <div className="pm-settings-row-between">
                  <FieldLabel className="mb-0">Language hints</FieldLabel>
                  <Button
                    variant="ghost"
                    size="xs"
                    onClick={(e) => {
                      e.preventDefault()
                      add()
                    }}
                  >
                    <Plus className="h-3 w-3" />
                    Add
                  </Button>
                </div>
                <p className="pm-meta">
                  Codes appear in the transcription language selector. Leave empty for provider defaults.
                </p>
                {fileTransLangHints.map((hint, idx) => (
                  <div key={idx} className="flex gap-2 items-center">
                    <Input
                      className="flex-1"
                      placeholder="Code (e.g. zh)"
                      value={hint.code}
                      onChange={(e) => update(idx, "code", e.target.value)}
                    />
                    <Input
                      className="flex-1"
                      placeholder="Label (e.g. 中文)"
                      value={hint.label}
                      onChange={(e) => update(idx, "label", e.target.value)}
                    />
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={(e) => {
                        e.preventDefault()
                        remove(idx)
                      }}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
              </div>
            )
          }}
        />

        <SimpleProviderDialog
          open={rtTransDialogOpen}
          provider={editingRtTrans}
          title="Realtime Transcription Provider"
          kicker="Realtime transcription"
          fields={rtTransFields}
          getTransFields={getRtTransFields}
          defaults={{ adapter: rtAdapterOpts[0]?.value ?? "", is_active: "false", device: "auto" }}
          onOpenChange={setRtTransDialogOpen}
          onSaved={() => {
            setRtTransDialogOpen(false)
            setEditingRtTrans(null)
            fetchRtTransProviders()
          }}
          onCreate={(data) =>
            createRealtimeTranscriptionProvider(data as Partial<TranscriptionProvider>)
          }
          onUpdate={(id, data) =>
            updateRealtimeTranscriptionProvider(id, data as Partial<TranscriptionProvider>)
          }
          checkboxField="is_active"
          checkboxLabel="Set as active"
          modelFetchSection="transcription"
        />
      </div>

      <Dialog
        open={deleteLocalModelsOpen}
        onOpenChange={(v) => {
          if (deletingModelIds.size > 0) return
          setDeleteLocalModelsOpen(v)
        }}
      >
        <DialogContent
          className="pm-dialog pm-dialog-confirm sm:max-w-[320px]"
          showCloseButton={false}
        >
          <DialogHeader>
            <DialogKicker>Local models</DialogKicker>
            <DialogTitle>Delete local models?</DialogTitle>
            <DialogDescription>
              File and realtime FunASR packs will be removed from disk. You can
              re-download later.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={deletingModelIds.size > 0}
              onClick={() => setDeleteLocalModelsOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive-solid"
              size="sm"
              disabled={deletingModelIds.size > 0}
              onClick={() => void confirmDeleteLocalModels()}
            >
              {deletingModelIds.size > 0 ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : null}
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ModelDownloadDialog
        open={modelDownloadOpen}
        onOpenChange={setModelDownloadOpen}
        onDownloadStart={() => {
          startPolling(true)
          void refreshModelDownloaded()
        }}
        onComplete={() => {
          fetchProviders()
          fetchEmbProviders()
          fetchRerankProviders()
          refreshModelDownloaded()
          startPolling()
        }}
      />
      <HotWordsManager open={hotWordsManagerOpen} onOpenChange={setHotWordsManagerOpen} />
      <PeopleManager open={peopleManagerOpen} onOpenChange={setPeopleManagerOpen} />
      <OneShotDashscopeDialog
        open={oneshotDialogOpen}
        onOpenChange={setOneshotDialogOpen}
        onSaved={refreshAfterOneshot}
      />
      <OneShotOpenRouterDialog
        open={openrouterDialogOpen}
        onOpenChange={setOpenrouterDialogOpen}
        onSaved={refreshAfterOneshot}
      />

    </div>
  )

}
