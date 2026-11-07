import { useId, useRef, useState, type DragEvent } from "react"
import { Loader2, Upload } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useT } from "@/i18n/use-t"
import { createQaWorkbookUpload } from "@/lib/qa-upload"

export function QaWorkbookUpload({ busy, onFile }: { busy: boolean; onFile: (file: File) => Promise<void> }) {
  const t = useT()
  const input = useRef<HTMLInputElement>(null)
  const dragDepth = useRef(0)
  const hintId = useId()
  const [upload] = useState(createQaWorkbookUpload)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState("")

  async function receive(files: ArrayLike<File>) {
    if (busy) return
    setError("")
    try {
      const issue = await upload.receive(files, onFile, busy)
      if (issue) setError(t(`qa.uploadError.${issue}`))
    } catch { setError(t("qa.failed")) }
  }
  const isFiles = (event: DragEvent) => Array.from(event.dataTransfer.types).includes("Files")

  return <div className={`qa-upload-target ${dragging && !busy ? "is-dragging" : ""}`}
    onDragEnter={event => {
      if (!isFiles(event)) return
      event.preventDefault(); event.stopPropagation()
      dragDepth.current++
      if (!busy) setDragging(true)
    }}
    onDragOver={event => {
      event.preventDefault(); event.stopPropagation()
      event.dataTransfer.dropEffect = !busy && isFiles(event) ? "copy" : "none"
    }}
    onDragLeave={event => {
      event.preventDefault(); event.stopPropagation()
      dragDepth.current = Math.max(0, dragDepth.current - 1)
      if (!dragDepth.current) setDragging(false)
    }}
    onDrop={event => {
      event.preventDefault(); event.stopPropagation()
      dragDepth.current = 0; setDragging(false)
      if (!busy && isFiles(event)) void receive(event.dataTransfer.files)
    }}>
    <input ref={input} type="file" hidden accept=".xlsx" disabled={busy} onChange={event => {
      const files = Array.from(event.target.files || [])
      event.target.value = ""
      if (files.length) void receive(files)
    }} />
    <Button type="button" variant="ghost" className="qa-upload" disabled={busy} aria-busy={busy}
      aria-describedby={hintId} onClick={() => input.current?.click()}>
      <span className="qa-upload-icon" aria-hidden>{busy ? <Loader2 className="animate-spin" /> : <Upload />}</span>
      <span className="qa-upload-title">{t(busy ? "qa.processing" : dragging ? "qa.dropWorkbook" : "qa.chooseExcel")}</span>
      <span className="qa-upload-hint" id={hintId}>{t("qa.workbookDropHint")}</span>
    </Button>
    {error && <p className="qa-notice" role="alert">{error}</p>}
  </div>
}
