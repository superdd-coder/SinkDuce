import { useEffect, useState, useRef, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Combobox } from "@/components/ui/combobox"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Plus, Star, Pencil, Trash2, Plug, Loader2, Eye, EyeOff, Zap, Download, RefreshCw, Sparkles, MessageSquare, ChevronRight } from "lucide-react"
import { DropdownSelect } from "@/components/ui/dropdown-select"
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
} from "@/api/client"
import { useProviderTypes } from "@/hooks/use-provider-types"
import { toast } from "sonner"
import { ProviderCard } from "./provider-card"
import { AddProviderDialog } from "./add-provider-dialog"
import { LocalModelCard } from "./local-model-card"
import type { LoadState } from "./local-model-card"
import { ModelDownloadDialog } from "@/components/model-download-dialog"
import { HotWordsManager } from "./hot-words-manager"
import { OneShotDashscopeDialog } from "./oneshot-dashscope-dialog"
import { OneShotOpenRouterDialog } from "./oneshot-openrouter-dialog"

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

  const statusColor = status === "ready" ? "bg-emerald-500" : status === "error" ? "bg-red-500" : "bg-muted-foreground/40"

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
      else { toast.success(res.message || "Deleted"); onRefresh() }
    } catch { toast.error("Delete failed") }
  }

  const handleSetDefault = async () => {
    try {
      const res = await onSetDefault(provider.id)
      if (res.error) toast.error(res.error)
      else { toast.success(res.message || "Default updated"); onRefresh() }
    } catch { toast.error("Failed to set default") }
  }

  return (
    <div className="border border-border/50 rounded-lg p-4 flex flex-col h-full">
      {/* Row 1: Provider name + status + default badge */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <span className="text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground">{provider.name || "Unnamed"}</span>
          <div className={`h-2 w-2 rounded-full shrink-0 ${statusColor}`} />
        </div>
        {provider.is_default && (
          <Badge className="text-[10px] font-medium uppercase tracking-[0.1em] bg-emerald-50 text-emerald-700 hover:bg-emerald-50 dark:bg-emerald-950/30 dark:text-emerald-400 shrink-0"><Star className="h-3 w-3 mr-1" />DEFAULT</Badge>
        )}
      </div>

      {/* Row 2: Model */}
      <div className="mt-1 min-h-[1.25rem]">
        <p className="font-normal text-[12px] text-muted-foreground/80">{provider.model || ""}</p>
        {subtitle && <p className="font-normal text-[11px] text-muted-foreground/80">{subtitle}</p>}
      </div>

      {/* Row 3: URL */}
      <div className="min-h-[1rem]">
        <p className="font-normal text-[11px] text-muted-foreground/80 truncate max-w-[200px]">{provider.base_url || ""}</p>
      </div>

      {/* Row 4: Buttons */}
      <div className="flex gap-2 mt-auto pt-3">
        <Button variant="outline" size="sm" onClick={handleTest} disabled={testing} className="font-medium uppercase tracking-[0.1em] text-[10px]">
          {testing ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Plug className="h-3 w-3 mr-1" />}
          TEST
        </Button>
        <Button variant="outline" size="sm" onClick={handleSetDefault} disabled={provider.is_default} className="font-medium uppercase tracking-[0.1em] text-[10px]">
          <Star className="h-3 w-3 mr-1" />DEFAULT
        </Button>
        <Button variant="outline" size="sm" onClick={() => onEdit(provider)} className="font-medium uppercase tracking-[0.1em] text-[10px]">
          <Pencil className="h-3 w-3 mr-1" />EDIT
        </Button>
        <Button variant="outline" size="sm" onClick={handleDelete} className="font-medium uppercase tracking-[0.1em] text-[10px] hover:text-orange-600 dark:hover:text-orange-400">
          <Trash2 className="h-3 w-3 mr-1" />DELETE
        </Button>
      </div>
    </div>
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
  open, provider, title, fields, getTransFields, defaults, onOpenChange, onSaved, onCreate, onUpdate,
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
      <DialogContent className="max-h-[85vh] overflow-y-auto overflow-x-hidden">
        <DialogHeader>
          <DialogTitle>{provider ? `Edit ${title}` : `Add ${title}`}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 py-2 min-w-0">
          {resolvedFields.map((f) => (
            <div key={f.key} className="space-y-1.5">
              <label className="text-sm font-light uppercase tracking-wider">{f.label}</label>
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
                      variant="outline"
                      className="h-8 w-8 shrink-0 p-0"
                      onClick={fetchModels}
                      disabled={fetchingModels || !form.base_url?.trim()}
                    >
                      {fetchingModels ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <RefreshCw className="h-4 w-4" />
                      )}
                    </Button>
                  </div>
                  {availableModels.length === 0 && !fetchingModels && form.base_url?.trim() && (
                    <p className="text-xs text-muted-foreground">Click the refresh button to fetch models from the base URL.</p>
                  )}
                </>
              ) : f.options ? (
                <DropdownSelect
                  value={form[f.key] || ""}
                  onChange={(v) => set(f.key, v)}
                  options={f.options}
                />
              ) : f.key === "api_key" ? (
                <div className="relative">
                  <Input type={showApiKey ? "text" : "password"} value={form[f.key] || ""} onChange={(e) => set(f.key, e.target.value)} placeholder={f.placeholder} />
                  <Button variant="ghost" size="icon" className="absolute right-0 top-0 h-full px-3" onClick={() => setShowApiKey(!showApiKey)}>
                    {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </Button>
                </div>
              ) : (
                <Input type={f.type || "text"} value={form[f.key] || ""} onChange={(e) => set(f.key, e.target.value)} placeholder={f.placeholder} className={f.key === "name" ? "uppercase" : ""} />
              )}
            </div>
          ))}
          {renderExtra?.(form, set)}
          <label className="flex items-center gap-2 text-sm font-light uppercase tracking-wider cursor-pointer">
            <input type="checkbox" checked={form[checkboxField] === "true"} onChange={(e) => set(checkboxField, e.target.checked ? "true" : "false")} className="rounded" />
            {checkboxLabel}
          </label>
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={() => onOpenChange(false)} className="font-light uppercase">Cancel</Button>
          <Button onClick={handleSave} disabled={saving} className="font-light uppercase">{saving ? "Saving..." : provider ? "Update" : "Create"}</Button>
        </div>
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

function TranscriptionProviderCard({ provider, kind, onEdit, onRefresh, onDelete, onSetActive, onTest }: TranscriptionProviderCardProps) {
  const modelLabel = provider.adapter === "dashscope"
    ? kind === "file" ? "fun-asr" : "fun-asr-realtime"
    : provider.adapter
  const [status, setStatus] = useState<"unknown" | "ready" | "error">("unknown")
  const [testing, setTesting] = useState(false)
  const statusColor = status === "ready" ? "bg-emerald-500" : status === "error" ? "bg-red-500" : "bg-muted-foreground/40"

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
      else { toast.success(res.message || "Deleted"); onRefresh() }
    } catch { toast.error("Delete failed") }
  }

  const handleSetActive = async () => {
    try {
      const res = await onSetActive(provider.id)
      if (res.error) toast.error(res.error)
      else { toast.success(res.message || "Default provider updated"); onRefresh() }
    } catch { toast.error("Failed to set default") }
  }

  return (
    <div className="border border-border/50 rounded-lg p-4 flex flex-col h-full">
      {/* Row 1: Provider name + status + default badge */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <span className="text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground">{provider.name || "Unnamed"}</span>
          <div className={`h-2 w-2 rounded-full shrink-0 ${statusColor}`} />
        </div>
        {provider.is_active && (
          <Badge className="text-[10px] font-medium uppercase tracking-[0.1em] bg-emerald-50 text-emerald-700 hover:bg-emerald-50 dark:bg-emerald-950/30 dark:text-emerald-400 shrink-0"><Star className="h-3 w-3 mr-1" />DEFAULT</Badge>
        )}
      </div>

      {/* Row 2: Model */}
      <div className="mt-1 min-h-[1.25rem]">
        <p className="font-normal text-[12px] text-muted-foreground/80">{modelLabel}</p>
      </div>

      {/* Row 3: URL (reserved for uniform height) */}
      <div className="min-h-[1rem]" />

      {/* Row 4: Buttons */}
      <div className="flex gap-2 mt-auto pt-3">
        <Button variant="outline" size="sm" onClick={handleSetActive} disabled={provider.is_active} className="font-medium uppercase tracking-[0.1em] text-[10px]">
          <Star className="h-3 w-3 mr-1" />DEFAULT
        </Button>
        <Button variant="outline" size="sm" onClick={handleTest} disabled={testing} className="font-medium uppercase tracking-[0.1em] text-[10px]">
          {testing ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Zap className="h-3 w-3 mr-1" />}
          TEST
        </Button>
        <Button variant="outline" size="sm" onClick={() => onEdit(provider)} className="font-medium uppercase tracking-[0.1em] text-[10px]">
          <Pencil className="h-3 w-3 mr-1" />EDIT
        </Button>
        <Button variant="outline" size="sm" onClick={handleDelete} className="font-medium uppercase tracking-[0.1em] text-[10px] hover:text-orange-600 dark:hover:text-orange-400">
          <Trash2 className="h-3 w-3 mr-1" />DELETE
        </Button>
      </div>
    </div>
  )
}

