import {useId,useRef,useState} from 'react'
import {getChatReport,type ChatReport} from '@/api/client'
import {discussionChanges,comparisonEvidence,discussionSections,discussionChangeKind} from '@/lib/report-discussion-history'
import type {RevisionSuggestion,ReportChange} from '@/lib/report-review'
import {useT} from '@/i18n/use-t'
import {AnswerMarkdown} from './answer-markdown'
import {CitationScope} from './citation-scope'
import {Dialog,DialogContent,DialogTitle,DialogDescription} from '@/components/ui/dialog'
import {ArrowUpRight,Loader2} from 'lucide-react'

/** Load immutable source context only when the user asks for an earlier change. */
export function ReportDiscussionChanges({sessionId,reportId,round,roundNumber}:{sessionId:string;reportId:string;round:RevisionSuggestion;roundNumber?:number}) {
  const t=useT()
  const descriptionId=useId()
  const [open,setOpen]=useState(false)
  const [changes,setChanges]=useState<ReportChange[]|null>(null)
  const [snapshots,setSnapshots]=useState<{before:ChatReport;after:ChatReport}|null>(null)
  const [error,setError]=useState('')
  const loading=useRef(false)
  const [busy,setBusy]=useState(false)
  const load=async()=>{
    if(changes||loading.current)return
    loading.current=true;setBusy(true);setError('')
    try{
      const [before,after]=await Promise.all([getChatReport(sessionId,reportId,round.base_revision),getChatReport(sessionId,reportId,round.result_revision)])
      setChanges(discussionChanges(after,round));setSnapshots({before,after})
    }catch(e){setError(e instanceof Error?e.message:t('chat.reports.loadFailed'))}
    finally{loading.current=false;setBusy(false)}
  }
  const scope=snapshots?comparisonEvidence(snapshots.before,snapshots.after):{citations:[],source:''}
  const sections=snapshots?discussionSections(changes||[],snapshots.before.markdown,snapshots.after.markdown):[]
  return <>
    <button type="button" className="pm-review-compare-trigger" onClick={()=>{setOpen(true);return load()}}>{t('chat.review.compareChanges')}<ArrowUpRight className="size-3"/></button>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent className="pm-review-comparison-dialog" onClick={e=>e.stopPropagation()} aria-describedby={round.comments?.length?descriptionId:undefined}>
      <header className="pm-review-comparison-heading"><DialogTitle>{roundNumber?t('chat.review.roundComparison',{n:roundNumber}):t('chat.review.compareChanges')}</DialogTitle>
        {!!round.comments?.length&&<DialogDescription id={descriptionId}><span className="pm-review-comparison-meta-label">{t('chat.review.userRequest')}</span>{round.comments.map(c=>c.text).join(' · ')}</DialogDescription>}
        {!!round.summary&&<div className="pm-review-comparison-reply"><span>{t('chat.review.agentReply')}</span><p>{round.summary}</p></div>}
      </header>
      {busy&&<div className="pm-review-comparison-loading" role="status"><Loader2 className="size-4 animate-spin"/>{t('common.loading')}</div>}
      {error&&<button type="button" onClick={()=>void load()}>{t('common.retry')}</button>}
      {changes?.length===0&&!busy&&<p className="pm-review-comparison-empty">{t('chat.review.noRoundChanges')}</p>}
      {!!changes?.length&&<div className="pm-revision-legend"><span className="is-removed">{t('chat.review.legendRemoved')}</span><span className="is-added">{t('chat.review.legendAdded')}</span></div>}
      {!!changes?.length&&<div className="pm-review-comparison-scroll"><CitationScope citations={scope.citations} source={scope.source} sessionId={sessionId}>
        {sections.map(section=><section key={section.key} className="pm-revision-section" data-comparison-section={section.key}>
          <h3>{section.title||t('chat.review.documentOpening')}</h3>
          {section.changes.map(change=><div key={change.id} className="pm-revision-passage chat-answer-markdown" data-comparison-pair={change.id}>
            {discussionChangeKind(change)==='citation'&&<span className="pm-revision-caption-label">{t('chat.review.change.citation')}</span>}
            {/(?:^|\n)\s*(?:`{3,}|~{3,})/.test(change.before+'\n'+change.after)?<>
              {change.before&&<div className="pm-revision-visual is-removed"><span>{t('chat.review.legendRemoved')}</span><AnswerMarkdown source={change.before} report restrictImages/></div>}
              {change.after&&<div className="pm-revision-visual is-added"><span>{t('chat.review.legendAdded')}</span><AnswerMarkdown source={change.after} report restrictImages/></div>}
            </>:<AnswerMarkdown source={change.after} previousSource={change.before} restrictImages/>}
          </div>)}
        </section>)}
      </CitationScope></div>}
    </DialogContent></Dialog>
  </>
}
