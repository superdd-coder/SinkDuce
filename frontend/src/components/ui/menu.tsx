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
  /** Horizontal alignment relative to anchor when portaled */
  align?: "start" | "center" | "end"
  exitMs?: number
}) {
  const [mounted, setMounted] = useState(false)
  const [shown, setShown] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(
    null
  )

  useEffect(() => {
    let exitTimer: ReturnType<typeof setTimeout> | null = null
    let raf1 = 0
    let raf2 = 0

    if (open) {
      setMounted(true)
      raf1 = requestAnimationFrame(() => {
        raf2 = requestAnimationFrame(() => {
          setShown(true)
        })
      })
    } else {
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
    if (!open || !portal || !anchorRef?.current) {
      if (!open) setCoords(null)
      return
    }
    const place = () => {
      const el = anchorRef.current
      if (!el) return
      const r = el.getBoundingClientRect()
      let left = r.left
      if (align === "center") left = r.left + r.width / 2
      else if (align === "end") left = r.right
      setCoords({ top: r.bottom + 6, left })
    }
    place()
    window.addEventListener("scroll", place, true)
    window.addEventListener("resize", place)
    return () => {
      window.removeEventListener("scroll", place, true)
      window.removeEventListener("resize", place)
    }
  }, [open, portal, anchorRef, align, mounted])

  if (!mounted) return null

  const portalStyle: React.CSSProperties | undefined =
    portal && coords
      ? {
          position: "fixed",
          top: coords.top,
          left: coords.left,
          zIndex: 400,
          margin: 0,
        }
      : undefined

  const menu = (
    <Menu
      ref={menuRef}
      data-menu-portal={portal ? "true" : undefined}
      data-menu-align={portal ? align : undefined}
      className={cn(
        "pm-menu--soft",
        shown && "is-open",
        portal && "pm-menu--portal",
        portal && align === "center" && "is-align-center",
        portal && align === "end" && "is-align-end",
        className
      )}
      style={{
        ...portalStyle,
        ...(exitMs !== MENU_MS
          ? ({ ["--pm-menu-ms" as string]: `${exitMs}ms` } as React.CSSProperties)
          : null),
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
