import { useEffect, useRef, useState, type DragEvent, type ReactNode } from "react"
import { ArrowUp, Check, FileText, FolderOpen, Loader2, Paperclip, RotateCcw, Upload, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useT } from "@/i18n/use-t"
import { getQaMaterials, getQaMaterialVersion, retryQaMaterial, uploadQaMaterial, type QaMaterial } from "@/api/client"
import { QaActionMenu, QaMenuItem } from "./qa-controls"
import "./qa-draft-instructions.css"

const maxAttachments = 8
const fileTypes = ".pdf,.docx,.xlsx,.pptx,.md,.txt,.csv,.tsv"
const slowAfterPolls = 25
type Attachment = QaMaterial & { pinnedVersion: string; polls: number; lookupFailed?: boolean }
const versionId = (item: QaMaterial) => item.version_id || item.current_version_id
const ready = (item: Attachment) => !item.archived && !item.lookupFailed && item.parse_state === "ready"
const waiting = (item: Attachment) => !item.archived && !item.lookupFailed && !["ready", "failed"].includes(item.parse_state)

export function QaDraftInstructions({ collectionId, listId, panelId, followUp, disabled, onGenerate, children }: {
  collectionId: string; listId: string; panelId: string; followUp: boolean; disabled: boolean; children: ReactNode;
  onGenerate: (guidance: string, materialVersionIds: string[]) => Promise<boolean>;
}) {
  const t = useT()
  const input = useRef<HTMLInputElement>(null)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const work = useRef<AbortController | null>(null)
  const libraryRequest = useRef<AbortController | null>(null)
  const sending = useRef(false)
  const dragDepth = useRef(0)
  const [guidance, setGuidance] = useState("")
  const [attached, setAttached] = useState<Attachment[]>([])
  const [sentVersions, setSentVersions] = useState<string[]>([])
  const [fileBusy, setFileBusy] = useState(false)
  const [notice, setNotice] = useState("")
  const [dragging, setDragging] = useState(false)
  const [picking, setPicking] = useState(false)
  const [materials, setMaterials] = useState<QaMaterial[] | null>(null)
  const [libraryFailed, setLibraryFailed] = useState(false)

  useEffect(() => () => { work.current?.abort(); libraryRequest.current?.abort() }, [])
  useEffect(() => {
    const field = textarea.current
    if (!field || CSS.supports("field-sizing", "content")) return
    field.style.height = "auto"
    field.style.height = `${field.scrollHeight}px`
  }, [guidance])
  useEffect(() => {
    const pending = attached.filter(waiting)
    if (!pending.length) return
    const controller = new AbortController()
    const timer = setTimeout(async () => {
      const results = await Promise.allSettled(pending.map(item => getQaMaterialVersion(collectionId, listId, item.id, item.pinnedVersion, controller.signal)))
      if (controller.signal.aborted) return
      const byVersion = new Map(pending.map((item, index) => [item.pinnedVersion, results[index]]))
      setAttached(current => current.map(item => {
        const result = byVersion.get(item.pinnedVersion)
        return result?.status === "fulfilled" ? { ...item, ...result.value, pinnedVersion: item.pinnedVersion, polls: item.polls + 1 }
          : result?.status === "rejected" ? { ...item, lookupFailed: true } : item
      }))
    }, 1200)
    return () => { clearTimeout(timer); controller.abort() }
  }, [attached, collectionId, listId])

  const blocked = disabled || fileBusy
  const hasNewReference = attached.some(item => !sentVersions.includes(item.pinnedVersion))
  const canSend = !blocked && (!followUp || !!guidance.trim() || hasNewReference) && attached.every(ready)

  async function runFiles(operation: (signal: AbortSignal) => Promise<void>) {
    if (blocked || work.current || sending.current) return
    const controller = new AbortController()
    work.current = controller
    setFileBusy(true); setNotice("")
    try { await operation(controller.signal) }
    catch { if (!controller.signal.aborted) setNotice("qa.failed") }
    finally {
      if (work.current === controller) work.current = null
      if (!controller.signal.aborted) setFileBusy(false)
    }
  }

  function add(material: QaMaterial) {
    if (material.archived) { setNotice("qa.aiMaterialArchived"); return }
    const pinnedVersion = versionId(material)
    if (!pinnedVersion) { setNotice("qa.aiMaterialUnavailable"); return }
    setAttached(items => {
      const next = { ...material, pinnedVersion, polls: 0 }
      if (items.some(item => item.id === material.id)) return items.map(item => item.id === material.id ? next : item)
      return items.length >= maxAttachments ? items : [...items, next]
    })
  }

  function receive(files: File[]) {
    if (!files.length || blocked || work.current || sending.current) return
    const remaining = maxAttachments - attached.length
    if (!remaining) { setNotice("qa.aiAttachmentLimit"); return }
    void runFiles(async signal => {
      if (files.length > remaining) setNotice("qa.aiAttachmentLimit")
      for (const file of files.slice(0, remaining)) {
        if (signal.aborted) return
        try {
          const material = await uploadQaMaterial(collectionId, listId, file, undefined, signal)
          if (!signal.aborted) add(material)
        } catch (error) {
          if (signal.aborted) return
          const message = error instanceof Error ? error.message : ""
          setNotice(message.includes("ambiguous_material_name") ? "qa.aiMaterialNameConflict" : "qa.aiMaterialUploadFailed")
        }
      }
    })
  }

  function chooseMaterials() {
    setPicking(true)
    if (libraryRequest.current) return
    const controller = new AbortController()
    libraryRequest.current = controller
    setMaterials(null); setLibraryFailed(false)
    void getQaMaterials(collectionId, listId, controller.signal)
      .then(items => {
        if (controller.signal.aborted) return
        setMaterials(items)
        setAttached(current => current.map(item => {
          const latest = items.find(entry => entry.id === item.id)
          return latest ? { ...item, current_version_id: latest.current_version_id, archived: latest.archived } : item
        }))
      })
      .catch(() => { if (!controller.signal.aborted) setLibraryFailed(true) })
      .finally(() => { if (libraryRequest.current === controller) libraryRequest.current = null })
  }

  function retry(item: Attachment) {
    void runFiles(async signal => {
      if (!item.lookupFailed) await retryQaMaterial(collectionId, listId, item.id, item.pinnedVersion)
      if (!signal.aborted) setAttached(current => current.map(entry => entry.pinnedVersion === item.pinnedVersion ? { ...entry, lookupFailed: false, parse_state: "queued", polls: 0 } : entry))
    })
  }

  async function submit() {
    if (!canSend || sending.current || work.current) return
    sending.current = true
    const versions = attached.map(item => item.pinnedVersion)
    try {
      if (await onGenerate(guidance.trim(), versions)) {
        setSentVersions(versions)
        setGuidance(current => current === guidance ? "" : current)
        setPicking(false)
      }
    } finally { sending.current = false }
  }

  const isFiles = (event: DragEvent) => Array.from(event.dataTransfer.types).includes("Files")
  const available = (materials || []).filter(item => !item.archived)
  const sendLabel = t(followUp ? "qa.aiReviseDraft" : "qa.aiGenerateDraft")
  return <div className="qa-ai-panel qa-draft-assistant" id={panelId}
    onDragEnter={event => { if (!isFiles(event)) return; event.preventDefault(); event.stopPropagation(); dragDepth.current++; if (!blocked) setDragging(true) }}
    onDragOver={event => { if (!isFiles(event)) return; event.preventDefault(); event.stopPropagation(); event.dataTransfer.dropEffect = blocked ? "none" : "copy" }}
    onDragLeave={event => { if (!isFiles(event) && !dragDepth.current) return; event.preventDefault(); event.stopPropagation(); dragDepth.current = Math.max(0, dragDepth.current - 1); if (!dragDepth.current) setDragging(false) }}
    onDrop={event => { if (!isFiles(event)) return; event.preventDefault(); event.stopPropagation(); dragDepth.current = 0; setDragging(false); receive(Array.from(event.dataTransfer.files)) }}>
    {children}
    <form className="qa-draft-instructions" onSubmit={event => { event.preventDefault(); void submit() }}>
      {picking && <section className="qa-draft-materials" aria-label={t("qa.materials")}>
        <header><span className="pm-label">{t("qa.materials")}</span>
          <Button type="button" variant="ghost" size="icon-xs" aria-label={t("common.close")} onClick={() => setPicking(false)}><X /></Button>
        </header>
        {libraryFailed ? <p className="qa-meta" role="alert">{t("qa.failed")} <Button type="button" variant="ghost" size="sm" disabled={blocked} onClick={chooseMaterials}>{t("qa.retry")}</Button></p>
          : !materials ? <p className="qa-meta" role="status">{t("qa.loading")}</p>
          : available.length ? <ul>{available.map(item => {
            const selected = attached.some(entry => entry.pinnedVersion === versionId(item))
            const replacement = attached.some(entry => entry.id === item.id)
            return <li key={item.id}>
              <Button type="button" variant="ghost" size="sm" title={item.filename} disabled={blocked || selected || (!replacement && attached.length >= maxAttachments)} onClick={() => add(item)}>
                {selected ? <Check aria-hidden /> : <FileText aria-hidden />}<span>{item.filename}</span>
              </Button>
            </li>
          })}</ul> : <p className="qa-meta">{t("qa.aiNoMaterials")}</p>}
      </section>}
      <div className="qa-draft-compose-surface" data-dragging={dragging && !blocked || undefined}>
        <input ref={input} type="file" hidden multiple accept={fileTypes} disabled={blocked} onChange={event => {
          const files = Array.from(event.target.files || [])
          event.target.value = ""
          receive(files)
        }} />
        {attached.length > 0 && <ul className="qa-draft-attachments" aria-label={t("qa.aiAttachments")}>{attached.map(item => {
          const slow = waiting(item) && item.polls >= slowAfterPolls
          return <li key={item.pinnedVersion}>
            {waiting(item) ? <Loader2 aria-hidden className="size-3.5 animate-spin" /> : <FileText aria-hidden className="size-3.5" />}
            <span title={item.filename}>{item.filename}</span>
            {(item.number > 1 || item.pinnedVersion !== item.current_version_id) && <small>V{item.number}</small>}
            {!ready(item) && <small>{t(item.archived || item.lookupFailed ? "qa.aiAttachmentUnavailable" : slow ? "qa.aiMaterialSlow" : waiting(item) ? "qa.processing" : "qa.parseFailed")}</small>}
            {!item.archived && (slow || item.lookupFailed || item.parse_state === "failed") && <Button type="button" size="icon-xs" variant="ghost" disabled={blocked} aria-label={t("qa.retry")} onClick={() => retry(item)}><RotateCcw /></Button>}
            <Button type="button" size="icon-xs" variant="ghost" disabled={blocked} aria-label={t("qa.aiRemoveAttachment", { name: item.filename })} onClick={() => setAttached(current => current.filter(entry => entry.pinnedVersion !== item.pinnedVersion))}><X /></Button>
          </li>
        })}</ul>}
        <div className="qa-draft-compose-row">
          <QaActionMenu label={t("qa.aiAddAttachment")} icon={<Paperclip aria-hidden />} disabled={blocked}>
            <QaMenuItem disabled={attached.length >= maxAttachments} onClick={() => input.current?.click()}><Upload />{t("qa.aiUploadMaterial")}</QaMenuItem>
            <QaMenuItem onClick={chooseMaterials}><FolderOpen />{t("qa.aiChooseMaterial")}</QaMenuItem>
          </QaActionMenu>
          <textarea ref={textarea} className="qa-draft-guidance" autoFocus maxLength={32767} rows={1} aria-label={t("qa.aiGuidance")} value={guidance}
            placeholder={t(followUp ? "qa.aiRevisePlaceholder" : "qa.aiGuidancePlaceholder")}
            onChange={event => setGuidance(event.target.value)} onKeyDown={event => {
              if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return
              event.preventDefault()
              void submit()
            }} />
          <Button type="submit" size="icon-sm" className="qa-draft-compose-send" aria-label={sendLabel} title={sendLabel} disabled={!canSend}>
            <ArrowUp aria-hidden className="size-4" strokeWidth={2} />
          </Button>
        </div>
      </div>
      {fileBusy && <p className="qa-meta" role="status">{t("qa.processing")}</p>}
      {notice && <p className="qa-notice" role="alert">{t(notice, { count: maxAttachments })}</p>}
    </form>
  </div>
}
