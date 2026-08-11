import * as React from "react"
import { Input as InputPrimitive } from "@base-ui/react/input"

import { cn } from "@/lib/utils"

/**
 * Premium Input — white nested field, soft border, green focus ring.
 * Avoid commas inside Tailwind arbitrary values (breaks CSS generation).
 */
function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <InputPrimitive
      type={type}
      data-slot="input"
      className={cn(
        "h-8 w-full min-w-0 box-border",
        "rounded-[var(--pm-r-sm)]",
        "border border-[color-mix(in_srgb,var(--pm-ink)_8%,transparent)]",
        "bg-white px-3 py-1.5",
        "font-[family-name:var(--pm-ff)] text-[13px] font-normal leading-normal",
        "text-[var(--pm-text)] placeholder:text-[var(--pm-faint)] placeholder:font-normal",
        "outline-none transition-[border-color,box-shadow] duration-150",
        "ease-[cubic-bezier(0.22,1,0.36,1)]",
        "focus-visible:border-[color-mix(in_srgb,var(--pm-green)_42%,transparent)]",
        "focus-visible:shadow-[0_0_0_3px_var(--pm-green-wash)]",
        "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
        "file:inline-flex file:h-6 file:border-0 file:bg-transparent",
        "file:text-[11px] file:font-normal file:text-[var(--pm-muted)]",
        "aria-invalid:border-[var(--pm-danger)]",
        "aria-invalid:shadow-[0_0_0_3px_color-mix(in_srgb,var(--pm-danger)_12%,transparent)]",
        className
      )}
      {...props}
    />
  )
}

export { Input }
