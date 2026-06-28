import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Pencil, Trash2, Plug, Star, Loader2 } from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import { deleteLLMProvider, testLLMProvider, setDefaultLLMProvider, updateConfig } from "@/api/client"
import type { LLMProvider } from "@/stores/app-store"
import { toast } from "sonner"

interface ProviderCardProps {
  provider: LLMProvider
  onEdit: (provider: LLMProvider) => void
  onRefresh: () => void
}

export function ProviderCard({ provider, onEdit, onRefresh }: ProviderCardProps) {
  const { setProviders } = useAppStore()
  const [testing, setTesting] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const statusColor = provider.status === "ready"
    ? "bg-emerald-500"
    : provider.status === "error"
      ? "bg-red-500"
      : "bg-muted-foreground/40"

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
        toast.success(res.message || "Default updated")
        // Auto-sync default_chat_model when provider has function_call models
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
    <div className="border border-border/50 rounded-lg p-4 flex flex-col h-full">
      {/* Row 1: Provider name + status + default badge */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <span className="text-[14px] font-[350] uppercase tracking-[0.08em] text-muted-foreground">{provider.name || "Unnamed"}</span>
          <div className={`h-2 w-2 rounded-full shrink-0 ${statusColor}`} />
        </div>
        {provider.is_default && (
          <Badge className="text-[10px] font-medium uppercase tracking-[0.1em] bg-emerald-50 text-emerald-700 hover:bg-emerald-50 dark:bg-emerald-950/30 dark:text-emerald-400 shrink-0">
            <Star className="h-3 w-3 mr-1" />
            DEFAULT
          </Badge>
        )}
      </div>

      {/* Row 2: Model */}
      <div className="mt-1 min-h-[1.25rem]">
        {provider.selected_models && provider.selected_models.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {provider.selected_models.map((m) => {
              const isVisual = provider.visual_model_ids?.includes(m)
              return (
                <span
                  key={m}
                  className={`text-[10px] px-1.5 py-0.5 rounded font-mono inline-flex items-center gap-0.5 ${
                    m === provider.default_model
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted text-muted-foreground"
                  }`}
                >
                  {isVisual && (
                    <span className="text-[8px] opacity-70" title="Visual enabled">👁</span>
                  )}
                  {m}
                </span>
              )
            })}
          </div>
        ) : (
          <p className="font-normal text-[12px] text-muted-foreground/80">{provider.model || ""}</p>
        )}
      </div>

      {/* Row 3: URL */}
      <div className="min-h-[1rem]">
        <p className="font-normal text-[11px] text-muted-foreground/80 truncate max-w-[200px]">
          {provider.base_url || ""}
        </p>
      </div>

      {/* Row 4: Buttons */}
      <div className="flex gap-2 mt-auto pt-3">
        <Button variant="outline" size="sm" onClick={handleTest} disabled={testing} className="font-medium uppercase tracking-[0.1em] text-[10px]">
          {testing ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Plug className="h-3 w-3 mr-1" />}
          Test
        </Button>
        <Button variant="outline" size="sm" onClick={handleSetDefault} disabled={provider.is_default} className="font-medium uppercase tracking-[0.1em] text-[10px]">
          <Star className="h-3 w-3 mr-1" />
          Default
        </Button>
        <Button variant="outline" size="sm" onClick={() => onEdit(provider)} className="font-medium uppercase tracking-[0.1em] text-[10px]">
          <Pencil className="h-3 w-3 mr-1" />
          Edit
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={handleDelete}
          disabled={deleting}
          className="font-medium uppercase tracking-[0.1em] text-[10px] hover:text-orange-600 dark:hover:text-orange-400"
        >
          <Trash2 className="h-3 w-3 mr-1" />
          Delete
        </Button>
      </div>
    </div>
  )
}
