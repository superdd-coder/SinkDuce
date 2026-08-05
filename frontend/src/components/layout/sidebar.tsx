import { useCallback, useLayoutEffect, useRef, useState } from "react"
import { useShallow } from "zustand/react/shallow"
import { cn } from "@/lib/utils"
import { useAppStore, type SidebarView } from "@/stores/app-store"
import { X, ArrowUpRight } from "lucide-react"
import { useUpdateCheck } from "@/hooks/use-update-check"
import { UpdateDialog } from "./update-dialog"

/**
 * Premium shell nav:
 * Slim rail · system label type · one shared diamond plate that
 * slides + quarter-turns when switching views.
 */
const navItems: Array<{ view: SidebarView; label: string }> = [
  { view: "chat", label: "Chat" },
  { view: "database", label: "Library" },
  { view: "recall", label: "Recall" },
  { view: "meeting", label: "Meeting" },
  { view: "llm_provider", label: "Settings" },
]

type PlateState = {
  /** Center Y of active item relative to list (px) */
  y: number
  /** Accumulated rotation — +90° each switch so diamond “rolls” to next */
  rot: number
  ready: boolean
}

export function Sidebar() {
  const { sidebarView, setSidebarView } = useAppStore(
    useShallow((s) => ({
      sidebarView: s.sidebarView,
      setSidebarView: s.setSidebarView,
    }))
  )
  const { update, ignored, ignoreVersion, currentVersion } = useUpdateCheck()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [leaving, setLeaving] = useState(false)

  const listRef = useRef<HTMLElement>(null)
  const itemRefs = useRef<Map<SidebarView, HTMLButtonElement>>(new Map())
  const rotRef = useRef(45)
  const prevViewRef = useRef<SidebarView | null>(null)
  const [plate, setPlate] = useState<PlateState>({ y: 0, rot: 45, ready: false })

  const measurePlate = useCallback(() => {
    const btn = itemRefs.current.get(sidebarView)
    const list = listRef.current
    if (!btn || !list) return

    const y = btn.offsetTop + btn.offsetHeight / 2

    // First paint: place without spin. Later: +90° quarter-turn while sliding.
    if (prevViewRef.current === null) {
      prevViewRef.current = sidebarView
      rotRef.current = 45
      setPlate({ y, rot: 45, ready: false })
      requestAnimationFrame(() =>
        setPlate((p) => ({ ...p, y, rot: 45, ready: true }))
      )
      return
    }

    if (prevViewRef.current !== sidebarView) {
      rotRef.current += 90
      prevViewRef.current = sidebarView
    }

    setPlate({ y, rot: rotRef.current, ready: true })
  }, [sidebarView])

  useLayoutEffect(() => {
    measurePlate()
  }, [measurePlate])

  useLayoutEffect(() => {
    const list = listRef.current
    if (!list) return
    const ro = new ResizeObserver(() => measurePlate())
    ro.observe(list)
    window.addEventListener("resize", measurePlate)
    return () => {
      ro.disconnect()
      window.removeEventListener("resize", measurePlate)
    }
  }, [measurePlate])

  const setItemRef = useCallback((view: SidebarView, el: HTMLButtonElement | null) => {
    if (el) itemRefs.current.set(view, el)
    else itemRefs.current.delete(view)
  }, [])

  const showCard = update && !ignored && !leaving

  const handleIgnore = () => {
    setLeaving(true)
    setTimeout(() => {
      ignoreVersion()
      setLeaving(false)
    }, 250)
  }

  return (
    <>
      <aside className="pm-shell-nav" aria-label="Main navigation">
        <nav ref={listRef} className="pm-shell-nav-list">
          {/* Shared diamond — slides + rotates to active item */}
          <span
            className={cn(
              "pm-shell-nav-plate",
              plate.ready && "is-ready"
            )}
            style={{
              /* top = item center; transform only centers + spins the diamond */
              top: plate.y,
              transform: `translate(-50%, -50%) rotate(${plate.rot}deg)`,
            }}
            aria-hidden
          />

          {navItems.map(({ view, label }) => {
            const active = sidebarView === view
            return (
              <button
                key={view}
                ref={(el) => setItemRef(view, el)}
                type="button"
                className={cn("pm-shell-nav-item", active && "is-active")}
                onClick={() => setSidebarView(view)}
                aria-current={active ? "page" : undefined}
              >
                <span className="pm-shell-nav-text">{label}</span>
              </button>
            )
          })}
        </nav>

        <div className="pm-shell-nav-bottom">
          {showCard && update && (
            <div
              className={cn(
                "pm-shell-update-card mb-3 transition-all duration-300",
                leaving && "opacity-0 -translate-x-2"
              )}
            >
              <div className="flex items-center justify-between mb-1.5">
                <span className="pm-shell-update-label">Update</span>
                <button
                  type="button"
                  onClick={handleIgnore}
                  className="text-[var(--pm-faint)] hover:text-[var(--pm-muted)]"
                  title="Ignore this version"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
              <p className="pm-shell-update-ver mb-2">{update.latestVersion}</p>
              <div className="flex flex-col gap-1">
                <button
                  type="button"
                  onClick={() => setDialogOpen(true)}
                  className="pm-shell-update-link"
                >
                  Details <ArrowUpRight className="h-2.5 w-2.5" />
                </button>
                <button type="button" onClick={handleIgnore} className="pm-shell-update-ghost">
                  Ignore
                </button>
              </div>
            </div>
          )}
          <span className="pm-shell-version">v{currentVersion}</span>
        </div>
      </aside>

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
