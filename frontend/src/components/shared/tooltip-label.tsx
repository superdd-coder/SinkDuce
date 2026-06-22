import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip"
import { Info } from "lucide-react"

export function TooltipLabel({ label, tooltip, className }: { label: string; tooltip: string; className?: string }) {
  return (
    <label className={className ?? "text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground inline-flex items-center gap-1"}>
      {label}
      <Tooltip>
        <TooltipTrigger className="cursor-help inline-flex">
          <Info className="h-3 w-3" />
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs">
          {tooltip}
        </TooltipContent>
      </Tooltip>
    </label>
  )
}
