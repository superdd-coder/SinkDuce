import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Pencil, Trash2, Plug, Star, Loader2 } from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import { deleteLLMProvider, testLLMProvider, setDefaultLLMProvider } from "@/api/client"
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
          <span className="text-sm font-light uppercase tracking-wider">{provider.name || "Unnamed"}</span>
          <div className={`h-2 w-2 rounded-full shrink-0 ${statusColor}`} />
        </div>
        {provider.is_default && (
          <Badge className="text-xs bg-emerald-50 text-emerald-700 hover:bg-emerald-50 dark:bg-emerald-950/30 dark:text-emerald-400 shrink-0">
            <Star className="h-3 w-3 mr-1" />
            Default
          </Badge>
        )}
      </div>

      {/* Row 2: Model */}
      <div className="mt-1 min-h-[1.25rem]">
        {provider.selected_models && provider.selected_models.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {provider.selected_models.map((m) => (
              <span
                key={m}
                className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
                  m === provider.default_model
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground"
                }`}
              >
                {m}
              </span>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">{provider.model || ""}</p>
        )}
      </div>

      {/* Row 3: URL */}
      <div className="min-h-[1rem]">
        <p className="text-xs text-muted-foreground truncate max-w-[200px]">
          {provider.base_url || ""}
        </p>
      </div>

      {/* Row 4: Buttons */}
      <div className="flex gap-2 mt-auto pt-3">
        <Button variant="outline" size="sm" onClick={handleTest} disabled={testing} className="font-light uppercase">
          {testing ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Plug className="h-3 w-3 mr-1" />}
          Test
        </Button>
        <Button variant="outline" size="sm" onClick={handleSetDefault} disabled={provider.is_default} className="font-light uppercase">
          <Star className="h-3 w-3 mr-1" />
          Default
        </Button>
        <Button variant="outline" size="sm" onClick={() => onEdit(provider)} className="font-light uppercase">
          <Pencil className="h-3 w-3 mr-1" />
          Edit
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={handleDelete}
          disabled={deleting}
          className="font-light uppercase hover:text-orange-600 dark:hover:text-orange-400"
        >
          <Trash2 className="h-3 w-3 mr-1" />
          Delete
        </Button>
      </div>
    </div>
  )
}
