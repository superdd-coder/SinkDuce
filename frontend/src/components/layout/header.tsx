import { useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Terminal } from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import { getHealth } from "@/api/client"
import { cn } from "@/lib/utils"

export function Header() {
  const { isOnline, setOnline, toggleLogPanel, sidebarOpen } = useAppStore()

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
      {/* Left block — width tracks the sidebar. No overflow-hidden: the logo
          is intentionally allowed to extend past the 28px collapsed width
          so it stays fully readable. The right block's tagline fades out
          when collapsed, so there's nothing to overlap with. */}
      <div
        className={cn(
          "px-4 flex items-center gap-3 shrink-0 h-full transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)]",
          sidebarOpen ? "w-[172px]" : "w-[28px]"
        )}
      >
        <h1
          className="text-[16px] font-light tracking-[0.18em] uppercase whitespace-nowrap"
          style={{ fontFamily: "var(--font-serif)", color: "var(--ze-ink)" }}
        >
          SINK
          <em
            style={{
              fontStyle: "italic",
              fontWeight: 300,
              color: "var(--ze-green)",
            }}
          >
            DUCE
          </em>
        </h1>
      </div>

      {/* Right side of header */}
      <div className="flex items-center gap-3.5 px-5 flex-1">
        <span
          className={cn(
            "text-[12px] font-light uppercase tracking-[0.15em] text-muted-foreground transition-opacity duration-500 ease-[cubic-bezier(0.23,1,0.32,1)]",
            sidebarOpen ? "opacity-100" : "opacity-0"
          )}
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

        <div className="flex items-center gap-1.5 text-[11px] font-normal uppercase tracking-[0.12em] text-muted-foreground/80">
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
