import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog"
import { Loader2, Eye, EyeOff } from "lucide-react"
import {
  getLLMProviders, updateLLMProvider,
  getEmbeddingProviders, updateEmbeddingProvider, createEmbeddingProvider,
  getRerankProviders, updateRerankProvider, createRerankProvider,
  getFileTranscriptionProviders, updateFileTranscriptionProvider, createFileTranscriptionProvider,
  getRealtimeTranscriptionProviders, updateRealtimeTranscriptionProvider, createRealtimeTranscriptionProvider,
  createLLMProvider,
  updateConfig,
} from "@/api/client"
import { toast } from "sonner"

interface OneShotDashscopeDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}

const DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

export function OneShotDashscopeDialog({ open, onOpenChange, onSaved }: OneShotDashscopeDialogProps) {
  const [apiKey, setApiKey] = useState("")
  const [llmModel, setLlmModel] = useState("deepseek-v4-flash")
  const [chatModel, setChatModel] = useState("deepseek-v4-flash")
  const [visualModel, setVisualModel] = useState("qwen3.5-flash")
  const [embModel, setEmbModel] = useState("text-embedding-v4")
  const [rerankerModel, setRerankerModel] = useState("qwen3-rerank")
  const [fileTransModel, setFileTransModel] = useState("fun-asr")
  const [rtTransModel, setRtTransModel] = useState("fun-asr-realtime")
  const [showApiKey, setShowApiKey] = useState(false)
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    if (!apiKey.trim()) {
      toast.error("API Key is required")
      return
    }
    setSaving(true)
    try {
      // Clear existing defaults/actives before creating new ones
      const [llmList, embList, rerankList, fileTransList, rtTransList] = await Promise.all([
        getLLMProviders(),
        getEmbeddingProviders(),
        getRerankProviders(),
        getFileTranscriptionProviders(),
        getRealtimeTranscriptionProviders(),
      ])

      // Unset defaults one by one (avoid concurrent async_reload_services races)
      for (const p of llmList.filter((p) => p.is_default)) {
        await updateLLMProvider(p.id, { ...p, is_default: false })
      }
      for (const p of embList.filter((p) => p.is_default)) {
        await updateEmbeddingProvider(p.id, { ...p, is_default: false })
      }
      for (const p of rerankList.filter((p) => p.is_default)) {
        await updateRerankProvider(p.id, { ...p, is_default: false })
      }
      for (const p of fileTransList.filter((p) => p.is_active)) {
        await updateFileTranscriptionProvider(p.id, { ...p, is_active: false })
      }
      for (const p of rtTransList.filter((p) => p.is_active)) {
        await updateRealtimeTranscriptionProvider(p.id, { ...p, is_active: false })
      }

      // Build selected_models: all models (deduplicate)
      const selectedModels = [...new Set([llmModel.trim(), chatModel.trim(), visualModel.trim()].filter(Boolean))]
      const visualModelIds = visualModel.trim() ? [visualModel.trim()] : []

      // Create new providers with default/active set
      await Promise.all([
        createLLMProvider({
          name: "Dashscope",
          provider: "openai_compatible",
          model: llmModel.trim(),
          base_url: DASHSCOPE_BASE_URL,
          api_key: apiKey.trim(),
          is_default: true,
          selected_models: selectedModels,
          default_model: llmModel.trim(),
          visual_model_ids: visualModelIds,
          function_call_model_ids: [chatModel.trim()],
        }),
        createEmbeddingProvider({
          name: "Dashscope",
          provider: "openai_compatible",
          model: embModel.trim(),
          base_url: DASHSCOPE_BASE_URL,
          api_key: apiKey.trim(),
          dimensions: 1536,
          batch_size: 10,
          is_default: true,
        }),
        createRerankProvider({
          name: "Dashscope",
          provider: "qwen",
          model: rerankerModel.trim(),
          api_key: apiKey.trim(),
          is_default: true,
        }),
        createFileTranscriptionProvider({
          name: "Dashscope",
          adapter: "dashscope_funasr",
          model: fileTransModel.trim(),
          api_key: apiKey.trim(),
          is_active: true,
        }),
        createRealtimeTranscriptionProvider({
          name: "Dashscope",
          adapter: "dashscope_funasr_realtime",
          model: rtTransModel.trim(),
          api_key: apiKey.trim(),
          is_active: true,
        }),
      ])
      // Set global model configs
      await updateConfig("default_chat_model", { default_chat_model: chatModel.trim() })
      if (visualModel.trim()) {
        await updateConfig("visual_model_id", { visual_model_id: visualModel.trim() })
      }
      toast.success("All Dashscope providers created")
      onSaved()
      onOpenChange(false)
      // Reset form
      setApiKey("")
    } catch (err) {
      toast.error(`Setup failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto overflow-x-hidden">
        <DialogHeader>
          <DialogTitle>OneShot Setting with Dashscope API</DialogTitle>
          <DialogDescription>
            Enter your Dashscope API Key to configure all providers at once. Model names are prefilled with defaults.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-2 min-w-0">
          {/* API Key */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Dashscope API Key</label>
            <div className="relative">
              <Input
                type={showApiKey ? "text" : "password"}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-..."
              />
              <Button
                variant="ghost"
                size="icon"
                className="absolute right-0 top-0 h-full px-3"
                onClick={() => setShowApiKey(!showApiKey)}
              >
                {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </Button>
            </div>
          </div>

          {/* LLM Model */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">LLM Model</label>
            <Input value={llmModel} onChange={(e) => setLlmModel(e.target.value)} placeholder="deepseek-v4-flash" />
            <p className="text-xs text-muted-foreground">Base URL: {DASHSCOPE_BASE_URL}</p>
          </div>

          {/* Chat Model */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Chat Model</label>
            <Input value={chatModel} onChange={(e) => setChatModel(e.target.value)} placeholder="deepseek-v4-flash" />
            <p className="text-xs text-muted-foreground">Model used for Chat function calling. Must support Functions.</p>
          </div>

          {/* Visual Model */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Visual Model</label>
            <Input
              value={visualModel}
              onChange={(e) => setVisualModel(e.target.value)}
              placeholder="qwen3.5-flash"
            />
            <p className="text-xs text-muted-foreground">
              Vision-capable model for image description. Will be checked as Visual in LLM settings.
            </p>
          </div>

          {/* Embedding Model */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Embedding Model</label>
            <Input
              value={embModel}
              onChange={(e) => setEmbModel(e.target.value)}
              placeholder="text-embedding-v4"
            />
            <p className="text-xs text-muted-foreground">
              Base URL: {DASHSCOPE_BASE_URL}
            </p>
          </div>

          {/* Reranker Model */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Reranker Model</label>
            <Input
              value={rerankerModel}
              onChange={(e) => setRerankerModel(e.target.value)}
              placeholder="qwen3-rerank"
            />
            <p className="text-xs text-muted-foreground">
              Provider: Qwen (DashScope)
            </p>
          </div>

          {/* File Transcription Model */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">File Transcription Model</label>
            <Input
              value={fileTransModel}
              onChange={(e) => setFileTransModel(e.target.value)}
              placeholder="fun-asr"
            />
            <p className="text-xs text-muted-foreground">
              Adapter: DashScope FunASR (file)
            </p>
          </div>

          {/* Realtime Transcription Model */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Realtime Transcription Model</label>
            <Input
              value={rtTransModel}
              onChange={(e) => setRtTransModel(e.target.value)}
              placeholder="fun-asr-realtime"
            />
            <p className="text-xs text-muted-foreground">
              Adapter: DashScope FunASR (realtime)
            </p>
          </div>
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? <><Loader2 className="h-4 w-4 mr-2 animate-spin" />Setting up...</> : "Apply All"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
