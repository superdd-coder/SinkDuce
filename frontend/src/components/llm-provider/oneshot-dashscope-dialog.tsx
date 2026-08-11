import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogKicker, DialogTitle } from "@/components/ui/dialog"
import { FieldLabel } from "@/components/ui/field-label"
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
import { cn } from "@/lib/utils"

interface OneShotDashscopeDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}

const DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
// File ASR is selectable in Settings (fun-asr | qwen-audio-3.0-asr-flash-filetrans).
// OneShot defaults to fun-asr (precompiled hot words + stable batch path).
const FILE_TRANS_MODEL = "fun-asr"
const RT_TRANS_MODEL = "qwen-audio-3.0-asr-flash-streaming"

export function OneShotDashscopeDialog({ open, onOpenChange, onSaved }: OneShotDashscopeDialogProps) {
  const [apiKey, setApiKey] = useState("")
  const [llmModel, setLlmModel] = useState("deepseek-v4-flash")
  const [chatModel, setChatModel] = useState("deepseek-v4-flash")
  const [visualModel, setVisualModel] = useState("qwen3.5-flash")
  const [embModel, setEmbModel] = useState("text-embedding-v4")
  const [rerankerModel, setRerankerModel] = useState("qwen3-rerank")
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
          model: FILE_TRANS_MODEL,
          api_key: apiKey.trim(),
          is_active: true,
        }),
        createRealtimeTranscriptionProvider({
          name: "Dashscope",
          adapter: "dashscope_funasr_realtime",
          model: RT_TRANS_MODEL,
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
      <DialogContent
        className={cn(
          "pm-dialog pm-dialog--silk pm-settings-dlg",
          "sm:max-w-md",
          "!animate-none data-open:!animate-none data-closed:!animate-none",
        )}
        overlayClassName="pm-dialog-overlay--silk"
      >
        <DialogHeader>
          <DialogKicker>Settings</DialogKicker>
          <DialogTitle>OneShot Dashscope</DialogTitle>
          <DialogDescription>
            One API key configures LLM, embedding, rerank, and transcription defaults.
          </DialogDescription>
        </DialogHeader>

        <div className="pm-settings-dlg-scroll">
          <div className="pm-dialog-body pm-settings-dlg-body">
            <section className="pm-settings-dlg-card">
              <span className="pm-settings-dlg-card-kicker">API key</span>
              <div className="pm-settings-dlg-field">
                <FieldLabel>Dashscope key</FieldLabel>
                <div className="pm-settings-dlg-secret">
                  <Input
                    type={showApiKey ? "text" : "password"}
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="sk-..."
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
            </section>

            <section className="pm-settings-dlg-card">
              <span className="pm-settings-dlg-card-kicker">Models</span>
              <div className="pm-settings-dlg-fields">
                <div className="pm-settings-dlg-field">
                  <FieldLabel>LLM</FieldLabel>
                  <Input value={llmModel} onChange={(e) => setLlmModel(e.target.value)} placeholder="deepseek-v4-flash" />
                </div>
                <div className="pm-settings-dlg-field">
                  <FieldLabel>Chat (tools)</FieldLabel>
                  <Input value={chatModel} onChange={(e) => setChatModel(e.target.value)} placeholder="deepseek-v4-flash" />
                  <p className="pm-settings-dlg-card-hint mt-1.5">Must support function calling.</p>
                </div>
                <div className="pm-settings-dlg-field">
                  <FieldLabel>Visual</FieldLabel>
                  <Input value={visualModel} onChange={(e) => setVisualModel(e.target.value)} placeholder="qwen3.5-flash" />
                </div>
                <div className="pm-settings-dlg-grid">
                  <div className="pm-settings-dlg-field">
                    <FieldLabel>Embedding</FieldLabel>
                    <Input value={embModel} onChange={(e) => setEmbModel(e.target.value)} placeholder="text-embedding-v4" />
                  </div>
                  <div className="pm-settings-dlg-field">
                    <FieldLabel>Reranker</FieldLabel>
                    <Input value={rerankerModel} onChange={(e) => setRerankerModel(e.target.value)} placeholder="qwen3-rerank" />
                  </div>
                </div>
                <p className="pm-settings-dlg-card-hint">Base URL · {DASHSCOPE_BASE_URL}</p>
              </div>
            </section>

            <section className="pm-settings-dlg-card">
              <span className="pm-settings-dlg-card-kicker">Transcription</span>
              <div className="pm-settings-dlg-callout">
                <span className="pm-label">Fixed models</span>
                <p className="pm-meta">File · <span className="font-mono">{FILE_TRANS_MODEL}</span></p>
                <p className="pm-meta">
                  Realtime · <span className="font-mono">{RT_TRANS_MODEL}</span>
                  {" · "}hot words · semantic punctuation
                </p>
              </div>
            </section>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="default" onClick={handleSave} disabled={saving}>
            {saving ? <><Loader2 className="h-4 w-4 animate-spin" />Setting up…</> : "Apply all"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )

}
