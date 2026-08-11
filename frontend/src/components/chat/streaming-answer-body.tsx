import { memo, useEffect, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { trimEmphasisInteriorSpaces } from "@/lib/md-emphasis"
import { cn } from "@/lib/utils"

/** Fallback when no premium host class is passed (e.g. Quick Chat sizing). */
const MD_PROSE =
  "prose prose-sm dark:prose-invert max-w-none prose-p:my-1 prose-pre:my-2 prose-ul:my-1 prose-ol:my-1 prose-li:my-0"

/** Light cleanup + close open fences so remark does not thrash on partial streams. */
function stabilizeStreamingMarkdown(md: string): string {
  let s = trimEmphasisInteriorSpaces(md)
  const fences = s.match(/^```/gm)
  if (fences && fences.length % 2 === 1) s += "\n```"
  return s
}

/**
 * Live answer rendering (main Chat + Quick Chat).
 *
 * Full ReactMarkdown on every token freezes the main thread. While streaming:
 *  - Always paint the latest `content` as plain text (never lag the SSE store)
 *  - Optionally re-parse **completed lines** as Markdown on a timer for format
 * When done: one full Markdown pass.
 */
const STREAM_MD_MS = 200

export const StreamingAnswerBody = memo(function StreamingAnswerBody({
  content,
  isStreaming,
  className,
}: {
  content: string
  isStreaming: boolean
  /** Extra classes on the outer prose wrapper (e.g. Quick Chat table sizing). */
  className?: string
}) {
  // Completed-line prefix for Markdown (may trail `content` by up to STREAM_MD_MS)
  const [mdHead, setMdHead] = useState("")
  const contentRef = useRef(content)
  contentRef.current = content

  useEffect(() => {
    if (!isStreaming) {
      setMdHead("")
      return
    }
    const syncHead = () => {
      const c = contentRef.current
      const nl = c.lastIndexOf("\n")
      const next = nl >= 0 ? c.slice(0, nl + 1) : ""
      setMdHead((prev) => (prev === next ? prev : next))
    }
    const id = window.setInterval(syncHead, STREAM_MD_MS)
    return () => clearInterval(id)
  }, [isStreaming])

  // Prefer premium host (pm-chat-prose); fall back to generic prose utilities.
  const proseClass = cn(
    className?.includes("pm-chat-prose") ? className : cn(MD_PROSE, className),
  )

  if (!isStreaming) {
    return (
      <div className={proseClass}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      </div>
    )
  }

  // Instant path: always show full content as plain text so stream never stalls.
  // Overlay formatted completed lines above when available (optional polish).
  const head = mdHead && content.startsWith(mdHead) ? mdHead : ""
  const tail = head ? content.slice(head.length) : content

  return (
    <div className={proseClass}>
      {head ? (
        <ReactMarkdown remarkPlugins={[remarkGfm]}>
          {stabilizeStreamingMarkdown(head)}
        </ReactMarkdown>
      ) : null}
      <span className="whitespace-pre-wrap break-words text-[var(--pm-ink,#121410)]">
        {tail}
      </span>
    </div>
  )
})
