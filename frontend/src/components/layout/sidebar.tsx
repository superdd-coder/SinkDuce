import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { useShallow } from "zustand/react/shallow"
import { cn } from "@/lib/utils"
import { useAppStore, type SidebarView } from "@/stores/app-store"
import { X, ArrowUpRight } from "lucide-react"
import { useUpdateCheck } from "@/hooks/use-update-check"
import { useT } from "@/i18n/use-t"
import { UpdateDialog } from "./update-dialog"

/**
 * Premium shell nav:
 * Slim rail · system label type · one shared diamond plate that
 * slides + quarter-turns when switching views.
 */

type PlateState = {
  /** Center Y of active item relative to list (px) */
  y: number
  /** Accumulated rotation — +90° each switch so diamond “rolls” to next */
  rot: number
  ready: boolean
}

/** Float update card fade duration — keep in sync with --pm-update-card-ms in CSS */
const UPDATE_CARD_MS = 280

export function Sidebar() {
  const t = useT()
  // RBAC（化妆）：按角色能力隐藏入口。
  // - chat / database / recall / settings ← modules 开关
  // - meeting ← **会议 ACL 的 read 档**（三域没有 modules 开关）
  // 真正的墙是服务端（各域路由的 require_domain + 库级可见性）；这里只是别让
  // 用户点进一个必然失败的地方。
  const identity = useAppStore((s) => s.identity)
  const mod = (k: string) => identity?.modules?.[k] !== false
  const ALL_NAV: Array<{ view: SidebarView; label: string }> = [
    { view: "chat", label: t("nav.chat") },
    { view: "meeting", label: t("nav.meeting") },
    { view: "database", label: t("nav.library") },
    { view: "recall", label: t("nav.recall") },
    { view: "llm_provider", label: t("nav.settings") },
  ]
  const navItems: Array<{ view: SidebarView; label: string }> = ALL_NAV.filter((it) => {
    if (!identity) return true // 画像未到：先全显示（首帧不闪），到货后收窄
    if (it.view === "meeting") return identity.domains?.meeting !== false
    const key =
      it.view === "llm_provider" ? "settings" : (it.view as string)
    return mod(key)
  })
  const { sidebarView, setSidebarView, developerMode } = useAppStore(
    useShallow((s) => ({
      sidebarView: s.sidebarView,
      setSidebarView: s.setSidebarView,
      developerMode: s.developerMode,
    }))
  )
  const { update, ignored, ignoreVersion, currentVersion } = useUpdateCheck()
  const [dialogOpen, setDialogOpen] = useState(false)
  /** After Ignore or closing Details — hide float card; version becomes update pill */
  const [cardDismissed, setCardDismissed] = useState(false)
  /** Soft enter/leave for float card (keep mounted while fading out) */
  const [cardMounted, setCardMounted] = useState(false)
  const [cardIn, setCardIn] = useState(false)
  /** Soft enter for the flowing-green version pill (avoid hard cut) */
  const [pillIn, setPillIn] = useState(false)

  const listRef = useRef<HTMLElement>(null)
  const recallSlotRef = useRef<HTMLDivElement>(null)
  const itemRefs = useRef<Map<SidebarView, HTMLButtonElement>>(new Map())
  const rotRef = useRef(45)
  const prevViewRef = useRef<SidebarView | null>(null)
  const [plate, setPlate] = useState<PlateState>({ y: 0, rot: 45, ready: false })

  const measurePlate = useCallback(() => {
    const btn = itemRefs.current.get(sidebarView)
    const list = listRef.current
    if (!btn || !list) return

    const y = btn.offsetTop + btn.offsetHeight / 2

    // First paint: place without spin. Later: +90° quarter-turn while sliding.
    if (prevViewRef.current === null) {
      prevViewRef.current = sidebarView
      rotRef.current = 45
      setPlate({ y, rot: 45, ready: false })
      requestAnimationFrame(() =>
        setPlate((p) => ({ ...p, y, rot: 45, ready: true }))
      )
      return
    }

    if (prevViewRef.current !== sidebarView) {
      rotRef.current += 90
      prevViewRef.current = sidebarView
    }

    setPlate({ y, rot: rotRef.current, ready: true })
  }, [sidebarView])

  /* Recall view lives behind developer mode — bounce out when it turns off
     (also covers stale persisted sidebarView="recall" on a fresh load) */
  useLayoutEffect(() => {
    if (!developerMode && sidebarView === "recall") setSidebarView("chat")
  }, [developerMode, sidebarView, setSidebarView])

  useLayoutEffect(() => {
    measurePlate()
  }, [measurePlate])

  useLayoutEffect(() => {
    const list = listRef.current
    if (!list) return
    const ro = new ResizeObserver(() => measurePlate())
    ro.observe(list)
    /* The recall slot grows/collapses with developer mode — the list box
       itself never resizes, so track the slot to retarget the plate. */
    const slot = recallSlotRef.current
    if (slot) ro.observe(slot)
    window.addEventListener("resize", measurePlate)
    return () => {
      ro.disconnect()
      window.removeEventListener("resize", measurePlate)
    }
  }, [measurePlate])

  const setItemRef = useCallback((view: SidebarView, el: HTMLButtonElement | null) => {
    if (el) itemRefs.current.set(view, el)
    else itemRefs.current.delete(view)
  }, [])

  /** Logical visibility — fade keep-alive is cardMounted / cardIn */
  const shouldShowCard = !!update && !ignored && !cardDismissed
  /**
   * Keep the version as the green pill whenever an update exists (even while
   * the float card is open). That way card `left: 0` and the pill share one
   * left edge in the same stack — no shift when the card is dismissed.
   */
  const showUpdatePill = !!update

  /* Float card: mount → double-rAF is-in; hide → is-in off then unmount after UPDATE_CARD_MS */
  useEffect(() => {
    if (shouldShowCard) {
      setCardMounted(true)
      setCardIn(false)
      let raf2 = 0
      const raf1 = requestAnimationFrame(() => {
        raf2 = requestAnimationFrame(() => setCardIn(true))
      })
      return () => {
        cancelAnimationFrame(raf1)
        if (raf2) cancelAnimationFrame(raf2)
      }
    }
    setCardIn(false)
    const t = window.setTimeout(() => setCardMounted(false), UPDATE_CARD_MS)
    return () => window.clearTimeout(t)
  }, [shouldShowCard])

  /* Mount pill closed → next frames is-in for soft scale/fade (not a hard cut) */
  useEffect(() => {
    if (!showUpdatePill) {
      setPillIn(false)
      return
    }
    setPillIn(false)
    let raf2 = 0
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => setPillIn(true))
    })
    return () => {
      cancelAnimationFrame(raf1)
      if (raf2) cancelAnimationFrame(raf2)
    }
  }, [showUpdatePill])

  const handleIgnore = () => {
    /* Flag dismiss first so leave animation runs; ignore persists after */
    setCardDismissed(true)
    ignoreVersion()
  }

  const handleDialogOpenChange = (open: boolean) => {
    setDialogOpen(open)
    if (!open && update) {
      /* Close Details → fade float card into version pill */
      setCardDismissed(true)
    }
  }

  const openUpdateDetails = () => {
    if (!update) return
    setDialogOpen(true)
  }

  return (
    <>
      <aside className="pm-shell-nav" aria-label={t("nav.main")}>
        <nav ref={listRef} className="pm-shell-nav-list">
          {/* Shared diamond — slides + rotates to active item */}
          <span
            className={cn(
              "pm-shell-nav-plate",
              plate.ready && "is-ready"
            )}
            style={{
              /* top = item center; transform only centers + spins the diamond */
              top: plate.y,
              transform: `translate(-50%, -50%) rotate(${plate.rot}deg)`,
            }}
            aria-hidden
          />

          {navItems.map(({ view, label }) => {
            const active = sidebarView === view
            const item = (
              <button
                ref={(el) => setItemRef(view, el)}
                type="button"
                className={cn("pm-shell-nav-item", active && "is-active")}
                onClick={() => setSidebarView(view)}
                aria-current={active ? "page" : undefined}
              >
                <span className="pm-shell-nav-text">{label}</span>
              </button>
            )
            /* Recall mounts behind developer mode — the slot animates
               open/closed so the entries below slide instead of jumping. */
            if (view === "recall") {
              return (
                <div
                  key={view}
                  ref={recallSlotRef}
                  className={cn("pm-shell-nav-slot", developerMode && "is-open")}
                  inert={!developerMode}
                >
                  <div className="pm-shell-nav-slot-clip">{item}</div>
                </div>
              )
            }
            return <Fragment key={view}>{item}</Fragment>
          })}
        </nav>

        <div className="pm-shell-nav-bottom">
          {/* Shared stack so float card + version pill share the same left edge */}
          <div className="pm-shell-update-stack">
            {cardMounted && update && (
              <div
                className={cn(
                  "pm-shell-update-card",
                  cardIn && "is-in",
                )}
                role="status"
              >
                <div className="pm-shell-update-card-head">
                  <span className="pm-shell-update-label">{t("shell.update")}</span>
                  <button
                    type="button"
                    onClick={handleIgnore}
                    className="pm-shell-update-dismiss"
                    title={t("shell.ignoreVersion")}
                    aria-label={t("shell.ignoreVersion")}
                  >
                    <X className="h-3 w-3" strokeWidth={2} />
                  </button>
                </div>
                <p className="pm-shell-update-ver">
                  {update.latestVersion.startsWith("v")
                    ? update.latestVersion
                    : `v${update.latestVersion}`}
                </p>
                <p className="pm-shell-update-from">
                  {t("shell.fromVersion", {
                    version: update.currentVersion.replace(/^v/, ""),
                  })}
                </p>
                <div className="pm-shell-update-actions">
                  <button
                    type="button"
                    onClick={openUpdateDetails}
                    className="pm-shell-update-link"
                  >
                    {t("common.details")}
                    <ArrowUpRight className="h-3 w-3" strokeWidth={2} />
                  </button>
                  <button
                    type="button"
                    onClick={handleIgnore}
                    className="pm-shell-update-ghost"
                  >
                    {t("common.ignore")}
                  </button>
                </div>
              </div>
            )}
            {showUpdatePill ? (
              <button
                type="button"
                className={cn(
                  "pm-shell-version-pill is-update",
                  pillIn && "is-in",
                )}
                onClick={openUpdateDetails}
                title={t("shell.updateAvailable")}
                aria-label={t("shell.updateAvailableAria", { version: currentVersion })}
              >
                <span className="pm-shell-version-pill-ring" aria-hidden />
                <span className="pm-shell-version-pill-label">
                  v{currentVersion}
                </span>
              </button>
            ) : (
              <span className="pm-shell-version">v{currentVersion}</span>
            )}
          </div>
        </div>
      </aside>

      {update && (
        <UpdateDialog
          open={dialogOpen}
          onOpenChange={handleDialogOpenChange}
          update={update}
        />
      )}
    </>
  )
}