// ── Main View ──

export function LLMProviderView() {
  const { providers, setProviders } = useAppStore()
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

  // OneShot Dashscope dialog
  const [oneshotDialogOpen, setOneshotDialogOpen] = useState(false)
const [openrouterDialogOpen, setOpenrouterDialogOpen] = useState(false)

  // Language hints config editor state for file transcription openai_compatible adapter
  const [fileTransLangHints, setFileTransLangHints] = useState<LanguageHintOption[]>([])

  // Local model device
  const [localDevice, setLocalDevice] = useState<string>("cpu")

  // Visual Model selection
  const [visualModelId, setVisualModelId] = useState<string>("")
  const [showVisualModelDropdown, setShowVisualModelDropdown] = useState(false)
  const visualModelBtnRef = useRef<HTMLButtonElement>(null)
  const visualModelMenuRef = useRef<HTMLDivElement>(null)

  // Chat Model selection
  const [chatModelId, setChatModelId] = useState<string>("")
  const [showChatModelDropdown, setShowChatModelDropdown] = useState(false)
  const chatModelBtnRef = useRef<HTMLButtonElement>(null)
  const chatModelMenuRef = useRef<HTMLDivElement>(null)
  const [chatModelPos, setChatModelPos] = useState({ top: 0, left: 0, width: 0 })
  const [visualModelPos, setVisualModelPos] = useState({ top: 0, left: 0, width: 0 })

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
  const [meetingModel,setMeetingModel]=useState("");const [meetingThinking,setMeetingThinking]=useState(true);const [meetingThinkingConfirmOpen,setMeetingThinkingConfirmOpen]=useState(false)
  const [showAdvanced,setShowAdvanced]=useState(false)
  const [showModelConfig,setShowModelConfig]=useState(false)
  const enrichModelBtnRef=useRef<HTMLButtonElement>(null);const enrichModelMenuRef=useRef<HTMLDivElement>(null);const [showEnrichModelDropdown,setShowEnrichModelDropdown]=useState(false);const [enrichModelPos,setEnrichModelPos]=useState<{top:number;left:number;width:number}>({top:0,left:0,width:0})
  const meetingModelBtnRef=useRef<HTMLButtonElement>(null);const meetingModelMenuRef=useRef<HTMLDivElement>(null);const [showMeetingModelDropdown,setShowMeetingModelDropdown]=useState(false);const [meetingModelPos,setMeetingModelPos]=useState<{top:number;left:number;width:number}>({top:0,left:0,width:0})
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

  // MinerU custom dropdown state
  const [showMineruModelDropdown, setShowMineruModelDropdown] = useState(false)
  const [showMineruLanguageDropdown, setShowMineruLanguageDropdown] = useState(false)
  const mineruModelBtnRef = useRef<HTMLButtonElement>(null)
  const mineruModelMenuRef = useRef<HTMLDivElement>(null)
  const mineruLangBtnRef = useRef<HTMLButtonElement>(null)
  const mineruLangMenuRef = useRef<HTMLDivElement>(null)

  // Close dropdowns on click outside
  useEffect(() => {
    if (!showVisualModelDropdown && !showChatModelDropdown && !showMineruModelDropdown && !showMineruLanguageDropdown) return
    const handler = (e: MouseEvent) => {
      if (showVisualModelDropdown &&
          !visualModelBtnRef.current?.contains(e.target as Node) &&
          !visualModelMenuRef.current?.contains(e.target as Node)) {
        setShowVisualModelDropdown(false)
      }
      if (showChatModelDropdown &&
          !chatModelBtnRef.current?.contains(e.target as Node) &&
          !chatModelMenuRef.current?.contains(e.target as Node)) {
        setShowChatModelDropdown(false)
      }
      if (showMineruModelDropdown &&
          !mineruModelBtnRef.current?.contains(e.target as Node) &&
          !mineruModelMenuRef.current?.contains(e.target as Node)) {
        setShowMineruModelDropdown(false)
      }
      if (showMineruLanguageDropdown &&
          !mineruLangBtnRef.current?.contains(e.target as Node) &&
          !mineruLangMenuRef.current?.contains(e.target as Node)) {
        setShowMineruLanguageDropdown(false)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [showVisualModelDropdown, showMineruModelDropdown, showMineruLanguageDropdown])

  // Dropdown position state — computed after DOM layout to avoid flying-in on first click
  const [mineruModelPos, setMineruModelPos] = useState({ top: 0, left: 0, width: 0 })
  const [mineruLangPos, setMineruLangPos] = useState({ top: 0, left: 0, width: 0 })

  const updateMineruPositions = useCallback(() => {
    if (mineruModelMenuRef.current && mineruModelBtnRef.current) {
      const r = mineruModelMenuRef.current.getBoundingClientRect()
      setMineruModelPos({ top: r.bottom + 4, left: r.left, width: mineruModelBtnRef.current.getBoundingClientRect().width })
    }
    if (mineruLangMenuRef.current && mineruLangBtnRef.current) {
      const r = mineruLangMenuRef.current.getBoundingClientRect()
      setMineruLangPos({ top: r.bottom + 4, left: r.left, width: mineruLangBtnRef.current.getBoundingClientRect().width })
    }
  }, [])

  // Position is computed synchronously in onClick handlers (before state update)
  // to avoid rendering at a stale position. Scroll/resize updates use the callback.

  useEffect(() => {
    if (!showVisualModelDropdown && !showMineruModelDropdown && !showMineruLanguageDropdown) return
    const updateAll = () => {
      updateMineruPositions()
      if (visualModelMenuRef.current && visualModelBtnRef.current) {
        const r = visualModelMenuRef.current.getBoundingClientRect()
        setVisualModelPos({ top: r.bottom + 4, left: r.left, width: visualModelBtnRef.current.getBoundingClientRect().width })
      }
    }
    window.addEventListener("scroll", updateAll, true)
    window.addEventListener("resize", updateAll)
    return () => {
      window.removeEventListener("scroll", updateAll, true)
      window.removeEventListener("resize", updateAll)
    }
  }, [showVisualModelDropdown, showMineruModelDropdown, showMineruLanguageDropdown, updateMineruPositions])

  // Runtime load states from backend
  const [loadStates, setLoadStates] = useState<Record<string, string>>({})

  // Model download status (id → downloaded)
  const [modelDownloaded, setModelDownloaded] = useState<Record<string, boolean>>({})

  const refreshModelDownloaded = async () => {
    try {
      const status = await getModelStatus()
      const map: Record<string, boolean> = {}
      for (const m of status) {
        map[m.id] = m.downloaded
      }
      // builtin-local-file needs transcription + vad + speaker + punc
      // builtin-local-rt needs realtime
      const fileTransReady = (map.transcription && map.vad && map.speaker && map.punc)
      setModelDownloaded({
        "builtin-local-file": fileTransReady,
        "builtin-local-rt": map.realtime || false,
      })
    } catch { /* ignore */ }
  }

  // Fetch runtime load states, poll while any model is loading/downloading
  const refreshLoadStates = async () => {
    try {
      const state = await getModelState()
      setLoadStates(state.load_states || {})
      return state.load_states || {}
    } catch { return {} }
  }

  const startPolling = () => {
    const poll = async () => {
      const states = await refreshLoadStates()
      await refreshModelDownloaded()
      const stillLoading = Object.values(states).some((v) => v === "loading")
      // Check if any model is still downloading
      let stillDownloading = false
      try {
        const ms = await getModelStatus()
        stillDownloading = ms.some((m) => m.status === "downloading")
      } catch { /* ignore */ }
      if (stillLoading || stillDownloading) {
        setTimeout(poll, 1500)
      }
    }
    // Always start first poll immediately regardless of current state
    setTimeout(poll, 1500)
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
        const isDownloading = ms.some((m) => m.status === "downloading")
        if (isLoading || isDownloading) {
          startPolling()
        }
      } catch { /* ignore */ }
    }
    init()
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

  useEffect(() => {
    fetchProviders()
    fetchEmbProviders()
    fetchRerankProviders()
    fetchFileTransProviders()
    fetchRtTransProviders()
    refreshModelDownloaded()
    getConfig().then((c) => {
      setLocalDevice(typeof c.transcription?.local_device === "string" ? c.transcription.local_device as string : "cpu")
      // Load Visual Model config
      if (c.visual_model_id && typeof c.visual_model_id === "string") setVisualModelId(c.visual_model_id)
      if (c.default_chat_model && typeof c.default_chat_model === "string") setChatModelId(c.default_chat_model)
      if(c.rag){if(typeof c.rag.top_k==="number")setRagTopK(String(c.rag.top_k));if(typeof c.rag.rerank_top_k==="number")setRagRerankTopK(String(c.rag.rerank_top_k));if(typeof c.rag.max_parallel_queries==="number")setRagMaxParallel(String(c.rag.max_parallel_queries));if(typeof c.rag.max_iterations==="number")setRagMaxIter(String(c.rag.max_iterations));if(typeof c.rag.default_search_mode==="string")setRagSearchMode(c.rag.default_search_mode);if(typeof c.rag.min_score==="number")setRagMinScore(String(c.rag.min_score))}
      if(c.direct_rag){if(typeof c.direct_rag.top_k==="number")setDirTopK(String(c.direct_rag.top_k));if(typeof c.direct_rag.rerank_top_k==="number")setDirRerankTopK(String(c.direct_rag.rerank_top_k));if(typeof c.direct_rag.default_search_mode==="string")setDirSearchMode(c.direct_rag.default_search_mode);setDirRerankEnabled(c.direct_rag.use_reranker!==false);if(typeof c.direct_rag.min_score==="number")setDirMinScore(String(c.direct_rag.min_score))}
      if(c.enrichment){if(typeof c.enrichment.max_parallel_context==="number")setEnrichMaxParallel(String(c.enrichment.max_parallel_context));if(typeof c.enrichment.enrichment_model==="string")setEnrichModel(c.enrichment.enrichment_model);if(typeof c.enrichment.meeting_model==="string")setMeetingModel(c.enrichment.meeting_model);if(typeof c.enrichment.meeting_thinking==="boolean")setMeetingThinking(c.enrichment.meeting_thinking)}
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
    if (adapter.startsWith("funasr_local")) {
      return [
        ...fileTransFields,
        { key: "device", label: "Device", options: [
          { value: "auto", label: "Auto (recommended)" },
          { value: "mps", label: "Apple Silicon (MPS)" },
          { value: "cuda", label: "CUDA (NVIDIA)" },
          { value: "cpu", label: "CPU" },
        ]},
      ]
    }
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
    // Remote adapters: only api_key
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
    if (adapter.startsWith("funasr_local")) {
      return [
        ...rtTransFields,
        { key: "device", label: "Device", options: [
          { value: "auto", label: "Auto (recommended)" },
          { value: "mps", label: "Apple Silicon (MPS)" },
          { value: "cuda", label: "CUDA (NVIDIA)" },
          { value: "cpu", label: "CPU" },
        ]},
      ]
    }
    if (adapter === "openai_compatible") {
      return [
        ...rtTransFields,
        { key: "base_url", label: "Base URL", placeholder: "https://api.openai.com/v1" },
        { key: "model", label: "Model", placeholder: "gpt-4o-realtime-preview" },
        { key: "api_key", label: "API Key", type: "password", placeholder: "sk-..." },
      ]
    }
    return [
      ...rtTransFields,
      { key: "api_key", label: "API Key", type: "password", placeholder: "sk-..." },
    ]
  }

  return (
    <div className="h-full overflow-auto p-6">
      <div className="max-w-4xl mx-auto space-y-2">
        {/* ── OneShot Dashscope ── */}
        <div className="flex items-center justify-between pb-6 mb-2 border-b border-dashed border-border">
          <div>
            <p className="text-[18px] font-[350] tracking-tight uppercase">QUICK SETUP</p>
            <p className="font-normal text-[12px] text-muted-foreground/80 leading-relaxed">Quickly configure providers with a single API Key</p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setOneshotDialogOpen(true)} className="font-light uppercase">
              <Zap className="h-4 w-4 mr-2" />Dashscope
            </Button>
            <Button variant="outline" onClick={() => setOpenrouterDialogOpen(true)} className="font-light uppercase">
              <Zap className="h-4 w-4 mr-2" />OpenRouter
            </Button>
          </div>
        </div>

        {/* ── LLM Providers ── */}
        <section className="border-b border-border pb-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-[18px] font-[350] tracking-tight uppercase">MODELS</h2>
            </div>
            <div className="flex gap-2">
              <Button variant="default" onClick={handleAdd} className="font-light uppercase">ADD</Button>
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {providers.filter((p) => !p.id.startsWith("builtin-")).map((p) => (
              <ProviderCard key={p.id} provider={p} onEdit={handleEdit} onRefresh={fetchProviders} />
            ))}
          </div>

          {/* ── Model Configuration (collapsible sub-menu) ── */}
          <div className="pt-4 mt-4 border-t border-border/50">
          <button
            onClick={() => setShowModelConfig(!showModelConfig)}
            className="flex items-center justify-between w-full text-left"
          >
            <div>
              <h2 className="text-[14px] font-[350] tracking-tight uppercase">MORE</h2>
            </div>
            <ChevronRight className={`w-4 h-4 text-muted-foreground transition-transform duration-300 ${showModelConfig ? "rotate-90" : ""}`} />
          </button>
          <div className={`grid transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${showModelConfig ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"}`}>
            <div className="overflow-hidden">

            {/* Visual Model */}
            <div className="pt-4">
              <h3 className="text-[12px] font-[350] uppercase tracking-tight mb-2">VISUAL MODEL</h3>
              {(() => {
                const visualModels = providers.flatMap((p) =>
                  (p.visual_model_ids || []).map((m) => ({
                    model: m,
                    providerName: p.name || p.id,
                    providerId: p.id,
                  }))
                )
                if (visualModels.length === 0) {
                  return (
                    <div className="border border-dashed border-muted-foreground/30 rounded-lg p-4 text-center">
                      <Sparkles className="h-6 w-6 mx-auto mb-1 text-muted-foreground/40" />
                      <p className="font-normal text-[11px] text-muted-foreground/70">No visual-capable models configured.</p>
                    </div>
                  )
                }
                return (
                  <div className="flex items-center gap-3">
                    <span className="text-[11px] font-[350] uppercase tracking-[0.08em] text-muted-foreground whitespace-nowrap">MODEL</span>
                    <div className="flex-1 max-w-md relative" ref={visualModelMenuRef}>
                      <button type="button" ref={visualModelBtnRef} onClick={() => { const r = visualModelMenuRef.current?.getBoundingClientRect(); if (r) setVisualModelPos({ top: r.bottom + 4, left: r.left, width: visualModelBtnRef.current?.getBoundingClientRect().width || r.width }); setShowVisualModelDropdown(!showVisualModelDropdown) }} className="group relative flex items-center justify-center overflow-hidden rounded px-3 py-2 t-sans-family transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] w-full" style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", color: showVisualModelDropdown ? "var(--color-primary-foreground)" : visualModelId ? "var(--color-primary)" : "var(--color-muted-foreground)" }}>
                        <span className="relative z-10 whitespace-nowrap">{visualModelId ? visualModels.find(vm => vm.model === visualModelId)?.model || visualModelId : "NONE (DISABLED)"}</span>
                        <span className="absolute inset-0 z-0 transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] bg-primary" style={{ transform: showVisualModelDropdown ? "scaleX(1)" : "scaleX(0)", transformOrigin: showVisualModelDropdown ? "right" : "left" }} />
                      </button>
                      <div className={`fixed z-[100] mt-1 flex-col overflow-hidden rounded border border-primary/40 bg-popover shadow-md transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] max-h-64 overflow-y-auto ${showVisualModelDropdown ? "opacity-100 visible translate-y-0 pointer-events-auto" : "opacity-0 invisible -translate-y-3 pointer-events-none"}`} style={{ width: visualModelPos.width, top: visualModelPos.top, left: visualModelPos.left }}>
                        {[{ value: "", label: "NONE (DISABLED)" }, ...visualModels.map(vm => ({ value: vm.model, label: `${vm.providerName} / ${vm.model}` }))].map((opt) => (
                          <button key={opt.value} type="button" onClick={async () => { setVisualModelId(opt.value); setShowVisualModelDropdown(false); try { await updateConfig("visual_model_id", { visual_model_id: opt.value || null }); toast.success("Visual model updated") } catch { toast.error("Failed to update visual model") } }} className="relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group" style={{ background: "none", border: "none" }}>
                            <span className="relative z-10 flex items-center gap-2 px-3 py-2 w-full text-[10px]" style={{ letterSpacing: "0.05em" }}>
                              {visualModelId === opt.value || (!visualModelId && opt.value === "") ? <span className="w-1.5 h-1.5 bg-primary group-hover:bg-primary-foreground rotate-45 shrink-0 transition-colors duration-700" /> : <span className="w-1.5 h-1.5 shrink-0" />}
                              {opt.label}
                            </span>
                            <span className="absolute inset-0 z-0 bg-primary transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                )
              })()}
            </div>

            {/* Chat Model */}
            <div className="pt-4 mt-3 border-t border-border/50">
              <h3 className="text-[12px] font-[350] uppercase tracking-tight mb-2">CHAT MODEL</h3>
              {(() => {
                const chatModels = providers.flatMap((p) =>
                  (p.selected_models || (p.model ? [p.model] : [])).map((m) => ({
                    model: m,
                    providerName: p.name || p.id,
                    providerId: p.id,
                    isFunctionCall: ((p as any).function_call_model_ids || []).includes(m),
                  }))
                ).filter((cm) => cm.isFunctionCall)
                if (chatModels.length === 0) {
                  return (
                    <div className="border border-dashed border-muted-foreground/30 rounded-lg p-4 text-center">
                      <MessageSquare className="h-6 w-6 mx-auto mb-1 text-muted-foreground/40" />
                      <p className="font-normal text-[11px] text-muted-foreground/70">No chat-capable models configured.</p>
                    </div>
                  )
                }
                return (
                  <div className="flex items-center gap-3">
                    <span className="text-[11px] font-[350] uppercase tracking-[0.08em] text-muted-foreground whitespace-nowrap">MODEL</span>
                    <div className="flex-1 max-w-md relative" ref={chatModelMenuRef}>
                      <button type="button" ref={chatModelBtnRef} onClick={() => { const r = chatModelMenuRef.current?.getBoundingClientRect(); if (r) setChatModelPos({ top: r.bottom + 4, left: r.left, width: chatModelBtnRef.current?.getBoundingClientRect().width || r.width }); setShowChatModelDropdown(!showChatModelDropdown) }} className="group relative flex items-center justify-center overflow-hidden rounded px-3 py-2 t-sans-family transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] w-full" style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", color: showChatModelDropdown ? "var(--color-primary-foreground)" : chatModelId ? "var(--color-primary)" : "var(--color-muted-foreground)" }}>
                        <span className="relative z-10 whitespace-nowrap">{chatModelId ? chatModels.find(vm => vm.model === chatModelId)?.model || chatModelId : "DEFAULT"}</span>
                        <span className="absolute inset-0 z-0 transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] bg-primary" style={{ transform: showChatModelDropdown ? "scaleX(1)" : "scaleX(0)", transformOrigin: showChatModelDropdown ? "right" : "left" }} />
                      </button>
                      <div className={`fixed z-[100] mt-1 flex-col overflow-hidden rounded border border-primary/40 bg-popover shadow-md transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] max-h-64 overflow-y-auto ${showChatModelDropdown ? "opacity-100 visible translate-y-0 pointer-events-auto" : "opacity-0 invisible -translate-y-3 pointer-events-none"}`} style={{ width: chatModelPos.width, top: chatModelPos.top, left: chatModelPos.left }}>
                        {[{ value: "", label: "DEFAULT" }, ...chatModels.map(vm => ({ value: vm.model, label: `${vm.providerName} / ${vm.model}` }))].map((opt) => (
                          <button key={opt.value} type="button" onClick={async () => { setChatModelId(opt.value); setShowChatModelDropdown(false); try { await updateConfig("default_chat_model", { default_chat_model: opt.value || null }); toast.success("Chat model updated") } catch { toast.error("Failed to update chat model") } }} className="relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group" style={{ background: "none", border: "none" }}>
                            <span className="relative z-10 flex items-center gap-2 px-3 py-2 w-full text-[10px]" style={{ letterSpacing: "0.05em" }}>
                              {chatModelId === opt.value || (!chatModelId && opt.value === "") ? <span className="w-1.5 h-1.5 bg-primary group-hover:bg-primary-foreground rotate-45 shrink-0 transition-colors duration-700" /> : <span className="w-1.5 h-1.5 shrink-0" />}
                              {opt.label}
                            </span>
                            <span className="absolute inset-0 z-0 bg-primary transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                )
              })()}
            </div>

            {/* Meeting Summary Model */}
            <div className="pt-4 mt-3 border-t border-border/50 pb-2">
              <h3 className="text-[12px] font-[350] uppercase tracking-tight mb-2">MEETING SUMMARY MODEL</h3>
              {(() => {
                const meetingModels = providers.flatMap((p) =>
                  (p.selected_models || (p.model ? [p.model] : [])).map((m) => ({
                    model: m,
                    providerName: p.name || p.id,
                    providerId: p.id,
                  }))
                )
                if (meetingModels.length === 0) {
                  return (
                    <div className="border border-dashed border-muted-foreground/30 rounded-lg p-4 text-center">
                      <p className="font-normal text-[11px] text-muted-foreground/70">No LLM providers configured.</p>
                    </div>
                  )
                }
                return (
                  <div className="flex items-center gap-3">
                    <span className="text-[11px] font-[350] uppercase tracking-[0.08em] text-muted-foreground whitespace-nowrap">MODEL</span>
                    <div className="flex-1 max-w-md relative" ref={meetingModelMenuRef}>
                      <button type="button" ref={meetingModelBtnRef} onClick={()=>{const r=meetingModelMenuRef.current?.getBoundingClientRect();if(r)setMeetingModelPos({top:r.bottom+4,left:r.left,width:meetingModelBtnRef.current?.getBoundingClientRect().width||r.width});setShowMeetingModelDropdown(!showMeetingModelDropdown)}} className="group relative flex items-center justify-center overflow-hidden rounded px-3 py-2 t-sans-family transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] w-full" style={{fontSize:"10px",fontWeight:500,letterSpacing:"0.1em",textTransform:"uppercase",color:showMeetingModelDropdown?"var(--color-primary-foreground)":meetingModel?"var(--color-primary)":"var(--color-muted-foreground)"}}>
                        <span className="relative z-10 whitespace-nowrap">{meetingModel?(()=>{const [pid,mid]=meetingModel.split("|");const p=providers.find(x=>x.id===pid);return p?(p.name||p.id)+" / "+mid:meetingModel})():"DEFAULT"}</span>
                        <span className="absolute inset-0 z-0 transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] bg-primary" style={{transform:showMeetingModelDropdown?"scaleX(1)":"scaleX(0)",transformOrigin:showMeetingModelDropdown?"right":"left"}}/>
                      </button>
                      <div className={`fixed z-[100] mt-1 flex-col overflow-hidden rounded border border-primary/40 bg-popover shadow-md transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] max-h-64 overflow-y-auto ${showMeetingModelDropdown?"opacity-100 visible translate-y-0 pointer-events-auto":"opacity-0 invisible -translate-y-3 pointer-events-none"}`} style={{width:meetingModelPos.width,top:meetingModelPos.top,left:meetingModelPos.left}}>
                        {[{value:"",label:"DEFAULT",providerId:"",model:""},...meetingModels.map(m=>({value:`${m.providerId}|${m.model}`,label:`${m.providerName} / ${m.model}`,providerId:m.providerId,model:m.model}))].map(opt=>(<button key={opt.value} type="button" onClick={async()=>{setMeetingModel(opt.value);setShowMeetingModelDropdown(false);try{await updateConfig("enrichment",{meeting_model:opt.value,meeting_thinking:meetingThinking});toast.success("Meeting model updated")}catch{toast.error("Failed to update")}}} className="relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group" style={{background:"none",border:"none"}}><span className="relative z-10 flex items-center gap-2 px-3 py-2 w-full text-[10px]" style={{letterSpacing:"0.05em"}}>{meetingModel===opt.value||(!meetingModel&&opt.value==="")?<span className="w-1.5 h-1.5 bg-primary group-hover:bg-primary-foreground rotate-45 shrink-0 transition-colors duration-700"/>:<span className="w-1.5 h-1.5 shrink-0"/>}{opt.label}</span><span className="absolute inset-0 z-0 bg-primary transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right"/></button>))}
                      </div>
                    </div>
                    <button type="button" onClick={()=>{if(meetingThinking){setMeetingThinkingConfirmOpen(true)}else{setMeetingThinking(true);updateConfig("enrichment",{meeting_model:meetingModel,meeting_thinking:true}).catch(()=>{toast.error("Failed to update");setMeetingThinking(false)})}}} className={`flex items-center gap-1.5 cursor-pointer t-sans-family transition-all ${meetingThinking?"sk-thinking-flow text-primary":"border-none bg-transparent text-muted-foreground hover:text-primary"}`} style={{fontSize:"10px",fontWeight:500,letterSpacing:"0.1em",textTransform:"uppercase",padding:meetingThinking?"2px 7px":"0",borderRadius:"2px"}} title={meetingThinking?"Deep thinking ON":"Deep thinking OFF"}>
                      THINKING {meetingThinking?"ON":"OFF"}
                    </button>
                  </div>
                )
              })()}
            </div>

            </div>
          </div>
          </div>
        </section>

        {/* ── Embedding Providers ── */}
        <section className="border-b border-border pb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-[18px] font-[350] tracking-tight uppercase">EMBEDDING MODELS</h2>
            <Button variant="default" onClick={() => { setEditingEmb(null); setEmbDialogOpen(true) }} className="font-light uppercase">ADD</Button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {embProviders.filter((p) => !p.id.startsWith("builtin-")).map((p) => (
              <SimpleProviderCard key={p.id} provider={p}
                onEdit={(p) => { setEditingEmb(p); setEmbDialogOpen(true) }}
                onRefresh={fetchEmbProviders} onTest={testEmbeddingProvider}
                onDelete={deleteEmbeddingProvider} onSetDefault={setDefaultEmbeddingProvider} />
            ))}
          </div>
        </section>

        {/* ── Rerank Providers ── */}
        <section className="border-b border-border pb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-[18px] font-[350] tracking-tight uppercase">RERANK MODELS</h2>
            <Button variant="default" onClick={() => { setEditingRerank(null); setRerankDialogOpen(true) }} className="font-light uppercase">ADD</Button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {rerankProviders.filter((p) => !p.id.startsWith("builtin-")).map((p) => (
              <SimpleProviderCard key={p.id} provider={p} subtitle={undefined}
                onEdit={(p) => { setEditingRerank(p); setRerankDialogOpen(true) }}
                onRefresh={fetchRerankProviders} onTest={testRerankProvider}
                onDelete={deleteRerankProvider} onSetDefault={setDefaultRerankProvider} />
            ))}
          </div>
        </section>

        {/* ── Transcription ── */}
        <section className="border-b border-border pb-6">
          <h2 className="text-[18px] font-[350] tracking-tight uppercase mb-4">TRANSCRIPTION</h2>

          {/* File Transcription */}
          <div className="mb-6">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground">FILE TRANSCRIPTION</h3>
              <Button variant="default" size="sm" onClick={() => { setEditingFileTrans(null); setFileTransLangHints([]); setFileTransDialogOpen(true) }} className="font-light uppercase">ADD</Button>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {builtinFileTrans && (
                <LocalModelCard
                  id={builtinFileTrans.id} name={builtinFileTrans.name} model={builtinFileTrans.model || builtinFileTrans.adapter}
                  isDefault={builtinFileTrans?.is_active ?? false} loadState={(loadStates[builtinFileTrans.id] || "unloaded") as LoadState}
                  isDownloaded={modelDownloaded["builtin-local-file"] ?? false}
                  onTest={async () => { const r = await testFileTranscriptionProvider(builtinFileTrans.id); return { success: r.success, message: r.message, error: r.error } }}
                  onSetDefault={async () => { await setActiveFileTranscriptionProvider(builtinFileTrans.id); fetchFileTransProviders() }}
                  onToggleLoad={async () => {
                    const res = await toggleModelLoad(builtinFileTrans.id)
                    if (!res.success && res.error) toast.error(res.error)
                    fetchFileTransProviders()
                    startPolling()
                  }}
                  onDownload={() => setModelDownloadOpen(true)}
                />
              )}
              {cloudFileProviders.map((p) => (
                <TranscriptionProviderCard key={p.id} provider={p} kind="file"
                  onEdit={(p) => { setEditingFileTrans(p); setFileTransLangHints(p.language_hints_config || []); setFileTransDialogOpen(true) }}
                  onRefresh={fetchFileTransProviders} onDelete={deleteFileTranscriptionProvider}
                  onSetActive={setActiveFileTranscriptionProvider} onTest={testFileTranscriptionProvider} />
              ))}
            </div>
          </div>

          {/* Realtime Transcription */}
          <div className="mb-6">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground">REALTIME TRANSCRIPTION</h3>
              <Button variant="default" size="sm" onClick={() => { setEditingRtTrans(null); setRtTransDialogOpen(true) }} className="font-light uppercase">ADD</Button>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {builtinRtTrans && (
                <LocalModelCard
                  id={builtinRtTrans.id} name={builtinRtTrans.name} model={builtinRtTrans.model || builtinRtTrans.adapter}
                  isDefault={builtinRtTrans?.is_active ?? false} loadState={(loadStates[builtinRtTrans.id] || "unloaded") as LoadState}
                  isDownloaded={modelDownloaded["builtin-local-rt"] ?? false}
                  onTest={async () => { const r = await testRealtimeTranscriptionProvider(builtinRtTrans.id); return { success: r.success, message: r.message, error: r.error } }}
                  onSetDefault={async () => { await setActiveRealtimeTranscriptionProvider(builtinRtTrans.id); fetchRtTransProviders() }}
                  onToggleLoad={async () => {
                    const res = await toggleModelLoad(builtinRtTrans.id)
                    if (!res.success && res.error) toast.error(res.error)
                    fetchRtTransProviders()
                    startPolling()
                  }}
                  onDownload={() => setModelDownloadOpen(true)}
                />
              )}
              {cloudRtProviders.map((p) => (
                <TranscriptionProviderCard key={p.id} provider={p} kind="realtime"
                  onEdit={(p) => { setEditingRtTrans(p); setRtTransDialogOpen(true) }}
                  onRefresh={fetchRtTransProviders} onDelete={deleteRealtimeTranscriptionProvider}
                  onSetActive={setActiveRealtimeTranscriptionProvider} onTest={testRealtimeTranscriptionProvider} />
              ))}
            </div>
          </div>

        </section>

        {/* ── Hot Words Management ── */}
        <section className="border-b border-border pb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-[18px] font-[350] tracking-tight uppercase">HOT WORDS</h2>
            <Button variant="default" onClick={() => setHotWordsManagerOpen(true)} className="font-light uppercase">
              Manage
            </Button>
          </div>
          <p className="font-normal text-[12px] text-muted-foreground/80 leading-relaxed">
            Manage hot word libraries to improve transcription accuracy for domain-specific terms like names, acronyms, and jargon.
          </p>
        </section>

        {/* ── MinerU CLOUD PARSING ── */}
        <section className="pb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-[18px] font-[350] tracking-tight">MinerU CLOUD PARSING</h2>
          </div>
          <div className="space-y-4">
              <p className="font-normal text-[12px] text-muted-foreground/80 mb-4 leading-relaxed">
                Use MinerU's cloud API for high-quality document parsing with better table, formula, and layout preservation.
                Get your API token at{" "}
                <a href="https://mineru.net/apiManage/token" target="_blank" rel="noopener noreferrer" className="text-primary underline">mineru.net/apiManage/token</a>.
                When enabled, activate per-collection in Collection Settings.
              </p>

              <div className="flex items-center justify-between">
                <div>
                  <span className="text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground">ENABLE MinerU</span>
                  <p className="font-normal text-[11px] text-muted-foreground/80 mt-0.5 leading-relaxed">Toggle cloud parsing globally</p>
                </div>
                <button
                  onClick={async () => {
                    const next = !mineruEnabled
                    setMineruEnabled(next)
                    try {
                      await updateConfig("mineru", {
                        enabled: next,
                        api_token: mineruToken,
                        base_url: "https://mineru.net/api/v4",
                        model_version: mineruModel,
                        is_ocr: mineruOcr,
                        enable_formula: mineruFormula,
                        enable_table: mineruTable,
                        language: mineruLanguage,
                      })
                      toast.success(next ? "MinerU enabled" : "MinerU disabled")
                    } catch {
                      toast.error("Failed to update MinerU setting")
                      setMineruEnabled(!next)
                    }
                  }}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${mineruEnabled ? "bg-primary" : "bg-input"}`}
                >
                  <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${mineruEnabled ? "translate-x-6" : "translate-x-1"}`} />
                </button>
              </div>

              <div className={`grid transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${mineruEnabled ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"}`}>
                <div className="overflow-hidden">
                  <div className="space-y-4 pt-4">
                    <div className="space-y-2">
                      <label className="text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground">API Token</label>
                  <div className="relative">
                    <Input
                      type={showMineruToken ? "text" : "password"}
                      value={mineruToken}
                      onChange={(e) => setMineruToken(e.target.value)}
                      placeholder="Enter your MinerU API token"
                      disabled={!mineruEnabled}
                      className="pr-10"
                    />
                    <button
                      type="button"
                      onClick={() => setShowMineruToken(!showMineruToken)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                    >
                      {showMineruToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  {/* Model Version — custom dropdown */}
                  <div className="space-y-2">
                    <label className="text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground">Model Version</label>
                    <div className="relative" ref={mineruModelMenuRef}>
                      <button
                        type="button"
                        ref={mineruModelBtnRef}
                        onClick={() => { if (!mineruEnabled) return; updateMineruPositions(); setShowMineruLanguageDropdown(false); setShowMineruModelDropdown(!showMineruModelDropdown) }}
                        disabled={!mineruEnabled}
                        className="group relative flex items-center justify-center overflow-hidden rounded px-3 py-2 t-sans-family transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] w-full"
                        style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", color: showMineruModelDropdown ? "var(--color-primary-foreground)" : "var(--color-primary)" }}
                      >
                        <span className="relative z-10 whitespace-nowrap">
                          {MINERU_MODEL_OPTIONS.find(o => o.value === mineruModel)?.label || mineruModel}
                        </span>
                        <span
                          className="absolute inset-0 z-0 transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] bg-primary"
                          style={{ transform: showMineruModelDropdown ? "scaleX(1)" : "scaleX(0)", transformOrigin: showMineruModelDropdown ? "right" : "left" }}
                        />
                      </button>
                      <div
                        className={`fixed z-[100] mt-1 flex-col overflow-hidden rounded border border-primary/40 bg-popover shadow-md transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${
                          showMineruModelDropdown ? "opacity-100 visible translate-y-0 pointer-events-auto" : "opacity-0 invisible -translate-y-3 pointer-events-none"
                        }`}
                        style={{ width: mineruModelPos.width, top: mineruModelPos.top, left: mineruModelPos.left }}
                      >
                        {MINERU_MODEL_OPTIONS.map((opt) => (
                          <button
                            key={opt.value}
                            type="button"
                            onClick={() => { setMineruModel(opt.value); setShowMineruModelDropdown(false) }}
                            className="relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group"
                            style={{ background: "none", border: "none" }}
                          >
                            <span className="relative z-10 flex items-center gap-2 px-3 py-2 w-full text-[10px]" style={{ letterSpacing: "0.05em" }}>
                              {mineruModel === opt.value ? (
                                <span className="w-1.5 h-1.5 bg-primary group-hover:bg-primary-foreground rotate-45 shrink-0 transition-colors duration-700" />
                              ) : (
                                <span className="w-1.5 h-1.5 shrink-0" />
                              )}
                              {opt.label}
                            </span>
                            <span className="absolute inset-0 z-0 bg-primary transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>

                  {/* Language — custom dropdown */}
                  <div className="space-y-2">
                    <label className="text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground">Language</label>
                    <div className="relative" ref={mineruLangMenuRef}>
                      <button
                        type="button"
                        ref={mineruLangBtnRef}
                        onClick={() => { if (!mineruEnabled) return; updateMineruPositions(); setShowMineruModelDropdown(false); setShowMineruLanguageDropdown(!showMineruLanguageDropdown) }}
                        disabled={!mineruEnabled}
                        className="group relative flex items-center justify-center overflow-hidden rounded px-3 py-2 t-sans-family transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] w-full"
                        style={{ fontSize: "10px", fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", color: showMineruLanguageDropdown ? "var(--color-primary-foreground)" : "var(--color-primary)" }}
                      >
                        <span className="relative z-10 whitespace-nowrap">
                          {MINERU_LANGUAGE_OPTIONS.find(o => o.value === mineruLanguage)?.label || mineruLanguage}
                        </span>
                        <span
                          className="absolute inset-0 z-0 transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] bg-primary"
                          style={{ transform: showMineruLanguageDropdown ? "scaleX(1)" : "scaleX(0)", transformOrigin: showMineruLanguageDropdown ? "right" : "left" }}
                        />
                      </button>
                      <div
                        className={`fixed z-[100] mt-1 flex-col overflow-hidden rounded border border-primary/40 bg-popover shadow-md transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] max-h-64 overflow-y-auto ${
                          showMineruLanguageDropdown ? "opacity-100 visible translate-y-0 pointer-events-auto" : "opacity-0 invisible -translate-y-3 pointer-events-none"
                        }`}
                        style={{ width: mineruLangPos.width, top: mineruLangPos.top, left: mineruLangPos.left }}
                      >
                        {MINERU_LANGUAGE_OPTIONS.map((opt) => (
                          <button
                            key={opt.value}
                            type="button"
                            onClick={() => { setMineruLanguage(opt.value); setShowMineruLanguageDropdown(false) }}
                            className="relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group"
                            style={{ background: "none", border: "none" }}
                          >
                            <span className="relative z-10 flex items-center gap-2 px-3 py-2 w-full text-[10px]" style={{ letterSpacing: "0.05em" }}>
                              {mineruLanguage === opt.value ? (
                                <span className="w-1.5 h-1.5 bg-primary group-hover:bg-primary-foreground rotate-45 shrink-0 transition-colors duration-700" />
                              ) : (
                                <span className="w-1.5 h-1.5 shrink-0" />
                              )}
                              {opt.label}
                            </span>
                            <span className="absolute inset-0 z-0 bg-primary transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="space-y-3">
                  <label className="text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground">Parsing Options</label>
                  <div className="space-y-2">
                    <label className={`flex items-start gap-2 text-sm ${!mineruEnabled ? "cursor-default" : "cursor-pointer"}`}>
                      <input type="checkbox" checked={mineruOcr} onChange={(e) => setMineruOcr(e.target.checked)} disabled={!mineruEnabled} className="rounded mt-0.5" />
                      <div>
                        <span className="text-[12px] font-normal uppercase tracking-[0.08em] text-muted-foreground">Force OCR</span>
                        <p className="font-normal text-[11px] text-muted-foreground/80 mt-0.5 leading-relaxed">Force OCR on all pages. When off, MinerU auto-detects whether pages need OCR (scanned/image pages will still be OCR'd automatically).</p>
                      </div>
                    </label>
                    <label className={`flex items-start gap-2 text-sm ${!mineruEnabled ? "cursor-default" : "cursor-pointer"}`}>
                      <input type="checkbox" checked={mineruFormula} onChange={(e) => setMineruFormula(e.target.checked)} disabled={!mineruEnabled} className="rounded mt-0.5" />
                      <div>
                        <span className="text-[12px] font-normal uppercase tracking-[0.08em] text-muted-foreground">Formula Recognition</span>
                        <p className="font-normal text-[11px] text-muted-foreground/80 mt-0.5 leading-relaxed">Recognize mathematical formulas and convert to LaTeX. Recommended for academic/technical documents.</p>
                      </div>
                    </label>
                    <label className={`flex items-start gap-2 text-sm ${!mineruEnabled ? "cursor-default" : "cursor-pointer"}`}>
                      <input type="checkbox" checked={mineruTable} onChange={(e) => setMineruTable(e.target.checked)} disabled={!mineruEnabled} className="rounded mt-0.5" />
                      <div>
                        <span className="text-[12px] font-normal uppercase tracking-[0.08em] text-muted-foreground">Table Recognition</span>
                        <p className="font-normal text-[11px] text-muted-foreground/80 mt-0.5 leading-relaxed">Detect and extract tables as structured Markdown. Recommended for documents with tabular data.</p>
                      </div>
                    </label>
                  </div>
                </div>
                  </div>
                </div>
              </div>

          </div>
        </section>

        {/* Advanced */}
        <div className="border-b border-border pb-6">
          <h2 onClick={()=>{setShowAdvanced(!showAdvanced);if(!showAdvanced)setTimeout(()=>document.getElementById("advanced-content")?.scrollIntoView({behavior:"smooth",block:"start"}),100)}} className="text-[18px] font-[350] tracking-tight uppercase cursor-pointer hover:text-primary transition-colors">ADVANCED</h2>
          <div id="advanced-content" className={`transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] overflow-hidden ${showAdvanced?"max-h-[4000px] opacity-100":"max-h-0 opacity-0"}`}>
          <div className="space-y-8 pt-4">

            {/* Enrichment */}
            <div className="pb-6">
              <h3 className="text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground mb-3">ENRICHMENT</h3>
              <p className="font-normal text-[11px] text-muted-foreground/80 leading-relaxed mb-4">Context generation for each chunk during document ingestion.</p>
              <div className="flex items-center gap-3 mb-4">
                <span className="text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground whitespace-nowrap">MODEL</span>
                <div className="flex-1 max-w-md relative" ref={enrichModelMenuRef}>
                  <button type="button" ref={enrichModelBtnRef} onClick={()=>{const r=enrichModelMenuRef.current?.getBoundingClientRect();if(r)setEnrichModelPos({top:r.bottom+4,left:r.left,width:enrichModelBtnRef.current?.getBoundingClientRect().width||r.width});setShowEnrichModelDropdown(!showEnrichModelDropdown)}} className="group relative flex items-center justify-center overflow-hidden rounded px-3 py-2 t-sans-family transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] w-full" style={{fontSize:"10px",fontWeight:500,letterSpacing:"0.1em",textTransform:"uppercase",color:showEnrichModelDropdown?"var(--color-primary-foreground)":enrichModel?"var(--color-primary)":"var(--color-muted-foreground)"}}>
                    <span className="relative z-10 whitespace-nowrap">{enrichModel?(()=>{const p=providers.find(x=>x.id===enrichModel);return p?p.name+" / "+p.model:enrichModel})():"DEFAULT"}</span>
                    <span className="absolute inset-0 z-0 transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] bg-primary" style={{transform:showEnrichModelDropdown?"scaleX(1)":"scaleX(0)",transformOrigin:showEnrichModelDropdown?"right":"left"}}/>
                  </button>
                  <div className={`fixed z-[100] mt-1 flex-col overflow-hidden rounded border border-primary/40 bg-popover shadow-md transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] max-h-64 overflow-y-auto ${showEnrichModelDropdown?"opacity-100 visible translate-y-0 pointer-events-auto":"opacity-0 invisible -translate-y-3 pointer-events-none"}`} style={{width:enrichModelPos.width,top:enrichModelPos.top,left:enrichModelPos.left}}>
                    {[{value:"",label:"DEFAULT"},...providers.map(p=>({value:p.id,label:`${p.name} / ${p.model}`}))].map(opt=>(<button key={opt.value} type="button" onClick={async()=>{setEnrichModel(opt.value);setShowEnrichModelDropdown(false);try{await updateConfig("enrichment",{enrichment_model:opt.value,max_parallel_context:enrichMaxParallel,batch_poll_interval:30});toast.success("Enrichment model updated")}catch{toast.error("Failed to update")}}} className="relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group" style={{background:"none",border:"none"}}><span className="relative z-10 flex items-center gap-2 px-3 py-2 w-full text-[10px]" style={{letterSpacing:"0.05em"}}>{enrichModel===opt.value||(!enrichModel&&opt.value==="")?<span className="w-1.5 h-1.5 bg-primary group-hover:bg-primary-foreground rotate-45 shrink-0 transition-colors duration-700"/>:<span className="w-1.5 h-1.5 shrink-0"/>}{opt.label}</span><span className="absolute inset-0 z-0 bg-primary transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right"/></button>))}
                  </div>
                </div>
              </div>
              <div className="space-y-2">
                <div className="flex items-center gap-3">
                  <label className="text-[11px] uppercase tracking-wider text-muted-foreground w-24">Parallel</label>
                  <input
                    type="text" inputMode="numeric"
                    value={enrichMaxParallel}
                    onChange={(e) => setEnrichMaxParallel(e.target.value)}
                    onBlur={() => {
                      const v = parseInt(enrichMaxParallel) || 50
                      setEnrichMaxParallel(String(Math.max(1, Math.min(100, v))))
                      updateConfig("enrichment", { max_parallel_context: v, batch_poll_interval: 30, enrichment_model: enrichModel }).catch(() => {})
                    }}
                    className="w-10 h-7 border-0 border-b border-primary/40 bg-transparent rounded-none px-0 text-xs text-center focus:border-primary"
                  />
                </div>
              </div>
            </div>

            {/* Agentic RAG */}
            <div className="pb-6">
              <h3 className="text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground mb-3">AGENTIC RAG DEFAULTS</h3>
              <p className="font-normal text-[11px] text-muted-foreground/80 leading-relaxed mb-4">Applies to decompose, rewrite loop, and aggregate.</p>
              <div className="flex items-center gap-4 mb-3">
                <button type="button" onClick={()=>{const m=ragSearchMode==="hybrid"?"dense":"hybrid";setRagSearchMode(m);_saveRag(m)}} className="group relative flex items-center justify-center overflow-hidden rounded t-sans-family transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)]" style={{fontSize:"10px",fontWeight:500,letterSpacing:"0.1em",textTransform:"uppercase",padding:"3px 8px",borderRadius:"2px",color:ragSearchMode==="hybrid"?"var(--color-primary-foreground)":"var(--color-muted-foreground)"}}><span className="relative z-10">{ragSearchMode==="hybrid"?"HYBRID":"DENSE"}</span><span className={`absolute inset-0 z-0 bg-primary transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${ragSearchMode==="hybrid"?"scale-x-100":"scale-x-0"}`} style={{transformOrigin:"left"}}/></button>
              </div>
              <div className="space-y-2">
                <div className="flex items-center gap-3">
                  <label className="text-[11px] uppercase tracking-wider text-muted-foreground w-24">Top K</label>
                  <input type="text" inputMode="numeric" value={ragTopK}
                    onChange={(e) => setRagTopK(e.target.value)}
                    onBlur={() => { const v = parseInt(ragTopK) || 20; setRagTopK(String(Math.max(1, Math.min(100, v)))); _saveRag() }}
                    className="w-10 h-7 border-0 border-b border-primary/40 bg-transparent rounded-none px-0 text-xs text-center focus:border-primary"
                  />
                </div>
                <div className="flex items-center gap-3">
                  <label className="text-[11px] uppercase tracking-wider text-muted-foreground w-24">Rerank Top K</label>
                  <input type="text" inputMode="numeric" value={ragRerankTopK}
                    onChange={(e) => setRagRerankTopK(e.target.value)}
                    onBlur={() => { const v = parseInt(ragRerankTopK) || 5; setRagRerankTopK(String(Math.max(1, Math.min(50, v)))); _saveRag() }}
                    className="w-10 h-7 border-0 border-b border-primary/40 bg-transparent rounded-none px-0 text-xs text-center focus:border-primary"
                  />
                </div>
                <div className="flex items-center gap-3">
                  <label className="text-[11px] uppercase tracking-wider text-muted-foreground w-24">Parallel</label>
                  <input type="text" inputMode="numeric" value={ragMaxParallel}
                    onChange={(e) => setRagMaxParallel(e.target.value)}
                    onBlur={() => { const v = parseInt(ragMaxParallel) || 10; setRagMaxParallel(String(Math.max(1, Math.min(32, v)))); _saveRag() }}
                    className="w-10 h-7 border-0 border-b border-primary/40 bg-transparent rounded-none px-0 text-xs text-center focus:border-primary"
                  />
                </div>
                <div className="flex items-center gap-3">
                  <label className="text-[11px] uppercase tracking-wider text-muted-foreground w-24">Iter</label>
                  <input type="text" inputMode="numeric" value={ragMaxIter}
                    onChange={(e) => setRagMaxIter(e.target.value)}
                    onBlur={() => { const v = parseInt(ragMaxIter) || 8; setRagMaxIter(String(Math.max(1, Math.min(20, v)))); _saveRag() }}
                    className="w-10 h-7 border-0 border-b border-primary/40 bg-transparent rounded-none px-0 text-xs text-center focus:border-primary"
                  />
                </div>
                {ragSearchMode === "dense" && (
                  <div className="flex items-center gap-3">
                    <label className="text-[11px] uppercase tracking-wider text-muted-foreground w-24">Min Score</label>
                    <input type="text" inputMode="numeric" value={ragMinScore}
                      onChange={(e) => setRagMinScore(e.target.value)}
                      onBlur={() => { const v = parseInt(ragMinScore) || 25; setRagMinScore(String(Math.max(0, Math.min(100, v)))); _saveRag() }}
                      className="w-10 h-7 border-0 border-b border-primary/40 bg-transparent rounded-none px-0 text-xs text-center focus:border-primary"
                    />
                  </div>
                )}
              </div>
            </div>

            {/* Direct RAG */}
            <div className="pb-6">
              <h3 className="text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground mb-3">DIRECT RAG DEFAULTS</h3>
              <p className="font-normal text-[11px] text-muted-foreground/80 leading-relaxed mb-4">Used when Agentic mode is disabled. Direct retrieval with optional rerank.</p>
              <div className="flex items-center gap-4 mb-3">
                <button type="button" onClick={()=>{const n=!dirRerankEnabled;setDirRerankEnabled(n);setDirTopK(n?"20":"10");updateConfig("direct_rag",{use_reranker:n}).catch(()=>{})}} className="group relative flex items-center justify-center overflow-hidden rounded t-sans-family transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)]" style={{fontSize:"10px",fontWeight:500,letterSpacing:"0.1em",textTransform:"uppercase",padding:"3px 8px",borderRadius:"2px",color:dirRerankEnabled?"var(--color-primary-foreground)":"var(--color-muted-foreground)"}}><span className="relative z-10">{dirRerankEnabled?"RERANK ON":"RERANK OFF"}</span><span className={`absolute inset-0 z-0 bg-primary transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${dirRerankEnabled?"scale-x-100":"scale-x-0"}`} style={{transformOrigin:"left"}}/></button>
                <button type="button" onClick={()=>{const m=dirSearchMode==="hybrid"?"dense":"hybrid";setDirSearchMode(m);_saveDir(m)}} className="group relative flex items-center justify-center overflow-hidden rounded t-sans-family transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)]" style={{fontSize:"10px",fontWeight:500,letterSpacing:"0.1em",textTransform:"uppercase",padding:"3px 8px",borderRadius:"2px",color:dirSearchMode==="hybrid"?"var(--color-primary-foreground)":"var(--color-muted-foreground)"}}><span className="relative z-10">{dirSearchMode==="hybrid"?"HYBRID":"DENSE"}</span><span className={`absolute inset-0 z-0 bg-primary transition-transform duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${dirSearchMode==="hybrid"?"scale-x-100":"scale-x-0"}`} style={{transformOrigin:"left"}}/></button>
              </div>
              <div className="space-y-2">
                <div className="flex items-center gap-3">
                  <label className="text-[11px] uppercase tracking-wider text-muted-foreground w-24">Top K</label>
                  <input type="text" inputMode="numeric" value={dirTopK}
                    onChange={(e) => setDirTopK(e.target.value)}
                    onBlur={() => { const v = parseInt(dirTopK) || (dirRerankEnabled ? 20 : 10); setDirTopK(String(Math.max(1, Math.min(100, v)))); _saveDir() }}
                    className="w-10 h-7 border-0 border-b border-primary/40 bg-transparent rounded-none px-0 text-xs text-center focus:border-primary"
                  />
                </div>
                {dirRerankEnabled && (
                  <div className="flex items-center gap-3">
                    <label className="text-[11px] uppercase tracking-wider text-muted-foreground w-24">Rerank Top K</label>
                    <input type="text" inputMode="numeric" value={dirRerankTopK}
                      onChange={(e) => setDirRerankTopK(e.target.value)}
                      onBlur={() => { const v = parseInt(dirRerankTopK) || 5; setDirRerankTopK(String(Math.max(1, Math.min(50, v)))); _saveDir() }}
                      className="w-10 h-7 border-0 border-b border-primary/40 bg-transparent rounded-none px-0 text-xs text-center focus:border-primary"
                    />
                  </div>
                )}
                {dirSearchMode === "dense" && (
                  <div className="flex items-center gap-3">
                    <label className="text-[11px] uppercase tracking-wider text-muted-foreground w-24">Min Score</label>
                    <input type="text" inputMode="numeric" value={dirMinScore}
                      onChange={(e) => setDirMinScore(e.target.value)}
                      onBlur={() => { const v = parseInt(dirMinScore) || 25; setDirMinScore(String(Math.max(0, Math.min(100, v)))); _saveDir() }}
                      className="w-10 h-7 border-0 border-b border-primary/40 bg-transparent rounded-none px-0 text-xs text-center focus:border-primary"
                    />
                  </div>
                )}
              </div>
            </div>

            {/* Local Model Settings */}
            <div className="pb-6">
              <h3 className="text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground mb-3">LOCAL MODEL SETTINGS</h3>
              <div className="flex items-center gap-3">
                <span className="text-sm font-light uppercase tracking-wider">Device</span>
                {(["cpu", "auto", "cuda", "mps"] as const).map((d) => (
                  <Button key={d} variant={localDevice === d ? "default" : "outline"} size="sm" className="font-light uppercase"
                    onClick={() => {
                      updateConfig("transcription", { local_device: d })
                        .then(() => { toast.success(`Device set to ${d}`); setLocalDevice(d) })
                        .catch(() => toast.error("Failed to update device"))
                    }}>
                    {d.toUpperCase()}
                  </Button>
                ))}
                <div className="flex-1" />
                <Button variant="default" onClick={() => setModelDownloadOpen(true)} className="font-light uppercase">
                  <Download className="h-4 w-4 mr-2" />Download Models
                </Button>
              </div>
            </div>

          </div>
          </div>
        </div>


        {/* ── Dialogs ── */}
        <AddProviderDialog open={dialogOpen} provider={editingProvider} onOpenChange={setDialogOpen} onSaved={handleSaved} />

        <SimpleProviderDialog
          open={embDialogOpen}
          provider={editingEmb}
          title="Embedding Provider"
          fields={embFields}
          defaults={{ provider: "openai_compatible", batch_size: "10", is_default: "false" }}
          onOpenChange={setEmbDialogOpen}
          onSaved={() => { setEmbDialogOpen(false); setEditingEmb(null); fetchEmbProviders() }}
          onCreate={(data) => createEmbeddingProvider(data as Partial<EmbeddingProvider>)}
          onUpdate={(id, data) => updateEmbeddingProvider(id, data as Partial<EmbeddingProvider>)}
          modelFetchSection="embedding"
        />

        <SimpleProviderDialog
          open={rerankDialogOpen}
          provider={editingRerank}
          title="Rerank Provider"
          fields={rerankFields}
          defaults={{ provider: "openai_compatible", is_default: "false" }}
          onOpenChange={setRerankDialogOpen}
          onSaved={() => { setRerankDialogOpen(false); setEditingRerank(null); fetchRerankProviders() }}
          onCreate={(data) => createRerankProvider(data as Partial<RerankProvider>)}
          onUpdate={(id, data) => updateRerankProvider(id, data as Partial<RerankProvider>)}
          modelFetchSection="rerank"
        />

        <SimpleProviderDialog
          open={fileTransDialogOpen}
          provider={editingFileTrans}
          title="File Transcription Provider"
          fields={fileTransFields}
          getTransFields={getFileTransFields}
          defaults={{ adapter: ftAdapterOpts[0]?.value ?? "", is_active: "false", device: "auto" }}
          onOpenChange={(open) => { setFileTransDialogOpen(open); if (!open) setFileTransLangHints([]) }}
          onSaved={() => { setFileTransDialogOpen(false); setEditingFileTrans(null); setFileTransLangHints([]); fetchFileTransProviders() }}
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
            const remove = (idx: number) => setFileTransLangHints((prev) => prev.filter((_, i) => i !== idx))
            const update = (idx: number, field: string, value: string) =>
              setFileTransLangHints((prev) => prev.map((item, i) => (i === idx ? { ...item, [field]: value } : item)))
            return (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">Language Hints Config</label>
                  <Button variant="ghost" size="sm" onClick={(e) => { e.preventDefault(); add() }}>
                    <Plus className="h-3 w-3 mr-1" />Add
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Configure which language codes this provider supports. These appear in the transcription language selector.
                </p>
                {fileTransLangHints.length === 0 && (
                  <p className="text-xs text-muted-foreground italic">No language hints configured. Provider default will be used.</p>
                )}
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
                    <Button variant="ghost" size="icon" onClick={(e) => { e.preventDefault(); remove(idx) }}>
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
          fields={rtTransFields}
          getTransFields={getRtTransFields}
          defaults={{ adapter: rtAdapterOpts[0]?.value ?? "", is_active: "false", device: "auto" }}
          onOpenChange={setRtTransDialogOpen}
          onSaved={() => { setRtTransDialogOpen(false); setEditingRtTrans(null); fetchRtTransProviders() }}
          onCreate={(data) => createRealtimeTranscriptionProvider(data as Partial<TranscriptionProvider>)}
          onUpdate={(id, data) => updateRealtimeTranscriptionProvider(id, data as Partial<TranscriptionProvider>)}
          checkboxField="is_active"
          checkboxLabel="Set as active"
          modelFetchSection="transcription"
        />
      </div>
      <ModelDownloadDialog
        open={modelDownloadOpen}
        onOpenChange={setModelDownloadOpen}
        onComplete={() => { fetchProviders(); fetchEmbProviders(); fetchRerankProviders(); refreshModelDownloaded(); startPolling() }}
      />
      <HotWordsManager
        open={hotWordsManagerOpen}
        onOpenChange={setHotWordsManagerOpen}
      />
      <OneShotDashscopeDialog
        open={oneshotDialogOpen}
        onOpenChange={setOneshotDialogOpen}
        onSaved={() => {
          fetchProviders()
          fetchEmbProviders()
          fetchRerankProviders()
          fetchFileTransProviders()
          fetchRtTransProviders()
          getConfig().then((c) => {
            if (c.visual_model_id && typeof c.visual_model_id === "string") setVisualModelId(c.visual_model_id)
            if (c.default_chat_model && typeof c.default_chat_model === "string") setChatModelId(c.default_chat_model)
          }).catch(() => {})
        }}
      />

      <OneShotOpenRouterDialog
        open={openrouterDialogOpen}
        onOpenChange={setOpenrouterDialogOpen}
        onSaved={() => {
          fetchProviders()
          fetchEmbProviders()
          fetchRerankProviders()
          fetchFileTransProviders()
          getConfig().then((c) => {
            if (c.visual_model_id && typeof c.visual_model_id === "string") setVisualModelId(c.visual_model_id)
            if (c.default_chat_model && typeof c.default_chat_model === "string") setChatModelId(c.default_chat_model)
          }).catch(() => {})
        }}
      />

      <Dialog open={meetingThinkingConfirmOpen} onOpenChange={setMeetingThinkingConfirmOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Turn off deep thinking for meetings?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Disabling thinking will significantly reduce the accuracy of Section breakdown and section allocation. Blueprint extraction and content routing may also degrade.
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setMeetingThinkingConfirmOpen(false)}>Keep thinking on</Button>
            <Button variant="destructive" onClick={async () => {
              setMeetingThinkingConfirmOpen(false)
              setMeetingThinking(false)
              try {
                await updateConfig("enrichment", { meeting_model: meetingModel, meeting_thinking: false })
              } catch {
                toast.error("Failed to update")
                setMeetingThinking(true)
              }
            }}>Turn off anyway</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
