import {useRef,useState} from 'react'
import {ArrowUpRight,Check,ChevronDown,Loader2,MessageSquarePlus,Pencil,Trash2,X} from 'lucide-react'
import {Button} from '@/components/ui/button'
import {useAppStore} from '@/stores/app-store'
import {useT} from '@/i18n/use-t'
import type {ReportComment,ReviewOutcome,RevisionSuggestion} from '@/lib/report-review'
import type {ReportFollowUpContext} from '@/api/client'
import {ReportDiscussionChanges} from './report-discussion-changes'

export function CommentCard({comment,index,active,history,outcome,onFocus,onSave,onDelete,suggestion,rounds=[],latest=true,onDecision,onPrepareFollowUp,onFollowUp,followUps=[],onFocusFollowUp,onSaveFollowUp,onDeleteFollowUp,embedded=false,relatedSuggestions=[],onFocusRelated,onAcceptInstruction}:{
  comment:ReportComment;index:number;active:boolean;history?:boolean;outcome?:ReviewOutcome;suggestion?:RevisionSuggestion;rounds?:RevisionSuggestion[];latest?:boolean
  onFocus:()=>void;onSave?:(text:string)=>Promise<void>;onDelete?:()=>Promise<void>
  onDecision?:(decision:'accepted'|'rejected',operationId:string)=>Promise<void>
  onPrepareFollowUp?:()=>Promise<ReportFollowUpContext>;onFollowUp?:(text:string,operationId:string)=>Promise<void>
  followUps?:ReportComment[];embedded?:boolean
  onFocusFollowUp?:(comment:ReportComment)=>void
  onSaveFollowUp?:(comment:ReportComment,text:string)=>Promise<void>;onDeleteFollowUp?:(comment:ReportComment)=>Promise<void>
  relatedSuggestions?:RevisionSuggestion[];onFocusRelated?:(id:string)=>void;onAcceptInstruction?:(operationId:string)=>Promise<void>
}) {
  const t=useT(),editKey=`${comment.session_id}/${comment.report_id}/edit/${comment.id}`,followKey=`${editKey}/followup`
  const editDraft=useAppStore(s=>s.reportCommentDrafts[editKey]),followDraft=useAppStore(s=>s.reportCommentDrafts[followKey])
  const [editing,setEditing]=useState(!!editDraft),[busy,setBusy]=useState(false),[error,setError]=useState('')
  const [follow,setFollow]=useState<ReportFollowUpContext|null>(null)
  const intents=useRef<Record<string,string>>({})
  const text=editDraft?.text??comment.text
  const setText=(text:string)=>useAppStore.getState().setReportCommentDraft(editKey,{anchor:comment.anchor,text})
  const run=async(action:()=>Promise<void>)=>{setBusy(true);setError('');try{await action();setEditing(false);useAppStore.getState().setReportCommentDraft(editKey,null)}catch(e){setError(e instanceof Error?e.message:t('chat.review.saveFailed'))}finally{setBusy(false)}}
  const intent=(key:string)=>intents.current[key]||(intents.current[key]=crypto.randomUUID())
  const pending=suggestion?.decision==='pending'
  const quote=history?suggestion?.location_quote||comment.anchor.quote:comment.deleted?t('chat.review.deletedPassage'):comment.current_text??comment.anchor.quote
  const pendingRelated=relatedSuggestions.filter(g=>g.decision==='pending')
  const status=suggestion?t(`chat.review.decision.${suggestion.decision}`):outcome?t(`chat.review.status.${outcome.status}`):''
  return <section tabIndex={0} aria-current={active?'true':undefined} data-review-card={history?`history-${suggestion?.id||comment.id}`:comment.id} className={`pm-review-comment${active?' is-active':''}${history?' is-history':''}${embedded?' is-followup':''}`}
    onClick={e=>{if(embedded)e.stopPropagation();if(!(e.target as Element).closest('button,a,input,textarea,summary,details'))onFocus()}}
    onKeyDown={e=>{if(e.target===e.currentTarget&&(e.key==='Enter'||e.key===' ')){e.preventDefault();onFocus()}}}>
    <div className="pm-review-comment-head"><button type="button" onClick={onFocus} className="pm-review-location">{!embedded&&<span className="pm-review-number">{index+1}</span>}<span>{t(history?'chat.review.revision':embedded?'chat.review.followUp':'chat.review.comment')}</span><ArrowUpRight className="size-3"/></button>
      {status&&<span className={`pm-review-status${pending?' is-pending':''}`}>{status}</span>}
      {!history&&<div className="pm-review-actions">{onSave&&<Button variant="ghost" size="icon-sm" disabled={busy} aria-label={t('chat.review.edit')} onClick={()=>{if(editing){setEditing(false);useAppStore.getState().setReportCommentDraft(editKey,null)}else{setText(comment.text);setEditing(true)}}}><Pencil className="size-3"/></Button>}
      {onDelete&&<Button variant="ghost" size="icon-sm" disabled={busy} aria-label={t('chat.review.delete')} onClick={()=>run(onDelete)}><Trash2 className="size-3"/></Button>}</div>}
    </div>
    {!embedded&&<button type="button" className="pm-review-quote" onClick={onFocus}>{quote}</button>}
    {editing&&!history?<form onSubmit={e=>{e.preventDefault();if(onSave)void run(()=>onSave(text))}}><textarea aria-label={t('chat.review.comment')} value={text} maxLength={4000} onChange={e=>setText(e.target.value)} autoFocus/><div className="pm-review-editor-actions"><Button type="button" variant="ghost" size="sm" onClick={()=>{setEditing(false);useAppStore.getState().setReportCommentDraft(editKey,null)}}>{t('common.cancel')}</Button><Button type="submit" size="sm" disabled={busy||!text.trim()}>{busy?<Loader2 className="size-3 animate-spin"/>:t('common.save')}</Button></div></form>:(!suggestion||suggestion.comments.length>0)&&<p className="pm-review-request">{comment.text}</p>}
    {suggestion&&suggestion.comments.length>1&&<details className="pm-review-related-comments"><summary>{t('chat.review.relatedComments',{n:suggestion.comments.length-1})}</summary>{suggestion.comments.slice(1).map(c=><p key={c.id}>{c.text}</p>)}</details>}
    {(outcome||suggestion)&&<div className="pm-review-outcome"><p>{outcome?.summary||suggestion?.summary||t('chat.review.genericChange')}</p></div>}
    {history&&suggestion&&suggestion.patches.length>0&&<ReportDiscussionChanges sessionId={comment.session_id} reportId={comment.report_id} round={suggestion}/>}
    {history&&suggestion&&latest&&<div className="pm-review-decisions">
      {pending&&onDecision&&<><Button variant="ghost" size="sm" aria-label={t('chat.review.accept')} disabled={busy} onClick={()=>run(()=>onDecision('accepted',intent('accepted')))}><Check className="size-3.5"/>{t('chat.review.accept')}</Button><Button variant="ghost" size="sm" aria-label={t('chat.review.reject')} disabled={busy} onClick={()=>run(()=>onDecision('rejected',intent('rejected')))}><X className="size-3.5"/>{t('chat.review.reject')}</Button></>}
      {onPrepareFollowUp&&<Button variant="ghost" size="sm" aria-label={t('chat.review.followUp')} disabled={busy} onClick={()=>run(async()=>{setFollow(await onPrepareFollowUp())})}><MessageSquarePlus className="size-3.5"/>{t('chat.review.followUp')}</Button>}
    </div>}
    {history&&relatedSuggestions.length>1&&<details className="pm-review-instruction" data-instruction-id={suggestion?.instruction_id}>
      <summary><span>{t('chat.review.instructionGroup')}</span><span className="pm-review-instruction-toggle"><span className="pm-review-instruction-count">{t('chat.review.locationCount',{n:relatedSuggestions.length})}</span><ChevronDown className="size-3"/></span></summary>
      <div className="pm-review-instruction-body">
        <p className="pm-review-instruction-request">{comment.text}</p>
        <ol>{relatedSuggestions.map((related,i)=><li key={related.id}><button type="button" aria-current={related.id===suggestion?.id?'location':undefined} onClick={()=>onFocusRelated?.(related.id)}><span className="pm-review-instruction-index">{i+1}</span><span>{related.location_quote||related.focus?.before||related.patches[0]?.before||t('chat.review.directChange')}</span><span className={`pm-review-instruction-state is-${related.decision}`} aria-label={t(`chat.review.decision.${related.decision}`)}>{related.decision==='accepted'?<Check className="size-3"/>:related.decision==='rejected'?<X className="size-3"/>:<span/>}</span></button></li>)}</ol>
        {latest&&pendingRelated.length>1&&onAcceptInstruction&&<Button size="sm" disabled={busy} className="pm-review-instruction-accept" onClick={()=>run(()=>onAcceptInstruction(intent('accept-instruction')))}><Check className="size-3.5"/>{t('chat.review.acceptLocations',{n:pendingRelated.length})}</Button>}
      </div>
    </details>}
    {follow&&<form className="pm-review-followup" onSubmit={e=>{e.preventDefault();if(onFollowUp&&followDraft?.text.trim())void run(async()=>{await onFollowUp(followDraft.text,intent(`follow:${followDraft.text}`));setFollow(null);useAppStore.getState().setReportCommentDraft(followKey,null)})}}>
      <details className="pm-review-followup-context"><summary>{t('chat.review.followUpContext')}</summary><p data-followup-quote={true} className="pm-review-quote">{follow.deleted?t('chat.review.deletedPassage'):follow.current_text}</p></details>
      <textarea autoFocus aria-label={t('chat.review.followUp')} placeholder={t('chat.review.followUpPlaceholder')} value={followDraft?.text||''} maxLength={4000} onChange={e=>useAppStore.getState().setReportCommentDraft(followKey,{anchor:follow.anchor||comment.anchor,text:e.target.value})}/>
      <div className="pm-review-editor-actions"><Button type="button" variant="ghost" size="sm" onClick={()=>{setFollow(null);useAppStore.getState().setReportCommentDraft(followKey,null)}}>{t('common.cancel')}</Button><Button type="submit" size="sm" disabled={busy||!followDraft?.text.trim()}>{t('common.save')}</Button></div>
    </form>}
    {rounds.length>0&&<details className="pm-review-round-history"><summary>{t('chat.review.discussionHistory',{n:rounds.length})}</summary><ol className="pm-review-timeline">{rounds.map((round,i)=><li key={round.id} className="pm-review-round" data-discussion-round={round.id}>
      <div className="pm-review-round-heading"><span className="pm-review-round-number">{t('chat.review.roundNumber',{n:i+1})}</span><span className="pm-review-round-status">{t(`chat.review.decision.${round.decision}`)}</span></div>
      <p className="pm-review-round-request">{round.comments.map(c=>c.text).join(' · ')||t('chat.review.directChange')}</p>
      {!!round.summary&&<p className="pm-review-round-reply">{round.summary}</p>}
      {round.patches.length>0&&<ReportDiscussionChanges key={round.id} sessionId={comment.session_id} reportId={comment.report_id} round={round} roundNumber={i+1}/>}
    </li>)}</ol></details>}
    {!!followUps.length&&<div className="pm-review-thread-followups">{followUps.map((current,i)=><CommentCard key={current.id} comment={current} index={i} active={active} embedded
      onFocus={()=>onFocusFollowUp?.(current)} onSave={current.state==='pending'&&onSaveFollowUp?text=>onSaveFollowUp(current,text):undefined}
      onDelete={onDeleteFollowUp?()=>onDeleteFollowUp(current):undefined}/>)}</div>}
    {comment.state==='submitted'&&!history&&<small>{t('chat.review.submitted')}</small>}
    {comment.anchor_stale&&!history&&<small>{t('chat.review.previousAnchor')}</small>}
    {error&&<p role="alert" className="pm-report-error">{error}</p>}
  </section>
}
