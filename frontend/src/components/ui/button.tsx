import { Button as ButtonPrimitive } from "@base-ui/react/button"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

/**
 * Premium Button — single design language.
 *
 * Tailwind arbitrary values CANNOT contain unescaped commas.
 * Broken: bg-[rgba(18,20,16,0.05)]  bg-[var(--x,#hex)]
 * Use:    color-mix(...) or var(--pm-*) only.
 */
const buttonVariants = cva(
  [
    "group/button inline-flex shrink-0 items-center justify-center gap-1.5",
    "whitespace-nowrap select-none outline-none",
    "font-[family-name:var(--pm-ff)] text-[11px] font-normal leading-[1.3] tracking-[0.04em]",
    "rounded-[var(--pm-r-sm)] border border-transparent",
    "transition-[background-color,color,box-shadow,transform,opacity]",
    "duration-150 ease-[cubic-bezier(0.22,1,0.36,1)]",
    "disabled:pointer-events-none disabled:opacity-50",
    "focus-visible:ring-2 focus-visible:ring-[color-mix(in_srgb,var(--pm-green)_30%,transparent)]",
    "focus-visible:ring-offset-0",
    "active:not-aria-[haspopup]:translate-y-px",
    "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3.5",
  ].join(" "),
  {
    variants: {
      variant: {
        /* Solid fills: tiny contact shadow only — never card-level --pm-shadow-sm
         * (multi-layer soft float looks like a second pill behind the button). */
        default: [
          "bg-[var(--pm-green)] text-[var(--pm-on)] border-transparent",
          "shadow-none",
          "hover:bg-[var(--pm-green-deep)]",
        ].join(" "),
        ghost: [
          "bg-[color-mix(in_srgb,var(--pm-ink)_5%,transparent)] text-[var(--pm-text)]",
          "shadow-none",
          "hover:bg-[color-mix(in_srgb,var(--pm-ink)_8%,transparent)] hover:text-[var(--pm-ink)]",
        ].join(" "),
        outline: [
          "border-[color-mix(in_srgb,var(--pm-ink)_10%,transparent)] bg-white text-[var(--pm-text)]",
          "shadow-none",
          "hover:bg-[var(--pm-green-wash)] hover:text-[var(--pm-green)]",
        ].join(" "),
        secondary: [
          "bg-[var(--pm-green-wash)] text-[var(--pm-green)]",
          "shadow-none",
          "hover:bg-[var(--pm-green-soft)]",
        ].join(" "),
        destructive: [
          "bg-[color-mix(in_srgb,var(--pm-danger)_10%,transparent)] text-[var(--pm-danger)]",
          "shadow-none",
          "hover:bg-[color-mix(in_srgb,var(--pm-danger)_16%,transparent)]",
        ].join(" "),
        "destructive-solid": [
          "bg-[var(--pm-danger)] text-white border-transparent",
          "shadow-none",
          "hover:brightness-95",
        ].join(" "),
        link: [
          "h-auto border-0 bg-transparent px-0 shadow-none",
          "text-[var(--pm-green)] underline-offset-4 hover:underline",
        ].join(" "),
      },
      size: {
        default: "h-8 px-3",
        sm: "h-7 px-2.5",
        xs: "h-7 gap-1.5 px-3 text-[11px] [&_svg:not([class*='size-'])]:size-3.5",
        lg: "h-9 px-4",
        icon: "size-8 p-0",
        "icon-sm": "size-7 p-0",
        "icon-xs": "size-6 p-0 [&_svg:not([class*='size-'])]:size-3",
        "icon-lg": "size-9 p-0",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonPrimitive.Props & VariantProps<typeof buttonVariants>) {
  return (
    <ButtonPrimitive
      data-slot="button"
      data-variant={variant}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  )
}

export { Button, buttonVariants }
