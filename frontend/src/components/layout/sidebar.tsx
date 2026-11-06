import { useEffect, useLayoutEffect, useState } from "react"
import { useShallow } from "zustand/react/shallow"
import { cn } from "@/lib/utils"
import { useAppStore, type SidebarView } from "@/stores/app-store"
import { X, ArrowUpRight, ArrowUpCircle, BookOpen, FlaskConical, Info, MessageCircle, Mic, Settings2, type LucideIcon } from "lucide-react"
import { EDITION_LABEL } from "#edition/edition-label"
import { useUpdateCheck } from "@/hooks/use-update-check"
import { useT } from "@/i18n/use-t"
import { UpdateDialog } from "./update-dialog"

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
  const ALL_NAV: Array<{ view: SidebarView; label: string; Icon: LucideIcon }> = [
    { view: "chat", label: t("nav.chat"), Icon: MessageCircle },
    { view: "database", label: t("nav.library"), Icon: BookOpen },
    { view: "meeting", label: t("nav.meeting"), Icon: Mic },
    { view: "recall", label: t("nav.recall"), Icon: FlaskConical },
    { view: "llm_provider", label: t("nav.settings"), Icon: Settings2 },
  ]
  const navItems: Array<{ view: SidebarView; label: string; Icon: LucideIcon }> = ALL_NAV.filter((it) => {
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

  /* Recall view lives behind developer mode — bounce out when it turns off
     (also covers stale persisted sidebarView="recall" on a fresh load) */
  useLayoutEffect(() => {
    if (!developerMode && sidebarView === "recall") setSidebarView("chat")
  }, [developerMode, sidebarView, setSidebarView])

  /** Logical visibility — fade keep-alive is cardMounted / cardIn */
  const shouldShowCard = !!update && !ignored && !cardDismissed
  /**
   * Keep the version as the green pill whenever an update exists (even while
   * the float card is open). That way card `left: 0` and the pill share one
   * left edge in the same stack — no shift when the card is dismissed.
   */
  const showUpdatePill = !!update

  // Mount before fading in; keep the last frame for the exit transition.
  useEffect(() => {
    let raf2 = 0
    let raf3 = 0
    let exitTimer = 0
    const raf1 = requestAnimationFrame(() => {
      if (shouldShowCard) {
        setCardMounted(true)
        setCardIn(false)
        raf2 = requestAnimationFrame(() => {
          raf3 = requestAnimationFrame(() => setCardIn(true))
        })
      } else {
        setCardIn(false)
        exitTimer = window.setTimeout(() => setCardMounted(false), UPDATE_CARD_MS)
      }
    })
    return () => {
      cancelAnimationFrame(raf1)
      cancelAnimationFrame(raf2)
      cancelAnimationFrame(raf3)
      window.clearTimeout(exitTimer)
    }
  }, [shouldShowCard])

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
      <div className="sd-nav-slot">
        <aside className="sd-navigation" aria-label={t("nav.main")}>
          <div className="sd-nav-brand">
            <img src="/sinkduce-mark.png" alt="SinkDuce" width="32" height="32" />
            <span aria-hidden="true">SinkDuce</span>
          </div>
          <nav className="sd-nav-list" aria-label={t("nav.main")}>
            {navItems.filter(({ view }) => view !== "llm_provider" && (view !== "recall" || developerMode)).map(({ view, label, Icon }) => (
              <button
                key={view}
                type="button"
                className={cn("sd-nav-item", sidebarView === view && "is-active")}
                onClick={() => setSidebarView(view)}
                aria-label={label}
                title={label}
                aria-current={sidebarView === view ? "page" : undefined}
              >
                <Icon size={18} strokeWidth={1.65} aria-hidden="true" />
                <span className="sd-nav-label">{label}</span>
              </button>
            ))}
          </nav>
          <div className="sd-nav-bottom">
            {navItems.filter(({ view }) => view === "llm_provider").map(({ view, label, Icon }) => (
              <button key={view} type="button" className={cn("sd-nav-item", sidebarView === view && "is-active")}
                onClick={() => setSidebarView(view)} aria-label={label} title={label}
                aria-current={sidebarView === view ? "page" : undefined}>
                <Icon size={18} strokeWidth={1.65} aria-hidden="true" /><span className="sd-nav-label">{label}</span>
              </button>
            ))}
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
                  className="pm-shell-version-pill is-update is-in"
                  onClick={openUpdateDetails}
                  title={t("shell.updateAvailable")}
                  aria-label={t("shell.updateAvailableAria", { version: currentVersion })}
                >
                  <ArrowUpCircle size={18} strokeWidth={1.65} aria-hidden="true" />
                  <span className="pm-shell-version-pill-label">
                    v{currentVersion}
                  </span>
                </button>
              ) : (
                <div className="sd-nav-version" title={`SinkDuce · ${EDITION_LABEL} · v${currentVersion}`} aria-label={`SinkDuce ${EDITION_LABEL} v${currentVersion}`}><Info size={18} strokeWidth={1.65} aria-hidden="true" /><span className="sd-nav-label">v{currentVersion}</span></div>
              )}
            </div>
          </div>
        </aside>
      </div>

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
