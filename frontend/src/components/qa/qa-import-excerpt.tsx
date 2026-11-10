import { useLayoutEffect, useMemo, useRef, useState } from "react"
import { useT } from "@/i18n/use-t"
import { replyRuns } from "@/lib/qa-reply-format"

export function QaImportExcerpt({ text, formatted = false, expanded, onToggle }: {
  text: string; formatted?: boolean; expanded: boolean; onToggle: () => void
}) {
  const t = useT()
  const runs = useMemo(() => formatted ? replyRuns(text) : [{ text, bold: false, italic: false }], [formatted, text])
  const plain = runs.map(run => run.text).join("")
  const [preview, setPreview] = useState<string | null>(null)
  const copy = useRef<HTMLDivElement>(null)
  const measure = useRef<HTMLParagraphElement>(null)
  const moreLabel = t("qa.importPreview.full")

  useLayoutEffect(() => {
    const container = copy.current
    const sample = measure.current
    const body = sample?.firstElementChild
    const tail = sample?.lastElementChild as HTMLElement | null
    if (!container || !sample || !body || !tail) return

    let width = 0
    function fit() {
      if (!container || !sample || !body || !tail) return
      const nextWidth = container.getBoundingClientRect().width
      if (!nextWidth || nextWidth === width) return
      width = nextWidth
      const limit = parseFloat(getComputedStyle(sample).lineHeight) * 3 + 1
      const fits = () => sample.getBoundingClientRect().height <= limit
      body.textContent = plain
      tail.hidden = true
      if (fits()) {
        setPreview(null)
        return
      }
      tail.hidden = false
      const characters = Array.from(plain)
      const prefix = (count: number) => characters.slice(0, count).join("").trimEnd()
      let low = 0
      let high = characters.length - 1
      while (low < high) {
        const middle = Math.ceil((low + high) / 2)
        body.textContent = prefix(middle)
        if (fits()) low = middle
        else high = middle - 1
      }
      setPreview(prefix(low))
    }
    fit()
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(fit)
    observer?.observe(container)
    return () => observer?.disconnect()
  }, [plain, moreLabel])

  return <div className="qa-import-copy" ref={copy}>
    <p className="qa-import-excerpt">
      {expanded && formatted ? runs.map((run, index) => {
        const content = run.italic ? <em>{run.text}</em> : run.text
        return run.bold ? <strong key={index}>{content}</strong> : <span key={index}>{content}</span>
      }) : <span>{expanded ? plain : preview ?? plain}</span>}
      {preview !== null && <span className="qa-import-excerpt-tail">
        {expanded ? " " : "… "}<button type="button" className="qa-import-read-more" aria-expanded={expanded} onClick={onToggle}>
          {expanded ? t("qa.importPreview.less") : moreLabel}
        </button>
      </span>}
    </p>
    <div className="qa-import-excerpt-measure" aria-hidden="true">
      <p className="qa-import-excerpt" ref={measure}>
        <span /><span className="qa-import-excerpt-tail" hidden>… <span className="qa-import-read-more">{moreLabel}</span></span>
      </p>
    </div>
  </div>
}
