import { useState, useEffect, useCallback } from "react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { FieldLabel } from "@/components/ui/field-label"
import { Eye, EyeOff, Loader2, RefreshCw, Star, Wrench } from "lucide-react"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { cn } from "@/lib/utils"
import { createLLMProvider, updateLLMProvider, getAvailableModels, updateConfig, type LLMProvider } from "@/api/client"
import { useProviderTypes } from "@/hooks/use-provider-types"
import { toast } from "sonner"

interface AddProviderDialogProps {
  open: boolean
  provider: LLMProvider | null
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}

const defaultForm = {
  name: "",
  provider: "openai_compatible",
  model: "",
  base_url: "",
  api_key: "",
  is_default: false,
  function_call_model_ids: [] as string[],
  selected_models: [] as string[],
  default_model: "",
  visual_model_ids: [] as string[],
}

export function AddProviderDialog({ open, provider, onOpenChange, onSaved }: AddProviderDialogProps) {
  const [form, setForm] = useState(defaultForm)
  const [showApiKey, setShowApiKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [fetchingModels, setFetchingModels] = useState(false)
  const [availableModels, setAvailableModels] = useState<string[]>([])

  const llmOptions = useProviderTypes().llm

  useEffect(() => {
    if (provider) {
      setForm({
        name: provider.name || "",
        provider: provider.provider || "openai_compatible",
        model: provider.model || "",
        base_url: provider.base_url || "",
        api_key: provider.api_key || "",
        is_default: provider.is_default,
        function_call_model_ids: (provider as { function_call_model_ids?: string[] }).function_call_model_ids || [],
        selected_models: provider.selected_models || (provider.model ? [provider.model] : []),
        default_model: provider.default_model || provider.model || "",
        visual_model_ids: (provider as { visual_model_ids?: string[] }).visual_model_ids || [],
      })
    } else {
      setForm(defaultForm)
    }
    setShowApiKey(false)
    setAvailableModels([])
  }, [provider, open])

  const set = (key: string, value: string | boolean | string[]) =>
    setForm((prev) => ({ ...prev, [key]: value }))

  const fetchModels = useCallback(async () => {
    if (!form.base_url.trim()) {
      toast.error("Enter a base URL first")
      return
    }
    setFetchingModels(true)
    try {
      const res = await getAvailableModels("llm", {
        base_url: form.base_url,
        api_key: form.api_key || undefined,
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
  }, [form.base_url, form.api_key])

  const toggleModelSelection = (model: string) => {
    setForm((prev) => {
      const selected = prev.selected_models.includes(model)
        ? prev.selected_models.filter((m) => m !== model)
        : [...prev.selected_models, model]
      let defaultModel = prev.default_model
      if (!selected.includes(defaultModel)) {
        defaultModel = selected[0] || ""
      }
      return { ...prev, selected_models: selected, default_model: defaultModel }
    })
  }

  const setDefaultModel = (model: string) => {
    setForm((prev) => ({ ...prev, default_model: model }))
  }

  const toggleVisualModel = (model: string) => {
    setForm((prev) => {
      const visual = prev.visual_model_ids.includes(model)
        ? prev.visual_model_ids.filter((m) => m !== model)
        : [...prev.visual_model_ids, model]
      return { ...prev, visual_model_ids: visual }
    })
  }

  const toggleFunctionCallModel = (model: string) => {
    setForm((prev) => {
      const fc = prev.function_call_model_ids.includes(model)
        ? prev.function_call_model_ids.filter((m) => m !== model)
        : [...prev.function_call_model_ids, model]
      return { ...prev, function_call_model_ids: fc }
    })
  }

  const handleSave = async () => {
    if (!form.name.trim()) {
      toast.error("Name is required")
      return
    }
    setSaving(true)
    try {
      const data = {
        name: form.name.trim(),
        provider: form.provider,
        model: form.default_model || form.selected_models[0],
        base_url: form.base_url,
        api_key: form.api_key || undefined,
        is_default: form.is_default,
        function_call_model_ids: form.function_call_model_ids,
        selected_models: form.selected_models,
        default_model: form.default_model || form.selected_models[0],
        visual_model_ids: form.visual_model_ids,
      }
      if (provider) {
        await updateLLMProvider(provider.id, data)
        toast.success("Provider updated")
      } else {
        await createLLMProvider(data)
        toast.success("Provider created")
      }
      if (form.is_default && form.function_call_model_ids.length > 0) {
        const chatModel = form.default_model || form.function_call_model_ids[0]
        await updateConfig("default_chat_model", { default_chat_model: chatModel })
      }
      onSaved()
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setSaving(false)
    }
  }

  const modelList = availableModels.length > 0 ? availableModels : form.selected_models

  const renderModelRow = (model: string) => {
    const selected = form.selected_models.includes(model)
    const isVisual = form.visual_model_ids.includes(model)
    const isFc = form.function_call_model_ids.includes(model)
    return (
      <div
        key={model}
        role="button"
        tabIndex={0}
        onClick={() => toggleModelSelection(model)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault()
            toggleModelSelection(model)
          }
        }}
        className={cn("pm-settings-model-row", selected && "is-selected")}
      >
        <span className="pm-settings-model-diamond" aria-hidden />
        <span className="pm-settings-model-id" title={model}>{model}</span>
        {selected && (
          <>
            <button
              type="button"
              className={cn(
                "pm-settings-model-tag",
                form.default_model === model && "is-on",
              )}
              onClick={(e) => {
                e.stopPropagation()
                setDefaultModel(model)
              }}
            >
              {form.default_model === model ? "Default" : "Set"}
            </button>
            <button
              type="button"
              className={cn("pm-settings-model-tag", isFc && "is-soft-on")}
              onClick={(e) => {
                e.stopPropagation()
                toggleFunctionCallModel(model)
              }}
              title={isFc ? "Function calling enabled" : "Enable function calling"}
            >
              <Wrench className="h-3 w-3" />
            </button>
            <button
              type="button"
              className={cn("pm-settings-model-tag", isVisual && "is-soft-on")}
              onClick={(e) => {
                e.stopPropagation()
                toggleVisualModel(model)
              }}
              title={isVisual ? "Visual enabled" : "Enable for visual"}
            >
              <Eye className="h-3 w-3" />
            </button>
          </>
        )}
      </div>
    )
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
          <DialogKicker>LLM</DialogKicker>
          <DialogTitle>{provider ? "Edit provider" : "Add provider"}</DialogTitle>
          <DialogDescription>
            Connect an OpenAI-compatible endpoint and pick models for chat, tools, and vision.
          </DialogDescription>
        </DialogHeader>

        <div className="pm-settings-dlg-scroll">
          <div className="pm-dialog-body pm-settings-dlg-body">
            {/* Connection */}
            <section className="pm-settings-dlg-card">
              <header className="pm-settings-row-between">
                <span className="pm-settings-dlg-card-kicker">Connection</span>
              </header>
              <div className="pm-settings-dlg-fields">
                <div className="pm-settings-dlg-field">
                  <FieldLabel>Name</FieldLabel>
                  <Input
                    value={form.name}
                    onChange={(e) => set("name", e.target.value)}
                    placeholder="My LLM"
                  />
                </div>
                <div className="pm-settings-dlg-field">
                  <FieldLabel>Provider type</FieldLabel>
                  <DropdownSelect
                    value={form.provider}
                    onChange={(v) => set("provider", v)}
                    options={llmOptions.map((p) => ({ value: p.name, label: p.display_name }))}
                  />
                </div>
                <div className="pm-settings-dlg-field">
                  <FieldLabel>Base URL</FieldLabel>
                  <Input
                    value={form.base_url}
                    onChange={(e) => set("base_url", e.target.value)}
                    placeholder="https://api.openai.com/v1"
                  />
                </div>
                <div className="pm-settings-dlg-field">
                  <FieldLabel>API key</FieldLabel>
                  <div className="pm-settings-dlg-secret">
                    <Input
                      type={showApiKey ? "text" : "password"}
                      value={form.api_key}
                      onChange={(e) => set("api_key", e.target.value)}
                      placeholder="sk-..."
                    />
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      className="pm-settings-dlg-secret-btn"
                      onClick={() => setShowApiKey(!showApiKey)}
                      title={showApiKey ? "Hide key" : "Show key"}
                    >
                      {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </Button>
                  </div>
                </div>
              </div>
              <div className="pm-settings-dlg-pref">
                <p className="pm-settings-dlg-pref-label">Prefer this provider for new chats</p>
                <button
                  type="button"
                  className={cn("pm-field-chip", form.is_default && "is-on")}
                  aria-pressed={form.is_default}
                  onClick={() => set("is_default", !form.is_default)}
                >
                  <Star className="h-3 w-3" strokeWidth={1.75} />
                  {form.is_default ? "Default" : "Set as default"}
                </button>
              </div>
            </section>

            {/* Models */}
            <section className="pm-settings-dlg-card">
              <header className="pm-settings-row-between">
                <div className="min-w-0">
                  <span className="pm-settings-dlg-card-kicker">Models</span>
                  <p className="pm-settings-dlg-card-hint mt-1">
                    Select models, mark default · tools · vision.
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="xs"
                  onClick={fetchModels}
                  disabled={fetchingModels || !form.base_url.trim()}
                >
                  {fetchingModels ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <RefreshCw className="h-3 w-3" />
                  )}
                  Fetch
                </Button>
              </header>

              {modelList.length > 0 ? (
                <div className="pm-settings-model-list">
                  {modelList.map(renderModelRow)}
                </div>
              ) : (
                !fetchingModels && (
                  <p className="pm-settings-dlg-card-hint">
                    Enter a base URL and fetch models, or select them after creation.
                  </p>
                )
              )}
            </section>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="default" onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : provider ? "Update" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
