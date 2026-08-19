import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import {
  ArrowUpRight,
  ChevronRight,
  FolderOpen,
  GitBranch,
  PinOff,
} from "lucide-react"
import type { FileNodeRef, FilePath } from "@/types/file-mgmt"

/**
 * Two-step delete (× → DELETE) — same anti-mis-tap pattern as message sidebar
 * (message-card.tsx · .pm-msg-delete).
 */
export function LogMsgDeleteButton({
  disabled,
  onConfirm,
}: {
  disabled?: boolean
  onConfirm: () => void
}) {
  const [deleteArmed, setDeleteArmed] = useState(false)
  const deleteArmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const deleteBtnRef = useRef<HTMLButtonElement>(null)

  const disarmDelete = useCallback(() => {
    setDeleteArmed(false)
    if (deleteArmTimerRef.current) {
      clearTimeout(deleteArmTimerRef.current)
      deleteArmTimerRef.current = null
    }
  }, [])

  const armDelete = useCallback(() => {
    setDeleteArmed(true)
    if (deleteArmTimerRef.current) clearTimeout(deleteArmTimerRef.current)
    deleteArmTimerRef.current = setTimeout(() => disarmDelete(), 4000)
  }, [disarmDelete])

  useEffect(() => {
    if (!deleteArmed) return
    const onPointerDown = (ev: Event) => {
      const t = ev.target as Node | null
      if (t && deleteBtnRef.current?.contains(t)) return
      disarmDelete()
    }
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") disarmDelete()
    }
    const t = window.setTimeout(() => {
      document.addEventListener("pointerdown", onPointerDown, true)
      document.addEventListener("keydown", onKey, true)
    }, 0)
    return () => {
      window.clearTimeout(t)
      document.removeEventListener("pointerdown", onPointerDown, true)
      document.removeEventListener("keydown", onKey, true)
    }
  }, [deleteArmed, disarmDelete])

  useEffect(() => {
    return () => {
      if (deleteArmTimerRef.current) clearTimeout(deleteArmTimerRef.current)
    }
  }, [])

  return (
    <button
      ref={deleteBtnRef}
      type="button"
      disabled={disabled}
      className={cn(
        "pm-msg-delete",
        deleteArmed ? "is-confirm opacity-100" : "opacity-0 group-hover:opacity-100",
        "transition-opacity"
      )}
      title={deleteArmed ? "Click again to delete" : "Delete message"}
      aria-label={
        deleteArmed ? "Confirm delete message" : "Delete message"
      }
      aria-expanded={deleteArmed}
      onClick={(e) => {
        e.preventDefault()
        e.stopPropagation()
        if (disabled) return
        if (!deleteArmed) {
          armDelete()
          return
        }
        disarmDelete()
        onConfirm()
      }}
    >
      <span className="pm-msg-delete-x" aria-hidden>
        ×
      </span>
      <span className="pm-msg-delete-label">Delete</span>
    </button>
  )
}

/** Dropdown row — shared Menu primitive. */
export function ActionMenuItem({
  icon,
  title,
  description,
  onClick,
  destructive,
}: {
  icon: ReactNode
  title: string
  description: string
  onClick: () => void
  destructive?: boolean
}) {
  return (
    <button
      type="button"
      role="menuitem"
      className={cn("pm-files-menu-item", destructive && "is-danger")}
      onClick={onClick}
    >
      <span
        className={cn(
          "mt-0.5 shrink-0",
          destructive ? "text-[var(--pm-danger)]" : "text-[var(--pm-muted)]"
        )}
      >
        {icon}
      </span>
      <span className="min-w-0">
        <span className="pm-files-menu-item-title">{title}</span>
        <span className="pm-files-menu-item-desc">{description}</span>
      </span>
    </button>
  )
}

export function SummarySection({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <h5 className="pm-ws-section-label">
        {title}
      </h5>
      <ul className="space-y-1">
        {items.map((item, i) => (
          <li key={i} className="pm-ws-prose-item">
            {item}
          </li>
        ))}
      </ul>
    </div>
  )
}

