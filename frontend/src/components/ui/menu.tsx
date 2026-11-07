import * as React from "react"
import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { cn } from "@/lib/utils"
import { Check } from "lucide-react"
import { placeConnectedMenu, placeFloatingMenu } from "@/lib/menu-position"
import { MENU_DURATION, useMenuPresence } from "@/hooks/use-menu-presence"

/** Open/close duration — keep in sync with CSS `--pm-menu-ms` */
export const MENU_MS = MENU_DURATION
/** Silk exit (export menus etc.) — keep in sync with `.pm-meeting-export-menu` */
export const MENU_SILK_MS = MENU_DURATION

const Menu = React.forwardRef<
  HTMLDivElement,
  React.ComponentProps<"div">
>(function Menu({ className, ...props }, ref) {
  return (
    <div
      ref={ref}
      data-slot="menu"
      role="menu"
      className={cn("pm-menu", className)}
      {...props}
    />
  )
})

/**
 * Soft float menu shell — same motion language as DropdownSelect.
 * Mounts closed → rAF open; on close waits exitMs then unmounts so CSS can finish.
 *
 * `portal` + `anchorRef`: render fixed to document.body so overflow:hidden
 * ancestors (e.g. .pm-fmt-inner) cannot clip secondary menus.
 */
function SoftMenu({
  open,
  className,
  children,
  portal = false,
  anchorRef,
  align = "start",
  /**
   * Portaled placement relative to anchor:
   * - bottom (default): under the anchor
   * - right: flyout to the right of the parent menu (submenu)
   */
  placement = "bottom",
  /**
   * When this value changes while open, recompute fixed position
   * (e.g. submenu moves between collection rows).
   */
  repositionKey,
  /**
   * Explicit fixed coords (viewport). When set, skips anchor-based placement.
   * Use for submenus that already measured the parent menu + row.
   */
  fixedCoords,
  /**
   * Match menu width to the anchor element and left-align under it
   * (e.g. choose-collection pill dropdown).
   */
  matchAnchorWidth = false,
  /** Unmount delay after close — match CSS transition (default MENU_MS). */
  exitMs = MENU_MS,
  style,
  connected = false,
  connectedPanel,
  connectedPanelKey,
  rootWidth = 220,
  childWidth = 260,
  onClose,
  onSubmenuBack,
  autoFocus = false,
  onKeyDown,
  ...props
}: Omit<React.ComponentProps<"div">, "children"> & {
  open: boolean
  children: React.ReactNode
  /** Portal to body with fixed position under anchor (toolbar submenus). */
  portal?: boolean
  anchorRef?: React.RefObject<HTMLElement | null>
  /** Horizontal alignment relative to anchor when portaled (bottom placement). */
  align?: "start" | "center" | "end"
  placement?: "bottom" | "right"
  repositionKey?: string | number | null
  fixedCoords?: { top: number; left: number } | null
  matchAnchorWidth?: boolean
  exitMs?: number
  /** Keep the root's layout stable even when the current row has no child. */
  connected?: boolean
  connectedPanel?: React.ReactNode
  /** Stable category identity; only a category change replays the child reveal. */
  connectedPanelKey?: string | null
  rootWidth?: number
  childWidth?: number
  onClose?: () => void
  onSubmenuBack?: () => void
  autoFocus?: boolean
}) {
  const { mounted, shown } = useMenuPresence(open, exitMs)
  const childPresence = useMenuPresence(open && Boolean(connectedPanel), exitMs)
  const [lastPanel, setLastPanel] = useState({ content: connectedPanel, key: connectedPanelKey })
  // Retain the last child while it fades out; its closing surface is inert.
  if (open && connectedPanel && (lastPanel.content !== connectedPanel || lastPanel.key !== connectedPanelKey)) {
    setLastPanel({ content: connectedPanel, key: connectedPanelKey })
  }
  const visiblePanel = connectedPanel || (childPresence.mounted ? lastPanel.content : null)
  const menuRef = useRef<HTMLDivElement>(null)
  const rootContentRef = useRef<HTMLDivElement>(null)
  const childContentRef = useRef<HTMLDivElement>(null)
  const pendingChildFocus = useRef(false)
  const [coords, setCoords] = useState<{
    top: number
    left: number
    maxHeight?: number
    flip?: "up" | "down"
    side?: "left" | "right"
    maxWidth?: number
    width?: number
    height?: number
    root?: ReturnType<typeof placeConnectedMenu>["root"]
    child?: ReturnType<typeof placeConnectedMenu>["child"]
  } | null>(null)
  const [anchorWidth, setAnchorWidth] = useState<number | null>(null)
  const hasConnectedPanel = Boolean(visiblePanel)
  const isConnectedMenu = connected || connectedPanelKey !== undefined || hasConnectedPanel
  const explicitTop = fixedCoords?.top
  const explicitLeft = fixedCoords?.left

  useLayoutEffect(() => {
    // While closing (open=false), keep last top/left/width so the exit animation
    // does not jump to 0,0 or unstyled flow.
    if (!open || !portal) {
      return
    }
    // Explicit coords win (submenu measured by caller)
    if (explicitTop != null && explicitLeft != null) {
      setCoords({ top: explicitTop, left: explicitLeft })
      setAnchorWidth(null)
      return
    }
    if (!anchorRef?.current) {
      return
    }
    const place = () => {
      const el = anchorRef.current
      if (!el) return
      const r = el.getBoundingClientRect()
      const fly = menuRef.current
      const width = matchAnchorWidth ? Math.max(220, r.width) : fly?.offsetWidth || 260
      const next = isConnectedMenu ? placeConnectedMenu({
        anchor: r, rootWidth: matchAnchorWidth ? Math.max(rootWidth, r.width - 20) : rootWidth,
        rootHeight: (rootContentRef.current?.offsetHeight || 28) + 10,
        childWidth, childHeight: hasConnectedPanel ? (childContentRef.current?.offsetHeight || 28) + 10 : null,
        viewportWidth: window.innerWidth, viewportHeight: window.innerHeight, align,
      }) : placeFloatingMenu({
        anchor: r, width, height: Math.max(fly?.offsetHeight || 0, fly?.scrollHeight || 0, 48),
        viewportWidth: window.innerWidth, viewportHeight: window.innerHeight,
        align, placement,
      })
      setCoords(prev => JSON.stringify(prev) === JSON.stringify(next) ? prev : next)
      const w = matchAnchorWidth ? Math.min(width, next.maxWidth) : null
      setAnchorWidth(prev => prev === w ? prev : w)
    }
    place()
    // Second pass after paint — flyout has real width/height for flip/clamp
    const raf = requestAnimationFrame(() => place())
    const menuEl = menuRef.current
    const ro =
      typeof ResizeObserver !== "undefined" && menuEl
        ? new ResizeObserver(() => place())
        : null
    if (menuEl && ro) ro.observe(menuEl)
    if (rootContentRef.current && ro) ro.observe(rootContentRef.current)
    if (childContentRef.current && ro) ro.observe(childContentRef.current)
    // Sidebar anchors resize while the icon rail expands; keep its flyout attached.
    if (anchorRef.current && ro) ro.observe(anchorRef.current)
    window.addEventListener("scroll", place, true)
    window.addEventListener("resize", place)
    return () => {
      cancelAnimationFrame(raf)
      ro?.disconnect()
      window.removeEventListener("scroll", place, true)
      window.removeEventListener("resize", place)
    }
  }, [
    open,
    portal,
    anchorRef,
    align,
    placement,
    mounted,
    repositionKey,
    explicitTop,
    explicitLeft,
    matchAnchorWidth,
    isConnectedMenu,
    hasConnectedPanel,
    connectedPanelKey,
    rootWidth,
    childWidth,
  ])

  useEffect(() => {
    if (!open || !onClose) return
    const outside = (event: PointerEvent) => {
      const target = event.target as Node
      if (!menuRef.current?.contains(target) && !anchorRef?.current?.contains(target)) onClose()
    }
    document.addEventListener("pointerdown", outside)
    return () => document.removeEventListener("pointerdown", outside)
  }, [open, onClose, anchorRef])

  useEffect(() => {
    if (!open || !mounted || !autoFocus) return
    const frame = requestAnimationFrame(() => {
      const menu = menuRef.current
      const first = menu?.querySelector<HTMLElement>('input:not(:disabled)')
        || menu?.querySelector<HTMLElement>('[aria-selected="true"]:not(:disabled), [aria-checked="true"]:not(:disabled)')
        || menu?.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled), [role="option"]:not(:disabled), [role="menuitemradio"]:not(:disabled), [role="menuitemcheckbox"]:not(:disabled)')
      first?.focus({ preventScroll: true })
      first?.scrollIntoView({ block: 'nearest' })
    })
    return () => cancelAnimationFrame(frame)
  }, [open, mounted, autoFocus])

  useEffect(() => {
    if (!open) { pendingChildFocus.current = false; return }
    if (!connectedPanel || !pendingChildFocus.current) return
    const first = menuRef.current?.querySelector<HTMLElement>('.sd-menu-child [role="menuitemradio"]:not(:disabled), .sd-menu-child [role="menuitem"]:not(:disabled)')
    if (first) { first.focus({ preventScroll: true }); pendingChildFocus.current = false }
  }, [open, connectedPanel])

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    onKeyDown?.(event)
    if (event.defaultPrevented || event.nativeEvent.isComposing) return
    const target = event.target as HTMLElement
    if (event.key === "Escape" && onClose) {
      event.preventDefault(); event.stopPropagation()
      if (connectedPanel && onSubmenuBack) {
        menuRef.current?.querySelector<HTMLElement>('[data-submenu-trigger][aria-expanded="true"]')?.focus()
        onSubmenuBack()
      } else {
        onClose()
        anchorRef?.current?.focus({ preventScroll: true })
      }
      return
    }
    const child = target.closest('.sd-menu-child')
    const openKey = coords?.side === "left" ? "ArrowLeft" : "ArrowRight"
    const backKey = openKey === "ArrowLeft" ? "ArrowRight" : "ArrowLeft"
    if (event.key === backKey && child) {
      event.preventDefault()
      menuRef.current?.querySelector<HTMLElement>('[data-submenu-trigger][aria-expanded="true"]')?.focus()
      return
    }
    if (event.key === openKey && target.closest('[data-submenu-trigger]')) {
      event.preventDefault()
      pendingChildFocus.current = true
      if (target.getAttribute('aria-expanded') !== 'true') target.click()
      requestAnimationFrame(() => {
        const first = menuRef.current?.querySelector<HTMLElement>('.sd-menu-child [role="menuitemradio"]:not(:disabled), .sd-menu-child [role="menuitem"]:not(:disabled)')
        if (first) { first.focus({ preventScroll: true }); pendingChildFocus.current = false }
      })
      return
    }
    if (event.key === "Tab" && onClose && event.currentTarget.getAttribute("role") === "menu") { onClose(); return }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
    if (target.matches('input, textarea, [contenteditable="true"]') && ['Home', 'End'].includes(event.key)) return
    const scope = target.closest('[data-menu-list]') || menuRef.current
    const items = Array.from(scope?.querySelectorAll<HTMLElement>('[role="menuitem"], [role="menuitemradio"], [role="menuitemcheckbox"], [role="option"]') || [])
      .filter(item => !item.matches(':disabled, [aria-disabled="true"], [data-disabled]') && !item.closest('[inert]') && item.getClientRects().length)
    if (!items.length) return
    event.preventDefault()
    const current = items.indexOf(target)
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
      : current === -1 ? (event.key === 'ArrowUp' ? items.length - 1 : 0)
      : (current + (event.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length
    items[next].focus({ preventScroll: true }); items[next].scrollIntoView({ block: 'nearest' })
  }

  if (!mounted) return null

  const resolved: typeof coords = fixedCoords ?? coords
  // CSS `.pm-menu--portal` is position:fixed. Without top/left/width a
  // block-level fixed node fills the viewport (white screen) until measured.
  const portalStyle: React.CSSProperties | undefined = portal
    ? resolved
      ? {
          position: "fixed",
          top: resolved.top,
          left: resolved.left,
          zIndex: placement === "right" ? 420 : 400,
          margin: 0,
          maxWidth: resolved.maxWidth != null ? resolved.maxWidth : "calc(100vw - 24px)",
          maxHeight: resolved.maxHeight != null ? resolved.maxHeight : "calc(100dvh - 24px)",
          ...("maxHeight" in resolved && resolved.maxHeight
            ? {
                ["--pm-menu-viewport-max" as string]: `${resolved.maxHeight}px`,
              }
            : null),
          ...(matchAnchorWidth && anchorWidth
            ? {
                width: anchorWidth,
                minWidth: anchorWidth,
                maxWidth: anchorWidth,
                boxSizing: "border-box" as const,
              }
            : null),
          ...(resolved.root ? {
            width: resolved.width, minWidth: resolved.width, maxWidth: resolved.width,
            height: resolved.height,
          } : null),
        }
      : {
          position: "fixed",
          top: 0,
          left: 0,
          visibility: "hidden",
          pointerEvents: "none",
          width: "max-content",
        }
    : undefined

  const menu = (
    <Menu
      ref={menuRef}
      inert={!open || undefined}
      aria-hidden={!open || undefined}
      onKeyDown={handleKeyDown}
      data-menu-side={coords?.side}
      data-menu-portal={portal ? "true" : undefined}
      data-menu-align={
        portal ? (matchAnchorWidth ? "start" : align) : undefined
      }
      data-menu-placement={portal ? placement : undefined}
      data-menu-flip={
        portal &&
        placement === "bottom" &&
        resolved &&
        "flip" in resolved &&
        resolved.flip
          ? resolved.flip
          : undefined
      }
      data-menu-match-width={matchAnchorWidth ? "true" : undefined}
      className={cn(
        "pm-menu--soft",
        shown && "is-open",
        portal && "pm-menu--portal",
        portal && matchAnchorWidth && "is-match-width",
        portal && placement === "right" && "is-placement-right",
        isConnectedMenu && "sd-menu-connected",
        hasConnectedPanel && "has-child",
        className
      )}
      style={{
        ...portalStyle,
        // Keep CSS transition duration in lockstep with unmount delay
        ...( {
          ["--pm-menu-ms" as string]: `${exitMs}ms`,
          ["--sd-menu-root-width" as string]: `${rootWidth}px`,
          ["--sd-menu-child-width" as string]: `${childWidth}px`,
        } as React.CSSProperties),
        ...(style as React.CSSProperties | undefined),
      }}
      {...props}
    >
      {isConnectedMenu ? <>
        <div className="sd-menu-root" data-menu-list style={coords?.root}>
          <div ref={rootContentRef}>{children}</div>
        </div>
        {hasConnectedPanel && <div className={cn("sd-menu-child", childPresence.shown && "is-open")} data-menu-list
          style={coords?.child ?? undefined}
          inert={!open || !connectedPanel || undefined} aria-hidden={!open || !connectedPanel || undefined}>
          <div ref={childContentRef} key={connectedPanel ? connectedPanelKey : lastPanel.key} className="sd-menu-child-content">{visiblePanel}</div>
        </div>}
      </> : children}
    </Menu>
  )

  if (portal && typeof document !== "undefined") {
    return createPortal(menu, document.body)
  }
  return menu
}

