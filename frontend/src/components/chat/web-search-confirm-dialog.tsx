import { useEffect, useLayoutEffect, useState, useSyncExternalStore, type CSSProperties } from "react"
import { createPortal } from "react-dom"
import { Globe } from "lucide-react"
import { useT } from "@/i18n/use-t"
import {
  getWebSearchConfirmState,
  subscribeWebSearchConfirm,
  answerWebSearchConfirm,
  getWebSearchConfirmAnchor,
  getWebSearchConfirmAnchorSnapshot,
  subscribeWebSearchConfirmAnchor,
  shouldShowWebSearchConfirm,
} from "@/lib/web-search-confirm"

function ConfirmCard({ query }: { query: string }) {
  const t = useT()
  return (
    <div className="pm-web-confirm pointer-events-auto w-full" role="group">
      <div className="pm-web-confirm-head">
        <div className="pm-web-confirm-icon" aria-hidden>
          <Globe className="h-3.5 w-3.5" strokeWidth={1.75} />
        </div>
        <div className="pm-web-confirm-copy min-w-0 flex-1">
          <div className="pm-web-confirm-title">{t("chat.webSearchTitle")}</div>
          <p className="pm-web-confirm-desc">
            {t("chat.webSearchDesc", {
              external: t("chat.webSearchExternal"),
              turn: t("chat.webSearchThisTurn"),
            })}
          </p>
        </div>
      </div>
      <p className="pm-web-confirm-query" title={query}>
        {query || "—"}
      </p>
      <div className="pm-web-confirm-actions">
        <button
          type="button"
          className="pm-web-confirm-btn pm-web-confirm-btn--ghost"
          onClick={() => answerWebSearchConfirm(false)}
        >
          {t("common.decline")}
        </button>
        <button
          type="button"
          className="pm-web-confirm-btn pm-web-confirm-btn--soft"
          onClick={() => answerWebSearchConfirm(true, { always: true })}
          title={t("chat.alwaysAllowTitle")}
        >
          {t("chat.alwaysAllow")}
        </button>
        <button
          type="button"
          className="pm-web-confirm-btn pm-web-confirm-btn--pri"
          onClick={() => answerWebSearchConfirm(true)}
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
 * Web-search HITL — only on the session that asked.
 * Switching Chat / opening Quick Chat hides the card; the stream stays paused
 * until the user comes back and answers (or the 120s timeout declines).
 */
export function WebSearchConfirmDialog() {
  const t = useT()
  const state = useSyncExternalStore(
    subscribeWebSearchConfirm,
    getWebSearchConfirmState,
    getWebSearchConfirmState,
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

  if (!state.open || !shouldShowWebSearchConfirm()) return null
  if (typeof document === "undefined") return null

  const host = getWebSearchConfirmAnchor()

  // Quick Chat: only when the panel slot is actually visible
  if (isUsableInlineHost(host)) {
    return createPortal(
      <div role="dialog" aria-label={t("chat.confirmWebSearch")} className="w-full px-0 pb-1">
        <ConfirmCard query={state.query} />
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
    // Not a full-screen modal — only the card captures clicks (pointer-events-none
    // on the wrapper). aria-modal would trap focus and make the rest of the app
    // feel "unclickable" while waiting for Allow/Decline.
    <div
      role="dialog"
      aria-label={t("chat.confirmWebSearch")}
      className="pointer-events-none"
      style={style}
    >
      <ConfirmCard query={state.query} />
    </div>,
    document.body,
  )
}