export function PathRow({
  path,
  sourceNodeTitle,
  folderHasPinned = false,
  canUnpin = false,
  busy,
  onNavigate,
  onPromote,
  onUnpin,
}: {
  path: FilePath
  /** Display name of the timeline node that created this path (derived only). */
  sourceNodeTitle?: string | null
  /**
   * True when any path for the same folder is pinned (source_node_id null).
   * Sibling *derived* rows keep their “From node” label; Pin is hidden because
   * the folder is already covered by the pin.
   */
  folderHasPinned?: boolean
  /**
   * True when demote can re-link to a node or drop a pin that has a derived
   * sibling. Plain folder mounts (no node) must not show Unpin — that used to
   * delete the only path row and make the card vanish.
   */
  canUnpin?: boolean
  busy: boolean
  onNavigate: () => void
  onPromote: () => void
  onUnpin: () => void
}) {
  /** Persistent path: source_node_id is null (pin or plain folder mount). */
  const isPersistent = !path.source_node_id
  /** Timeline pin that can be demoted (vs plain “in folder” mount). */
  const isTimelinePin = isPersistent && canUnpin
  const typeLabel = isPersistent
    ? isTimelinePin
      ? "Pinned to folder"
      : "In folder"
    : `From node · ${sourceNodeTitle || "Untitled node"}`
  return (
    <li
      className={cn(
        "pm-ws-path-row",
        path.is_greyed && "opacity-50"
      )}
    >
      <FolderOpen className="h-3.5 w-3.5 mt-0.5 shrink-0 text-[var(--pm-faint)]" />
      <div className="flex-1 min-w-0">
        <button
          type="button"
          className="text-left truncate w-full bg-transparent border-0 p-0 cursor-pointer hover:text-[var(--pm-green)] transition-colors"
          onClick={onNavigate}
          title={path.folder_path || path.folder_id || ""}
          disabled={!path.folder_id}
        >
          {path.folder_path || path.folder_id || "(no folder)"}
        </button>
        <div className="flex items-center gap-1.5 mt-0.5 min-w-0">
          <span
            className="pm-meta truncate"
            title={typeLabel}
          >
            {typeLabel}
          </span>
          {path.is_primary && (
            <Badge variant="outline" className="pm-meta h-4 shrink-0">
              main
            </Badge>
          )}
          {path.is_greyed && (
            <span className="pm-meta text-[var(--pm-danger)] shrink-0">archived</span>
          )}
        </div>
      </div>
      {/* Right column: actions left-aligned with each other across rows */}
      <div className="shrink-0 flex flex-col items-start justify-start pt-0.5 min-w-[7.5rem]">
        {isTimelinePin ? (
          <Button
            size="sm"
            variant="ghost"
            className="pm-ws-action !h-6 justify-start"
            disabled={busy}
            title="Unpin from folder. If a node-derived path exists for the same folder, only the pin is removed."
            onClick={onUnpin}
          >
            <PinOff className="h-3 w-3 mr-0.5" />
            Unpin
          </Button>
        ) : isPersistent ? (
          // Plain folder mount — not a demotable timeline pin
          <span
            className="h-6 px-1.5 pm-meta leading-6 opacity-50"
            title="Folder placement. Use Remove from folder in the footer to unlink."
          >
            —
          </span>
        ) : folderHasPinned ? (
          // Derived sibling: folder already has a real pin — no second Pin action
          <span
            className="h-6 px-1.5 pm-meta leading-6 opacity-50"
            title="This folder is already pinned. Use Unpin on the “Pinned to folder” row."
          >
            —
          </span>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            className="pm-ws-action !h-6 justify-start"
            disabled={busy}
            title="Pin this file to the folder even if the timeline node is removed or archived."
            onClick={onPromote}
          >
            <ArrowUpRight className="h-3 w-3 mr-0.5" />
            Pin to folder
          </Button>
        )}
      </div>
    </li>
  )
}

export function NodeRow({
  node,
  onClick,
}: {
  node: FileNodeRef
  onClick: () => void
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "pm-ws-path-row",
          node.greyed && "opacity-50"
        )}
      >
        <GitBranch className="h-3.5 w-3.5 mt-0.5 shrink-0 text-[var(--pm-faint)]" />
        <div className="flex-1 min-w-0">
          <p className="truncate pm-title">
            {node.title || "Untitled node"}
          </p>
          <p className="pm-meta mt-0.5 truncate">
            {[
              node.group_name || (node.group_id ? "Group" : "No group"),
              node.chain_title || (node.chain_id ? "Chain" : null),
              node.node_type,
            ]
              .filter(Boolean)
              .join(" · ")}
            {node.greyed ? " · greyed" : ""}
          </p>
        </div>
        <ChevronRight className="h-3.5 w-3.5 mt-0.5 shrink-0 text-[var(--pm-faint)]" />
      </button>
    </li>
  )
}
