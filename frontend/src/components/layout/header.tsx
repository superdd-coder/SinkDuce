import { useEffect } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { PanelLeft, Terminal } from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import { getHealth } from "@/api/client"

export function Header() {
  const { isOnline, setOnline, toggleSidebar, logPanelOpen, toggleLogPanel } = useAppStore()

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
    <header className="flex items-center gap-3 px-4 h-14 border-b border-border bg-background shrink-0">
      <Button variant="ghost" size="icon" onClick={toggleSidebar} className="shrink-0">
        <PanelLeft className="h-5 w-5" />
      </Button>

      <h1 className="text-lg font-semibold tracking-tight flex items-center gap-2">
        <img src="/favicon.png" alt="SinkDuce" className="h-6 w-6" />
        SinkDuce
      </h1>

      <div className="flex-1" />

      <Button
        variant={logPanelOpen ? "secondary" : "ghost"}
        size="icon"
        onClick={toggleLogPanel}
        className="shrink-0"
        title="Toggle backend logs"
      >
        <Terminal className="h-4 w-4" />
      </Button>

      <Badge variant={isOnline ? "default" : "destructive"} className="gap-1.5">
        <span className={`h-2 w-2 rounded-full ${isOnline ? "bg-green-400" : "bg-red-400"}`} />
        {isOnline ? "online" : "offline"}
      </Badge>
    </header>
  )
}
