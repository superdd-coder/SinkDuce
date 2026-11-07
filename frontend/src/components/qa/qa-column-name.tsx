import { useEffect, useRef, useState } from "react"
import { Pencil } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useT } from "@/i18n/use-t"

export function QaColumnName({ label, editable, initialEditing = false, onSave, onCancel, variant = "column" }: {
  label: string; editable: boolean; initialEditing?: boolean
  onSave: (label: string) => Promise<boolean>; onCancel?: () => void
  variant?: "column" | "title"
}) {
  const t = useT()
  const [editing, setEditing] = useState(initialEditing)
  const [value, setValue] = useState(label)
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const lock = useRef(false)
  const cancelled = useRef(false)

  useEffect(() => {
    if (editing) {
      input.current?.focus()
      input.current?.select()
      input.current?.scrollIntoView({ block: "nearest", behavior: "smooth" })
    }
  }, [editing])

  async function save() {
    if (lock.current || cancelled.current) return
    const next = value.trim() || label
    if (!initialEditing && next === label) { setEditing(false); return }
    lock.current = true; setSaving(true); setFailed(false)
    try {
      if (await onSave(next)) setEditing(false)
      else setFailed(true)
    } catch { setFailed(true) }
    finally { lock.current = false; setSaving(false) }
  }

  return <div className={`qa-column-name ${variant === "title" ? "qa-list-name" : ""}`}>
    {editing && editable ? <>
      <Input ref={input} value={value} maxLength={200} aria-label={t(variant === "title" ? "qa.name" : "qa.columnName")}
        aria-invalid={failed || undefined} disabled={saving} onChange={e => setValue(e.target.value)}
        onBlur={() => { void save() }} onKeyDown={e => {
          if (e.nativeEvent.isComposing) return
          if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur() }
          if (e.key === "Escape") {
            e.preventDefault(); cancelled.current = true; setEditing(false); onCancel?.()
          }
        }} />
      {failed && <span className="qa-inline-error" role="alert">{t("qa.inlineSaveFailed")}</span>}
    </> : <>
      {variant === "title" ? <h2 className="qa-title" title={label}>{label}</h2> : <h4>{label}</h4>}
      {editable && <Button type="button" variant="ghost" size="icon-xs" aria-label={t(variant === "title" ? "qa.rename" : "qa.renameColumn")} title={t(variant === "title" ? "qa.rename" : "qa.renameColumn")}
        onClick={() => { cancelled.current = false; setValue(label); setEditing(true) }}><Pencil /></Button>}
    </>}
  </div>
}
