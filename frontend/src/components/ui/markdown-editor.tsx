import { useState, useRef, type ReactNode } from "react"
import { cn } from "@/lib/utils"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { TiptapEditor } from "./tiptap-editor"
import { preprocessDistillBlocks, postprocessDistillBlocks } from "./tiptap-editor"

interface MarkdownEditorProps {
  value: string
  onChange?: (value: string) => void
  className?: string
  minHeight?: string
  placeholder?: string
  children?: ReactNode
  readonly?: boolean
  /** "block" = Tiptap WYSIWYG (default). "plain" = simple textarea + preview overlay. */
  variant?: "block" | "plain"
  /** Custom image upload handler. Receives a File, returns the URL to insert. */
  onImageUpload?: (file: File) => Promise<string>
  /** Called when a user clicks a note-id:// link inside the editor. */
  onNoteLinkClick?: (noteId: string) => void
  /** Called when user triggers distill action from slash command. */
  onDistill?: () => void
  /** Called when the Tiptap editor instance is ready. */
  onEditorReady?: (editor: any) => void
}

// ─── Tiptap WYSIWYG editor ────────────────────────────────────────────────
// Supports both edit mode and read-only mode via `readonly` prop.

function TyporaEditor({
  value,
  onChange,
  className,
  minHeight,
  placeholder,
  children,
  readonly = false,
  onImageUpload,
  onNoteLinkClick,
  onDistill,
  onEditorReady,
}: Omit<MarkdownEditorProps, "variant">) {
  return (
    <TiptapEditor
      value={value}
      onChange={onChange}
      className={className}
      minHeight={minHeight}
      placeholder={placeholder}
      children={children}
      readonly={readonly}
      onImageUpload={onImageUpload}
      onNoteLinkClick={onNoteLinkClick}
      onDistill={onDistill}
      onEditorReady={onEditorReady}
    />
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
        onChange={(e) => onChange?.(e.target.value)}
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

// Re-export utilities for backward compatibility
export { preprocessDistillBlocks, postprocessDistillBlocks }
