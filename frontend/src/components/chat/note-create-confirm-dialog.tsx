import { useEffect, useLayoutEffect, useMemo, useState, useSyncExternalStore, type CSSProperties } from "react"
import { createPortal } from "react-dom"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { ArrowRight, FileText, FolderOpen } from "lucide-react"
import { useT } from "@/i18n/use-t"
import { confirmNoteCreate } from "@/api/client"
import { DropdownSelect, type DropdownSelectOption } from "@/components/ui/dropdown-select"
import { useAppStore } from "@/stores/app-store"
import {
  getWebSearchConfirmAnchor,
  getWebSearchConfirmAnchorSnapshot,
} from "@/lib/web-search-confirm"
import {
  answerNoteCreateConfirm,
  getNoteCreateConfirmState,
  shouldShowNoteCreateConfirm,
  subscribeNoteCreateConfirm,
  subscribeWebSearchConfirmAnchor,
} from "@/lib/note-create-confirm"

async function resolveNoteCreate(
  approved: boolean,
  confirmId: string,
  overrideCollection?: string,
) {
  // Wake the backend wait() first so the SSE generator can resume even if
  // the stream reader is still blocked on promptNoteCreateConfirm.
  if (confirmId) {
    try {
      await confirmNoteCreate(confirmId, approved, overrideCollection)
    } catch {
      // Stream handler also POSTs; 404 if this call already resolved it.
    }
  }
  answerNoteCreateConfirm(approved, overrideCollection)
}

function ConfirmCard({
  title,
  collectionId,
  collectionName,
  preview,
  confirmId,
}: {
  title: string
  collectionId: string
  collectionName: string
  preview: string
  confirmId: string
}) {
  const t = useT()
  const collections = useAppStore((s) => s.collections)
  const fetchCollections = useAppStore((s) => s.fetchCollections)

  // Picker starts on the agent's pick; the card is keyed by confirmId so a
  // new card remounts fresh. Refresh the directory so the dropdown lists
  // collections created after page load.
  const [selectedId, setSelectedId] = useState(collectionId)
  useEffect(() => {
    void fetchCollections()
  }, [fetchCollections])

  const options: DropdownSelectOption[] = useMemo(() => {
    const opts = collections.map((c) => ({ value: c.id, label: c.name }))
    // Pin the agent's pick so the pill never blanks while the directory
    // loads (or if the collection was renamed server-side).
    if (
      collectionName &&
      !opts.some((o) => o.value === selectedId)
    ) {
      opts.unshift({ value: selectedId || collectionName, label: collectionName })
    }
    return opts
  }, [collections, collectionName, selectedId])

  const allow = () =>
    void resolveNoteCreate(
      true,
      confirmId,
      selectedId && selectedId !== collectionId ? selectedId : undefined,
    )

  return (
    <div className="pm-web-confirm pointer-events-auto w-full" role="group">
      <div className="pm-web-confirm-head">
        <div className="pm-web-confirm-icon" aria-hidden>
          <FileText className="h-3.5 w-3.5" strokeWidth={1.75} />
        </div>
        <div className="pm-web-confirm-copy min-w-0 flex-1">
          <div className="pm-web-confirm-title">{t("chat.noteCreateTitle")}</div>
          <p className="pm-web-confirm-desc">{t("chat.noteCreateDesc")}</p>
        </div>
      </div>
      {collectionName ? (
        <div className="pm-web-confirm-target-row" title={title}>
          <span className="pm-web-confirm-target-title">{title || "—"}</span>
          <ArrowRight className="pm-web-confirm-target-arrow" aria-hidden />
          {options.length > 0 ? (
            <DropdownSelect
              size="tag"
              chevron
              menuZIndex={520}
              className="pm-web-confirm-target-select"
              leading={
                <FolderOpen className="h-3 w-3 shrink-0" strokeWidth={1.75} aria-hidden />
              }
              value={selectedId}
              onChange={setSelectedId}
              options={options}
            />
          ) : (
            <span className="pm-web-confirm-target-pill">
              <FolderOpen className="h-3 w-3" strokeWidth={1.75} aria-hidden />
              <span className="pm-web-confirm-target-name">{collectionName}</span>
            </span>
          )}
        </div>
      ) : (
        <p className="pm-web-confirm-query" title={title}>
          {title || "—"}
        </p>
      )}
      {preview ? (
        <div
          className="pm-web-confirm-note-preview prose prose-sm dark:prose-invert max-w-none prose-p:my-1 prose-pre:my-2 prose-ul:my-1 prose-ol:my-1 prose-li:my-0"
          title={preview}
        >
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{preview}</ReactMarkdown>
        </div>
      ) : null}
      <div className="pm-web-confirm-actions">
        <button
          type="button"
          className="pm-web-confirm-btn pm-web-confirm-btn--ghost"
          onClick={() => void resolveNoteCreate(false, confirmId)}
        >
          {t("common.decline")}
        </button>
        <button
          type="button"
          className="pm-web-confirm-btn pm-web-confirm-btn--pri"
          onClick={allow}
        >
          {t("common.allow")}
        </button>
      </div>
    </div>
  )
}

