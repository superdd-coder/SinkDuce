import { useEffect, useRef } from 'react'
import { getPendingReportComments, type ReportReference } from '@/api/client'
import { useAppStore } from '@/stores/app-store'
import { reviewRequests } from '@/lib/report-review'

const EMPTY: never[] = []
const loads = new Map<string, number>()
export async function refreshReportComments(sid: string) {
  const epoch=useAppStore.getState().reportReviewEpoch[sid]||0
  const token = (loads.get(sid) || 0)+1
  loads.set(sid,token)
  const comments=await getPendingReportComments(sid)
  if (Array.isArray(comments) && loads.get(sid) === token && (useAppStore.getState().reportReviewEpoch[sid]||0)===epoch) useAppStore.getState().setReportComments(sid,comments)
}
export function consumeReportComments(sid: string, ids: string[]) {
  loads.set(sid,(loads.get(sid)||0)+1)
  const store=useAppStore.getState()
  store.setReportComments(sid,(store.reportComments[sid]||[]).filter(c=>!ids.includes(c.id)))
}
export function useReportReview(sid: string | null, busy=false) {
  const comments=useAppStore(s=>sid ? s.reportComments[sid] || EMPTY : EMPTY)
  useEffect(()=> {if(sid) void refreshReportComments(sid).catch(()=>{})},[sid,busy])
  return {comments,requests:reviewRequests(comments),hasComments:comments.length>0}
}
export function useReportReviewNavigation(sid: string | null, onOpen: (report: ReportReference)=>void) {
  const focus=useAppStore(s=>s.reportCommentFocus)
  const callback=useRef(onOpen)
  useEffect(()=>{callback.current=onOpen},[onOpen])
  useEffect(()=>{if(focus?.report.session_id===sid) callback.current(focus.report)},[focus,sid])
}

export function consumeSubmittedReviews(sid:string,reviews:unknown){
  if(!Array.isArray(reviews))return
  consumeReportComments(sid,reviews.flatMap(b=>Array.isArray(b?.comments)?b.comments.flatMap((c:{id?:unknown})=>typeof c.id==='string'?[c.id]:[]):[]))
}
