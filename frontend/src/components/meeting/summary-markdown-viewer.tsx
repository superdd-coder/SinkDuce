/**
 * Meeting Summary display viewer — readonly TipTap (same engine as edit mode)
 * so **bold**, lists, and paragraphs match the editor. Citation / priority
 * markers become interactive chips via custom nodes (meeting-summary-marks).
 *
 * Display-only: never write prepared HTML or speaker-resolved text back to disk.
 */
import { useEffect, useMemo, useRef } from "react"
import { useEditor, EditorContent } from "@tiptap/react"
import StarterKit from "@tiptap/starter-kit"
import { Markdown } from "tiptap-markdown"
import { trimEmphasisInteriorSpaces } from "@/lib/md-emphasis"
import {
  createMeetingPriorityExtension,
  createMeetingRefExtension,
  prepareMeetingSummaryForTiptapView,
  type RefClickHandler,
} from "./meeting-summary-marks"

/**
 * Undo Tiptap markdown over-escaping (edit save path + shared helpers).
 */
export function unescapeMarkdownOverEscapes(md: string): string {
  return (md || "")
    .replace(/\\\[/g, "[")
    .replace(/\\\]/g, "]")
    .replace(/\\~/g, "~")
    .replace(/\\_/g, "_")
    .replace(/\\\*/g, "*")
}

/**
 * Light cleanup for streaming / shared callers.
 * Trims spaces inside **bold** / *italic* only; preserves exterior word spaces.
 */
export function normalizeMd(md: string): string {
  return trimEmphasisInteriorSpaces(unescapeMarkdownOverEscapes(md))
}

export interface SummaryMarkdownViewerProps {
  md: string
  speakerNames?: Record<string, string> | null
  onRefClick?: (id: string) => void
  className?: string
}

/**
 * Display-only Meeting Summary viewer (readonly TipTap).
 */
export function SummaryMarkdownViewer({
  md,
  speakerNames,
  onRefClick,
  className,
}: SummaryMarkdownViewerProps) {
  const onRefClickRef = useRef<RefClickHandler>(onRefClick ?? (() => {}))
  onRefClickRef.current = onRefClick ?? (() => {})

  const MeetingRef = useMemo(
    () => createMeetingRefExtension(() => onRefClickRef.current),
    [],
  )
  const MeetingPri = useMemo(() => createMeetingPriorityExtension(), [])

  const prepared = useMemo(
    () => prepareMeetingSummaryForTiptapView(md, speakerNames),
    [md, speakerNames],
  )

  const editor = useEditor({
    // React 19 Strict Mode: avoid double-render crash / empty first paint
    immediatelyRender: false,
    extensions: [
      StarterKit.configure({
        // Keep defaults; heading levels used in meeting MD
        heading: { levels: [1, 2, 3] },
      }),
      MeetingRef,
      MeetingPri,
      Markdown.configure({
        html: true,
        tightLists: true,
        bulletListMarker: "-",
        linkify: false,
        transformPastedText: false,
        transformCopiedText: false,
      }),
    ],
    content: prepared || "",
    editable: false,
    editorProps: {
      attributes: {
        class: "pm-meeting-summary-pm focus:outline-none",
      },
    },
  })

  // Sync when source markdown or speaker map changes
  useEffect(() => {
    if (!editor || editor.isDestroyed) return
    const next = prepared || ""
    // Avoid redundant setContent (cursor flash / scroll jump)
    const cur = editor.getHTML()
    // Compare via markdown-ish re-prep is heavy; set when md/names change
    editor.commands.setContent(next, { emitUpdate: false })
    void cur
  }, [editor, prepared])

  if (!md) {
    return (
      <p className="pm-meta py-8 text-center">
        No content yet.
      </p>
    )
  }

  return (
    <div className={className ?? "pm-meeting-md pm-meeting-summary-tiptap"}>
      <EditorContent editor={editor} />
    </div>
  )
}
