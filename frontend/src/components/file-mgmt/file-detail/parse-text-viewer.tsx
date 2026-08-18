import { useMemo } from "react"

export interface ParseTextViewerProps {
  text: string
  collectionId: string
  fileId?: string | null
}

function flattenExtract(text: string): string {
  let s = (text || "").replace(/\r\n/g, "\n")
  if (s.includes(":::image")) {
    s = s.replace(/:::image[\s\S]*?^:::/gm, "[Image]")
  }
  return s
}

/**
 * Read-only Parse body as plain text. No markdown/table engine —
 * a full renderer on large extracts stalled the machine.
 */
export function ParseTextViewer({ text }: ParseTextViewerProps) {
  const body = useMemo(() => flattenExtract(text), [text])
  return (
    <div data-parse-root className="h-full overflow-auto">
      <pre className="pm-ws-parse-pre">{body}</pre>
    </div>
  )
}
