import { ReportCommentRail, type CommentRailEntry } from "./report-comment-rail"
import { passageRange, installHighlights, focusReviewPassage, reviewPassageTarget, type TextPassage } from "@/lib/report-review-dom"
import { CitationScope } from "./citation-scope"
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { MessageSquarePlus, MessageSquareText, History, Loader2 } from 'lucide-react'
import { addReportComment, editReportComment, deleteReportComment, getReportComments, getReportReviewHistory, getChatReport, decideReportSuggestion, getReportFollowUpContext, followUpReportSuggestion, type ChatReport } from '@/api/client'
import { Button } from '@/components/ui/button'
import { useAppStore } from '@/stores/app-store'
import { useT } from '@/i18n/use-t'
import { refreshReportComments } from '@/hooks/use-report-review'
import { commentAnchor, relocateReviewDraft, withReportBlocks, type CommentAnchor, type ReportComment, type ReviewOutcome, type ReviewBatch } from '@/lib/report-review'
import { AnswerMarkdown } from './answer-markdown'
import {CommentCard} from './report-review-card'
import {ReportTrackedBody} from './report-tracked-body'
import {latestSuggestions,suggestionDiscussion} from '@/lib/report-revision-tracking'
import type {RevisionSuggestion} from '@/lib/report-review'
import './report-review.css'

