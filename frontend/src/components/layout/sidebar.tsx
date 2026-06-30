import { useState, useRef, useEffect } from "react"
import { cn } from "@/lib/utils"
import { useAppStore, type SidebarView } from "@/stores/app-store"
import { Button } from "@/components/ui/button"
import { X, ArrowUpRight } from "lucide-react"
import { useUpdateCheck } from "@/hooks/use-update-check"
import { UpdateDialog } from "./update-dialog"

/* Small diamond bullet */
const DiamondDot = () => (
  <svg style={{ width: "7px", height: "7px" }} viewBox="0 0 4 4" fill="currentColor" stroke="none">
    <polygon points="2,0 4,2 2,4 0,2" />
  </svg>
)

const navItems: Array<{ view: SidebarView; label: string }> = [
  { view: "chat", label: "Chat" },
  { view: "database", label: "Collection" },
  { view: "recall", label: "Recall" },
  { view: "meeting", label: "Meeting" },
  { view: "llm_provider", label: "Settings" },
]

const COLLAPSE_DELAY_MS = 1000

export function Sidebar() {
  const { sidebarView, setSidebarView, sidebarOpen, setSidebarOpen } = useAppStore()
  const { update, ignored, ignoreVersion, currentVersion } = useUpdateCheck()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [leaving, setLeaving] = useState(false)
  const collapseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const showCard = update && !ignored && !leaving
  const showDot = update !== null

  const handleIgnore = () => {
    setLeaving(true)
    setTimeout(() => {
      ignoreVersion()
      setLeaving(false)
    }, 250)
  }

  /* Hover-driven expand: enter → expand immediately, leave → wait then collapse.
     Header watches the same `sidebarOpen` so the logo block tracks the width. */
  const clearCollapseTimer = () => {
    if (collapseTimerRef.current) {
      clearTimeout(collapseTimerRef.current)
      collapseTimerRef.current = null
    }
  }
  const handleMouseEnter = () => {
    clearCollapseTimer()
    if (!sidebarOpen) setSidebarOpen(true)
  }
  const handleMouseLeave = () => {
    clearCollapseTimer()
    collapseTimerRef.current = setTimeout(() => {
      setSidebarOpen(false)
      collapseTimerRef.current = null
    }, COLLAPSE_DELAY_MS)
  }
  /* Clean up pending timer on unmount */
  useEffect(() => () => clearCollapseTimer(), [])

  return (
    <>
      <aside
        className={cn(
          "border-r border-border flex flex-col shrink-0 bg-background overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)]",
          sidebarOpen ? "w-[172px] py-6 px-4" : "w-[28px] py-6"
        )}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
      >
        {sidebarOpen ? (
        <nav className="flex flex-col flex-1">
          <div
            className="text-[14px] font-[300] uppercase tracking-[0.25em] text-muted-foreground mb-4 select-none"
            title="Navigation"
          >
            Navigate
          </div>

          {navItems.map(({ view, label }) => (
            <div key={view} className="mb-0.5">
              <Button
                variant="ghost"
                className={cn(
                  "w-full justify-start gap-2.5 py-2 px-0 h-auto text-xs uppercase tracking-wider relative rounded-none",
                  "hover:bg-transparent hover:text-primary",
                  sidebarView === view ? "font-[400] text-primary" : "font-[300] text-muted-foreground",
                )}
                onClick={() => setSidebarView(view)}
              >
                <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: "8px", height: "8px", flexShrink: 0, opacity: sidebarView === view ? 1 : 0.4, lineHeight: 0 }}>
                  <DiamondDot />
                </span>
                {label}
                {sidebarView === view && (
                  <span
                    className="absolute bottom-0 left-0 h-[1.5px] w-5"
                    style={{ background: "var(--ze-green)" }}
                  />
                )}
              </Button>
            </div>
          ))}

          {/* ── Bottom group: update card + version line ── */}
          <div className="mt-auto">
          <div
            className={cn(
              "overflow-hidden transition-all duration-300 ease-out",
              showCard ? "translate-x-0 opacity-100 max-h-32 mb-3" : "-translate-x-full opacity-0 max-h-0 mb-0"
            )}
          >
          {update && !ignored && (
            <div
              className="rounded-sm p-3"
              style={{
                border: "1px solid #1a3a2a",
                backgroundColor: "#faf7f2",
                boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
              }}
            >
              {/* Header row */}
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-1.5">
                  <span style={{ color: "#1a3a2a", lineHeight: 0 }}>
                    <DiamondDot />
                  </span>
                  <span
                    className="text-[10px] uppercase tracking-[0.12em]"
                    style={{ color: "#1a3a2a", fontFamily: "var(--font-serif)" }}
                  >
                    New Version
                  </span>
                </div>
                <button
                  onClick={handleIgnore}
                  className="text-muted-foreground/50 hover:text-muted-foreground transition-colors"
                  title="Ignore this version"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>

              {/* Version number */}
              <p
                className="text-sm font-light mb-3"
                style={{ fontFamily: "var(--font-serif)", color: "var(--ze-ink)" }}
              >
                {update.latestVersion}
              </p>

              {/* Buttons */}
              <div className="flex items-center gap-2">
                <button
                  onClick={handleIgnore}
                  className="text-[10px] uppercase tracking-[0.08em] text-muted-foreground/60 hover:text-muted-foreground transition-colors"
                  style={{ fontFamily: "var(--font-serif)" }}
                >
                  Ignore
                </button>
                <button
                  onClick={() => setDialogOpen(true)}
                  className="text-[10px] uppercase tracking-[0.08em] font-medium transition-colors flex items-center gap-1"
                  style={{ color: "#1a3a2a", fontFamily: "var(--font-serif)" }}
                >
                  Update <ArrowUpRight className="h-2.5 w-2.5" />
                </button>
              </div>
            </div>
          )}
          </div>

          {/* ── Version line ── */}
          <div className="pt-5 border-t border-dashed border-border">
            <div className="flex items-center justify-between">
              <span
                className="text-[10px] font-[300] tracking-[0.1em] text-muted-foreground/60"
                style={{ fontFamily: "var(--font-serif)" }}
              >
                v{currentVersion}
              </span>
              {showDot && (
                <button
                  onClick={() => setDialogOpen(true)}
                  className="flex items-center gap-1 text-[10px] font-[300] tracking-[0.08em] hover:opacity-80 transition-opacity"
                  style={{ color: "#dc2626", fontFamily: "var(--font-serif)" }}
                  title={`Update ${update.latestVersion} available`}
                >
                  <span className="w-1 h-1 rounded-full" style={{ backgroundColor: "#dc2626" }} />
                  {update.latestVersion.replace(/^v/, "")}
                </button>
              )}
            </div>
          </div>
          </div>
        </nav>
        ) : (
          /* Collapsed: vertical "Navigate" hint — hover the bar to expand */
          <div
            className="flex-1 flex items-center justify-center group"
            title="Hover to open navigation"
          >
            <span
              className="text-[10px] font-[300] uppercase tracking-[0.25em] text-muted-foreground group-hover:text-primary transition-colors select-none whitespace-nowrap"
              style={{
                writingMode: "vertical-rl",
                transform: "rotate(180deg)",
                fontFamily: "var(--font-serif)",
              }}
            >
              Navigate
            </span>
          </div>
        )}
      </aside>

      {/* ── Update detail dialog ── */}
      {update && (
        <UpdateDialog
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          update={update}
        />
      )}
    </>
  )
}
