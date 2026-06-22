import { useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Terminal } from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import { getHealth } from "@/api/client"

export function Header() {
  const { isOnline, setOnline, toggleLogPanel } = useAppStore()

  useEffect(() => {
    const check = async () => {
      try {
        const h = await getHealth()
        setOnline(h.status === "ok")
      } catch {
        setOnline(false)
      }
    }
    check()
    const interval = setInterval(check, 30000)
    return () => clearInterval(interval)
  }, [])

  return (
    <header className="flex items-center h-[42px] border-b border-border shrink-0 bg-background">
      {/* Left block aligned with sidebar width */}
      <div className="w-[172px] px-4 flex items-center gap-3 shrink-0 border-r border-border h-full">
        <h1
          className="text-[14px] font-light tracking-[0.15em] uppercase"
          style={{ fontFamily: "var(--font-serif)", color: "var(--ze-ink)" }}
        >
          SINKDUCE
        </h1>
      </div>

      {/* Right side of header */}
      <div className="flex items-center gap-3.5 px-5 flex-1">
        <span
          className="text-[12px] font-light uppercase tracking-[0.15em] text-muted-foreground"
          style={{ fontFamily: "var(--font-serif)" }}
        >
          Spark. Sink. Educe.
        </span>

        <div className="flex-1" />

        <Button
          variant="ghost"
          size="icon"
          onClick={toggleLogPanel}
          className="shrink-0 h-7 w-7 text-muted-foreground hover:text-primary"
          title="Toggle backend logs"
        >
          <Terminal className="h-3.5 w-3.5" />
        </Button>

        <div className="flex items-center gap-1.5 text-[9px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
          <span
            className="w-1.5 h-1.5 rounded-full"
            style={{ background: isOnline ? "var(--ze-green)" : "#dc2626" }}
          />
          {isOnline ? "ONLINE" : "OFFLINE"}
        </div>
      </div>
    </header>
  )
}
