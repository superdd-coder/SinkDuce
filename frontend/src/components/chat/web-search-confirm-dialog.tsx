import { useEffect, useLayoutEffect, useState, useSyncExternalStore, type CSSProperties } from "react"
import { createPortal } from "react-dom"
import { Globe } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  getWebSearchConfirmState,
  subscribeWebSearchConfirm,
  answerWebSearchConfirm,
  getWebSearchConfirmAnchor,
  subscribeWebSearchConfirmAnchor,
} from "@/lib/web-search-confirm"

function ConfirmCard({ query }: { query: string }) {
  return (
    <div className="pointer-events-auto w-full rounded-lg border border-amber-500/40 bg-background shadow-xl px-3.5 py-3 space-y-2.5">
      <div className="flex items-start gap-2.5 min-w-0">
        <div className="mt-0.5 rounded-md bg-amber-500/15 p-1.5 text-amber-600 dark:text-amber-400 shrink-0">
          <Globe className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-[12px] font-semibold text-foreground leading-snug">
            Search the public internet?
          </div>
          <p className="mt-0.5 text-[11px] text-muted-foreground leading-relaxed">
            Results are{" "}
            <span className="font-medium text-amber-700 dark:text-amber-400">external WEB data</span>
            {" — "}not from your private knowledge base. Decision applies to{" "}
            <span className="font-medium">this turn</span>.
          </p>
          <p
            className="mt-1.5 text-[12px] text-amber-900 dark:text-amber-200 bg-amber-500/10 border border-amber-500/25 rounded px-2 py-1.5 break-words"
            title={query}
          >
            {query || "—"}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-end gap-1.5">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-8 text-[11px] px-2.5"
          onClick={() => answerWebSearchConfirm(false)}
        >
          Decline
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8 text-[11px] px-2.5 border-amber-500/40 text-amber-800 dark:text-amber-300"
          onClick={() => answerWebSearchConfirm(true, { always: true })}
          title="Allow web search for this chat session until the tab is closed"
        >
          Always allow this session
        </Button>
        <Button
          type="button"
          size="sm"
          className="h-8 text-[11px] px-3 bg-amber-600 hover:bg-amber-700 text-white"
          onClick={() => answerWebSearchConfirm(true)}
        >
          Allow
        </Button>
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
 * Web-search HITL — always visible when pending.
 * Prefer inline Quick Chat slot when that host is on-screen; otherwise fixed
 * on document.body above the composer (never silent-wait with no UI).
 */
export function WebSearchConfirmDialog() {
  const state = useSyncExternalStore(
    subscribeWebSearchConfirm,
    getWebSearchConfirmState,
    getWebSearchConfirmState,
  )
  // Re-render when anchor changes
  useSyncExternalStore(
    subscribeWebSearchConfirmAnchor,
    () => getWebSearchConfirmAnchor(),
    () => null,
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

  if (!state.open) return null
  if (typeof document === "undefined") return null

  const host = getWebSearchConfirmAnchor()

  // Quick Chat: only when the panel slot is actually visible
  if (isUsableInlineHost(host)) {
    return createPortal(
      <div role="dialog" aria-label="Confirm web search" className="w-full px-0 pb-1">
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
      aria-label="Confirm web search"
      className="pointer-events-none"
      style={style}
    >
      <ConfirmCard query={state.query} />
    </div>,
    document.body,
  )
}
