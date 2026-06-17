import { useState, useRef, useEffect, type ReactNode } from "react"
import { cn } from "@/lib/utils"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Crepe } from "@milkdown/crepe"
import { editorViewCtx, parserCtx } from "@milkdown/kit/core"
import "@milkdown/crepe/theme/common/style.css"
import "@milkdown/crepe/theme/common/reset.css"
import "@milkdown/crepe/theme/nord.css"

interface MarkdownEditorProps {
  value: string
  onChange?: (value: string) => void
  className?: string
  minHeight?: string
  placeholder?: string
  children?: ReactNode
  readonly?: boolean
  /** "block" = Milkdown Crepe WYSIWYG (default). "plain" = simple textarea + preview overlay. */
  variant?: "block" | "plain"
}

// ─── Milkdown Crepe WYSIWYG editor ────────────────────────────────────────
// Supports both edit mode (Typora-like) and read-only mode via `readonly` prop.

function TyporaEditor({
  value,
  onChange,
  className,
  minHeight,
  placeholder,
  children,
  readonly = false,
}: Omit<MarkdownEditorProps, "variant">) {
  const containerRef = useRef<HTMLDivElement>(null)
  const crepeRef = useRef<Crepe | null>(null)
  const lastEmitted = useRef(value)
  const onChangeRef = useRef(onChange)
  const initValueRef = useRef(value)
  const mountedRef = useRef(false)
  const createPromiseRef = useRef<Promise<void> | null>(null)
  onChangeRef.current = onChange

  useEffect(() => {
    const root = containerRef.current
    if (!root) return
    mountedRef.current = false

    const crepe = new Crepe({
      root,
      defaultValue: initValueRef.current,
      featureConfigs: {
        [Crepe.Feature.Placeholder]: {
          text: placeholder || "Start writing...",
        },
      },
    })

    crepe.on((api) => {
      api.markdownUpdated((_ctx, markdown) => {
        lastEmitted.current = markdown
        if (mountedRef.current && !externalUpdateRef.current) {
          console.log("[Milkdown onChange]", JSON.stringify(markdown.slice(0, 200)))
          onChangeRef.current?.(markdown)
        }
      })
      api.mounted(() => {
        mountedRef.current = true
        crepe.setReadonly(readonly)
      })
    })

    let destroyed = false
    createPromiseRef.current = crepe.create().then(() => {
      if (!destroyed) crepeRef.current = crepe
    }) as unknown as Promise<void>

    return () => {
      destroyed = true
      mountedRef.current = false
      crepeRef.current = null
      createPromiseRef.current = null
      crepe.destroy()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Toggle readonly mode
  useEffect(() => {
    const p = createPromiseRef.current
    if (!p) return
    p.then(() => { crepeRef.current?.setReadonly(readonly) })
  }, [readonly])

  // Sync external value changes
  const externalUpdateRef = useRef(false)
  useEffect(() => {
    const p = createPromiseRef.current
    if (!p) return
    if (value === lastEmitted.current) return
    p.then(() => {
      const crepe = crepeRef.current
      if (!crepe) return
      externalUpdateRef.current = true
      try {
        crepe.editor.action((ctx) => {
          const view = ctx.get(editorViewCtx)
          const parser = ctx.get(parserCtx)
          const doc = parser(value)
          if (doc) {
            const tr = view.state.tr.replaceWith(0, view.state.doc.content.size, doc)
            view.dispatch(tr)
          }
        })
      } catch { /* editor not ready yet */ }
      lastEmitted.current = value
      requestAnimationFrame(() => { externalUpdateRef.current = false })
    })
  }, [value])

  return (
    <div className={cn("milkdown-editor relative", readonly && "milkdown-readonly", className)} style={{ minHeight }} ref={containerRef}>
      {children && !readonly && (
        <div
          className="absolute top-2 right-2 z-10 flex gap-1 pointer-events-auto"
          onMouseDown={(e) => e.stopPropagation()}
        >
          {children}
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
