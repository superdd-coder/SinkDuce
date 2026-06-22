import { useState, useRef, useEffect } from "react"
import { ChevronDown } from "lucide-react"
import { cn } from "@/lib/utils"

interface DropdownSelectProps {
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
  placeholder?: string
  disabled?: boolean
  className?: string
}

export function DropdownSelect({ value, onChange, options, placeholder, disabled, className }: DropdownSelectProps) {
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [])

  const selectedLabel = options.find((o) => o.value === value)?.label || placeholder || "Select..."

  return (
    <div ref={containerRef} className={cn("relative", className)}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(!open)}
        className={cn(
          "flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm",
          "hover:bg-accent/50 transition-colors",
          disabled && "opacity-50 cursor-not-allowed",
        )}
      >
        <span className={value ? "text-foreground" : "text-muted-foreground"}>{selectedLabel}</span>
        <ChevronDown className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200", open && "rotate-180")} />
      </button>

      {open && (
        <div className="absolute z-50 mt-1 w-full rounded-md border bg-popover shadow-md max-h-60 overflow-y-auto">
          {options.map((opt) => (
            <label
              key={opt.value}
              onClick={() => { onChange(opt.value); setOpen(false) }}
              className={cn(
                "flex items-center gap-2 px-3 py-2 text-sm cursor-pointer hover:bg-accent transition-colors",
                opt.value === value && "bg-accent/50",
              )}
            >
              <span className={cn(
                "w-1.5 h-1.5 shrink-0 transition-all",
                opt.value === value ? "bg-primary rotate-45" : "border border-muted-foreground/30 rotate-45",
              )} />
              <span>{opt.label}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}