function cleanText(range: Range) {
  const fragment=range.cloneContents()
  fragment.querySelectorAll('button, [aria-hidden="true"], svg, [hidden], [data-review-deletion]').forEach(n=>n.remove())
  return fragment.textContent||''
}
function ancestorBlock(node: Node | null): HTMLElement | null {
  return (node instanceof Element ? node : node?.parentElement)?.closest<HTMLElement>('[data-report-line]')||null
}
function blockAt(root: HTMLElement, line: number) {
  return [...root.querySelectorAll<HTMLElement>('[data-report-line]')].find(el=>Number(el.dataset.reportLine)<=line && Number(el.dataset.reportEnd)>=line)
}
interface ReviewRow { key:string; comment:ReportComment; index:number; history:boolean; outcome?:ReviewOutcome; targetLine?:number; stale?:boolean; suggestion?:RevisionSuggestion; rounds?:RevisionSuggestion[]; followUps?:ReportComment[] }
function scrollToElement(el?: Element | null) {
  el?.scrollIntoView({block:'center',behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'})
}


function DiffExcerpt({source, passages, name, kind}: {source:string;passages:TextPassage[];name:string;kind:'added'|'removed'}) {
  const root=useRef<HTMLDivElement>(null)
  useEffect(()=>{if(!root.current)return;const ranges=passages.map(p=>passageRange(root.current!,p)).filter((r):r is Range=>!!r);return installHighlights(name,ranges,kind)},[source,passages,name,kind])
  return <div ref={root}><AnswerMarkdown source={source} restrictImages/></div>
}

export function ReportReviewPanel({report, latest=true, children, onRailOpenChange, toolbarTarget, onReportChange, onReportRefresh}: {report:ChatReport;latest?:boolean;children?:ReactNode;onRailOpenChange?:(open:boolean)=>void;toolbarTarget?:HTMLElement|null;onReportChange?:(report:ChatReport)=>void;onReportRefresh?:(report:ChatReport)=>void}) {
  const t=useT(), sid=report.session_id, rid=report.id, revision=report.revision
  const draftKey=`${sid}/${rid}/${revision}`
  const drafts=useAppStore(s=>s.reportCommentDrafts)
  const draft=drafts[draftKey]
  const draftAnchor=draft?.anchor, draftStale=draft?.anchor_stale
  const focus=useAppStore(s=>s.reportCommentFocus)
  const pending=useAppStore(s=>s.reportComments[sid])
  const [comments,setComments]=useState<ReportComment[]>([]),[railOpen,setRailOpen]=useState(false)
  const newestRound=report.suggestions?.at(-1)?.id
  const [trackingChoice,setTrackingChoice]=useState<{round?:string;open:boolean}>(()=>({round:newestRound,open:!!report.suggestions?.some(g=>g.decision==='pending')}))
  const tracking=trackingChoice.round===newestRound?trackingChoice.open:!!newestRound
  const setTracking=useCallback((next:boolean|((current:boolean)=>boolean))=>setTrackingChoice(current=>{const open=current.round===newestRound?current.open:!!newestRound;return {round:newestRound,open:typeof next==='function'?next(open):next}}),[newestRound])
  const [parentReport,setParentReport]=useState<ChatReport|null>(null)
  const [reviewHistory,setReviewHistory]=useState<ReviewBatch[]>([])
  const completedReview=reviewHistory.find(b=>(b.result_revision??b.revision)===revision)
  const reviewResult=completedReview||report.review
  const handledFocus=useRef<number|null>(null)
  const [activeId,setActiveId]=useState(''),[error,setError]=useState(''),[saving,setSaving]=useState(false)
  const [focusNonce,setFocusNonce]=useState(0)
  const [selection,setSelection]=useState<{anchor:CommentAnchor;x:number;y:number}|null>(null)
  const [hover,setHover]=useState<{line:number;end:number;x:number;y:number;quote:string}|null>(null)
  const root=useRef<HTMLDivElement>(null), body=useRef<HTMLDivElement>(null), rail=useRef<HTMLElement>(null)
  const draftRange=useRef<Range|null>(null)
  const selectionFrame=useRef<number|null>(null)
  const previousActive=useRef('')
  const mounted=useRef(true)
  const displayedDraft=useRef(draftKey)
  useLayoutEffect(()=>{displayedDraft.current=draftKey},[draftKey])
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[])
  useEffect(()=>{
    if(!latest||draft)return
    const previous=Object.entries(drafts).filter(([key])=>key.startsWith(`${sid}/${rid}/`)&&Number(key.split('/').at(-1))<revision)
      .sort(([a],[b])=>Number(b.split('/').at(-1))-Number(a.split('/').at(-1)))[0]
    if(!previous)return
    let alive=true
    const [oldKey,oldDraft]=previous
    void getChatReport(sid,rid,Number(oldKey.split('/').at(-1))).then(parent=>{
      const store=useAppStore.getState()
      if(!alive||store.reportCommentDrafts[draftKey]||store.reportCommentDrafts[oldKey]!==oldDraft)return
      store.setReportCommentDraft(draftKey,relocateReviewDraft(oldDraft,parent.markdown,report.markdown,parent.blocks,report.blocks))
      store.setReportCommentDraft(oldKey,null)
      setRailOpen(true)
    }).catch(()=>{})
    return()=>{alive=false}
  },[sid,rid,revision,draftKey,drafts,draft,latest,report.markdown,report.blocks])
  const refresh=useCallback(async()=>{
    const rows=await getReportComments(sid,rid,revision)
    if(mounted.current&&displayedDraft.current===draftKey) setComments(rows)
    await refreshReportComments(sid)
  },[sid,rid,revision,draftKey])
  const refreshSnapshot=onReportRefresh??(latest?onReportChange:undefined)
  useEffect(()=>{
    let alive=true
    void Promise.all([getReportComments(sid,rid,revision),getReportReviewHistory(sid,rid)]).then(async([rows,history])=>{
      if(!alive)return
      setComments(rows);setReviewHistory(history)
      const missingAnswer=history.some(batch=>batch.status==='unchanged'&&(batch.result_revision??batch.revision)===revision
        &&batch.comments.some(comment=>!report.suggestions?.some(group=>group.comments.some(old=>old.id===comment.id))))
      if(refreshSnapshot&&missingAnswer){
        const updated=await getChatReport(sid,rid,revision)
        if(alive&&updated.session_id===sid&&updated.id===rid&&updated.revision===revision)refreshSnapshot(updated)
      }
    }).catch(()=>{if(alive)setError(t('chat.review.loadFailed'))})
    return()=>{alive=false}
  },[sid,rid,revision,pending,t,refreshSnapshot,report.suggestions])
  const snapshot=focus?.report.id===rid&&focus.report.session_id===sid&&focus.report.revision===revision?focus.snapshot:undefined
  const currentRows=useMemo<ReviewRow[]>(()=>comments.filter(c=>c.state!=='resolved'&&![...reviewHistory,...(report.review?[report.review]:[])].some(b=>
    (!c.batch_id||c.batch_id===b.id)&&(b.comments||[]).some(old=>old.id===c.id&&old.text===c.text))).map((comment,index)=>({key:comment.id,comment,index,history:false,stale:comment.anchor_stale})),[comments,reviewHistory,report.review])
  const historyRows=useMemo<ReviewRow[]>(()=>{
    if(report.suggestions?.length){
      const groups=latestSuggestions(report.suggestions)
      return groups.filter(g=>g.decision!=='answered'||!groups.some(other=>other.decision==='pending'&&suggestionDiscussion(report.suggestions!,g).some(round=>round.id===other.id))).map((g,index)=>{
      const discussion=suggestionDiscussion(report.suggestions!,g)
      const newest=discussion.at(-1)
      const current=newest?.decision==='answered'?newest:g
      const selected=current.comments[0]
      const location=g.patches.length?g:[...discussion].reverse().find(round=>round.patches.some(p=>p.field==='markdown'))
      const patch=location&&(location.focus||location.patches[location.primary_patch||0]||location.patches.find(p=>p.field==='markdown'))
      const currentBlock=selected?.anchor.block_id?report.blocks?.find(block=>block.id===selected.anchor.block_id):undefined
      const targetLine=patch?Array.from(report.markdown).slice(0,patch.start).join('').split('\n').length:currentBlock?.start_line??(current.base_revision===revision?selected?.anchor.start_line:undefined)
      const comment=selected||{id:g.id,session_id:sid,report_id:rid,title:report.title,revision:g.base_revision,author:'agent',text:t('chat.review.directRevision'),state:'resolved' as const,created_at:report.updated_at,updated_at:report.updated_at,anchor:{start_line:targetLine||1,end_line:targetLine||1,quote:patch?.before||report.title,prefix:'',suffix:''}}
      return {key:`history-${g.id}`,comment,index,history:true,outcome:current.results[0],suggestion:g,targetLine,stale:true,rounds:discussion.filter(old=>old.id!==current.id)}
    })
    }
    const rows=[...(reviewResult?.comments||[])]
    if(snapshot&&!rows.some(c=>c.id===snapshot.id))rows.push(snapshot)
    return rows.map((comment,index)=>{
      const outcome=reviewResult?.results?.find(result=>result.comment_id===comment.id)
      const change=report.changes?.find(change=>outcome?.change_ids?.includes(change.id))
      return {key:`history-${comment.id}`,comment,index,history:true,outcome,targetLine:change?.new_start,stale:comment.revision!==revision||comment.anchor_stale}
    })
  },[reviewResult,snapshot,report,revision,sid,rid,t])
  const followUpParent=useCallback((comment:ReportComment)=>historyRows.find(row=>comment.parent_group_id&&(row.suggestion?.id===comment.parent_group_id||row.rounds?.some(round=>round.id===comment.parent_group_id)))
    ||(!comment.parent_group_id?historyRows.find(row=>comment.thread_id&&row.suggestion?.thread_ids.includes(comment.thread_id)):undefined),[historyRows])
  const visible=useMemo(()=>{
    if(!railOpen||!tracking)return [...(railOpen?currentRows:[]),...(tracking?historyRows:[])]
    return [...currentRows.filter(row=>!followUpParent(row.comment)),...historyRows.map(row=>({...row,
      followUps:currentRows.filter(current=>followUpParent(current.comment)?.key===row.key).map(current=>current.comment),
    }))]
  },[railOpen,currentRows,tracking,historyRows,followUpParent])
  useEffect(()=>{
    if(!tracking||!report.parent_revision)return
    let alive=true
    void getChatReport(sid,rid,report.parent_revision).then(parent=>{if(alive)setParentReport(parent)}).catch(()=>{if(alive)setError(t('chat.reports.loadFailed'))})
    return()=>{alive=false}
  },[tracking,report.parent_revision,sid,rid,t])
  const locate=useCallback((row:ReviewRow)=>{
    const parent=!row.history&&tracking?followUpParent(row.comment):undefined
    setActiveId(parent?.key||row.key)
    setFocusNonce(n=>n+1)
    if(row.history)setTracking(true);else setRailOpen(true)
    if(!body.current)return
    // Wait for a newly opened tracking layer; never scroll source and rail in succession.
    requestAnimationFrame(()=>{if(body.current)scrollToElement(reviewPassageTarget(body.current,{anchor:row.comment.anchor,groupId:row.suggestion?.id,targetLine:row.targetLine,stale:row.stale}))})
  },[setTracking,tracking,followUpParent])
  useEffect(()=>{
    if(focus?.report.session_id!==sid||focus.report.id!==rid||focus.report.revision!==revision)return
    const row=(focus.snapshot?historyRows:currentRows).find(row=>row.comment.id===focus.comment_id)
    if(row&&handledFocus.current!==focus.nonce){handledFocus.current=focus.nonce;locate(row)}
  // A focus request is consumed once; fetching comments can complete after navigation.
  },[focus,sid,rid,revision,currentRows,historyRows,locate])

  useLayoutEffect(()=>{
    const row=visible.find(row=>row.key===activeId)
    if(!body.current||!row)return
    return focusReviewPassage(body.current,{anchor:row.comment.anchor,groupId:row.suggestion?.id,targetLine:row.targetLine,stale:row.stale},`report-focus-${rid}`)
  },[activeId,focusNonce,visible,report,rid])

  useEffect(()=>{
    const content=body.current
    if(!content)return
    const elements=[...content.querySelectorAll<HTMLElement>('[data-report-line]')]
    for(const el of elements){el.removeAttribute('data-review-commented');el.removeAttribute('data-review-active');el.removeAttribute('data-review-changed')}
    for(const row of visible)if(!row.history&&!row.stale){const el=blockAt(content,row.comment.anchor.start_line);if(el){el.dataset.reviewCommented=row.key;if(activeId===row.key)el.dataset.reviewActive='true'}}
    if(tracking&&!report.suggestions?.length)for(const change of report.changes||[]) for(const el of elements) {
      if(change.new_end>=Number(el.dataset.reportLine)&&change.new_start<=Number(el.dataset.reportEnd))el.dataset.reviewChanged='true'
    }
    const ranges:Range[]=[],changedRanges:Range[]=[]
    if(draftAnchor&&railOpen&&!draftStale){
      const block=blockAt(content,draftAnchor.start_line)
      if(block){block.dataset.reviewActive='true';block.dataset.reviewCommented='draft';const range=draftRange.current||passageRange(content,draftAnchor);if(range)ranges.push(range)}
    }
    for(const row of visible){
      const c=row.comment
      const block=blockAt(content,c.anchor.start_line)
      if(!block||row.history||row.stale||c.anchor.kind==='block')continue
      const range=passageRange(content,c.anchor)
      if(range)ranges.push(range)
    }
    if(tracking&&!report.suggestions?.length)for(const change of report.changes||[])for(const el of elements){
      if(change.new_end<Number(el.dataset.reportLine)||change.new_start>Number(el.dataset.reportEnd))continue
      for(const p of change.added||[]){const range=passageRange(el,p);if(range){changedRanges.push(range);el.dataset.reviewChanged='partial'}}
    }
    const clearComments=installHighlights(`report-comment-${rid}`,ranges,'comment')
    const clearChanges=installHighlights(`report-change-${rid}`,changedRanges,'added')
    return()=>{clearComments();clearChanges()}
  },[visible,railOpen,tracking,activeId,report,rid,draftAnchor,draftStale])

  const capture=useCallback(()=>{
    const currentDraft=useAppStore.getState().reportCommentDrafts[draftKey]
    if(!latest||(currentDraft&&!currentDraft.anchor_stale)||!body.current){setSelection(null);return}
    const selection=window.getSelection()
    if(!selection||selection.isCollapsed||!selection.rangeCount){setSelection(null);return}
    const range=selection.getRangeAt(0)
    if([range.startContainer,range.endContainer].some(node=>(node instanceof Element?node:node.parentElement)?.closest('[data-review-deletion]'))||range.cloneContents().querySelector?.('[data-review-deletion]')){setSelection(null);return}
    const boundary=(node:Node,offset:number,end:boolean)=>{
      const direct=ancestorBlock(node)
      if(direct)return direct
      let child=node.childNodes?.[end?offset-1:offset]
      while(child?.childNodes?.length)child=child.childNodes[end?child.childNodes.length-1:0]
      return ancestorBlock(child||null)
    }
    const first=boundary(range.startContainer,range.startOffset,false),last=boundary(range.endContainer,range.endOffset,true)
    if(!first||!last||!body.current.contains(first)||!body.current.contains(last)){setSelection(null);return}
    const quote=cleanText(range)
    if(!quote.trim()||quote.length>8000)return
    const before=document.createRange(),after=document.createRange()
    before.selectNodeContents(first);before.setEnd(range.startContainer,range.startOffset)
    after.selectNodeContents(last);after.setStart(range.endContainer,range.endOffset)
    draftRange.current=range.cloneRange()
    const rect=range.getBoundingClientRect(),container=root.current!.getBoundingClientRect()
    const prefix=cleanText(before),suffix=cleanText(after)
    setSelection({anchor:commentAnchor(prefix+quote+suffix,quote,prefix.length,Number(first.dataset.reportLine),Number(last.dataset.reportEnd)),x:Math.max(8,Math.min(rect.left-container.left,container.width-150)),y:rect.top-container.top-40})
  },[latest,draftKey])
  const scheduleCapture=useCallback(()=>{
    if(selectionFrame.current!==null)cancelAnimationFrame(selectionFrame.current)
    selectionFrame.current=requestAnimationFrame(()=>{selectionFrame.current=null;capture()})
  },[capture])
  useEffect(()=>{
    if(typeof document==='undefined')return
    document.addEventListener('selectionchange',scheduleCapture)
    return()=>{document.removeEventListener('selectionchange',scheduleCapture);if(selectionFrame.current!==null)cancelAnimationFrame(selectionFrame.current)}
  },[scheduleCapture])
  const begin=(anchor:CommentAnchor)=>{
    if(anchor.kind==='block')draftRange.current=null
    previousActive.current=activeId
    useAppStore.getState().setReportCommentDraft(draftKey,{anchor:withReportBlocks(anchor,report.blocks),text:draft?.text||''})
    setActiveId('draft');setSelection(null);setHover(null);setRailOpen(true)
    window.getSelection()?.removeAllRanges()
  }
  const cancelDraft=()=>{
    if(selectionFrame.current!==null)cancelAnimationFrame(selectionFrame.current)
    selectionFrame.current=null;draftRange.current=null
    useAppStore.getState().setReportCommentDraft(draftKey,null)
    setSelection(null);setHover(null);setActiveId(previousActive.current);setRailOpen(currentRows.length>0)
    window.getSelection()?.removeAllRanges()
    body.current?.focus({preventScroll:true})
  }
  const save=async()=>{
    if(!draft?.text.trim()||draft.anchor_stale||saving)return
    setSaving(true);setError('')
    try{const c=await addReportComment(sid,rid,revision,draft.text,draft.anchor);useAppStore.getState().setReportCommentDraft(draftKey,null);await refresh();if(mounted.current){setActiveId(c.id);setRailOpen(true)}}catch(e){if(mounted.current)setError(e instanceof Error?e.message:t('chat.review.saveFailed'))}finally{if(mounted.current)setSaving(false)}
  }
  const showRail=railOpen||(tracking&&historyRows.length>0)
  useLayoutEffect(()=>{onRailOpenChange?.(showRail);return()=>onRailOpenChange?.(false)},[showRail,onRailOpenChange])
  const railEntries=useMemo<CommentRailEntry[]>(()=>[
    ...visible.map(row=>({id:row.key,anchor:row.comment.anchor,text:row.comment.text,index:row.index,history:row.history,stale:row.stale,targetLine:row.targetLine,groupId:row.suggestion?.id})),
    ...(draftAnchor&&railOpen?[{id:'draft',anchor:draftAnchor,text:'',index:currentRows.length,draft:true,stale:draftStale}]:[]),
  ],[visible,currentRows.length,draftAnchor,draftStale,railOpen])
  const controls=<div className="pm-review-tools">
    {(!!report.parent_revision||!!reviewResult||!!snapshot||!!report.suggestions?.length)&&<Button variant="ghost" size="sm" aria-label={t('chat.review.track')} title={t('chat.review.track')} aria-pressed={tracking} onClick={()=>{setTracking(v=>!v);setSelection(null)}}><History className="size-3.5"/><span className="pm-review-tool-label">{t('chat.review.track')}</span></Button>}
    <Button variant="ghost" size="sm" aria-label={t('chat.review.comments')} title={t('chat.review.comments')} aria-pressed={railOpen} onClick={()=>setRailOpen(v=>!v)}><MessageSquareText className="size-3.5"/><span className="pm-review-tool-label">{t('chat.review.comments')}</span>{currentRows.length>0&&<span className="pm-review-tool-count">{currentRows.length}</span>}</Button>
  </div>
  return <div ref={root} onPointerLeave={()=>setHover(null)} className={`pm-review-surface${showRail?' has-comments':''}${tracking?' is-tracking':''}`}>
    {toolbarTarget===undefined?controls:toolbarTarget?createPortal(controls,toolbarTarget):null}
    <div className="pm-review-columns"><div className="pm-review-document" ref={body} tabIndex={-1} onPointerUp={scheduleCapture} onKeyUp={capture} onClick={e=>{
      if((e.target as Element).closest('button,a')||window.getSelection()?.isCollapsed===false)return
      const group=(e.target as Element).closest<HTMLElement>('[data-review-group]');if(group){
        const candidates=visible.filter(r=>r.suggestion&&group.dataset.reviewGroup?.split(' ').includes(r.suggestion.id))
        const block=(e.target as Element).closest<HTMLElement>('[data-report-line]')
        const row=candidates.find(r=>r.targetLine!==undefined&&block&&Number(block.dataset.reportLine)<=r.targetLine&&Number(block.dataset.reportEnd)>=r.targetLine)||candidates[0]
        if(row)locate(row)
      }
      const el=(e.target as Element).closest<HTMLElement>('[data-review-commented]');if(el){const row=visible.find(row=>row.key===el.dataset.reviewCommented);if(row)locate(row)}
    }} onPointerMove={e=>{
      if(!latest||draft||e.buttons)return
      const block=(e.target as Element).closest<HTMLElement>('.chat-md-table-wrap')||(e.target as Element).closest<HTMLElement>('[data-report-line]')
      if(!block||!body.current?.contains(block))return
      const visual=block.matches('.chat-md-table-wrap')?block:block.querySelector<HTMLElement>('.chat-md-visual,img,svg,pre')
      if(visual){const rect=visual.getBoundingClientRect(),box=root.current!.getBoundingClientRect();const outside=rect.right-box.left+6;setHover({line:Number(block.dataset.reportLine),end:Number(block.dataset.reportEnd),x:outside+28<=box.width?outside:Math.max(0,rect.left-box.left+8),y:Math.max(0,rect.top-box.top+8),quote:Array.from(report.markdown.split('\n').slice(Number(block.dataset.reportLine)-1,Number(block.dataset.reportEnd)).join('\n')).slice(0,8000).join('')})}else setHover(null)
    }} onPointerLeave={e=>{const next=e.relatedTarget;if(next&&'nodeType' in next&&root.current?.contains(next as Node))return;setHover(null)}}>
      {children}
      <div className="chat-answer-markdown"><ReportTrackedBody report={report} tracking={tracking}/></div>
      {tracking&&!report.suggestions?.length&&<section className="pm-review-differences" aria-label={t('chat.review.changes')}><h2>{report.parent_revision?t('chat.review.comparison'):t('chat.review.results')}</h2>
        {(report.changes||[]).map((change,i)=><details key={change.id} data-review-change={change.id} open className="pm-review-difference"><summary>{t(`chat.review.change.${change.kind}`)} <span>{i+1}</span></summary>
          {!!change.before&&<div className="pm-review-before chat-answer-markdown"><span className="pm-review-diff-label">{t('chat.review.before')}</span><CitationScope citations={parentReport?.citations||[]} source={parentReport?.markdown||change.before} sessionId={sid}><DiffExcerpt source={change.before} passages={change.removed||[]} name={`report-old-${rid}-${i}`} kind="removed"/></CitationScope></div>}
          {!!change.after&&<div className="pm-review-after chat-answer-markdown"><span className="pm-review-diff-label">{t('chat.review.after')}</span><DiffExcerpt source={change.after} passages={change.added||[]} name={`report-new-${rid}-${i}`} kind="added"/></div>}
        </details>)}
      </section>}
    </div>
    <ReportCommentRail entries={railEntries} activeId={activeId} focusNonce={focusNonce} documentRef={body} railRef={rail} draftRange={draftRange} open={showRail} tracking={tracking}
      onActivate={id=>{if(id==='draft')setActiveId(id);else{const row=visible.find(row=>row.key===id);if(row)locate(row)}}}>
      {visible.map(row=><CommentCard key={row.key} comment={row.comment} index={row.index} active={activeId===row.key} history={row.history} outcome={row.outcome} suggestion={row.suggestion} rounds={row.rounds} followUps={row.followUps} latest={latest} onFocus={()=>locate(row)}
        relatedSuggestions={row.suggestion?.instruction_id?historyRows.filter(peer=>peer.suggestion?.instruction_id===row.suggestion?.instruction_id).map(peer=>peer.suggestion!):[]}
        onFocusRelated={gid=>{const related=historyRows.find(peer=>peer.suggestion?.id===gid);if(related)locate(related)}}
        onAcceptInstruction={row.suggestion&&latest?async operationId=>{const updated=await decideReportSuggestion(sid,rid,row.suggestion!.id,revision,'accepted',operationId,'instruction');onReportChange?.(updated);useAppStore.getState().bumpReportHistory();await refreshReportComments(sid)}:undefined}
        onFocusFollowUp={comment=>{const current=currentRows.find(current=>current.comment.id===comment.id);if(current)locate(current)}}
        onSaveFollowUp={async(comment,text)=>{await editReportComment(sid,rid,comment.id,text);await refresh()}}
        onDeleteFollowUp={async comment=>{await deleteReportComment(sid,rid,comment.id);await refresh()}}
        onDecision={row.suggestion&&latest?async(decision,operationId)=>{const updated=await decideReportSuggestion(sid,rid,row.suggestion!.id,revision,decision,operationId);onReportChange?.(updated);useAppStore.getState().bumpReportHistory();await refreshReportComments(sid)}:undefined}
        onPrepareFollowUp={row.suggestion&&latest?()=>getReportFollowUpContext(sid,rid,row.suggestion!.id,revision):undefined}
        onFollowUp={row.suggestion&&latest?async(text,operationId)=>{const c=await followUpReportSuggestion(sid,rid,row.suggestion!.id,revision,text,operationId);await refresh();setRailOpen(true);setActiveId(tracking?row.key:c.id)}:undefined}
        onSave={!row.history&&row.comment.state==='pending'?async text=>{await editReportComment(sid,rid,row.comment.id,text);await refresh()}:undefined}
        onDelete={!row.history?async()=>{await deleteReportComment(sid,rid,row.comment.id);await refresh()}:undefined}/>)}
      {draft&&railOpen&&<form className="pm-review-comment is-draft" data-review-editor="draft" onSubmit={e=>{e.preventDefault();void save()}}><div className="pm-review-draft-title"><MessageSquarePlus className="size-3.5"/>{t('chat.review.add')}</div><p className="pm-review-quote">{draft.anchor.kind==='block'?t('chat.review.blockAtLine',{n:draft.anchor.start_line}):draft.anchor.quote}</p>{draft.anchor_stale&&<p className="pm-review-reattach">{t('chat.review.reattach')}</p>}<textarea aria-label={t('chat.review.writeComment')} placeholder={t('chat.review.writeComment')} value={draft.text} maxLength={4000} onChange={e=>useAppStore.getState().setReportCommentDraft(draftKey,{...draft,text:e.target.value})}/><div className="pm-review-editor-actions"><Button type="button" size="sm" variant="ghost" onClick={cancelDraft}>{t('common.cancel')}</Button><Button type="submit" size="sm" disabled={saving||!!draft.anchor_stale||!draft.text.trim()}>{saving?<Loader2 className="size-3 animate-spin"/>:t('common.save')}</Button></div></form>}
    </ReportCommentRail></div>
    {error&&<p className="pm-report-error" role="alert">{error}</p>}
    {selection&&<button type="button" className="pm-review-selection" style={{left:selection.x,top:selection.y}} onPointerDown={e=>e.preventDefault()} onClick={()=>begin(selection.anchor)}><MessageSquarePlus className="size-3.5"/>{t('chat.review.add')}</button>}
    {hover&&!selection&&!draft&&<button type="button" className="pm-review-block-action" style={{left:hover.x,top:hover.y}} aria-label={t('chat.review.addBlock')} onPointerEnter={()=>setHover(hover)} onClick={()=>begin({start_line:hover.line,end_line:hover.end,quote:hover.quote,prefix:'',suffix:'',kind:'block'})}><MessageSquarePlus className="size-3.5"/></button>}
  </div>
}
