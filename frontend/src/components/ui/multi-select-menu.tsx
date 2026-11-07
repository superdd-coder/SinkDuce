import { useId, useRef, useState, type RefObject, type ReactNode } from "react"
import { CheckCheck, Eraser, FolderClosed, MoreHorizontal, Search, X } from "lucide-react"
import { SoftMenu, MenuItem, MenuCheck } from "./menu"
import { useT } from "@/i18n/use-t"
import { useMenuPresence } from "@/hooks/use-menu-presence"

export type MultiSelectOption = { value: string; label: string; description?: string; icon?: ReactNode; disabled?: boolean }

export function MultiSelectMenu({ open, onClose, anchorRef, value, onChange, options, label }: {
  open: boolean; onClose: () => void; anchorRef: RefObject<HTMLElement | null>
  value: string[]; onChange: (value: string[]) => void; options: MultiSelectOption[]; label: string
}) {
  const t = useT(), id = useId()
  const [query, setQuery] = useState("")
  const [onlySelected, setOnlySelected] = useState(false)
  const [bulkOpen, setBulkOpen] = useState(false)
  const bulkPresence = useMenuPresence(open && bulkOpen)
  const bulkButton = useRef<HTMLButtonElement>(null)
  const bulkPanel = useRef<HTMLDivElement>(null)
  const results = options.filter(option => (!onlySelected || value.includes(option.value))
    && `${option.label} ${option.description || ""}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  const closeBulk = () => { setBulkOpen(false); requestAnimationFrame(() => bulkButton.current?.focus()) }
  const close = () => { setBulkOpen(false); onClose() }
  const toggle = (key: string) => onChange(value.includes(key) ? value.filter(item => item !== key) : [...value, key])
  return <SoftMenu open={open} portal anchorRef={anchorRef} onClose={close} autoFocus
    className="sd-multi-menu" role="dialog" aria-label={label}
    onPointerDown={event => { if (bulkOpen && !bulkPanel.current?.contains(event.target as Node) && !bulkButton.current?.contains(event.target as Node)) setBulkOpen(false) }}
    onKeyDown={event => { if (event.key === "Escape" && bulkOpen) { event.preventDefault(); event.stopPropagation(); closeBulk() } }}
    onBlur={event => { if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget) && !anchorRef.current?.contains(event.relatedTarget)) close() }}>
    <div className="sd-multi-toolbar">
      <label className="sd-menu-search"><Search size={15} aria-hidden /><input aria-label={t("menus.search")} placeholder={t("menus.search")} value={query} onChange={event => setQuery(event.target.value)} />
        {query && <button type="button" className="sd-menu-icon-button" aria-label={t("menus.clearSearch")} onClick={() => setQuery("")}><X size={13} /></button>}
      </label>
      <button type="button" className="sd-multi-selected" aria-pressed={onlySelected}
        title={t(onlySelected ? "menus.showAll" : "menus.onlySelected")}
        aria-label={`${t("menus.selectedCount", { n: value.length })} · ${t(onlySelected ? "menus.showAll" : "menus.onlySelected")}`}
        onClick={() => setOnlySelected(current => !current)}><CheckCheck size={14} aria-hidden /><span aria-live="polite">{t("menus.selectedCount", { n: value.length })}</span></button>
      <button type="button" ref={bulkButton} className="sd-menu-icon-button" aria-label={t("menus.bulk")} title={t("menus.bulk")}
        aria-haspopup="menu" aria-expanded={bulkOpen} aria-controls={bulkOpen ? `${id}-bulk` : undefined}
        onClick={() => { if (bulkOpen) closeBulk(); else { setBulkOpen(true); requestAnimationFrame(() => bulkPanel.current?.querySelector<HTMLElement>('button:not(:disabled)')?.focus()) } }}><MoreHorizontal size={18} /></button>
      {bulkPresence.mounted && <div className={`sd-multi-bulk${bulkPresence.shown ? " is-open" : ""}`} role="menu" aria-label={t("menus.bulk")} id={`${id}-bulk`} ref={bulkPanel} data-menu-list
        inert={!open || !bulkOpen || undefined} aria-hidden={!open || !bulkOpen || undefined}>
        <MenuItem disabled={!results.some(option => !option.disabled && !value.includes(option.value))} onClick={() => { onChange([...new Set([...value, ...results.filter(option => !option.disabled).map(option => option.value)])]); closeBulk() }}><CheckCheck size={16} />{t("menus.selectResults")}</MenuItem>
        <MenuItem disabled={!value.length} onClick={() => { onChange(value.filter(key => options.some(option => option.value === key && option.disabled))); closeBulk() }}><Eraser size={16} />{t("menus.clearSelection")}</MenuItem>
      </div>}
    </div>
    <div className="sd-multi-options" role="listbox" aria-label={label} aria-multiselectable="true" data-menu-list>
      {results.map(option => <MenuItem key={option.value} role="option" aria-selected={value.includes(option.value)} disabled={option.disabled}
        title={option.label} active={value.includes(option.value)} onClick={() => toggle(option.value)}>
        {option.icon || <FolderClosed className="sd-menu-icon" size={18} aria-hidden />}
        <span><span className="sd-menu-label">{option.label}</span>{option.description && <small className="sd-menu-description">{option.description}</small>}</span>
        <MenuCheck checked={value.includes(option.value)} />
      </MenuItem>)}
      {!results.length && <div className="sd-menu-empty">{t(onlySelected && !value.length ? "menus.noneSelected" : "common.noMatches")}</div>}
    </div>
  </SoftMenu>
}
