import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Pencil, Trash2, Plug, Star, Loader2 } from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import { useShallow } from "zustand/react/shallow"
import { deleteLLMProvider, testLLMProvider, setDefaultLLMProvider, updateConfig } from "@/api/client"
import type { LLMProvider } from "@/stores/app-store"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"

interface ProviderCardProps {
  provider: LLMProvider
  onEdit: (provider: LLMProvider) => void
  onRefresh: () => void
}

export function ProviderCard({ provider, onEdit, onRefresh }: ProviderCardProps) {
  const t = useT()
  const { setProviders } = useAppStore(useShallow((s) => ({ setProviders: s.setProviders })))
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
      if (res.success) toast.success(t("settings.connectionOk", { name: provider.name }))
      else toast.error(t("settings.connectionFailed", { name: provider.name, error: res.error || t("errors.connection_failed") }))
    } catch {
      toast.error(t("settings.testFailed"))
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
        toast.success(
          res.message || t("settings.providerDeleted", { name: provider.name || t("common.unnamed") }),
        )
        onRefresh()
      }
    } catch {
      toast.error(t("settings.deleteFailed"))
    } finally {
      setDeleting(false)
    }
  }

  const handleSetDefault = async () => {
    try {
      const res = await setDefaultLLMProvider(provider.id)
      if (res.error) toast.error(res.error)
      else {
        toast.success(res.message || t("settings.providerSetDefault", { name: provider.name || t("common.unnamed") }))
        if ((provider.function_call_model_ids ?? []).length > 0) {
          const chatModel = provider.default_model || provider.function_call_model_ids![0]
          await updateConfig("default_chat_model", {
            default_chat_model: `${provider.id}|${chatModel}`,
          })
        }
        onRefresh()
      }
    } catch {
      toast.error(t("settings.failedSetDefault"))
    }
  }

  return (
    <div className="pm-settings-provider-card">
      <div className="pm-settings-provider-top">
        <div className="pm-settings-provider-name-row">
          <span className="pm-settings-provider-name">{provider.name || t("common.unnamed")}</span>
          <span className={cn("pm-settings-status-dot", statusClass)} aria-hidden />
        </div>
        {provider.is_default && (
          <Badge variant="default" className="shrink-0">
            {t("common.default")}
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
          {t("common.test")}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleSetDefault}
          disabled={provider.is_default}
        >
          <Star className="h-3 w-3" />
          {t("common.default")}
        </Button>
        <Button variant="secondary" size="sm" onClick={() => onEdit(provider)}>
          <Pencil className="h-3 w-3" />
          {t("common.edit")}
        </Button>
        <Button
          variant="destructive"
          size="sm"
          onClick={handleDelete}
          disabled={deleting}
        >
          <Trash2 className="h-3 w-3" />
          {t("common.delete")}
        </Button>
      </div>
    </div>
  )
}
