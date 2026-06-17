import { useState, useRef, useEffect } from "react"
import { cn } from "@/lib/utils"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkBreaks from "remark-breaks"

interface MarkdownEditorProps {
  value: string
  onChange: (value: string) => void
  className?: string
  minHeight?: string
  placeholder?: string
  /** "block" = Typora-style WYSIWYG (default). "plain" = simple textarea + preview overlay. */
  variant?: "block" | "plain"
}

// ─── Typora-style editor ──────────────────────────────────────────────────
// Focused: textarea text visible (raw markdown, caret visible)
// Blurred: rendered markdown preview visible (textarea transparent behind it)

function TyporaEditor({ value, onChange, className, minHeight, placeholder }: MarkdownEditorProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const previewRef = useRef<HTMLDivElement>(null)
  const [focused, setFocused] = useState(false)

  // Auto-grow textarea to match content
  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = "auto"
    ta.style.height = ta.scrollHeight + "px"
  }, [value])

  // Sync scroll between textarea and rendered layer
  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    const handleScroll = () => {
      if (previewRef.current) {
        previewRef.current.scrollTop = ta.scrollTop
      }
    }
    ta.addEventListener("scroll", handleScroll)
    return () => ta.removeEventListener("scroll", handleScroll)
  }, [])

  const isEmpty = !value.trim()

  return (
    <div
      className={cn("typora-editor", className)}
      style={{ minHeight }}
      onClick={() => textareaRef.current?.focus()}
    >
      {/* Textarea: visible when focused (raw markdown + caret), transparent when blurred */}
      <textarea
        ref={textareaRef}
        className={cn("typora-textarea", focused && "typora-textarea-focused")}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        placeholder={placeholder}
        spellCheck={false}
      />
      {/* Rendered preview: visible when blurred, hidden when focused */}
      {!focused && (
        <div ref={previewRef} className="typora-preview">
          <div className="prose prose-sm dark:prose-invert max-w-none prose-p:my-0.5 prose-pre:my-2 prose-ul:my-0.5 prose-ol:my-0.5 prose-li:my-0 prose-headings:my-1 prose-hr:my-2">
            {isEmpty ? (
              <span className="text-muted-foreground/50">{placeholder || "Start writing..."}</span>
            ) : (
              <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{value}</ReactMarkdown>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Plain variant (simple textarea + preview overlay) ──────────────────────

function PlainEditor({ value, onChange, className, minHeight, placeholder }: MarkdownEditorProps) {
  const [focused, setFocused] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const isEmpty = !value.trim()

  return (
    <div className={cn("md-editor", className)} style={{ minHeight }}>
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        className={cn("md-editor-textarea", focused && "md-editor-textarea-focused")}
        placeholder={placeholder}
      />
      {!focused && !isEmpty && (
        <div className="md-editor-overlay" onClick={() => textareaRef.current?.focus()}>
          <div className="prose prose-sm dark:prose-invert max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{value}</ReactMarkdown>
          </div>
        </div>
      )}
      {!focused && isEmpty && (
        <div className="md-editor-overlay" onClick={() => textareaRef.current?.focus()}>
          <span className="text-muted-foreground italic text-sm">
            {placeholder || "Nothing to preview"}
          </span>
        </div>
      )}
    </div>
  )
}

// ─── Public component ───────────────────────────────────────────────────────

export function MarkdownEditor(props: MarkdownEditorProps) {
  const { variant = "block" } = props
  if (variant === "plain") return <PlainEditor {...props} />
  return <TyporaEditor {...props} />
}
