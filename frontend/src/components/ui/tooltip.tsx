"use client"

import { Tooltip as TooltipPrimitive } from "@base-ui/react/tooltip"

import { cn } from "@/lib/utils"

function TooltipProvider({
  delay = 0,
  closeDelay = 0,
  ...props
}: TooltipPrimitive.Provider.Props) {
  return (
    <TooltipPrimitive.Provider
      data-slot="tooltip-provider"
      delay={delay}
      closeDelay={closeDelay}
      {...props}
    />
  )
}

function Tooltip({ ...props }: TooltipPrimitive.Root.Props) {
  return <TooltipPrimitive.Root data-slot="tooltip" {...props} />
}

function TooltipTrigger({ ...props }: TooltipPrimitive.Trigger.Props) {
  return <TooltipPrimitive.Trigger data-slot="tooltip-trigger" {...props} />
}

function TooltipContent({
  className,
  side = "top",
  sideOffset = 4,
  align = "center",
  alignOffset = 0,
  children,
  showArrow = true,
  positionerClassName,
  ...props
}: TooltipPrimitive.Popup.Props &
  Pick<
    TooltipPrimitive.Positioner.Props,
    "align" | "alignOffset" | "side" | "sideOffset"
  > & {
    /** Small rotated square under the bubble (default true). */
    showArrow?: boolean
    positionerClassName?: string
  }) {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Positioner
        align={align}
        alignOffset={alignOffset}
        side={side}
        sideOffset={sideOffset}
        className={cn("isolate z-50", positionerClassName)}
      >
        <TooltipPrimitive.Popup
          data-slot="tooltip-content"
          className={cn(
            "z-50 inline-flex w-fit max-w-xs origin-(--transform-origin) items-center gap-1.5",
            "rounded-[var(--pm-r-sm)] px-2.5 py-1.5",
            "bg-[var(--pm-ink)] text-[var(--pm-on)]",
            "font-[family-name:var(--pm-ff)] text-[11px] font-normal leading-[1.35] tracking-[0.02em]",
            "shadow-[var(--pm-shadow-sm)]",
            "data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2",
            "data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2",
            "data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95",
            "data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95",
            className
          )}
          {...props}
        >
          {children}
          {showArrow ? (
            <TooltipPrimitive.Arrow
              className={cn(
                "z-50 size-2.5 translate-y-[calc(-50%-2px)] rotate-45 rounded-[2px]",
                "bg-[var(--pm-ink)] fill-[var(--pm-ink)]",
                "data-[side=bottom]:top-1 data-[side=top]:-bottom-2.5",
                "data-[side=left]:top-1/2! data-[side=left]:-right-1 data-[side=left]:-translate-y-1/2",
                "data-[side=right]:top-1/2! data-[side=right]:-left-1 data-[side=right]:-translate-y-1/2"
              )}
            />
          ) : null}
        </TooltipPrimitive.Popup>
      </TooltipPrimitive.Positioner>
    </TooltipPrimitive.Portal>
  )
}

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider }
