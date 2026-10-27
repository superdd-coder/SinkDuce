import { useCallback, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { ChevronDown, MessageSquareText, ArrowUpRight } from 'lucide-react'
import { Popover } from '@/components/ui/popover'
import { useT } from '@/i18n/use-t'
import { useAppStore } from '@/stores/app-store'
import { commentReference, type ReportComment, type ReviewBatch } from '@/lib/report-review'
import './report-review.css'

export function ReportReviewChip({ comments, disabled=false, history=false }: {comments:ReportComment[];disabled?:boolean;history?:boolean}) {
  const t=useT()
  const [open,setOpen]=useState(false)
  const [surfaceActive,setSurfaceActive]=useState(false)
  const [origin,setOrigin]=useState({width:152,height:30,cardWidth:360})
  const trigger=useRef<HTMLButtonElement|null>(null)
  const restoreFocus=useRef(false)
  const measure=useCallback(()=>{
    const bounds=trigger.current?.getBoundingClientRect()
    if(!bounds)return
    const parent=trigger.current?.closest('.pm-chat-msg, .pm-qc-bubble')
    const cardWidth=Math.max(bounds.width,Math.min(360,parent?.getBoundingClientRect().width||360))
    setOrigin(previous=>previous.width===bounds.width&&previous.height===bounds.height&&previous.cardWidth===cardWidth?previous:{width:bounds.width,height:bounds.height,cardWidth})
  },[])
  useLayoutEffect(()=>{
    if(!open||!history||typeof ResizeObserver==='undefined')return
    const parent=trigger.current?.closest('.pm-chat-msg, .pm-qc-bubble')
    if(!parent)return
    const observer=new ResizeObserver(measure)
    observer.observe(parent)
    return()=>observer.disconnect()
  },[open,history,measure])
  if (!comments.length) return null
  const summary=<><span className="pm-review-chip-symbol"><MessageSquareText size={14} strokeWidth={1.7} aria-hidden="true"/></span><span className="pm-review-chip-label">{t('chat.review.chipLabel')}</span><span className="pm-review-chip-count">{comments.length}</span><ChevronDown className="size-3" aria-hidden="true"/></>
  return <Popover.Root open={open} onOpenChange={(value,details)=>{
    if(value){restoreFocus.current=false;measure();setSurfaceActive(true)}else restoreFocus.current=['escape-key','close-press','trigger-press'].includes(details?.reason)
    setOpen(value)
  }} onOpenChangeComplete={value=>{
    if(value)return
    setSurfaceActive(false)
    if(restoreFocus.current)requestAnimationFrame(()=>trigger.current?.focus({preventScroll:true}))
    restoreFocus.current=false
  }}>
    <Popover.Trigger ref={trigger} className={`pm-review-chip${history?' is-history':''}`} aria-label={t('chat.review.pill',{n:comments.length})} disabled={disabled} data-surface-active={history&&surfaceActive||undefined}>
      {summary}
    </Popover.Trigger>
    <Popover.Portal><Popover.Positioner side={history?'bottom':'top'} align={history?'end':'start'} sideOffset={history?-origin.height:8}
      collisionAvoidance={history?{side:'none',align:'shift',fallbackAxisSide:'none'}:undefined} className="pm-review-popover-positioner">
      <Popover.Popup className={`pm-review-popover${history?' pm-review-history-popover':''}`} finalFocus={false} style={history?{
        width:origin.cardWidth,'--pm-review-origin-w':`${origin.width}px`,'--pm-review-origin-h':`${origin.height}px`,
      } as CSSProperties:undefined}>
        <Popover.Title className={history?'sr-only':'pm-review-popover-title'}>{t('chat.review.comments')}</Popover.Title>
        {history&&<Popover.Close className="pm-review-collapse" aria-label={t('common.close')}>{summary}</Popover.Close>}
        <div className="pm-review-nav-list">{comments.map((c,i)=><button key={c.id} type="button" className="pm-review-nav-row" onClick={()=>{
          useAppStore.getState().focusReportComment({report:commentReference(c),comment_id:c.id,snapshot:history?c:undefined})
          restoreFocus.current=false
          trigger.current?.focus({preventScroll:true})
          setOpen(false)
        }}><span className="pm-review-number">{i+1}</span><span><strong>{c.text}</strong><small>{c.title} · {c.anchor.quote}</small></span><ArrowUpRight className="size-3.5"/></button>)}</div>
      </Popover.Popup>
    </Popover.Positioner></Popover.Portal>
  </Popover.Root>
}
export function ReportReviewHistory({batches}: {batches?:ReviewBatch[]}) {
  return <ReportReviewChip comments={batches?.flatMap(b=>b.comments)||[]} history/>
}
