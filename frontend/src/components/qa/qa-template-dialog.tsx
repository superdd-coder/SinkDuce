import { useQaModal } from "./use-qa-modal"
import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { QaField, QaFilePicker, QaToggle } from "./qa-controls"
import { Input } from "@/components/ui/input"
import { useT } from "@/i18n/use-t"
import { getQaLogo, getQaTemplates, saveQaTemplate, uploadQaLogo, type QaListDetail, type QaTemplate } from "@/api/client"
import "./qa-settings-refinement.css"

const defaults: Partial<QaTemplate> & { name: string } = { name: "", title: "", include_number: true, include_status: false, resequence: false, metadata_position: "left", logo_position: "right" }
export function QaTemplateDialog({ collectionId, list, close: onClosed, onSelect }: { collectionId: string; list: QaListDetail; close: () => void; onSelect: (id: string) => Promise<boolean> }) {
  const { open, close, onOpenChangeComplete } = useQaModal(onClosed)
  const t = useT()
  const [templates, setTemplates] = useState<QaTemplate[]>([])
  const [form, setForm] = useState(defaults)
  const [logo, setLogo] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  useEffect(() => { getQaTemplates(collectionId).then(rows => { setTemplates(rows); setForm(rows.find(r => r.id === list.template_id) || defaults) }).catch(() => setError(t("qa.failed"))) }, [collectionId, list.template_id, t])
  useEffect(() => {
    let alive = true, url: string | null = null
    if (form.logo_id) getQaLogo(collectionId, form.logo_id).then(blob => { if (alive) { url = URL.createObjectURL(blob); setLogo(url) } }).catch(() => {})
    return () => { alive = false; if (url) URL.revokeObjectURL(url) }
  }, [collectionId, form.logo_id])
  const update = (key: keyof QaTemplate, value: string | boolean) => setForm(p => ({ ...p, [key]: value }))
  return <Dialog open={open} onOpenChangeComplete={onOpenChangeComplete} onOpenChange={open => { if (!open && !busy) close() }}><DialogContent className="qa-dialog qa-refinement-dialog sm:max-w-3xl"><DialogHeader><DialogTitle>{t("qa.template")}</DialogTitle></DialogHeader>
    {error && <p className="qa-notice" role="alert">{error}</p>}
    <DropdownSelect aria-label={t("qa.template")} value={form.id || ""} onChange={value => { setForm(templates.find(r => r.id === value) || defaults); setLogo(null) }} options={[{ value: "", label: t("qa.newTemplate") }, ...templates.map(r => ({ value: r.id, label: r.name }))]} />
    <div className="qa-template-grid"><div className="qa-form">
      <QaField label={t("qa.templateName")}><Input value={form.name} onChange={e => update("name", e.target.value)} maxLength={100} /></QaField>
      <QaField label={t("qa.exportTitle")}><Input value={form.title || ""} placeholder={list.title} onChange={e => update("title", e.target.value)} maxLength={200} /></QaField>
      <QaField label={t("qa.metadataPosition")}><DropdownSelect aria-label={t("qa.metadataPosition")} value={form.metadata_position || "left"} onChange={value => update("metadata_position", value)} options={[{ value: "left", label: t("qa.left") }, { value: "right", label: t("qa.right") }]} /></QaField>
      <div className="qa-refinement-logo">
        <QaFilePicker label={t("qa.uploadLogo")} accept="image/png,image/jpeg" disabled={busy} onFile={async file => { setBusy(true); try { const result = await uploadQaLogo(collectionId, file); update("logo_id", result.id) } catch { setError(t("qa.failed")) } finally { setBusy(false) } }} />
        {form.logo_id && <><DropdownSelect size="sm" aria-label={t("qa.logoPosition")} value={form.logo_position || "right"} onChange={value => update("logo_position", value)} options={[{ value: "left", label: t("qa.left") }, { value: "right", label: t("qa.right") }]} /><Button type="button" size="sm" variant="ghost" onClick={() => { update("logo_id", ""); setLogo(null) }}>{t("qa.removeLogo")}</Button></>}
      </div>
      <div className="qa-refinement-toggles">{(["include_number", "include_status"] as const).map(k => <QaToggle key={k} checked={!!form[k]} onChange={value => update(k, value)} label={t(`qa.exportOption.${k}`)} />)}</div>
    </div>
    <figure className="qa-template-paper qa-refinement-template">
      <figcaption className="qa-meta">{t("common.preview")}</figcaption>
      <header style={{ flexDirection: logo && form.logo_position === "left" ? "row-reverse" : "row" }}><h3>{form.title || list.title}</h3>{logo && <img src={logo} alt="" />}</header>
      <p style={{ textAlign: form.metadata_position || "left" }}>V{list.version.number} · {new Date().toLocaleDateString()}</p>
      <div className="qa-template-columns">{form.include_number && <span>{t("qa.field.number")}</span>}<span>{t("qa.question")}</span>{form.include_status && <span>{t("qa.statusLabel")}</span>}{list.columns.filter(c => c.included).map(c => <span key={c.id}>{c.label}</span>)}</div>
      <div className="qa-paper-lines" aria-hidden="true" />
    </figure></div>
    <div className="qa-refinement-footer"><Button variant="ghost" disabled={busy} onClick={close}>{t("qa.cancel")}</Button><Button disabled={busy || !form.name.trim()} onClick={async () => { setBusy(true); setError(""); try { const result = await saveQaTemplate(collectionId, form); if (await onSelect(result.id)) close() } catch { setError(t("qa.failed")) } finally { setBusy(false) } }}>{t("qa.saveTemplate")}</Button></div>
  </DialogContent></Dialog>
}
