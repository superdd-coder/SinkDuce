import { cn } from "@/lib/utils"
import { useAppStore, type SidebarView } from "@/stores/app-store"
import { Button } from "@/components/ui/button"

/* Small diamond bullet — inline style overrides Tailwind SVG size rule */
const DiamondDot = () => (
  <svg style={{ width: "7px", height: "7px" }} viewBox="0 0 4 4" fill="currentColor" stroke="none">
    <polygon points="2,0 4,2 2,4 0,2" />
  </svg>
)

const navItems: Array<{ view: SidebarView; label: string }> = [
  { view: "chat", label: "Chat" },
  { view: "database", label: "Collection" },
  { view: "recall", label: "Recall" },
  { view: "meeting", label: "Recording" },
  { view: "llm_provider", label: "Settings" },
]

export function Sidebar() {
  const { sidebarView, setSidebarView } = useAppStore()

  return (
    <aside
      className="w-[172px] border-r border-border flex flex-col shrink-0 py-6 px-4 bg-background"
    >
      <nav className="flex flex-col flex-1">
        <div className="text-[14px] font-semibold uppercase tracking-[0.25em] text-muted-foreground mb-3">
          Navigate
        </div>

        {navItems.map(({ view, label }) => (
          <div key={view}>
            <Button
              variant="ghost"
              className={cn(
                "w-full justify-start gap-2.5 py-1.5 px-0 h-auto text-xs uppercase tracking-wider relative rounded-none",
                "hover:bg-transparent hover:text-primary",
                sidebarView === view ? "font-medium text-primary" : "font-normal text-muted-foreground",
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

        <div className="mt-auto pt-5 border-t border-dashed border-border">
          <div className="text-[9px] tracking-[0.1em] text-muted-foreground">
            SinkDuce v0.1 · RAG
          </div>
        </div>
      </nav>
    </aside>
  )
}
