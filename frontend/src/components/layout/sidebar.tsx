import { MessageSquare, Database, Search, Bot, Mic } from "lucide-react"
import { cn } from "@/lib/utils"
import { useAppStore, type SidebarView } from "@/stores/app-store"
import { Separator } from "@/components/ui/separator"
import { Button } from "@/components/ui/button"

const navItems: Array<{ view: SidebarView; icon: typeof MessageSquare; label: string }> = [
  { view: "chat", icon: MessageSquare, label: "Chat" },
  { view: "database", icon: Database, label: "Collection" },
  { view: "recall", icon: Search, label: "Recall" },
  { view: "meeting", icon: Mic, label: "Meeting" },
  { view: "llm_provider", icon: Bot, label: "Settings" },
]

export function Sidebar() {
  const { sidebarView, setSidebarView, sidebarOpen } = useAppStore()

  if (!sidebarOpen) return null

  return (
    <aside className="w-56 border-r border-border bg-sidebar-background flex flex-col shrink-0">
      <nav className="flex flex-col gap-1 p-3 flex-1">
        {navItems.map(({ view, icon: Icon, label }) => (
          <Button
            key={view}
            variant={sidebarView === view ? "secondary" : "ghost"}
            className={cn(
              "justify-start gap-3 w-full",
              sidebarView === view && "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
            )}
            onClick={() => setSidebarView(view)}
          >
            <Icon className="h-4 w-4" />
            {label}
          </Button>
        ))}
      </nav>

      <Separator />

      <div className="p-3 text-xs text-muted-foreground">
        SinkDuce v0.1
      </div>
    </aside>
  )
}
