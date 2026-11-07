import { useRef, useState } from "react"
import { ChevronRight, CircleDashed, EyeOff, Filter, PanelLeftClose, RotateCcw, Search, Tags } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { SoftMenu, MenuItem, MenuCheck, MenuSeparator } from "@/components/ui/menu"
import { useT } from "@/i18n/use-t"

type FilterOption = { value: string; label: string }

export interface QaQuestionFilterProps {
  count: number
  status: string
  type: string
  omitted: boolean
  statusOptions: FilterOption[]
  typeOptions: FilterOption[]
  onStatusChange: (value: string) => void
  onTypeChange: (value: string) => void
  onOmittedChange: (value: boolean) => void
  onClear: () => void
}

export function QaQuestionFilter({ count, status, type, omitted, statusOptions, typeOptions, onStatusChange, onTypeChange, onOmittedChange, onClear }: QaQuestionFilterProps) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [branch, setBranch] = useState<"status" | "type" | null>("status")
  const anchorRef = useRef<HTMLButtonElement>(null)
  const active = status !== "all" || type !== "all" || omitted
  const selected = [
    status !== "all" && statusOptions.find(option => option.value === status)?.label,
    type !== "all" && typeOptions.find(option => option.value === type)?.label,
    omitted && t("qa.showOmitted"),
  ].filter(Boolean)
  const label = `${t("qa.filterQuestions")}${selected.length ? `: ${selected.join(", ")}` : ""}`
  const value = branch === "type" ? type : status
  const choices = [{ value: "all", label: t(branch === "type" ? "qa.allTypes" : "qa.allStatus") },
    ...(branch === "type" ? typeOptions : statusOptions).filter(option => option.value !== "all")]
  return <div className={`qa-question-filter${active ? " is-active" : ""}`}>
    <span className="qa-question-count" aria-live="polite" aria-atomic="true">{count}</span>
    <Button ref={anchorRef} variant="outline" size="icon-sm" className="qa-filter-trigger" aria-label={label}
      title={t("qa.filterQuestions")} aria-haspopup="menu" aria-expanded={open}
      onClick={() => { setBranch("status"); setOpen(current => !current) }}><Filter aria-hidden="true" /></Button>
    <SoftMenu open={open} portal connected anchorRef={anchorRef} align="end" onClose={() => setOpen(false)}
      onSubmenuBack={() => setBranch(null)} autoFocus className="sd-qa-filter-menu" rootWidth={220} childWidth={250}
      aria-label={t("qa.filterQuestions")} connectedPanelKey={branch} connectedPanel={branch ? <>
        <div className="sd-menu-title">{t(branch === "type" ? "qa.typeLabel" : "qa.statusLabel")}</div>
        {choices.map(option => <MenuItem key={option.value} role="menuitemradio" aria-checked={value === option.value}
          active={value === option.value} title={option.label} onClick={() => {
            if (branch === "type") onTypeChange(option.value); else onStatusChange(option.value)
            setOpen(false); anchorRef.current?.focus()
          }}><span>{option.label}</span><MenuCheck checked={value === option.value} /></MenuItem>)}
      </> : undefined}>
      <div className="sd-menu-title">{t("qa.filterQuestions")}</div>
      {(["status", "type"] as const).map(key => {
        const Icon = key === "status" ? CircleDashed : Tags
        return <MenuItem key={key} data-submenu-trigger="" aria-haspopup="menu" aria-expanded={branch === key}
          onMouseEnter={() => setBranch(key)} onClick={() => setBranch(key)}>
          <Icon className="sd-menu-icon" /><span className="flex-1">{t(key === "status" ? "qa.statusLabel" : "qa.typeLabel")}</span><ChevronRight size={13} />
        </MenuItem>
      })}
      <MenuItem role="menuitemcheckbox" aria-checked={omitted} active={omitted}
        title={t("qa.showOmittedHelp")} onClick={() => onOmittedChange(!omitted)}>
        <EyeOff className="sd-menu-icon" /><span className="flex-1">{t("qa.showOmitted")}</span><MenuCheck checked={omitted} />
      </MenuItem>
      {active && <><MenuSeparator /><MenuItem onClick={onClear}><RotateCcw className="sd-menu-icon" />{t("qa.clearFilters")}</MenuItem></>}
    </SoftMenu>
  </div>
}

export function QaQuestionListHeader({ query, onQueryChange, onCollapse, ...filters }: QaQuestionFilterProps & {
  query: string
  onQueryChange: (value: string) => void
  onCollapse?: () => void
}) {
  const t = useT()
  return <div className="qa-nav-heading">
    <div className="qa-search">
      <Search className="size-3.5" aria-hidden="true" />
      <Input className="pm-settings-hw-input" aria-label={t("qa.search")} placeholder={t("qa.search")}
        value={query} onChange={event => onQueryChange(event.target.value)} />
    </div>
    <div className="qa-nav-actions">
      {onCollapse && <Button variant="ghost" size="icon-sm" aria-label={t("qa.collapseDetail")} title={t("qa.collapseDetail")} onClick={onCollapse}>
        <PanelLeftClose aria-hidden="true" />
      </Button>}
      <QaQuestionFilter {...filters} />
    </div>
  </div>
}
