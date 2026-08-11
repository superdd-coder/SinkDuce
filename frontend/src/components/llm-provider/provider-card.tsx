import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Pencil, Trash2, Plug, Star, Loader2 } from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import { deleteLLMProvider, testLLMProvider, setDefaultLLMProvider, updateConfig } from "@/api/client"
import type { LLMProvider } from "@/stores/app-store"
import { toast } from "sonner"
import { cn } from "@/lib/utils"

interface ProviderCardProps {
  provider: LLMProvider
  onEdit: (provider: LLMProvider) => void
  onRefresh: () => void
}

export function ProviderCard({ provider, onEdit, onRefresh }: ProviderCardProps) {
  const { setProviders } = useAppStore()
  const [testing, setTesting] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const statusClass =
    provider.status === "ready"
      ? "is-ready"
      : provider.status === "error"
        ? "is-error"
        : ""

  const handleTest = async () => {
    setTesting(true)
    try {
      const res = await testLLMProvider(provider.id)
      const newStatus = res.success ? "ready" : "error"
      setProviders((prev) =>
        prev.map((p) => (p.id === provider.id ? { ...p, status: newStatus } : p))
      )
      if (res.success) toast.success(`${provider.name}: connection OK`)
      else toast.error(`${provider.name}: ${res.error || "connection failed"}`)
    } catch {
      toast.error("Test failed")
    } finally {
      setTesting(false)
    }
  }

  const handleDelete = async () => {
    setDeleting(true)
    try {
      const res = await deleteLLMProvider(provider.id)
      if (res.error) toast.error(res.error)
      else {
        toast.success(res.message || "Provider deleted")
        onRefresh()
      }
    } catch {
      toast.error("Delete failed")
    } finally {
      setDeleting(false)
    }
  }

  const handleSetDefault = async () => {
    try {
      const res = await setDefaultLLMProvider(provider.id)
      if (res.error) toast.error(res.error)
      else {
        toast.success(res.message || `Provider '${provider.name || "Unnamed"}' set as default`)
        if ((provider.function_call_model_ids ?? []).length > 0) {
          const chatModel = provider.default_model || provider.function_call_model_ids![0]
          await updateConfig("default_chat_model", { default_chat_model: chatModel })
        }
        onRefresh()
      }
    } catch {
      toast.error("Failed to set default")
    }
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

      <div className="min-h-[1.25rem]">
        {provider.selected_models && provider.selected_models.length > 0 ? (
          <div className="pm-settings-model-chips">
            {provider.selected_models.map((m) => {
              const isVisual = provider.visual_model_ids?.includes(m)
              const isDefault = m === provider.default_model
              return (
                <span
                  key={m}
                  className={cn(
                    "pm-settings-model-chip",
                    isDefault && "is-default",
                    isVisual && "is-visual",
                  )}
                  title={m}
                >
                  <span className="pm-settings-model-chip-dot" aria-hidden />
                  {m}
                </span>
              )
            })}
          </div>
        ) : (
          <p className="pm-settings-provider-meta">{provider.model || ""}</p>
        )}
      </div>

      <p className="pm-settings-provider-meta" title={provider.base_url || undefined}>
        {provider.base_url || ""}
      </p>

      <div className="pm-settings-provider-actions">
        <Button variant="ghost" size="sm" onClick={handleTest} disabled={testing}>
          {testing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plug className="h-3 w-3" />}
          Test
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleSetDefault}
          disabled={provider.is_default}
        >
          <Star className="h-3 w-3" />
          Default
        </Button>
        <Button variant="secondary" size="sm" onClick={() => onEdit(provider)}>
          <Pencil className="h-3 w-3" />
          Edit
        </Button>
        <Button
          variant="destructive"
          size="sm"
          onClick={handleDelete}
          disabled={deleting}
        >
          <Trash2 className="h-3 w-3" />
          Delete
        </Button>
      </div>
    </div>
  )
}
