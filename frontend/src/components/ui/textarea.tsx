import * as React from "react"

import { cn } from "@/lib/utils"

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex field-sizing-content min-h-16 w-full box-border",
        "rounded-[var(--pm-r-sm)]",
        "border border-[color-mix(in_srgb,var(--pm-ink)_8%,transparent)]",
        "bg-white px-3 py-2",
        "font-[family-name:var(--pm-ff)] text-[13px] font-normal leading-normal",
        "text-[var(--pm-text)] placeholder:text-[var(--pm-faint)]",
        "outline-none transition-[border-color,box-shadow] duration-150",
        "ease-[cubic-bezier(0.22,1,0.36,1)]",
        "focus-visible:border-[color-mix(in_srgb,var(--pm-green)_42%,transparent)]",
        "focus-visible:shadow-[0_0_0_3px_var(--pm-green-wash)]",
        "disabled:cursor-not-allowed disabled:opacity-50",
        "aria-invalid:border-[var(--pm-danger)]",
        className
      )}
      {...props}
    />
  )
}

export { Textarea }
