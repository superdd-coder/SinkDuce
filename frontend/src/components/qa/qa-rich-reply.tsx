import { useEffect, useRef } from "react"
import { EditorContent, useEditor } from "@tiptap/react"
import Placeholder from "@tiptap/extension-placeholder"
import type { MarkdownStorage } from "tiptap-markdown"
import { replyDocument } from "@/lib/qa-reply-format"
import { replyExtensions } from "@/lib/qa-reply-document"
import { QaReplyText } from "./qa-reply-text"

export function QaRichReply({ value, label, placeholder, ready, invalid, onChange, onFocus, onBlur, onCompositionStart }: {
  value: string; label: string; placeholder: string; ready: boolean; invalid: boolean
  onChange: (value: string, composing: boolean) => void
  onFocus: () => void; onBlur: (withinComposer: boolean) => void; onCompositionStart: () => void
}) {
  const emitted = useRef(value)
  const composing = useRef(false)
  const editor = useEditor({
    immediatelyRender: false,
    extensions: [...replyExtensions(), Placeholder.configure({ placeholder })],
    content: replyDocument(value),
    editable: ready,
    editorProps: {
      attributes: { class: "qa-reply-rich-input", role: "textbox", "aria-multiline": "true", "aria-label": label, "aria-invalid": String(invalid) },
      handleDOMEvents: {
        compositionstart: () => { composing.current = true; onCompositionStart(); return false },
        compositionend: view => {
          composing.current = false
          // The final composition transaction is delivered after this DOM event.
          queueMicrotask(() => {
            if (view.isDestroyed) return
            const body = (editor?.storage as unknown as { markdown: MarkdownStorage })?.markdown.getMarkdown()
            if (body !== undefined) { emitted.current = body; onChange(body, false) }
          })
          return false
        },
      },
    },
    onUpdate: ({ editor }) => {
      const body = (editor.storage as unknown as { markdown: MarkdownStorage }).markdown.getMarkdown()
      emitted.current = body
      onChange(body, composing.current)
    },
    onFocus,
    onBlur: ({ editor, event }) => {
      if (composing.current) {
        const body = (editor.storage as unknown as { markdown: MarkdownStorage }).markdown.getMarkdown()
        emitted.current = body
        onChange(body, false)
      }
      composing.current = false
      onBlur(Boolean((event.target as HTMLElement)?.closest(".qa-composer")?.contains(event.relatedTarget as Node | null)))
    },
  })
  useEffect(() => {
    if (editor && value !== emitted.current && !composing.current) {
      editor.commands.setContent(replyDocument(value), { emitUpdate: false })
      emitted.current = value
    }
  }, [editor, value])
  useEffect(() => { editor?.setEditable(ready, false) }, [editor, ready])
  useEffect(() => { editor?.setOptions({ editorProps: { attributes: {
    class: "qa-reply-rich-input", role: "textbox", "aria-multiline": "true", "aria-label": label, "aria-invalid": String(invalid),
  } } }) }, [editor, label, invalid])
  return editor ? <EditorContent editor={editor} /> : <div className="qa-reply-rich-input" role="textbox" aria-label={label} aria-multiline="true" aria-readonly="true">
    <QaReplyText body={value || placeholder} className={!value ? "qa-rich-placeholder" : ""} />
  </div>
}
