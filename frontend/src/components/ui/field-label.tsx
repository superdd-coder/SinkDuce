import * as React from "react"
import { cn } from "@/lib/utils"

/**
 * Premium field label — Geist label role, uppercase faint.
 * Use above Input / DropdownSelect / Textarea.
 */
function FieldLabel({
  className,
  ...props
}: React.ComponentProps<"label">) {
  return (
    <label
      data-slot="field-label"
      className={cn("pm-field-label", className)}
      {...props}
    />
  )
}

export { FieldLabel }
