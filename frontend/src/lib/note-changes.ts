import type { NoteChange } from "../api/chat"
export type { NoteChange } from "../api/chat"

/** Accept persisted/SSE data and keep one current link per collection + Note ID. */
export function mergeNoteChanges(current: NoteChange[] = [], incoming: unknown): NoteChange[] {
  const notes = new Map(current.map((note) => [`${note.collection}\0${note.id}`, note]))
  if (!Array.isArray(incoming)) return [...notes.values()]
  for (const note of incoming) {
    if (!note || typeof note !== "object" || typeof note.id !== "string" || !note.id ||
      typeof note.collection !== "string" || !note.collection) continue
    notes.set(`${note.collection}\0${note.id}`, {
      id: note.id,
      collection: note.collection,
      title: typeof note.title === "string" && note.title ? note.title : note.id,
    })
  }
  return [...notes.values()]
}
