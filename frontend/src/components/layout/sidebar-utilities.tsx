import { useEffect, useState } from "react"
import { useShallow } from "zustand/react/shallow"
import { RotateCw, Terminal } from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import { getLocalHealth } from "@/api/client"
import { useT } from "@/i18n/use-t"
import { cn } from "@/lib/utils"
import { SourcePill } from "./source-pill"

export function SidebarUtilities() {
  const t = useT()
  const { toggleLogPanel, developerMode, logPanelOpen } =
    useAppStore(
      useShallow((s) => ({
        toggleLogPanel: s.toggleLogPanel,
        developerMode: s.developerMode,
        logPanelOpen: s.logPanelOpen,
      }))
    )
  const [desktop, setDesktop] = useState(false)

  useEffect(() => {
    const check = async () => {
      try {
        // 「是不是桌面壳」看本机（Reload 按钮属壳层 UI）；
        // 来源健康探测已统一交给 api/source-health.ts（SourcePill 挂载循环）。
        const local = await getLocalHealth()
        setDesktop(local.desktop === true)
      } catch {
        setDesktop(false)
      }
    }
    check()
    const interval = setInterval(check, 30000)
    return () => clearInterval(interval)
  }, [])

  return (
    <>
      {developerMode && (
        <button
          type="button"
          onClick={toggleLogPanel}
          className={cn("sd-nav-item", logPanelOpen && "is-active")}
          title={t("shell.toggleLogs")}
          aria-label={t("logs.backendLogs")}
          aria-pressed={logPanelOpen}
        >
          <Terminal size={18} strokeWidth={1.65} aria-hidden="true" />
          <span className="sd-nav-label">{t("logs.backendLogs")}</span>
        </button>
      )}

      {desktop && (
        <button
          type="button"
          className="sd-nav-item"
          aria-label={t("shell.reload")}
          title={t("shell.reload")}
          onClick={() => window.location.reload()}
        >
          <RotateCw size={18} strokeWidth={1.65} aria-hidden="true" />
          <span className="sd-nav-label">{t("common.refresh")}</span>
        </button>
      )}

      <SourcePill />
    </>
  )
}
