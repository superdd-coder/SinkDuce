import { useEffect, useRef, useState } from "react"
import { FileText, MessagesSquare, X } from "lucide-react"
import { getChatQaSource, type Evidence, type QaSnapshotReference, type QaSourceSnapshot } from "@/api/client"
import { QaMaterialPreview } from "@/components/qa/qa-materials"
import { QaReplyText } from "@/components/qa/qa-reply-text"
import { Button } from "@/components/ui/button"
import { DialogClose, DialogDescription, DialogTitle } from "@/components/ui/dialog"
import { useT } from "@/i18n/use-t"
import "@/components/qa/qa.css"

type AvailableReference = Extract<QaSnapshotReference, { available: true }>

export function QaCitationPreview({ evidence, sessionId }: { evidence: Evidence; sessionId: string }) {
  const t = useT()
  const snapshotId = String(evidence.source.qa_snapshot_id || "")
  const citedRound = String(evidence.source.qa_column_id || "")
  const [snapshot, setSnapshot] = useState<QaSourceSnapshot | null>(null)
  const [reference, setReference] = useState<AvailableReference | null>(null)
  const [failed, setFailed] = useState(false)
  const cited = useRef<HTMLElement | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    void getChatQaSource(sessionId, snapshotId, controller.signal).then(result => {
      if (controller.signal.aborted) return
      setSnapshot(result)
      if (evidence.kind !== "qa") {
        const match = result.replies.flatMap(r => r.sources).find((source): source is AvailableReference =>
          source.available && source.file_id === evidence.source.file_id &&
          source.version_id === evidence.source.version_id && source.chunk_id === evidence.source.chunk_id,
        )
        if (match) setReference(match)
        else setFailed(true)
      }
    }).catch(() => {
      if (!controller.signal.aborted) setFailed(true)
    })
    return () => controller.abort()
  }, [sessionId, snapshotId, evidence])

  useEffect(() => {
    if (snapshot && !reference) cited.current?.scrollIntoView({ block: "nearest" })
  }, [snapshot, reference])

  return <>
    <header className="qa-citation-header">
      <div className="qa-citation-identity">
        {snapshot && <p className="qa-meta qa-citation-project">
          <MessagesSquare size={14} aria-hidden="true" />
          <span>{t("qa.title")}</span>
          <span aria-hidden="true">·</span>
          <span title={snapshot.project_name}>{snapshot.project_name}</span>
        </p>}
        <DialogTitle title={snapshot?.list_title}>{snapshot?.list_title || t("qa.title")}</DialogTitle>
        <DialogDescription className="sr-only">{t("qa.sourceThreadDescription")}</DialogDescription>
      </div>
      {snapshot && <span className="qa-citation-version">V{snapshot.version_number}</span>}
      <DialogClose className="pm-citation-close" aria-label={t("common.close")}>
        <X size={17} strokeWidth={1.6} aria-hidden="true" />
      </DialogClose>
    </header>
    {failed || !snapshot ? <div className="qa-empty" role={failed ? "alert" : "status"}>
      {failed ? t("qa.sourceThreadUnavailable") : t("qa.loading")}
    </div> : reference ? <div className="qa-citation-reference">
        {reference.historical && <span className="qa-citation-historical qa-citation-mark">{t("qa.sourceHistoricalVersion")}</span>}
        <QaMaterialPreview
          key={reference.chunk_id}
          collectionId={snapshot.collection}
          listId={snapshot.list_id}
          source={reference}
          listButton={false}
          initialExcerpt
          backLabel={t("qa.backToThread")}
          onBack={() => setReference(null)}
        />
    </div> : <div className="qa-reading qa-citation-reading">
      <section className="qa-question-head" aria-current={!citedRound || undefined}
        ref={!citedRound ? cited : undefined}>
        <div className="qa-citation-question-label">
          <span className="qa-meta">{t("qa.question")}</span>
          {!citedRound && <span className="qa-citation-mark">{t("qa.sourceCited")}</span>}
        </div>
        <div className="qa-question-copy"><h3>{snapshot.question}</h3></div>
      </section>
      {snapshot.replies.length > 0 && <div className="qa-replies">
        {snapshot.replies.map((reply, index) => {
          const isCited = citedRound === reply.column_id
          return <article key={reply.column_id} className="qa-reply" aria-current={isCited || undefined}
            ref={isCited ? cited : undefined}>
            <header><div className="qa-reply-heading">
              <span className="qa-reply-number" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
              <h4>{reply.label}</h4>
              {isCited && <span className="qa-citation-mark">{t("qa.sourceCited")}</span>}
            </div></header>
            <QaReplyText body={reply.body} />
            {reply.sources.length > 0 && <div className="qa-sources">
              {reply.sources.map((source, sourceIndex) => source.available
                ? <Button variant="secondary" size="sm" key={`${source.chunk_id}/${sourceIndex}`}
                    title={source.filename} onClick={() => setReference(source)}>
                    <FileText className="size-3" aria-hidden="true" />
                    <span>{source.filename}</span>
                    {source.historical && <small>{t("qa.sourceHistoricalVersion")}</small>}
                  </Button>
                : <span className="qa-meta qa-citation-unavailable" key={sourceIndex}>
                    {source.filename} · {t("qa.sourceUnavailable")}
                  </span>
              )}
            </div>}
          </article>
        })}
      </div>}
    </div>}
  </>
}
