import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Combobox } from "@/components/ui/combobox"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Plus, Star, Pencil, Trash2, Plug, Loader2, Eye, EyeOff, Zap, Download, RefreshCw } from "lucide-react"
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
          <span className="text-sm font-light uppercase tracking-wider">{provider.name || "Unnamed"}</span>
          <div className={`h-2 w-2 rounded-full shrink-0 ${statusColor}`} />
        </div>
        {provider.is_default && (
          <Badge className="text-xs bg-emerald-50 text-emerald-700 hover:bg-emerald-50 dark:bg-emerald-950/30 dark:text-emerald-400 shrink-0"><Star className="h-3 w-3 mr-1" />Default</Badge>
        )}
      </div>

      {/* Row 2: Model */}
      <div className="mt-1 min-h-[1.25rem]">
        <p className="text-sm text-muted-foreground">{provider.model || ""}</p>
        {subtitle && <p className="text-[10px] text-muted-foreground/70">{subtitle}</p>}
      </div>

      {/* Row 3: URL */}
      <div className="min-h-[1rem]">
        <p className="text-xs text-muted-foreground truncate max-w-[200px]">{provider.base_url || ""}</p>
      </div>

      {/* Row 4: Buttons */}
      <div className="flex gap-2 mt-auto pt-3">
        <Button variant="outline" size="sm" onClick={handleTest} disabled={testing} className="font-light uppercase">
          {testing ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Plug className="h-3 w-3 mr-1" />}
          Test
        </Button>
        <Button variant="outline" size="sm" onClick={handleSetDefault} disabled={provider.is_default} className="font-light uppercase">
          <Star className="h-3 w-3 mr-1" />Default
        </Button>
        <Button variant="outline" size="sm" onClick={() => onEdit(provider)} className="font-light uppercase">
          <Pencil className="h-3 w-3 mr-1" />Edit
        </Button>
        <Button variant="outline" size="sm" onClick={handleDelete} className="font-light uppercase hover:text-orange-600 dark:hover:text-orange-400">
          <Trash2 className="h-3 w-3 mr-1" />Delete
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
              {f.key === "model" && modelFetchSection ? (
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
          <span className="text-sm font-light uppercase tracking-wider">{provider.name || "Unnamed"}</span>
          <div className={`h-2 w-2 rounded-full shrink-0 ${statusColor}`} />
        </div>
        {provider.is_active && (
          <Badge className="text-xs bg-emerald-50 text-emerald-700 hover:bg-emerald-50 dark:bg-emerald-950/30 dark:text-emerald-400 shrink-0"><Star className="h-3 w-3 mr-1" />Default</Badge>
        )}
      </div>

      {/* Row 2: Model */}
      <div className="mt-1 min-h-[1.25rem]">
        <p className="text-sm text-muted-foreground">{modelLabel}</p>
      </div>

      {/* Row 3: URL (reserved for uniform height) */}
      <div className="min-h-[1rem]" />

      {/* Row 4: Buttons */}
      <div className="flex gap-2 mt-auto pt-3">
        <Button variant="outline" size="sm" onClick={handleSetActive} disabled={provider.is_active} className="font-light uppercase">
          <Star className="h-3 w-3 mr-1" />Default
        </Button>
        <Button variant="outline" size="sm" onClick={handleTest} disabled={testing} className="font-light uppercase">
          {testing ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Zap className="h-3 w-3 mr-1" />}
          Test
        </Button>
        <Button variant="outline" size="sm" onClick={() => onEdit(provider)} className="font-light uppercase">
          <Pencil className="h-3 w-3 mr-1" />Edit
        </Button>
        <Button variant="outline" size="sm" onClick={handleDelete} className="font-light uppercase hover:text-orange-600 dark:hover:text-orange-400">
          <Trash2 className="h-3 w-3 mr-1" />Delete
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

  // Language hints config editor state for file transcription openai_compatible adapter
  const [fileTransLangHints, setFileTransLangHints] = useState<LanguageHintOption[]>([])

  // Local model device
  const [localDevice, setLocalDevice] = useState<string>("cpu")

  // MinerU cloud parsing settings
  const [mineruEnabled, setMineruEnabled] = useState(false)
  const [mineruToken, setMineruToken] = useState("")
  const [mineruModel, setMineruModel] = useState("pipeline")
  const [mineruOcr, setMineruOcr] = useState(false)
  const [mineruFormula, setMineruFormula] = useState(true)
  const [mineruTable, setMineruTable] = useState(true)
  const [mineruLanguage, setMineruLanguage] = useState("ch")
  const [showMineruToken, setShowMineruToken] = useState(false)
  const [savingMineru, setSavingMineru] = useState(false)

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
      setLocalDevice(typeof c.transcription?.local_device === "string" ? c.transcription.local_device : "cpu")
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
            <p className="text-lg font-light tracking-tight uppercase">Quick Setup</p>
            <p className="text-xs text-muted-foreground">Configure all providers with a single Dashscope API Key</p>
          </div>
          <Button variant="outline" onClick={() => setOneshotDialogOpen(true)} className="font-light uppercase">
            <Zap className="h-4 w-4 mr-2" />OneShot Dashscope
          </Button>
        </div>

        {/* ── LLM Providers ── */}
        <section className="border-b border-border pb-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-lg font-light tracking-tight uppercase">LLM Settings</h2>
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
        </section>

        {/* ── Embedding Providers ── */}
        <section className="border-b border-border pb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-light tracking-tight uppercase">Embedding Models</h2>
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
            <h2 className="text-lg font-light tracking-tight uppercase">Rerank Models</h2>
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
          <h2 className="text-lg font-light tracking-tight uppercase mb-4">Transcription</h2>

          {/* File Transcription */}
          <div className="mb-6">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-light tracking-tight uppercase">File Transcription</h3>
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
                  onToggleLoad={async () => { await toggleModelLoad(builtinFileTrans.id); fetchFileTransProviders(); startPolling() }}
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
              <h3 className="text-sm font-light tracking-tight uppercase">Realtime Transcription</h3>
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
                  onToggleLoad={async () => { await toggleModelLoad(builtinRtTrans.id); fetchRtTransProviders(); startPolling() }}
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

          {/* Local Transcription Model Settings */}
          <div>
            <h3 className="text-sm font-light tracking-tight uppercase mb-3">Local Model Settings</h3>
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
        </section>

        {/* ── Hot Words Management ── */}
        <section className="border-b border-border pb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-light tracking-tight uppercase">Hot Words</h2>
            <Button variant="default" onClick={() => setHotWordsManagerOpen(true)} className="font-light uppercase">
              Manage
            </Button>
          </div>
          <p className="text-sm text-muted-foreground">
            Manage hot word libraries to improve transcription accuracy for domain-specific terms like names, acronyms, and jargon.
          </p>
        </section>

        {/* ── MinerU CLOUD PARSING ── */}
        <section className="pb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-light tracking-tight">MinerU CLOUD PARSING</h2>
          </div>
          <div className="space-y-4">
              <p className="text-sm text-muted-foreground mb-4">
                Use MinerU's cloud API for high-quality document parsing with better table, formula, and layout preservation.
                Get your API token at{" "}
                <a href="https://mineru.net/apiManage/token" target="_blank" rel="noopener noreferrer" className="text-primary underline">mineru.net/apiManage/token</a>.
                When enabled, activate per-collection in Collection Settings.
              </p>

              <div className="flex items-center justify-between">
                <div>
                  <span className="text-sm font-light tracking-wider">ENABLE MinerU</span>
                  <p className="text-xs text-muted-foreground">Toggle cloud parsing globally</p>
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
                      <label className="text-sm font-light uppercase tracking-wider">API Token</label>
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
                  <div className="space-y-2">
                    <label className="text-sm font-light uppercase tracking-wider">Model Version</label>
                    <select
                      value={mineruModel}
                      onChange={(e) => setMineruModel(e.target.value)}
                      disabled={!mineruEnabled}
                      className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm"
                    >
                      <option value="pipeline">Pipeline (Default)</option>
                      <option value="vlm">VLM (Recommended)</option>
                      <option value="MinerU-HTML">MinerU HTML</option>
                    </select>
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-light uppercase tracking-wider">Language</label>
                    <select
                      value={mineruLanguage}
                      onChange={(e) => setMineruLanguage(e.target.value)}
                      disabled={!mineruEnabled}
                      className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm"
                    >
                      <option value="ch">Chinese + English + Traditional Chinese</option>
                      <option value="ch_server">Chinese + Japanese (server)</option>
                      <option value="en">English</option>
                      <option value="japan">Japanese</option>
                      <option value="korean">Korean</option>
                      <option value="chinese_cht">Traditional Chinese</option>
                      <option value="ta">Tamil</option>
                      <option value="te">Telugu</option>
                      <option value="ka">Kannada</option>
                      <option value="el">Greek</option>
                      <option value="th">Thai</option>
                      <option value="latin">Latin (40+ languages)</option>
                      <option value="arabic">Arabic</option>
                      <option value="cyrillic">Cyrillic (30+ languages)</option>
                      <option value="east_slavic">East Slavic (Russian/Ukrainian/Belarusian)</option>
                      <option value="devanagari">Devanagari (Hindi/Marathi/Nepali)</option>
                    </select>
                  </div>
                </div>

                <div className="space-y-3">
                  <label className="text-sm font-light uppercase tracking-wider">Parsing Options</label>
                  <div className="space-y-2">
                    <label className={`flex items-start gap-2 text-sm ${!mineruEnabled ? "cursor-default" : "cursor-pointer"}`}>
                      <input type="checkbox" checked={mineruOcr} onChange={(e) => setMineruOcr(e.target.checked)} disabled={!mineruEnabled} className="rounded mt-0.5" />
                      <div>
                        <span className="font-light uppercase tracking-wider">Force OCR</span>
                        <p className="text-xs text-muted-foreground">Force OCR on all pages. When off, MinerU auto-detects whether pages need OCR (scanned/image pages will still be OCR'd automatically).</p>
                      </div>
                    </label>
                    <label className={`flex items-start gap-2 text-sm ${!mineruEnabled ? "cursor-default" : "cursor-pointer"}`}>
                      <input type="checkbox" checked={mineruFormula} onChange={(e) => setMineruFormula(e.target.checked)} disabled={!mineruEnabled} className="rounded mt-0.5" />
                      <div>
                        <span className="font-light uppercase tracking-wider">Formula Recognition</span>
                        <p className="text-xs text-muted-foreground">Recognize mathematical formulas and convert to LaTeX. Recommended for academic/technical documents.</p>
                      </div>
                    </label>
                    <label className={`flex items-start gap-2 text-sm ${!mineruEnabled ? "cursor-default" : "cursor-pointer"}`}>
                      <input type="checkbox" checked={mineruTable} onChange={(e) => setMineruTable(e.target.checked)} disabled={!mineruEnabled} className="rounded mt-0.5" />
                      <div>
                        <span className="font-light uppercase tracking-wider">Table Recognition</span>
                        <p className="text-xs text-muted-foreground">Detect and extract tables as structured Markdown. Recommended for documents with tabular data.</p>
                      </div>
                    </label>
                  </div>
                </div>
                  </div>
                </div>
              </div>

              <div className="flex justify-end pt-4">
                <Button
                  disabled={savingMineru}
                  className="font-light uppercase"
                  onClick={async () => {
                    setSavingMineru(true)
                    try {
                      await updateConfig("mineru", {
                        enabled: mineruEnabled,
                        api_token: mineruToken,
                        base_url: "https://mineru.net/api/v4",
                        model_version: mineruModel,
                        is_ocr: mineruOcr,
                        enable_formula: mineruFormula,
                        enable_table: mineruTable,
                        language: mineruLanguage,
                      })
                      toast.success("MinerU settings saved")
                    } catch {
                      toast.error("Failed to save MinerU settings")
                    } finally {
                      setSavingMineru(false)
                    }
                  }}
                >
                  {savingMineru ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : null}
                  Save Settings
                </Button>
              </div>
          </div>
        </section>

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
        }}
      />
    </div>
  )
}
