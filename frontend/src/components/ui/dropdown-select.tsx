import { useId, useRef, useState, type ReactNode } from "react"
import { ChevronDown, Search, X } from "lucide-react"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"
import { SoftMenu, MenuCheck } from "./menu"

export type DropdownSelectOption = {
  value: string
  label: string
  detail?: string
  disabled?: boolean
  /**
   * Visual mark only (e.g. green status dot) — no text badge.
   * Used by Recall Evaluate for collections that already have test cases.
   */
  indicator?: boolean
}

interface DropdownSelectProps {
  id?: string
  "aria-label"?: string
  "aria-labelledby"?: string
  value: string
  onChange: (value: string) => void
  options: DropdownSelectOption[] | (() => DropdownSelectOption[])
  placeholder?: string
  disabled?: boolean
  className?: string
  /**
   * default — form field
   * sm — dense form
   * tag — pill chrome (Node group tag editor)
   */
  size?: "default" | "sm" | "tag"
  /**
   * z-index for the portaled fixed menu. Bump it when the host surface
   * stacks above the default 450 (e.g. HITL confirm cards at z-500).
   */
  menuZIndex?: number
  /** Show the chevron even in tag chrome (affordance for clickable pills). */
  chevron?: boolean
  /** Optional icon before the label (kept when a tag pill needs its mark). */
  leading?: ReactNode
  valueContent?: ReactNode
  menuClassName?: string
  searchable?: boolean
}

/** Shared soft-glass listbox; keyboard focus and the selected value are distinct. */
export function DropdownSelect({ id, "aria-label": ariaLabel, "aria-labelledby": ariaLabelledBy,
  value, onChange, options, placeholder, disabled, className, size = "default", menuZIndex, chevron, leading,
  valueContent, menuClassName, searchable,
}: DropdownSelectProps) {
  const t = useT(), generatedId = useId()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const triggerRef = useRef<HTMLButtonElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const isTag = size === "tag"
  const choices = typeof options === "function" ? open ? options() : [] : options
  const selected = choices.find(option => option.value === value)
  const filtered = choices.filter(option => `${option.label} ${option.detail || ""} ${option.value}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  const showSearch = searchable || choices.length > 8 || query.length > 0
  const close = () => setOpen(false)
  const choose = (next: string) => { onChange(next); close(); triggerRef.current?.focus({ preventScroll: true }) }
  return <div className={cn("relative", isTag && "pm-select--tag", className)}>
    <button ref={triggerRef} id={id} aria-label={ariaLabel} aria-labelledby={ariaLabelledBy}
      type="button" disabled={disabled} aria-haspopup="listbox" aria-expanded={open}
      aria-controls={open ? `${generatedId}-options` : undefined}
      onClick={() => { setQuery(""); setOpen(current => !current) }}
      onKeyDown={event => { if (!open && ["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); setQuery(""); setOpen(true) } }}
      className={cn("pm-select-trigger flex w-full items-center gap-1.5", isTag ? "justify-center" : "justify-between",
        size === "sm" && "pm-select-trigger--sm", size === "default" && "pm-select-trigger--md",
        isTag && "pm-select-trigger--tag", open && "is-open", disabled && "is-disabled")}>
      {leading}
      {valueContent || <span className={cn("pm-select-trigger-label min-w-0 truncate", isTag ? "text-center" : "text-left", !value && "is-placeholder")}>{selected?.label || placeholder || t("menus.choose")}</span>}
      {selected?.indicator && <span className="pm-select-opt-mark shrink-0" title="Has test cases" />}
      {(!isTag || chevron) && <ChevronDown className={cn("pm-select-chev shrink-0 h-3.5 w-3.5", open && "is-open")} strokeWidth={1.75} />}
    </button>
    <SoftMenu open={open && !disabled} portal anchorRef={triggerRef} matchAnchorWidth={!isTag} align={isTag ? "end" : "start"}
      onClose={close} autoFocus className={cn("pm-select-menu sd-select-menu", isTag && "pm-select-menu--tag", menuClassName)}
      style={{ zIndex: menuZIndex ?? 450 }} role="presentation"
      onBlur={event => { if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget) && !triggerRef.current?.contains(event.relatedTarget)) close() }}
      onKeyDown={event => {
        if (event.key === "Tab") { close(); return }
        if (event.nativeEvent.isComposing || event.target instanceof HTMLInputElement) return
        if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && event.key !== " ") {
          event.preventDefault(); setQuery(current => current + event.key)
          requestAnimationFrame(() => searchRef.current?.focus())
        }
      }}>
      {showSearch && <label className="sd-menu-search sd-select-search"><Search size={15} aria-hidden /><input ref={searchRef}
        value={query} onChange={event => setQuery(event.target.value)} aria-label={t("menus.search")} placeholder={t("menus.search")} />
        {query && <button type="button" className="sd-menu-icon-button" aria-label={t("menus.clearSearch")} onClick={() => { setQuery(""); searchRef.current?.focus() }}><X size={13} /></button>}
      </label>}
      <div id={`${generatedId}-options`} role="listbox" aria-label={ariaLabel || selected?.label || placeholder} aria-labelledby={ariaLabelledBy} className="sd-select-options" data-menu-list>
        {!filtered.length ? <div className="sd-menu-empty">{t(choices.length ? "common.noMatches" : "common.noOptions")}</div>
          : filtered.map(option => <button key={option.value} type="button" role="option" aria-selected={value === option.value}
            disabled={option.disabled} className={cn("pm-select-opt", value === option.value && "is-on")} title={option.label} onClick={() => choose(option.value)}>
            {option.detail ? <span className="pm-select-opt-copy min-w-0"><span className="pm-select-opt-label">{option.label}</span>
              <span className="pm-select-opt-detail">{option.detail}</span></span>
              : <span className="pm-select-opt-label min-w-0 truncate">{option.label}</span>}
            {option.indicator && <span className="pm-select-opt-mark" title="Has test cases" />}
            <MenuCheck checked={value === option.value} />
          </button>)}
      </div>
    </SoftMenu>
  </div>
}
