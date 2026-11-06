import { Menu as MenuPrimitive } from "@base-ui/react/menu"
import { Check, ChevronRight, Filter, PanelLeftClose, RotateCcw, Search } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Menu, MenuItem, MenuSeparator } from "@/components/ui/menu"
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

function FilterSubmenu({ label, allLabel, value, options, onChange }: {
  label: string; allLabel: string; value: string; options: FilterOption[]; onChange: (value: string) => void
}) {
  const choices = [{ value: "all", label: allLabel }, ...options.filter(option => option.value !== "all")]
  const selection = value === "all" ? null : choices.find(option => option.value === value)?.label

  return <MenuPrimitive.SubmenuRoot>
    <MenuPrimitive.SubmenuTrigger nativeButton render={<MenuItem className="qa-filter-submenu-trigger" />}>
      <span>{label}</span>
      {selection && <span className="qa-filter-selection">{selection}</span>}
      <ChevronRight aria-hidden="true" />
    </MenuPrimitive.SubmenuTrigger>
    <MenuPrimitive.Portal>
      <MenuPrimitive.Positioner side="right" align="start" sideOffset={6} className="z-[420]">
        <MenuPrimitive.Popup render={<Menu className="qa-action-menu qa-filter-submenu" />} aria-label={label}>
          <MenuPrimitive.RadioGroup value={value} onValueChange={onChange}>
            {choices.map(option => <MenuPrimitive.RadioItem
              key={option.value} value={option.value} nativeButton closeOnClick
              render={<MenuItem className="qa-filter-choice" active={value === option.value} />}>
              <span>{option.label}</span>
              <MenuPrimitive.RadioItemIndicator className="qa-filter-check">
                <Check aria-hidden="true" />
              </MenuPrimitive.RadioItemIndicator>
            </MenuPrimitive.RadioItem>)}
          </MenuPrimitive.RadioGroup>
        </MenuPrimitive.Popup>
      </MenuPrimitive.Positioner>
    </MenuPrimitive.Portal>
  </MenuPrimitive.SubmenuRoot>
}

export function QaQuestionFilter({ count, status, type, omitted, statusOptions, typeOptions, onStatusChange, onTypeChange, onOmittedChange, onClear }: QaQuestionFilterProps) {
  const t = useT()
  const active = status !== "all" || type !== "all" || omitted
  const selected = [
    status !== "all" && statusOptions.find(option => option.value === status)?.label,
    type !== "all" && typeOptions.find(option => option.value === type)?.label,
    omitted && t("qa.showOmitted"),
  ].filter(Boolean)
  const label = `${t("qa.filterQuestions")}${selected.length ? `: ${selected.join(", ")}` : ""}`

  return <div className={`qa-question-filter${active ? " is-active" : ""}`}>
    <span className="qa-question-count" aria-live="polite" aria-atomic="true">{count}</span>
    <MenuPrimitive.Root>
      <MenuPrimitive.Trigger render={<Button variant="outline" size="icon-sm" className="qa-filter-trigger" aria-label={label} title={t("qa.filterQuestions")} />}>
        <Filter aria-hidden="true" />
      </MenuPrimitive.Trigger>
      <MenuPrimitive.Portal>
        <MenuPrimitive.Positioner sideOffset={6} align="end" className="z-[400]">
          <MenuPrimitive.Popup render={<Menu className="qa-action-menu qa-filter-menu" />} aria-label={t("qa.filterQuestions")}>
            <FilterSubmenu label={t("qa.statusLabel")} allLabel={t("qa.allStatus")} value={status} options={statusOptions} onChange={onStatusChange} />
            <FilterSubmenu label={t("qa.typeLabel")} allLabel={t("qa.allTypes")} value={type} options={typeOptions} onChange={onTypeChange} />
            <MenuPrimitive.CheckboxItem checked={omitted} onCheckedChange={onOmittedChange} nativeButton
              title={t("qa.showOmittedHelp")} render={<MenuItem className="qa-filter-choice" active={omitted} />}>
              <span>{t("qa.showOmitted")}</span>
              <MenuPrimitive.CheckboxItemIndicator className="qa-filter-check">
                <Check aria-hidden="true" />
              </MenuPrimitive.CheckboxItemIndicator>
            </MenuPrimitive.CheckboxItem>
            {active && <>
              <MenuSeparator />
              <MenuPrimitive.Item nativeButton render={<MenuItem />} onClick={onClear}>
                <RotateCcw aria-hidden="true" />{t("qa.clearFilters")}
              </MenuPrimitive.Item>
            </>}
          </MenuPrimitive.Popup>
        </MenuPrimitive.Positioner>
      </MenuPrimitive.Portal>
    </MenuPrimitive.Root>
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
