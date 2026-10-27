import { Tabs as TabsPrimitive } from "@base-ui/react/tabs"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

function Tabs({
  className,
  orientation = "horizontal",
  ...props
}: TabsPrimitive.Root.Props) {
  return (
    <TabsPrimitive.Root
      data-slot="tabs"
      data-orientation={orientation}
      className={cn(
        "group/tabs flex gap-2 data-horizontal:flex-col",
        className
      )}
      {...props}
    />
  )
}

const tabsListVariants = cva(
  "group/tabs-list inline-flex w-fit items-center justify-center text-[var(--pm-muted)] group-data-vertical/tabs:h-fit group-data-vertical/tabs:flex-col",
  {
    variants: {
      variant: {
        pill: [
          "pm-tabs relative isolate !h-auto items-center gap-1 border-0 bg-transparent p-0 rounded-none",
        ].join(" "),
        line: "gap-1 bg-transparent rounded-none h-auto p-0",
        muted:
          "h-8 gap-0.5 rounded-[var(--pm-r-sm)] bg-[color-mix(in_srgb,var(--pm-ink)_5%,transparent)] p-[3px]",
        default: [
          "pm-tabs relative isolate !h-auto items-center gap-1 border-0 bg-transparent p-0 rounded-none",
        ].join(" "),
      },
    },
    defaultVariants: {
      variant: "pill",
    },
  }
)

function TabsList({
  className,
  variant = "pill",
  ...props
}: TabsPrimitive.List.Props & VariantProps<typeof tabsListVariants>) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      data-variant={variant === "default" ? "pill" : variant}
      className={cn(tabsListVariants({ variant }), className)}
      {...props}
    />
  )
}

function TabsTrigger({ className, ...props }: TabsPrimitive.Tab.Props) {
  return (
    <TabsPrimitive.Tab
      data-slot="tabs-trigger"
      className={cn(
        "relative z-[1] inline-flex items-center justify-center gap-1.5",
        "whitespace-nowrap outline-none select-none",
        "font-[family-name:var(--pm-ff)] text-[11px] font-normal leading-[1.3] tracking-[0.04em]",
        "transition-colors duration-180 ease-[cubic-bezier(0.22,1,0.36,1)]",
        "disabled:pointer-events-none disabled:opacity-40",
        "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3.5",
        "after:!opacity-0 after:!content-none after:!bg-transparent",
        "group-data-[variant=pill]/tabs-list:rounded-full group-data-[variant=pill]/tabs-list:px-3 group-data-[variant=pill]/tabs-list:py-1.5",
        "group-data-[variant=pill]/tabs-list:bg-transparent group-data-[variant=pill]/tabs-list:text-[var(--pm-muted)]",
        "group-data-[variant=pill]/tabs-list:hover:text-[var(--pm-green)]",
        "group-data-[variant=pill]/tabs-list:data-active:bg-transparent group-data-[variant=pill]/tabs-list:data-active:text-[var(--pm-green)]",
        "group-data-[variant=pill]/tabs-list:data-active:shadow-none",
        "group-data-[variant=default]/tabs-list:rounded-full group-data-[variant=default]/tabs-list:px-3 group-data-[variant=default]/tabs-list:py-1.5",
        "group-data-[variant=default]/tabs-list:bg-transparent group-data-[variant=default]/tabs-list:text-[var(--pm-muted)]",
        "group-data-[variant=default]/tabs-list:hover:text-[var(--pm-green)]",
        "group-data-[variant=default]/tabs-list:data-active:bg-transparent group-data-[variant=default]/tabs-list:data-active:text-[var(--pm-green)]",
        "group-data-[variant=line]/tabs-list:rounded-none group-data-[variant=line]/tabs-list:px-2 group-data-[variant=line]/tabs-list:py-1",
        "group-data-[variant=line]/tabs-list:text-[var(--pm-muted)]",
        "group-data-[variant=line]/tabs-list:data-active:text-[var(--pm-green)]",
        "group-data-[variant=muted]/tabs-list:rounded-[6px] group-data-[variant=muted]/tabs-list:px-2.5 group-data-[variant=muted]/tabs-list:py-1",
        "group-data-[variant=muted]/tabs-list:data-active:bg-white group-data-[variant=muted]/tabs-list:data-active:text-[var(--pm-green)]",
        "group-data-[variant=muted]/tabs-list:data-active:shadow-sm",
        className
      )}
      {...props}
    />
  )
}

function TabsContent({ className, ...props }: TabsPrimitive.Panel.Props) {
  return (
    <TabsPrimitive.Panel
      data-slot="tabs-content"
      className={cn("flex-1 outline-none", className)}
      {...props}
    />
  )
}

function TabsIndicator({ className, style, ...props }: TabsPrimitive.Indicator.Props) {
  return (
    <TabsPrimitive.Indicator
      data-slot="tabs-indicator"
      className={cn(
        "pointer-events-none absolute z-0 pm-tabs-indicator",
        "transition-[left,width,top,height] duration-300",
        "ease-[cubic-bezier(0.22,1,0.36,1)] will-change-[left,width,top,height]",
        className
      )}
      style={{
        left: "var(--active-tab-left)",
        width: "var(--active-tab-width)",
        ...style,
      }}
      {...props}
    />
  )
}

export { Tabs, TabsList, TabsTrigger, TabsIndicator, TabsContent, tabsListVariants }
