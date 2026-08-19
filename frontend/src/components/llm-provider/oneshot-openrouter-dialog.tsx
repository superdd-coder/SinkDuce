import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogKicker, DialogTitle } from "@/components/ui/dialog"
import { FieldLabel } from "@/components/ui/field-label"
import { Loader2, Eye, EyeOff } from "lucide-react"
import {
  getLLMProviders, updateLLMProvider, createLLMProvider,
  getEmbeddingProviders, updateEmbeddingProvider, createEmbeddingProvider,
  getRerankProviders, updateRerankProvider, createRerankProvider,
  updateConfig,
} from "@/api/client"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { oneshotSlotSnapshot, type OneshotSlotSnapshot } from "./oneshot-slots"

interface OneShotOpenRouterDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: (slots?: OneshotSlotSnapshot) => void
}

const BASE_URL = "https://openrouter.ai/api/v1"
const DEFAULT_MODEL = "deepseek/deepseek-v4-flash-0731"
const CHAT_MODEL = "qwen/qwen3.7-plus"
const LIBRARY_MODEL = "qwen/qwen3.7-flash"
const MEETING_MODEL = "deepseek/deepseek-v4-pro-0813"
// qwen3.7-plus / flash: vision + tools. DeepSeek: tools only, no vision.
const OPENROUTER_VISION_AND_TOOLS = ["qwen/qwen3.7-plus", "qwen/qwen3.7-flash"]

function uniqueModels(names: string[]): string[] {
  return [...new Set(names.map((n) => n.trim()).filter(Boolean))]
}

function openrouterIsVisionAndTools(name: string): boolean {
  return OPENROUTER_VISION_AND_TOOLS.includes(name)
}

function openrouterIsToolsOnly(name: string): boolean {
  return name.toLowerCase().includes("deepseek")
}

function openrouterCapabilityTags(selected: string[], chatModel: string, imageModel: string) {
  const visual_model_ids = uniqueModels([
    ...selected.filter(openrouterIsVisionAndTools),
    openrouterIsToolsOnly(imageModel) ? "" : imageModel,
  ])
  const function_call_model_ids = uniqueModels([
    ...selected.filter((m) => openrouterIsVisionAndTools(m) || openrouterIsToolsOnly(m)),
    chatModel,
  ])
  return { visual_model_ids, function_call_model_ids }
}

export function OneShotOpenRouterDialog({ open, onOpenChange, onSaved }: OneShotOpenRouterDialogProps) {
  const [apiKey, setApiKey] = useState("")
  const [llmModel, setLlmModel] = useState(DEFAULT_MODEL)
  const [agenticModel, setAgenticModel] = useState(DEFAULT_MODEL)
  const [chatModel, setChatModel] = useState(CHAT_MODEL)
  const [visualModel, setVisualModel] = useState(LIBRARY_MODEL)
  const [libraryModel, setLibraryModel] = useState(LIBRARY_MODEL)
  const [distillModel, setDistillModel] = useState(LIBRARY_MODEL)
  const [meetingModel, setMeetingModel] = useState(MEETING_MODEL)
  const [embModel, setEmbModel] = useState("qwen/qwen3-embedding-4b")
  const [rerankerModel, setRerankerModel] = useState("cohere/rerank-v3.5")
  const [showApiKey, setShowApiKey] = useState(false)
  const [saving, setSaving] = useState(false)

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
      const defaultRef = llmModel.trim() || DEFAULT_MODEL
      const agenticRef = agenticModel.trim() || defaultRef
      const chatRef = chatModel.trim() || CHAT_MODEL
      const visualRef = visualModel.trim() || LIBRARY_MODEL
      const libraryRef = libraryModel.trim() || LIBRARY_MODEL
      const distillRef = distillModel.trim() || LIBRARY_MODEL
      const meetingRef = meetingModel.trim() || MEETING_MODEL
      const selected = uniqueModels([
        defaultRef,
        agenticRef,
        chatRef,
        visualRef,
        libraryRef,
        distillRef,
        meetingRef,
      ])
      const { visual_model_ids, function_call_model_ids } = openrouterCapabilityTags(
        selected,
        chatRef,
        visualRef,
      )
      const llmCreated = await createLLMProvider({ name: "OpenRouter", provider: "openai_compatible", model: defaultRef, base_url: BASE_URL, api_key: apiKey.trim(), is_default: true, selected_models: selected as any, default_model: defaultRef, visual_model_ids, function_call_model_ids } as any)
      await createEmbeddingProvider({ name: "OpenRouter", provider: "openai_compatible", model: embModel.trim(), base_url: BASE_URL, api_key: apiKey.trim(), dimensions: 1536, batch_size: 10, is_default: true } as any)
      await createRerankProvider({ name: "OpenRouter", provider: "openai_compatible", model: rerankerModel.trim(), base_url: BASE_URL, api_key: apiKey.trim(), is_default: true } as any)
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
      toast.success("OpenRouter configured")
      onSaved(slots)
      onOpenChange(false)
      setApiKey("")
    } catch (err) { toast.error(`Setup failed: ${err instanceof Error ? err.message : String(err)}`) }
    finally { setSaving(false) }
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
            One API key configures each slot: type the model name for every function.
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
                    <Input value={embModel} onChange={(e) => setEmbModel(e.target.value)} placeholder="qwen/qwen3-embedding-4b" />
                  </div>
                  <div className="pm-settings-dlg-field">
                    <FieldLabel>Reranker</FieldLabel>
                    <Input value={rerankerModel} onChange={(e) => setRerankerModel(e.target.value)} placeholder="cohere/rerank-v3.5" />
                  </div>
                  <p className="pm-settings-dlg-card-hint">
                    qwen3.7-plus / flash: vision + tools · DeepSeek: tools only
                    {" · "}
                    Base URL · {BASE_URL}
                  </p>
                </div>
              </section>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="default" onClick={handleSave} disabled={saving}>
            {saving ? <><Loader2 className="h-4 w-4 animate-spin" />Setting up…</> : "Apply"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )

}
