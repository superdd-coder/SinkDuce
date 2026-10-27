import { useState, type ReactNode } from "react"
import { Info } from "lucide-react"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useT } from "@/i18n/use-t"
import { cn } from "@/lib/utils"
import "./help-hint.css"

/** Supplementary guidance, available on hover, keyboard focus and touch. */
export function HelpHint({ children, label, className }: { children: ReactNode; label?: string; className?: string }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  return (
    <Tooltip open={open} onOpenChange={setOpen}>
      <TooltipTrigger
        type="button"
        aria-label={label ?? t("common.moreInfo")}
        className={cn("pm-help-hint", className)}
        onClick={(event) => { event.stopPropagation(); setOpen(true) }}
      >
        <Info size={13} strokeWidth={1.65} aria-hidden="true" />
      </TooltipTrigger>
      <TooltipContent side="top" positionerClassName="z-[600]" className="max-w-[320px] text-xs leading-relaxed normal-case tracking-normal">
        {children}
      </TooltipContent>
    </Tooltip>
  )
}
