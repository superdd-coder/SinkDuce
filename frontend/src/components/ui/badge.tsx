import { mergeProps } from "@base-ui/react/merge-props"
import { useRender } from "@base-ui/react/use-render"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  [
    "group/badge inline-flex w-fit shrink-0 items-center justify-center gap-1",
    "overflow-hidden whitespace-nowrap border border-transparent",
    "rounded-full px-2 py-0.5",
    "font-[family-name:var(--pm-ff)] text-[11px] font-normal leading-[1.35] tracking-[0.02em]",
    "transition-colors duration-150",
    "[&>svg]:pointer-events-none [&>svg]:size-3",
  ].join(" "),
  {
    variants: {
      variant: {
        default: "bg-[var(--pm-green-soft)] text-[var(--pm-green)]",
        secondary:
          "bg-[color-mix(in_srgb,var(--pm-ink)_5%,transparent)] text-[var(--pm-muted)]",
        outline:
          "border-[color-mix(in_srgb,var(--pm-ink)_12%,transparent)] bg-transparent text-[var(--pm-muted)]",
        destructive:
          "bg-[color-mix(in_srgb,var(--pm-danger)_10%,transparent)] text-[var(--pm-danger)]",
        ghost: "bg-transparent text-[var(--pm-faint)]",
        live: "bg-[var(--pm-green-soft)] text-[var(--pm-green)]",
      },
    },
    defaultVariants: {
      variant: "secondary",
    },
  }
)

function Badge({
  className,
  variant = "secondary",
  render,
  ...props
}: useRender.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return useRender({
    defaultTagName: "span",
    props: mergeProps<"span">(
      {
        className: cn(badgeVariants({ variant }), className),
      },
      props
    ),
    render,
    state: {
      slot: "badge",
      variant,
    },
  })
}

export { Badge, badgeVariants }
