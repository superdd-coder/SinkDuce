import { useEffect, useState } from "react"
import { useShallow } from "zustand/react/shallow"
import { Button } from "@/components/ui/button"
import { Terminal } from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import { getLocalHealth } from "@/api/client"
import { useT } from "@/i18n/use-t"
import { EDITION_LABEL } from "#edition/edition-label"
import { SourcePill } from "./source-pill"

export function Header() {
  const t = useT()
  const { toggleLogPanel, developerMode } =
    useAppStore(
      useShallow((s) => ({
        toggleLogPanel: s.toggleLogPanel,
        developerMode: s.developerMode,
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
      <div className="pm-shell-brand-lockup shrink-0">
        {/* Keep product wordmark — SINK + italic green DUCE */}
        <h1 className="pm-shell-brand">
          SINK
          <em className="pm-shell-brand-em">DUCE</em>
        </h1>
        <span className="pm-shell-edition-label">{EDITION_LABEL}</span>
      </div>

      <span className="pm-shell-tagline hidden sm:inline">Spark. Sink. Educe.</span>

      <div className="flex-1" />

      {developerMode && (
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleLogPanel}
          className="shrink-0 h-7 w-7 text-[var(--pm-muted)] hover:text-[var(--pm-green)] hover:bg-[var(--pm-green-soft)]"
          title={t("shell.toggleLogs")}
        >
          <Terminal className="h-3.5 w-3.5" />
        </Button>
      )}

      {desktop && (
        <button
          type="button"
          className="pm-shell-refresh"
          title={t("shell.reload")}
          onClick={() => window.location.reload()}
        >
          {t("common.refresh")}
        </button>
      )}

      <SourcePill />
    </header>
  )
}
