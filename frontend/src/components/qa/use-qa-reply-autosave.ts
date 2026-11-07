import { useEffect, useRef, useState } from "react"
import { getQaDraft, putQaDraft, type QaDraft, type QaListDetail, type QaReply } from "@/api/client"
import { useAppStore } from "@/stores/app-store"
import { createQaEditorAutosave } from "@/lib/qa-autosave"

interface ReplyValue { body: string; proposalId?: string }

export function useQaReplyAutosave({ collectionId, data, questionId, reply, draftKey, onSave }: {
  collectionId: string; data: QaListDetail; questionId: string; reply: QaReply; draftKey: string
  onSave: (payload: Record<string, unknown>) => Promise<QaListDetail | null>
}) {
  const draft = useAppStore(s => s.qaDrafts[draftKey])
  const setDraft = useAppStore(s => s.setQaDraft)
  const [value, setValue] = useState(() => draft ?? reply.body)
  const [ready, setReady] = useState(false)
  const [editing, setEditing] = useState(false)
  const [state, setState] = useState<"idle" | "dirty" | "saving" | "saved" | "error">("idle")
  const [loadFailed, setLoadFailed] = useState(false)
  const [savedDraft, setSavedDraft] = useState<QaDraft | null>(null)
  const mounted = useRef(false)
  const server = useRef<QaDraft | null>(null)
  const baseRevision = useRef(reply.text_revision_id)
  const committed = useRef<QaListDetail | null>(null)
  const lastAcknowledged = useRef<QaListDetail | null>(null)
  // The parent keys each editor by list version, question and column. This context
  // remains valid while its final save completes after the editor is unmounted.
  const [context] = useState(() => ({ collectionId, data, questionId, columnId: reply.column_id, draftKey, onSave }))
  // The factory only stores these callbacks; refs are read later when a user edit is saved.
  // eslint-disable-next-line react-hooks/refs
  const [autosave] = useState(() => createQaEditorAutosave<ReplyValue>({ body: reply.body }, async value => {
    if (server.current?.body !== value.body || value.proposalId) {
      server.current = await putQaDraft(context.collectionId, context.data, context.questionId, context.columnId, {
        body: value.body,
        draft_revision: server.current?.draft_revision || 0,
        base_cell_revision_id: server.current?.base_cell_revision_id || baseRevision.current,
        ...(value.proposalId ? { proposal_id: value.proposalId } : {}),
      })
      if (mounted.current) setSavedDraft(server.current)
    }
    const saved = server.current!
    const next = await context.onSave({ body: value.body, draft_revision: saved.draft_revision, base_cell_revision_id: saved.base_cell_revision_id })
    if (!next) throw new Error("reply_save_failed")
    const cell = next.questions.find(q => q.id === context.questionId)?.replies.find(r => r.column_id === context.columnId)
    if (!cell) throw new Error("reply_missing_after_save")
    baseRevision.current = cell.text_revision_id
    committed.current = next
    lastAcknowledged.current = next
    server.current = null
    if (mounted.current) setSavedDraft(null)
  }, {
    equal: (a, b) => a.body === b.body && a.proposalId === b.proposalId,
    onState: next => {
      if (!mounted.current) return
      setState(next)
    },
    onEditing: active => { if (mounted.current) setEditing(active) },
    onSaved: value => {
      if (useAppStore.getState().qaDrafts[context.draftKey] === value.body) setDraft(context.draftKey, null)
    },
  }))

  useEffect(() => {
    mounted.current = true
    const preventLoss = (event: BeforeUnloadEvent) => {
      if (autosave.dirty()) { event.preventDefault(); event.returnValue = "" }
    }
    window.addEventListener("beforeunload", preventLoss)
    return () => {
      mounted.current = false
      window.removeEventListener("beforeunload", preventLoss)
      void autosave.flush()
    }
  }, [autosave])

  useEffect(() => {
    let cancelled = false
    getQaDraft(context.collectionId, context.data, context.questionId, context.columnId).then(value => {
      if (cancelled) return
      server.current = value
      setSavedDraft(value)
      if (value && useAppStore.getState().qaDrafts[context.draftKey] === undefined) {
        setDraft(context.draftKey, value.body)
        setValue(value.body)
      }
      setReady(true)
    }).catch(() => { if (!cancelled) setLoadFailed(true) })
    return () => { cancelled = true }
  }, [context, setDraft])

  useEffect(() => {
    // Zustand acknowledges the local draft before the parent React snapshot may
    // render. Keep the editor's own text until that response reaches its props.
    if (lastAcknowledged.current && data.revision < lastAcknowledged.current.revision) return
    if (!server.current && autosave.reset({ body: reply.body })) {
      baseRevision.current = reply.text_revision_id
      // External server revisions synchronize only after local input is clean.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (useAppStore.getState().qaDrafts[context.draftKey] === undefined) setValue(reply.body)
    }
  }, [autosave, context.draftKey, data.revision, reply.body, reply.text_revision_id])

  function edit(body: string, proposalId?: string) {
    autosave.begin()
    setValue(body)
    setDraft(draftKey, body)
    autosave.edit({ body, ...(proposalId ? { proposalId } : {}) })
  }
  return {
    value, ready, editing, state, loadFailed, savedDraft,
    edit, flush: autosave.flush,
    begin() {
      autosave.begin()
      if (draft !== undefined) autosave.edit({ body: draft })
    },
    async finish() {
      if (!await autosave.finish() || autosave.editing()) return null
      const latest = committed.current
      committed.current = null
      return latest
    },
    setComposingValue(body: string) { autosave.begin(); setValue(body); setDraft(draftKey, body) },
  }
}
