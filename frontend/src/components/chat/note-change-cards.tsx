import { lazy, Suspense, useState } from "react"
import { FilePenLine } from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import { useT } from "@/i18n/use-t"
import type { NoteChange } from "@/lib/note-changes"

const NoteEditorDialog = lazy(() => import("@/components/database/note-editor-dialog").then(
  (module) => ({ default: module.NoteEditorDialog }),
))

/** The same Note editor and collection sidebar are available from both chat surfaces. */
export function NoteChangeCards({ notes }: { notes?: NoteChange[] }) {
  const [selectedNote, setSelectedNote] = useState<NoteChange | null>(null)
  const collections = useAppStore((s) => s.collections)
  const t = useT()
  if (!notes?.length) return null

  return (
    <div className="mt-3 flex flex-wrap gap-2" aria-label={t("common.notes")}>
      {notes.map((note) => (
        <button
          key={`${note.collection}:${note.id}`}
          type="button"
          data-note-id={note.id}
          aria-haspopup="dialog"
          onClick={() => setSelectedNote(note)}
          className="flex min-w-0 max-w-full items-center gap-2.5 rounded-xl border border-[var(--pm-green-soft)] bg-[var(--pm-surface)] px-3 py-2 text-left transition-colors hover:bg-[var(--pm-green-wash)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--pm-green)]"
        >
          <FilePenLine className="size-4 shrink-0 text-[var(--pm-green)]" aria-hidden />
          <span className="min-w-0">
            <span className="block truncate text-xs font-medium text-[var(--pm-text)]">{note.title}</span>
            <span className="block truncate text-[10px] text-[var(--pm-muted)]">
              {collections.find((collection) => collection.id === note.collection)?.name ?? t("common.note")}
            </span>
          </span>
        </button>
      ))}
      {selectedNote && (
        <Suspense fallback={null}>
          <NoteEditorDialog
            collection={selectedNote.collection}
            noteId={selectedNote.id}
            open
            onOpenChange={(open) => { if (!open) setSelectedNote(null) }}
          />
        </Suspense>
      )}
    </div>
  )
}