function MenuItem({
  className,
  destructive,
  active,
  ...props
}: React.ComponentProps<"button"> & {
  destructive?: boolean
  active?: boolean
}) {
  return (
    <button
      type="button"
      data-slot="menu-item"
      role="menuitem"
      className={cn(
        "pm-menu-item",
        active && "is-on",
        destructive && "is-danger",
        className
      )}
      {...props}
    />
  )
}

function MenuItemTitle({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="menu-item-title"
      className={cn(
        "block text-[13px] font-normal text-[var(--pm-ink)]",
        className
      )}
      {...props}
    />
  )
}

function MenuItemDescription({
  className,
  ...props
}: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="menu-item-description"
      className={cn(
        "block mt-0.5 text-[11px] font-normal leading-snug text-[var(--pm-faint)]",
        className
      )}
      {...props}
    />
  )
}

function MenuCheck({ checked = true }: { checked?: boolean }) {
  return <span className="sd-menu-check" data-checked={checked || undefined} aria-hidden="true">{checked && <Check size={13} strokeWidth={2.2} />}</span>
}

function MenuSeparator({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="menu-separator"
      role="separator"
      className={cn(
        "my-1 h-px bg-[color-mix(in_srgb,var(--pm-ink)_8%,transparent)]",
        className
      )}
      {...props}
    />
  )
}

export {
  Menu,
  SoftMenu,
  MenuItem,
  MenuItemTitle,
  MenuItemDescription,
  MenuSeparator,
  MenuCheck,
}
