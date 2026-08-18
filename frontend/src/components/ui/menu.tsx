import * as React from "react"
import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { cn } from "@/lib/utils"

/** Open/close duration — keep in sync with CSS `--pm-menu-ms` */
export const MENU_MS = 180
/** Silk exit (export menus etc.) — keep in sync with `.pm-meeting-export-menu` */
export const MENU_SILK_MS = 280

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
}) {
  const [mounted, setMounted] = useState(false)
  const [shown, setShown] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const [coords, setCoords] = useState<{
    top: number
    left: number
    maxHeight?: number
    flip?: "up" | "down"
  } | null>(null)
  const [anchorWidth, setAnchorWidth] = useState<number | null>(null)

  useEffect(() => {
    let exitTimer: ReturnType<typeof setTimeout> | null = null
    let raf1 = 0
    let raf2 = 0

    if (open) {
      setMounted(true)
      // Double rAF: mount at closed style, then paint, then open (symmetric silk)
      raf1 = requestAnimationFrame(() => {
        raf2 = requestAnimationFrame(() => {
          setShown(true)
        })
      })
    } else {
      // Keep mounted + last coords so CSS can play close transition
      setShown(false)
      exitTimer = setTimeout(() => {
        setMounted(false)
      }, exitMs)
    }

    return () => {
      if (exitTimer) clearTimeout(exitTimer)
      if (raf1) cancelAnimationFrame(raf1)
      if (raf2) cancelAnimationFrame(raf2)
    }
  }, [open, exitMs])

  useLayoutEffect(() => {
    // While closing (open=false), keep last top/left/width so the exit animation
    // does not jump to 0,0 or unstyled flow.
    if (!open || !portal) {
      return
    }
    // Explicit coords win (submenu measured by caller)
    if (fixedCoords) {
      setCoords(fixedCoords)
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
      const gap = 6
      if (placement === "right") {
        // Prefer the parent SoftMenu's right edge (full column), not just the row.
        const parentMenu = el.closest(
          '[data-slot="menu"]',
        ) as HTMLElement | null
        const pr = parentMenu?.getBoundingClientRect() ?? r
        const fly = menuRef.current
        const mw = fly?.offsetWidth || 200
        const mh = fly?.offsetHeight || 200

        let left = pr.right + gap
        // Align top with hovered row; keep inside viewport
        let top = r.top
        if (left + mw > window.innerWidth - 8) {
          // Flip to left of parent menu
          left = Math.max(8, pr.left - mw - gap)
        }
        if (top + mh > window.innerHeight - 8) {
          top = Math.max(8, window.innerHeight - mh - 8)
        }
        if (top < 8) top = 8
        setCoords({ top, left })
        setAnchorWidth(null)
        return
      }
      // Match pill width + left-align under trigger (no translateX(-100%))
      if (matchAnchorWidth) {
        let top = r.bottom + gap
        const mh = menuRef.current?.offsetHeight || 320
        if (top + mh > window.innerHeight - 8) {
          // Prefer drop-up if not enough room below
          const up = r.top - gap - mh
          if (up >= 8) top = up
          else top = Math.max(8, window.innerHeight - mh - 8)
        }
        setCoords({ top, left: r.left })
        setAnchorWidth(Math.round(r.width))
        return
      }
      const fly = menuRef.current
      const pad = 8
      const vw = window.innerWidth
      const vh = window.innerHeight
      const mw = fly?.offsetWidth || 288
      const mh = fly?.offsetHeight || 320
      let left = r.left
      if (align === "center") left = r.left + r.width / 2
      else if (align === "end") left = r.right
      else {
        left = Math.min(Math.max(pad, left), Math.max(pad, vw - pad - mw))
      }

      const spaceBelow = vh - pad - (r.bottom + gap)
      const spaceAbove = r.top - gap - pad
      const flip: "up" | "down" =
        mh > spaceBelow && spaceAbove > spaceBelow ? "up" : "down"
      const side = flip === "up" ? spaceAbove : spaceBelow
      const maxBox = Math.max(
        160,
        Math.min(vh - pad * 2, side > 0 ? side : vh - pad * 2),
      )
      const used = Math.min(mh, maxBox)
      let top = flip === "up" ? r.top - gap - used : r.bottom + gap
      if (top < pad) top = pad
      if (top + used > vh - pad) top = Math.max(pad, vh - pad - used)
      setCoords({
        top,
        left,
        maxHeight: Math.max(160, Math.min(maxBox, vh - pad - top)),
        flip,
      })
      setAnchorWidth(null)
    }
    place()
    // Second pass after paint — flyout has real width/height for flip/clamp
    const raf = requestAnimationFrame(() => place())
    window.addEventListener("scroll", place, true)
    window.addEventListener("resize", place)
    return () => {
      cancelAnimationFrame(raf)
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
    fixedCoords?.top,
    fixedCoords?.left,
    matchAnchorWidth,
    children,
  ])

  if (!mounted) return null

  const resolved = fixedCoords ?? coords
  const portalStyle: React.CSSProperties | undefined =
    portal && resolved
      ? {
          position: "fixed",
          top: resolved.top,
          left: resolved.left,
          zIndex: placement === "right" ? 420 : 400,
          margin: 0,
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
        }
      : undefined

  const menu = (
    <Menu
      ref={menuRef}
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
        portal &&
          placement === "bottom" &&
          !matchAnchorWidth &&
          align === "center" &&
          "is-align-center",
        portal &&
          placement === "bottom" &&
          !matchAnchorWidth &&
          align === "end" &&
          "is-align-end",
        portal && matchAnchorWidth && "is-match-width",
        portal && placement === "right" && "is-placement-right",
        className
      )}
      style={{
        ...portalStyle,
        // Keep CSS transition duration in lockstep with unmount delay
        ...( {
          ["--pm-menu-ms" as string]: `${exitMs}ms`,
        } as React.CSSProperties),
        ...(style as React.CSSProperties | undefined),
      }}
      {...props}
    >
      {children}
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
}
