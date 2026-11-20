import { useEffect, useId, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { getCollectionConfig, getCollectionEmbeddingOptions, rebuildCollectionEmbedding, type CollectionEmbeddingOption } from "@/api/client"
import { formatApiError } from "@/api/http"
import { useT } from "@/i18n/use-t"
import { useAppStore } from "@/stores/app-store"
import { toast } from "sonner"

export function CollectionEmbeddingSettings({ collection, canEdit, onDimensions, onOpenIngestHistory }: {
  collection: string
  canEdit: boolean
  onDimensions: (dimensions: string) => void
  onOpenIngestHistory: () => void
}) {
  const t = useT()
  const [options, setOptions] = useState<CollectionEmbeddingOption[]>([])
  const [currentId, setCurrentId] = useState("")
  const [model, setModel] = useState("")
  const [pendingId, setPendingId] = useState("")
  const [state, setState] = useState("")
  const [error, setError] = useState("")
  const [confirm, setConfirm] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const selectId = useId()
  const cancelButton = useRef<HTMLButtonElement>(null)
  const disconnected = useAppStore(s => s.collections.find(c => c.id === collection)?.embedding_disconnected)
  const busy = state === "queued" || state === "building"

  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      try {
        const [cfg, choices] = await Promise.all([getCollectionConfig(collection), getCollectionEmbeddingOptions()])
        if (!alive) return
        const current = String(cfg.embedding_provider_id || "")
        const rebuild = cfg.embedding_rebuild as {state?: string; error?: string} | undefined
        setModel(String(cfg.embedding_model || ""))
        setCurrentId(current)
        setOptions(choices)
        setState(rebuild?.state || "idle")
        setError(rebuild?.error || "")
        if (cfg.dimensions) onDimensions(String(cfg.dimensions))
        if (rebuild?.state === "queued" || rebuild?.state === "building") {
          timer = setTimeout(() => void refresh(), 2000)
        }
      } catch (cause) {
        if (alive) {
          setError(formatApiError(cause, t))
          if (busy) timer = setTimeout(() => void refresh(), 3000)
        }
      }
    }
    void refresh()
    return () => { alive = false; clearTimeout(timer) }
  }, [collection, disconnected, busy, onDimensions, t])

  const target = options.find(option => option.id === pendingId)
  const canChange = canEdit && !busy && !!target && target.id !== currentId
  const targetLabel = target?.name && target.model && target.name !== target.model
    ? `${target.name} · ${target.model}`
    : target?.model || target?.name || ""

  function chooseModel(id: string) {
    if (id === currentId) return
    setPendingId(id)
    setConfirm(true)
  }

  async function confirmChange() {
    if (!confirm || !canChange || submitting) return
    setSubmitting(true)
    try {
      await rebuildCollectionEmbedding(collection, target.id)
      setError("")
      setConfirm(false)
      setState("queued")
      onOpenIngestHistory()
    } catch (cause) {
      toast.error(formatApiError(cause, t))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="pm-config-card">
      <header className="pm-config-card-head">
        <span className="pm-config-card-kicker">{t("library.embeddingModel")}</span>
      </header>
      <div className="grid min-w-0 gap-2">
        <p className="pm-meta">{t("library.boundEmbedding", { model: model || "—" })}</p>
        {!currentId && state && <p className="pm-meta">{t("library.embeddingDisconnected")}</p>}
        <DropdownSelect
          id={selectId}
          aria-label={t("library.embeddingModel")}
          size="sm"
          disabled={!canEdit || busy || !options.length}
          value={currentId}
          onChange={chooseModel}
          placeholder={model || t("library.noEmbedding")}
          options={options.map(option => ({
            value: option.id,
            label: option.name || option.model,
            detail: option.name && option.name !== option.model ? option.model : undefined,
          }))}
        />
        {busy && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="pm-meta" role="status">{t("library.rebuildingEmbedding")}</span>
            <Button type="button" variant="secondary" size="xs" onClick={onOpenIngestHistory}>
              {t("library.rebuildProgress")}
            </Button>
          </div>
        )}
        {error && <p className="pm-meta" role="alert">{error}</p>}
      </div>
      <Dialog
        open={confirm && canChange}
        onOpenChange={open => { if (!open && !submitting) setConfirm(false) }}
        onOpenChangeComplete={open => { if (!open) setPendingId("") }}
      >
        <DialogContent
          className="pm-dialog max-w-md"
          initialFocus={cancelButton}
          finalFocus={() => document.getElementById(selectId)}
          showCloseButton={!submitting}
        >
          <DialogHeader>
            <DialogTitle>{t("library.changeEmbeddingTitle")}</DialogTitle>
            <DialogDescription>{t("library.rebuildEmbeddingConfirm", { model: targetLabel })}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button ref={cancelButton} type="button" variant="secondary" disabled={submitting} onClick={() => setConfirm(false)}>
              {t("common.cancel")}
            </Button>
            <Button type="button" disabled={submitting || !canChange} onClick={confirmChange}>
              {t("library.changeEmbedding")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}
