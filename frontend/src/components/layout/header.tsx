import { useEffect, useState } from "react"
import { useShallow } from "zustand/react/shallow"
import { Button } from "@/components/ui/button"
import { RotateCw, Terminal } from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import { getLocalHealth } from "@/api/client"
import { useT } from "@/i18n/use-t"
import { EDITION_LABEL } from "#edition/edition-label"
import { SourcePill } from "./source-pill"

export function Header() {
  const t = useT()
  const { toggleLogPanel, developerMode, sidebarView } =
    useAppStore(
      useShallow((s) => ({
        toggleLogPanel: s.toggleLogPanel,
        developerMode: s.developerMode,
        sidebarView: s.sidebarView,
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
    <header className="pm-shell-header flex items-center h-[48px] shrink-0 px-5 gap-4">
      <h1 className="sd-shell-title">{t({ chat: "nav.chat", database: "nav.library", meeting: "nav.meeting", recall: "nav.recall", llm_provider: "nav.settings" }[sidebarView])}</h1>

      <div className="flex-1" />

      {developerMode && (
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleLogPanel}
          className="shrink-0 h-7 w-7 text-[var(--pm-muted)] hover:text-[var(--pm-green)] hover:bg-[var(--pm-green-soft)]"
          title={t("shell.toggleLogs")}
          aria-label={t("shell.toggleLogs")}
        >
          <Terminal className="h-3.5 w-3.5" />
        </Button>
      )}

      {desktop && (
        <button
          type="button"
          className="pm-shell-refresh"
          aria-label={t("shell.reload")}
          title={t("shell.reload")}
          onClick={() => window.location.reload()}
        >
          <RotateCw size={16} aria-hidden="true" />
        </button>
      )}

      <span className="pm-shell-edition-label">{EDITION_LABEL}</span>
      <SourcePill />
    </header>
  )
}
