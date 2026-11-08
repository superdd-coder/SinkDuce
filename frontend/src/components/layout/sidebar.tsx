import { useLayoutEffect, useState } from "react"
import { useShallow } from "zustand/react/shallow"
import { cn } from "@/lib/utils"
import { useAppStore, type SidebarView } from "@/stores/app-store"
import { ArrowUpCircle, BookOpen, FlaskConical, Info, MessageCircle, Mic, Settings2, type LucideIcon } from "lucide-react"
import { EDITION_LABEL } from "#edition/edition-label"
import { useUpdateCheck } from "@/hooks/use-update-check"
import { useT } from "@/i18n/use-t"
import { UpdateDialog } from "./update-dialog"
import { SidebarUtilities } from "./sidebar-utilities"
import { GlobalSearch } from "./global-search"

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

  /* Recall view lives behind developer mode — bounce out when it turns off
     (also covers stale persisted sidebarView="recall" on a fresh load) */
  useLayoutEffect(() => {
    if (!developerMode && sidebarView === "recall") setSidebarView("chat")
  }, [developerMode, sidebarView, setSidebarView])

  const hasUpdate = update !== null && !ignored
  const version = currentVersion === "—" ? currentVersion : `v${currentVersion.replace(/^v/, "")}`
  const versionAction = t(hasUpdate ? "shell.updateAvailableAria" : "shell.versionDetails", { version })
  const VersionIcon = hasUpdate ? ArrowUpCircle : Info
  const versionContent = (
    <>
      <VersionIcon size={18} strokeWidth={1.65} aria-hidden="true" />
      <span className="sd-nav-label sd-nav-meta">
        <span className="sd-nav-edition">{EDITION_LABEL}</span>
        <span>{version}</span>
      </span>
    </>
  )

  return (
    <>
      <div className="sd-nav-slot">
        <aside className="sd-navigation" aria-label={t("nav.main")}>
          <div className="sd-nav-brand">
            <img src="/sinkduce-mark.png" alt="SinkDuce" width="32" height="32" />
            <span className="sd-nav-wordmark" aria-hidden="true">
              <img src="/sinkduce-wordmark.png" alt="" width="761" height="160" />
            </span>
          </div>
          <GlobalSearch />
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
            <SidebarUtilities />
            {update ? (
              <button
                type="button"
                className={cn("sd-nav-item sd-nav-version", hasUpdate && "is-update")}
                onClick={() => setDialogOpen(true)}
                title={versionAction}
                aria-label={versionAction}
                aria-haspopup="dialog"
                aria-expanded={dialogOpen}
              >
                {versionContent}
              </button>
            ) : (
              <div className="sd-nav-version" title={`SinkDuce · ${EDITION_LABEL} · ${version}`}>
                {versionContent}
              </div>
            )}
          </div>
        </aside>
      </div>

      {update && (
        <UpdateDialog
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          update={update}
          onIgnore={ignored ? undefined : ignoreVersion}
        />
      )}
    </>
  )
}
