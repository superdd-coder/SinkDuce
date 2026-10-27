import { HelpHint } from "./help-hint"

export function TooltipLabel({ label, tooltip, className }: { label: string; tooltip: string; className?: string }) {
  return (
    <label className={className ?? "text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground inline-flex items-center gap-1"}>
      {label}
      <HelpHint label={label}>{tooltip}</HelpHint>
    </label>
  )
}
