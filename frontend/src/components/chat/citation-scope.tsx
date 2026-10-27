import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from "react"
import { ArrowUpRight, X } from 'lucide-react'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog"
import { ChunkMd } from "@/components/shared/chunk-md"
import { getMeetingTranscript, type Evidence } from "@/api/client"
import { useAppStore } from "@/stores/app-store"
import { useT } from "@/i18n/use-t"
import { CitationSourcePreview } from "./citation-source-preview"
import "./chat-reports.css"
import { citationMarkdown, citationOrder } from "@/lib/chat-citations"
import { CitationContext, useCitations } from "./citation-context"
import { citationFileTarget } from '@/lib/source-evidence'

const ManagedFileDetail = lazy(() => import('@/components/file-mgmt/file-detail/file-detail-dialog').then(module => ({default:module.FileMgmtDetailDialog})))

/** Render the preserved source content with the same policy as document chunks. */
export function CitationExcerpt({ evidence }: { evidence: Evidence }) {
  const t = useT()
  const rawCollection = String(evidence.source.collection || evidence.source.collection_name || "")
  const collection = useAppStore((state) => state.collections.find((item) => item.id === rawCollection || item.name === rawCollection)?.id || rawCollection)
  const [meetingExcerpt,setMeetingExcerpt] = useState<{id:string;text:string}|null>(null)
  useEffect(() => {
    if (evidence.quote || !evidence.source.meeting_id || !evidence.location.sentence_id) return
    let cancelled = false
    void getMeetingTranscript(String(evidence.source.meeting_id)).then(result => {
      const segment = result.segments.find(item => item.sentence_id === evidence.location.sentence_id)
      if (!cancelled && segment) setMeetingExcerpt({id:evidence.id,text:segment.text})
    }).catch(() => {})
    return () => {cancelled = true}
  },[evidence])
  return <blockquote className="pm-evidence-quote">
    <ChunkMd text={evidence.quote || (meetingExcerpt?.id === evidence.id ? meetingExcerpt.text : '') || t(evidence.kind === 'meeting' ? 'chat.reports.noExcerpt' : "chat.reports.pageEvidence")} collection={collection}
      fileId={String(evidence.source.file_id || "")} source={String(evidence.source.source || "")} compactFigures />
  </blockquote>
}

/** Keep the current streaming line immediate without flashing internal IDs. */
export function CitationText({ source, streaming = false }: { source: string; streaming?: boolean }) {
  const { records } = useCitations()
  const prepared = citationMarkdown(source, records, streaming)
  const parts: ReactNode[] = []
  const regex = /\[(\d+)\]\(#evidence-([A-Za-z0-9_-]+)\)/g
  let offset = 0
  for (const match of prepared.matchAll(regex)) {
    if (match.index! > offset) parts.push(prepared.slice(offset, match.index))
    parts.push(<CitationLink key={match.index} id={match[2]}>{match[1]}</CitationLink>)
    offset = match.index! + match[0].length
  }
  parts.push(prepared.slice(offset))
  return <>{parts}</>
}

export function CitationLink({ id, children }: { id: string; children: ReactNode }) {
  const { records, open, numbers } = useCitations()
  const record = records.find((e) => e.id === id)
  if (!record) return <span className="pm-citation-missing">?</span>
  const number = numbers.get(id) || children
  return <button type="button" className="pm-citation" title={record.title} aria-label={`${number} · ${record.title}`}
    onClick={(event) => { event.stopPropagation(); open(record) }}>{number}</button>
}

export function CitationScope({ citations = [], sessionId, children, source: answerSource = "" }: {
  citations?: Evidence[]; sessionId: string; children: ReactNode
  onOpenMeeting?: (evidence: Evidence) => void
  source?: string
  onNavigate?: () => void
}) {
  const t = useT()
  const [selected, setSelected] = useState<Evidence | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const collections = useAppStore(state => state.collections)
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const openEvidence = (record: Evidence) => {
    triggerRef.current = document.activeElement instanceof HTMLButtonElement ? document.activeElement : null
    setDetailsOpen(false); setSelected(record); setDialogOpen(true)
  }
  const detailTarget = selected ? citationFileTarget(selected, collections) : null
  const loc = selected?.location || {}
  const position = [loc.page_number != null && t("chat.reports.page", { n: String(loc.page_number) }),
    loc.slide_number != null && t("chat.reports.slide", { n: String(loc.slide_number) }),
    loc.paragraph_index != null && t("chat.reports.paragraph", { n: String(loc.paragraph_index) }),
    loc.line != null && t("chat.reports.line", { n: String(loc.line) }),
    loc.start_time != null && `${Math.floor(Number(loc.start_time) / 60)}:${String(Math.floor(Number(loc.start_time) % 60)).padStart(2, "0")}`].filter(Boolean).join(" · ")
  const numbers = new Map(citationOrder(answerSource).filter((id) => citations.some((e) => e.id === id)).map((id, index) => [id, index + 1]))
  return <CitationContext value={{ records: citations, numbers, open: openEvidence }}>
    {children}
    <Dialog open={dialogOpen} onOpenChange={opened => {setDialogOpen(opened);if(!opened)setDetailsOpen(false)}} onOpenChangeComplete={(opened) => { if (!opened && !dialogOpen) setSelected(null) }}>
      <DialogContent className="pm-citation-dialog" finalFocus={triggerRef} forceOverlay showCloseButton={false}>
        <div className="pm-citation-heading">
          <div className="pm-citation-identity">
            <DialogTitle>{t('chat.reports.evidence')}</DialogTitle>
            <DialogDescription className="sr-only">{selected?.title}</DialogDescription>
          </div>
          <div className="pm-citation-actions">
            {detailTarget && <button type="button" className="pm-citation-detail-button" onClick={() => setDetailsOpen(true)}>{t('chat.reports.fileDetails')}<ArrowUpRight size={13} aria-hidden="true"/></button>}
            <DialogClose className="pm-citation-close" aria-label={t('common.close')}><X size={17} strokeWidth={1.6} aria-hidden="true"/></DialogClose>
          </div>
        </div>
        {selected && <div className="pm-citation-split">
          <aside className="pm-citation-excerpt-pane">
            <div className="pm-citation-source-intro"><h2 title={selected.title}>{selected.title}</h2></div>
            <div className="pm-citation-excerpt-head"><span>{t("chat.reports.excerpt")}</span>{position && <span className="pm-report-meta">{position}</span>}</div>
            <CitationExcerpt evidence={selected} />
          </aside>
          <CitationSourcePreview key={selected.id} evidence={selected} sessionId={sessionId} />
        </div>}
        {detailsOpen && detailTarget && <Suspense fallback={null}><ManagedFileDetail {...detailTarget} open={detailsOpen} onOpenChange={setDetailsOpen}/></Suspense>}
      </DialogContent>
    </Dialog>
  </CitationContext>
}
