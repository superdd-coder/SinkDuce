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
import { oneshotSlotSnapshot, type OneshotSlotSnapshot } from "./oneshot-slots"

interface OneShotDashscopeDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: (slots?: OneshotSlotSnapshot) => void
}

const DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
// File / realtime ASR are selectable in Settings.
// OneShot defaults to Fun-ASR (precompiled hot words + the same Recognition API).
const FILE_TRANS_MODEL = "fun-asr"
const RT_TRANS_MODEL = "fun-asr-realtime"
const DEFAULT_MODEL = "deepseek-v4-flash-0731"
const CHAT_MODEL = "qwen3.7-plus"
const LIBRARY_MODEL = "qwen3.7-flash"
const MEETING_MODEL = "deepseek-v4-pro-0813"
// qwen3.7-plus / qwen3.7-flash: vision + tools. DeepSeek: tools only, no vision.
const DASHSCOPE_VISION_AND_TOOLS = ["qwen3.7-plus", "qwen3.7-flash"]

function uniqueModels(names: string[]): string[] {
  return [...new Set(names.map((n) => n.trim()).filter(Boolean))]
}

function dashscopeIsVisionAndTools(name: string): boolean {
  return DASHSCOPE_VISION_AND_TOOLS.includes(name)
}

function dashscopeIsToolsOnly(name: string): boolean {
  return name.toLowerCase().includes("deepseek")
}

function dashscopeCapabilityTags(selected: string[], chatModel: string, imageModel: string) {
  const visual_model_ids = uniqueModels([
    ...selected.filter(dashscopeIsVisionAndTools),
    dashscopeIsToolsOnly(imageModel) ? "" : imageModel,
  ])
  const function_call_model_ids = uniqueModels([
    ...selected.filter((m) => dashscopeIsVisionAndTools(m) || dashscopeIsToolsOnly(m)),
    chatModel,
  ])
  return { visual_model_ids, function_call_model_ids }
}

