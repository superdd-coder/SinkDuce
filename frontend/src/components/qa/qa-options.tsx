import { useQaModal } from "./use-qa-modal"
import { defaultQaOptions } from "@/lib/qa-options"
import { useState } from "react"
import { CircleMinus, Plus } from "lucide-react"
import type { QaListDetail, QaOption } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { useT } from "@/i18n/use-t"
import "./qa-settings-refinement.css"

const colors = ["#397b60", "#6486ad", "#b58c42", "#a46565", "#8873a6", "#7c8580"]

export function QaOptionsDialog({ data, close: onClosed, onSave }: { data: QaListDetail; close: () => void; onSave: (payload: Record<string, unknown>) => Promise<boolean> }) {
  const { open, close, onOpenChangeComplete } = useQaModal(onClosed)
  const t = useT()
  const original = (data.options || defaultQaOptions).statuses
  const [statuses, setStatuses] = useState(() => structuredClone(original))
  const [replacements, setReplacements] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  const removed = original.filter(o => !statuses.some(n => n.id === o.id))
  const labels = new Set(statuses.map(o => o.label.trim()))
  const invalid = labels.size !== statuses.length || labels.has("")
  function update(id: string, patch: Partial<QaOption>) { setStatuses(items => items.map(o => o.id === id ? { ...o, ...patch } : o)) }
  return <Dialog open={open} onOpenChangeComplete={onOpenChangeComplete} onOpenChange={open => { if (!open && !busy) close() }}><DialogContent className="qa-dialog qa-refinement-dialog sm:max-w-xl"><DialogHeader><DialogTitle>{t("qa.manageOptions")}</DialogTitle><DialogDescription>{t("qa.optionsScope")}</DialogDescription></DialogHeader>
    <div className="qa-options-list">{statuses.map(option => <div className="qa-option-row" key={option.id}>
      <Input aria-label={t("qa.optionName")} maxLength={200} value={option.label} disabled={busy} onChange={event => update(option.id, { label: event.target.value })} />
      <div className="qa-option-colors" role="group" aria-label={t("qa.optionColor")}>{colors.map((color, i) => <Button key={color} variant="ghost" size="icon-sm" aria-label={t("qa.colorChoice", { number: i + 1 })} aria-pressed={option.color === color} disabled={busy} onClick={() => update(option.id, { color })}><span style={{ backgroundColor: color }} /></Button>)}</div>
      <Button variant="ghost" size="icon-sm" aria-label={t("qa.remove")} disabled={busy} onClick={() => setStatuses(items => items.filter(v => v.id !== option.id))}><CircleMinus /></Button>
    </div>)}</div>
    <Button className="self-start" variant="secondary" size="sm" disabled={busy || statuses.length >= 200} onClick={() => setStatuses(items => [...items, { id: crypto.randomUUID(), label: "", color: colors[0] }])}><Plus />{t("qa.addOption")}</Button>
    {removed.map(option => <div className="qa-option-replacement" key={option.id}><span>{t("qa.replaceOption", { name: option.label })}</span><DropdownSelect size="sm" aria-label={t("qa.replacement")} disabled={busy} value={replacements[option.id] ?? "__choose"} onChange={value => setReplacements(r => ({ ...r, [option.id]: value }))} options={[{ value: "__choose", label: t("qa.chooseReplacement") }, { value: "", label: t("qa.unset") }, ...statuses.map(o => ({ value: o.id, label: o.label }))]} /></div>)}
    {error && <p className="qa-notice" role="alert">{t("qa.optionsSaveFailed")}</p>}
    <div className="qa-refinement-footer">{invalid && <p className="qa-meta" role="status">{t("qa.optionsInvalid")}</p>}<Button variant="ghost" disabled={busy} onClick={close}>{t("qa.cancel")}</Button><Button disabled={busy || invalid} onClick={async () => { setBusy(true); setError(false); const saved = await onSave({ options: { statuses }, replacements: { statuses: replacements } }); setBusy(false); if (saved) close(); else setError(true) }}>{t(busy ? "qa.saving" : "qa.save")}</Button></div>
  </DialogContent></Dialog>
}
