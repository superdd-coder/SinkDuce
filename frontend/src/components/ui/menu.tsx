import * as React from "react"
import { useEffect, useState } from "react"
import { cn } from "@/lib/utils"

/** Open/close duration — keep in sync with CSS `--pm-menu-ms` */
export const MENU_MS = 180

function Menu({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="menu"
      role="menu"
      className={cn("pm-menu", className)}
      {...props}
    />
  )
}

/**
 * Soft float menu shell — same motion language as DropdownSelect.
 * Mounts closed → rAF open; on close waits MENU_MS then unmounts.
 */
function SoftMenu({
  open,
  className,
  children,
  ...props
}: Omit<React.ComponentProps<"div">, "children"> & {
  open: boolean
  children: React.ReactNode
}) {
  const [mounted, setMounted] = useState(false)
  const [shown, setShown] = useState(false)

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
      }, MENU_MS)
    }

    return () => {
      if (exitTimer) clearTimeout(exitTimer)
      if (raf1) cancelAnimationFrame(raf1)
      if (raf2) cancelAnimationFrame(raf2)
    }
  }, [open])

  if (!mounted) return null

  return (
    <Menu
      className={cn("pm-menu--soft", shown && "is-open", className)}
      {...props}
    >
      {children}
    </Menu>
  )
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
