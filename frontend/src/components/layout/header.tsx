import { useEffect, useState } from "react"
import { useShallow } from "zustand/react/shallow"
import { Button } from "@/components/ui/button"
import { Terminal } from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import { getHealth } from "@/api/client"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"

export function Header() {
  const t = useT()
  const { isOnline, setOnline, toggleLogPanel, developerMode } =
    useAppStore(
      useShallow((s) => ({
        isOnline: s.isOnline,
        setOnline: s.setOnline,
        toggleLogPanel: s.toggleLogPanel,
        developerMode: s.developerMode,
      }))
    )
  const [desktop, setDesktop] = useState(false)

  useEffect(() => {
    const check = async () => {
      try {
        const h = await getHealth()
        setOnline(h.status === "ok")
        setDesktop(h.desktop === true)
      } catch {
        setOnline(false)
        setDesktop(false)
      }
    }
    check()
    const interval = setInterval(check, 30000)
    return () => clearInterval(interval)
  }, [setOnline])

  return (
    <header className="pm-shell-header flex items-center h-[48px] shrink-0 px-5 gap-4">
      {/* Keep product wordmark — SINK + italic green DUCE */}
      <h1 className="pm-shell-brand shrink-0">
        SINK
        <em className="pm-shell-brand-em">DUCE</em>
      </h1>

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

      <div
        className={cn(
          "pm-shell-status",
          isOnline ? "is-online" : "is-offline"
        )}
      >
        <span className="pm-shell-status-dot" aria-hidden />
        {isOnline ? t("shell.online") : t("shell.offline")}
      </div>
    </header>
  )
}
