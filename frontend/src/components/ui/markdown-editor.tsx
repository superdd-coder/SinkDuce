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
  /** Called when user clicks a distilled block to navigate to source note. */
  onDistillNavigate?: (noteId: string) => void
  /** Called when the Tiptap editor instance is ready. */
  onEditorReady?: (editor: any) => void
  /** Whether to show the built-in formatting toolbar. Default true. */
  showToolbar?: boolean
  /** Called when user clicks Visual Translate on an image. Receives image URL, returns description string. */
  onVisualTranslate?: (imageUrl: string) => Promise<string>
}

// ─── Tiptap WYSIWYG editor ────────────────────────────────────────────────
// Supports both edit mode and read-only mode via `readonly` prop.

function TyporaEditor({
  value,
  onChange,
  className,
  placeholder,
  children,
  readonly = false,
  onImageUpload,
  onNoteLinkClick,
  onDistill,
  onDistillNavigate,
  onEditorReady,
  showToolbar,
  onVisualTranslate,
}: Omit<MarkdownEditorProps, "variant">) {
  return (
    <TiptapEditor
      value={value}
      onChange={onChange}
      className={className}
      placeholder={placeholder}
      children={children}
      readonly={readonly}
      onImageUpload={onImageUpload}
      onNoteLinkClick={onNoteLinkClick}
      onDistill={onDistill}
      onDistillNavigate={onDistillNavigate}
      onEditorReady={onEditorReady}
      showToolbar={showToolbar}
      onVisualTranslate={onVisualTranslate}
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
