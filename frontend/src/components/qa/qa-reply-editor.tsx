import { useEffect, useId, useRef, useState } from "react"
import { FileText, Sparkles, X, RotateCcw, Check, Loader2, History } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { QaColumnName } from "./qa-column-name"
import { QaActionMenu, QaMenuItem } from "./qa-controls"
import { useQaReplyAutosave } from "./use-qa-reply-autosave"
import { QaRichReply } from "./qa-rich-reply"
import { QaReplyText } from "./qa-reply-text"
import { QaDraftInstructions } from "./qa-draft-instructions"
import { useT } from "@/i18n/use-t"
import { useAppStore } from "@/stores/app-store"
import { sourceScopedKey } from "@/api/source-scope"
import { createQaProgressSampler } from "@/lib/qa-draft-progress"
import { groupQaSources } from "@/lib/qa-sources"
import { streamQaDraft, getQaList, getQaReplyHistory,
  type QaDraftOptions, type QaDraftProgress, type QaListDetail, type QaProposal, type QaQuestion, type QaReply, type QaReplyRevision, type QaSource } from "@/api/client"

export function QaReplyEditor({ collectionId, data, question, reply, busy, onSave, onFinish, onRename, onSource, position, onToggle }: {
  collectionId: string; data: QaListDetail; question: QaQuestion; reply: QaReply; busy: boolean; position: number;
  onSave: (payload: Record<string, unknown>) => Promise<QaListDetail | null>;
  onFinish?: (saved: QaListDetail) => void;
  onRename: (label: string) => Promise<boolean>; onSource: (source: QaSource, sources?: QaSource[]) => void;
  onToggle?: (restoreReplies: boolean) => void;
}) {
  const t = useT()
  const actor = useAppStore(s => s.identity?.actor_id || "local")
  const key = sourceScopedKey(`${actor}/${collectionId}/${data.id}/${data.version.id}/${question.id}/${reply.column_id}`)
  const editor = useQaReplyAutosave({ collectionId, data, questionId: question.id, reply, draftKey: key, onSave })
  const [history, setHistory] = useState<QaReplyRevision[] | null>(null)
  const [assistant, setAssistant] = useState(false)
  const assistantId = useId()
  const [generating, setGenerating] = useState(false)
  const [draft, setDraft] = useState<{ proposal: QaProposal; body: string; input: string; composing: boolean } | null>(null)
  const [generationError, setGenerationError] = useState("")
  const [undo, setUndo] = useState<{ before: string; after: string } | null>(null)
  const [progress, setProgress] = useState<QaDraftProgress>({ stage: "preparing" })
  const generation = useRef<{ controller: AbortController; sampler: ReturnType<typeof createQaProgressSampler> } | null>(null)
  const adoptable = useRef<string | null>(null)
  const editable = data.can_edit && question.included && reply.included
  const sources = editor.savedDraft?.sources || reply.sources || []
  const followUp = draft !== null && editor.value === draft.input
  const draftTooLong = draft !== null && [...draft.body].length > 32767
  const draftBlocked = draftTooLong || !!draft?.composing
  const canAdopt = followUp && !draftBlocked && !!draft?.body.trim()
    && editor.ready && !generating && editor.state !== "saving" && !busy

  useEffect(() => () => {
    generation.current?.controller.abort()
    generation.current?.sampler.stop()
    generation.current = null
    adoptable.current = null
  }, [key])

  function closeAssistant() {
    generation.current?.controller.abort()
    generation.current?.sampler.stop()
    generation.current = null
    adoptable.current = null
    setAssistant(false); setGenerating(false); setDraft(null); setGenerationError("")
  }

  async function generate(guidance: string, materialVersionIds: string[]) {
    if (generation.current || !assistant || !editor.ready || !editable || busy) return false
    if (followUp && draftBlocked) return false
    const input = editor.value
    const options: QaDraftOptions = { guidance, material_version_ids: materialVersionIds }
    if (followUp && draft) {
      options.previous_proposal_id = draft.proposal.id
      if (draft.body !== draft.proposal.body) options.edited_draft = draft.body
    }
    const controller = new AbortController()
    const sampler = createQaProgressSampler(next => { if (generation.current?.controller === controller && !controller.signal.aborted) setProgress(next) })
    generation.current = { controller, sampler }
    const currentRun = () => generation.current?.controller === controller && !controller.signal.aborted
    setProgress({ stage: "preparing" }); setGenerating(true); setGenerationError("")
    try {
      if (!await editor.flush()) { if (currentRun()) setGenerationError("qa.replySaveFailed"); return false }
      if (!currentRun()) return false
      const current = await getQaList(collectionId, data.id, data.version.id, controller.signal)
      if (!currentRun()) return false
      const result = await streamQaDraft(collectionId, current, question.id, reply.column_id,
        followUp ? "" : input, sampler.update, controller.signal, options)
      if (!currentRun()) return false
      adoptable.current = result.id
      setDraft({ proposal: result, body: result.body, input, composing: false })
      return true
    } catch (error) {
      const message = error instanceof Error ? error.message : ""
      if (currentRun()) setGenerationError(message.includes("stale_draft") ? "qa.aiDraftStale"
        : /material_archived|material_not_ready/.test(message) || (materialVersionIds.length && message.includes("not_found")) ? "qa.aiMaterialUnavailable"
        : message.includes("ai_repeated_reply") ? "qa.aiRepeatedReply" : "qa.aiUnavailable")
      return false
    } finally {
      sampler.stop()
      if (generation.current?.controller === controller) { generation.current = null; setGenerating(false) }
    }
  }

  function adoptDraft() {
    if (!draft || !canAdopt || generation.current || adoptable.current !== draft.proposal.id) return
    setUndo({ before: editor.value, after: draft.body })
    closeAssistant()
    editor.edit(draft.body, draft.proposal.id)
    void editor.flush()
  }

  return <article className={`qa-reply ${!reply.included ? "is-removed" : ""}`}>
    <header>
      <div className="qa-reply-heading">
        <span className="qa-reply-number" aria-hidden>{String(position).padStart(2, "0")}</span>
        <QaColumnName label={reply.label} editable={data.can_edit} onSave={onRename} />
      </div>
      <div className="qa-reply-actions">
        {(reply.modified || (data.can_edit && onToggle)) && <QaActionMenu label={t("qa.columnActions")}>
          {reply.modified && <QaMenuItem onClick={() => history ? setHistory(null) : getQaReplyHistory(collectionId, data, question.id, reply.column_id).then(setHistory).catch(() => toast.error(t("qa.failed")))}><History aria-hidden />{t("qa.history")}</QaMenuItem>}
          {data.can_edit && onToggle && <>
            <QaMenuItem disabled={busy} onClick={() => onToggle(false)}>{t(reply.included ? "qa.removeColumn" : "qa.restoreColumnEmpty")}</QaMenuItem>
            {!reply.included && <QaMenuItem disabled={busy} onClick={() => onToggle(true)}>{t("qa.restoreColumnReplies")}</QaMenuItem>}
          </>}
        </QaActionMenu>}
        {!reply.included && <span className="qa-meta">{t("qa.omittedSince", { version: reply.absent_since || data.version.number })}</span>}
      </div>
    </header>
    {editor.loadFailed && <p className="qa-notice" role="alert">{t("qa.draftLoadFailed")}</p>}
    {editable ? <div className={`qa-composer${editor.state === "error" ? " is-error" : ""}`} onBlur={event => {
      if (!editor.ready || event.currentTarget.contains(event.relatedTarget)) return
      void editor.finish().then(saved => { if (saved) onFinish?.(saved) })
    }}>
      <QaRichReply value={editor.value} label={reply.label} placeholder={t("qa.replyPlaceholder")}
        ready={editor.ready} invalid={editor.state === "error"}
        onFocus={() => { if (editor.ready) editor.begin() }}
        onChange={(body, composing) => { if (composing) editor.setComposingValue(body); else editor.edit(body) }}
        onCompositionStart={() => { void editor.flush() }}
        onBlur={() => { if (editor.ready) void editor.flush() }} />
      <div className="qa-composer-footer">
        <span className="qa-save-status" role="status" aria-live="polite">
          {editor.state === "saving" ? <><Loader2 aria-hidden className="size-3 animate-spin" />{t("qa.saving")}</>
            : editor.state === "error" ? <>{t("qa.replySaveFailed")}<Button variant="ghost" size="icon-xs" aria-label={t("qa.retry")} title={t("qa.retry")} onClick={() => { void editor.flush() }}><RotateCcw /></Button></>
            : editor.state === "saved" ? <span className="qa-save-confirmed" role="img" aria-label={t("qa.autoSaved")} title={t("qa.autoSaved")}><Check aria-hidden className="size-3.5" /></span> : null}
        </span>
        <div className="qa-composer-actions">
          {undo && editor.value === undo.after && <Button variant="ghost" size="icon-sm" disabled={generating || editor.state === "saving" || busy} aria-label={t("qa.undoAiEdit")} title={t("qa.undoAiEdit")} onClick={() => { editor.edit(undo.before); setUndo(null); void editor.flush() }}><RotateCcw /></Button>}
          {editor.editing && !assistant && <Button type="button" variant="ghost" size="icon-sm" className="qa-reply-draft-trigger" aria-label={t("qa.aiDraft")} title={t("qa.aiDraft")}
            disabled={!editor.ready || busy} onMouseDown={e => e.preventDefault()} onClick={() => setAssistant(true)}><Sparkles aria-hidden /></Button>}
        </div>
      </div>
    </div> : <QaReplyText className={!reply.body ? "qa-empty-reply" : ""} body={reply.body || t("qa.emptyReply")} />}
    {assistant && editable && <QaDraftInstructions collectionId={collectionId} listId={data.id} panelId={assistantId}
      followUp={followUp} disabled={generating || busy || !editor.ready || (followUp && draftBlocked)} onGenerate={generate}>
      <div className="qa-ai-panel-heading">
        {generating ? <span className="qa-drafting" role="status" aria-live="polite"><Loader2 aria-hidden className="size-3.5 animate-spin" /><span>{t(`qa.draftProgress.${progress.stage === "drafting" && progress.operation ? progress.operation : progress.stage}`)}</span></span>
          : <span className="qa-draft-title">{draft?.proposal.operation ? t(`qa.aiResult.${draft.proposal.operation}`) : t("qa.aiDraft")}</span>}
        <Button type="button" variant="ghost" size="icon-xs" aria-label={t("common.close")} onClick={closeAssistant}><X /></Button>
      </div>
      {draft && <div className="qa-proposal">
        <QaRichReply key={draft.proposal.id} value={draft.body} label={t("qa.aiEditableDraft")} placeholder={t("qa.replyPlaceholder")}
          ready={followUp && !generating && !busy} invalid={draftTooLong}
          onChange={(body, composing) => {
            if (!generation.current && adoptable.current === draft.proposal.id) {
              setDraft(current => current?.proposal.id === draft.proposal.id ? { ...current, body, composing } : current)
            }
          }}
          onCompositionStart={() => setDraft(current => current?.proposal.id === draft.proposal.id ? { ...current, composing: true } : current)} />
        <QaEvidenceNotice proposal={draft.proposal} />
        {!followUp && <p className="qa-meta">{t("qa.aiInputChanged")}</p>}
        {draftTooLong && <p className="qa-notice" role="alert">{t("qa.aiDraftTooLong")}</p>}
        <div className="qa-proposal-actions">
          <QaSources sources={draft.proposal.sources} onSource={onSource} />
          <Button type="button" size="sm" disabled={!canAdopt} onClick={adoptDraft}>{t("qa.useDraft")}</Button>
        </div>
      </div>}
      {generationError && <p className="qa-notice" role="alert">{t(generationError)}</p>}
    </QaDraftInstructions>}
    {!editable && editor.savedDraft && <div className="qa-cleared"><span>{t("qa.draftReference")}</span><QaReplyText body={editor.savedDraft.body} /></div>}
    <QaSources sources={sources} onSource={onSource} />
    {history && <div className="qa-revision-history">{history.map(h => <div key={h.id}><time>{new Date(h.created_at).toLocaleString()}</time><QaReplyText body={h.body || t("qa.emptyReply")} /></div>)}</div>}
  </article>
}

export function QaEvidenceNotice({ proposal }: { proposal: Pick<QaProposal, "insufficient_evidence" | "unverified_sources"> }) {
  const t = useT()
  if (!proposal.insufficient_evidence) return null
  const sources = [...new Set((proposal.unverified_sources || []).map(source => source.trim()).filter(Boolean))]
  return <p className="qa-notice">{sources.length
    ? t("qa.unverifiedSources", { sources: sources.join(", ") })
    : t("qa.insufficientEvidence")}</p>
}

function QaSources({ sources, onSource }: { sources: QaSource[]; onSource: (source: QaSource, sources?: QaSource[]) => void }) {
  const t = useT()
  return sources.length ? <div className="qa-sources">{groupQaSources(sources).map(group => {
    const count = group.sources.filter(s => s.excerpt.trim()).length
    return <Button variant="secondary" size="sm" key={group.key} title={group.source.filename || t("qa.sourceFile")} onClick={() => onSource(group.source, group.sources)}><FileText className="size-3" /><span>{group.source.filename || t("qa.sourceFile")}</span>{count > 0 && <small>{t(count === 1 ? "qa.sourceExcerptCountOne" : "qa.sourceExcerptCount", { count })}</small>}</Button>
  })}</div> : null
}