export function OneShotDashscopeDialog({ open, onOpenChange, onSaved }: OneShotDashscopeDialogProps) {
  const [apiKey, setApiKey] = useState("")
  const [llmModel, setLlmModel] = useState(DEFAULT_MODEL)
  const [agenticModel, setAgenticModel] = useState(DEFAULT_MODEL)
  const [chatModel, setChatModel] = useState(CHAT_MODEL)
  const [visualModel, setVisualModel] = useState(LIBRARY_MODEL)
  const [libraryModel, setLibraryModel] = useState(LIBRARY_MODEL)
  const [distillModel, setDistillModel] = useState(LIBRARY_MODEL)
  const [meetingModel, setMeetingModel] = useState(MEETING_MODEL)
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
      const existingFile = fileTransList.find((p) => p.adapter === "dashscope_funasr")
      const existingRt = rtTransList.find((p) => p.adapter === "dashscope_funasr_realtime")
      for (const p of fileTransList.filter((p) => p.is_active && p.id !== existingFile?.id)) {
        await updateFileTranscriptionProvider(p.id, { ...p, is_active: false })
      }
      for (const p of rtTransList.filter((p) => p.is_active && p.id !== existingRt?.id)) {
        await updateRealtimeTranscriptionProvider(p.id, { ...p, is_active: false })
      }

      const defaultRef = llmModel.trim() || DEFAULT_MODEL
      const agenticRef = agenticModel.trim() || defaultRef
      const chatRef = chatModel.trim() || CHAT_MODEL
      const visualRef = visualModel.trim() || LIBRARY_MODEL
      const libraryRef = libraryModel.trim() || LIBRARY_MODEL
      const distillRef = distillModel.trim() || LIBRARY_MODEL
      const meetingRef = meetingModel.trim() || MEETING_MODEL
      const selectedModels = uniqueModels([
        defaultRef,
        agenticRef,
        chatRef,
        visualRef,
        libraryRef,
        distillRef,
        meetingRef,
      ])
      const { visual_model_ids, function_call_model_ids } = dashscopeCapabilityTags(
        selectedModels,
        chatRef,
        visualRef,
      )

      // Create new providers with default/active set
      const [llmCreated] = await Promise.all([
        createLLMProvider({
          name: "Dashscope",
          provider: "openai_compatible",
          model: defaultRef,
          base_url: DASHSCOPE_BASE_URL,
          api_key: apiKey.trim(),
          is_default: true,
          selected_models: selectedModels,
          default_model: defaultRef,
          visual_model_ids,
          function_call_model_ids,
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
        existingFile
          ? updateFileTranscriptionProvider(existingFile.id, {
              ...existingFile,
              model: FILE_TRANS_MODEL,
              api_key: apiKey.trim(),
              is_active: true,
            })
          : createFileTranscriptionProvider({
              name: "Dashscope",
              adapter: "dashscope_funasr",
              model: FILE_TRANS_MODEL,
              api_key: apiKey.trim(),
              is_active: true,
            }),
        existingRt
          ? updateRealtimeTranscriptionProvider(existingRt.id, {
              ...existingRt,
              model: RT_TRANS_MODEL,
              api_key: apiKey.trim(),
              is_active: true,
            })
          : createRealtimeTranscriptionProvider({
              name: "Dashscope",
              adapter: "dashscope_funasr_realtime",
              model: RT_TRANS_MODEL,
              api_key: apiKey.trim(),
              is_active: true,
            }),
      ])
      const slots = oneshotSlotSnapshot(llmCreated?.id, {
        chat: chatRef,
        visual: visualRef,
        library: libraryRef,
        meeting: meetingRef,
        agentic: agenticRef,
        distill: distillRef,
      })
      await updateConfig("default_chat_model", { default_chat_model: slots.default_chat_model })
      if (slots.visual_model_id) {
        await updateConfig("visual_model_id", { visual_model_id: slots.visual_model_id })
      }
      await updateConfig("enrichment", {
        meeting_model: slots.meeting_model,
        enrichment_model: slots.enrichment_model,
        agentic_query_model: slots.agentic_query_model,
        note_distill_model: slots.note_distill_model,
      })
      toast.success("All Dashscope providers created")
      onSaved(slots)
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
          "sm:max-w-lg",
          "!animate-none data-open:!animate-none data-closed:!animate-none",
        )}
        overlayClassName="pm-dialog-overlay--silk"
      >
        <DialogHeader>
          <DialogKicker>Settings</DialogKicker>
          <DialogTitle>OneShot Dashscope</DialogTitle>
          <DialogDescription>
            One API key configures each slot: type the model name for every function.
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
                  <FieldLabel>Default</FieldLabel>
                  <Input value={llmModel} onChange={(e) => setLlmModel(e.target.value)} placeholder={DEFAULT_MODEL} />
                </div>
                <div className="pm-settings-dlg-field">
                  <FieldLabel>Agentic query</FieldLabel>
                  <Input value={agenticModel} onChange={(e) => setAgenticModel(e.target.value)} placeholder={DEFAULT_MODEL} />
                </div>
                <div className="pm-settings-dlg-field">
                  <FieldLabel>Chat</FieldLabel>
                  <Input value={chatModel} onChange={(e) => setChatModel(e.target.value)} placeholder={CHAT_MODEL} />
                </div>
                <div className="pm-settings-dlg-field">
                  <FieldLabel>Image description</FieldLabel>
                  <Input value={visualModel} onChange={(e) => setVisualModel(e.target.value)} placeholder={LIBRARY_MODEL} />
                </div>
                <div className="pm-settings-dlg-field">
                  <FieldLabel>Library LLM</FieldLabel>
                  <Input value={libraryModel} onChange={(e) => setLibraryModel(e.target.value)} placeholder={LIBRARY_MODEL} />
                </div>
                <div className="pm-settings-dlg-field">
                  <FieldLabel>Note distill</FieldLabel>
                  <Input value={distillModel} onChange={(e) => setDistillModel(e.target.value)} placeholder={LIBRARY_MODEL} />
                </div>
                <div className="pm-settings-dlg-field">
                  <FieldLabel>Meeting summary</FieldLabel>
                  <Input value={meetingModel} onChange={(e) => setMeetingModel(e.target.value)} placeholder={MEETING_MODEL} />
                </div>
                <div className="pm-settings-dlg-field">
                  <FieldLabel>Embedding</FieldLabel>
                  <Input value={embModel} onChange={(e) => setEmbModel(e.target.value)} placeholder="text-embedding-v4" />
                </div>
                <div className="pm-settings-dlg-field">
                  <FieldLabel>Reranker</FieldLabel>
                  <Input value={rerankerModel} onChange={(e) => setRerankerModel(e.target.value)} placeholder="qwen3-rerank" />
                </div>
                <p className="pm-settings-dlg-card-hint">
                  qwen3.7-plus / flash: vision + tools · DeepSeek: tools only
                  {" · "}
                  Base URL · {DASHSCOPE_BASE_URL}
                </p>
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