function isUsableInlineHost(el: HTMLElement | null): el is HTMLElement {
  if (!el || !el.isConnected) return false
  if (el.dataset.webConfirmHost !== "inline") return false
  const r = el.getBoundingClientRect()
  // Hidden / zero-width Quick panel must not swallow the dialog
  return r.width >= 120 && r.height >= 0 && r.bottom > 0 && r.top < window.innerHeight
}

/**
 * Note-create HITL — same shell/placement as web-search confirm.
 * Switching Chat / opening Quick Chat hides the card; the stream stays paused
 * until the user comes back and answers (or the 120s timeout declines).
 */
export function NoteCreateConfirmDialog() {
  const t = useT()
  const state = useSyncExternalStore(
    subscribeNoteCreateConfirm,
    getNoteCreateConfirmState,
    getNoteCreateConfirmState,
  )
  useSyncExternalStore(
    subscribeWebSearchConfirmAnchor,
    getWebSearchConfirmAnchorSnapshot,
    getWebSearchConfirmAnchorSnapshot,
  )

  const [box, setBox] = useState<{ left: number; width: number; bottom: number } | null>(null)

  const measure = () => {
    const el = getWebSearchConfirmAnchor()
    if (!el || !el.isConnected || el.dataset.webConfirmHost === "inline") {
      // Inline hosts render into the node; fixed path uses fallback or chat composer
      if (el && el.dataset.webConfirmHost !== "inline") {
        const r = el.getBoundingClientRect()
        if (r.width >= 80) {
          setBox({
            left: r.left,
            width: r.width,
            bottom: Math.max(12, window.innerHeight - r.top + 10),
          })
          return
        }
      }
      setBox(null)
      return
    }
    const r = el.getBoundingClientRect()
    setBox({
      left: r.left,
      width: r.width,
      bottom: Math.max(12, window.innerHeight - r.top + 10),
    })
  }

  useLayoutEffect(() => {
    if (!state.open) return
    measure()
  }, [state.open, state.confirmId])

  useEffect(() => {
    if (!state.open) return
    measure()
    const onWin = () => measure()
    window.addEventListener("resize", onWin)
    window.addEventListener("scroll", onWin, true)
    return () => {
      window.removeEventListener("resize", onWin)
      window.removeEventListener("scroll", onWin, true)
    }
  }, [state.open, state.confirmId])

  if (!state.open || !shouldShowNoteCreateConfirm()) return null
  if (typeof document === "undefined") return null

  const host = getWebSearchConfirmAnchor()

  // Quick Chat: only when the panel slot is actually visible
  if (isUsableInlineHost(host)) {
    return createPortal(
      <div role="dialog" aria-label={t("chat.confirmNoteCreate")} className="w-full px-0 pb-1">
        <ConfirmCard
          key={state.confirmId}
          title={state.title}
          collectionId={state.collectionId}
          collectionName={state.collectionName}
          preview={state.preview}
          confirmId={state.confirmId}
        />
      </div>,
      host,
    )
}

  // Main Chat / fallback: fixed on body — always on screen
  const style: CSSProperties = box
    ? {
        position: "fixed",
        left: box.left,
        width: box.width,
        bottom: box.bottom,
        zIndex: 500,
      }
    : {
        position: "fixed",
        left: "50%",
        transform: "translateX(-50%)",
        width: "min(36rem, calc(100vw - 1.5rem))",
        bottom: "6.5rem",
        zIndex: 500,
      }

  return createPortal(
    <div
      role="dialog"
      aria-label={t("chat.confirmNoteCreate")}
      className="pointer-events-none"
      style={style}
    >
      <ConfirmCard
        key={state.confirmId}
        title={state.title}
        collectionId={state.collectionId}
        collectionName={state.collectionName}
        preview={state.preview}
        confirmId={state.confirmId}
      />
    </div>,
    document.body,
  )
}
