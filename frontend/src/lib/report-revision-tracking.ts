import type {ReportBlock} from './chat-reports.ts'
import type {RevisionSuggestion,RevisionPatch} from './report-review.ts'

export function latestSuggestions(groups:RevisionSuggestion[]) {
  return groups.filter((g,i)=>g.decision!=='superseded'&&(g.decision==='pending'||!groups.slice(i+1).some(newer=>
    g.location_id?newer.location_id===g.location_id||suggestionDiscussion(groups,newer).some(old=>old.id===g.id):g.thread_ids.some(t=>newer.thread_ids.includes(t)))))
}
export function suggestionDiscussion(groups:RevisionSuggestion[],group:RevisionSuggestion):RevisionSuggestion[] {
  if(!group.location_id)return groups.filter(g=>g.thread_ids.some(id=>group.thread_ids.includes(id)))
  const ids=new Set([group.id])
  let size=-1
  while(size!==ids.size){size=ids.size;for(const g of groups){
    if(g.location_id===group.location_id||ids.has(g.id)){ids.add(g.id);g.parent_ids.forEach(id=>ids.add(id))}
    else if(g.parent_ids.some(id=>ids.has(id)))ids.add(g.id)
  }}
  return groups.filter(g=>ids.has(g.id))
}
export interface TrackingSegment {kind:'plain'|'change';source:string;before?:string;lineOffset:number;groupIds:string[];patches:RevisionPatch[]}
/** Coordinates are server Unicode code points; display-only old text never changes the source. */
export function trackingSegments(source:string,blocks:Pick<ReportBlock,'start'|'end'>[],groups:RevisionSuggestion[]):TrackingSegment[] {
  const chars=Array.from(source)
  const slice=(a:number,b:number)=>chars.slice(a,b).join('')
  const ranges:{start:number;end:number;groupId:string;patch:RevisionPatch}[]=[]
  for(const g of groups)if(g.decision==='pending')for(const original of g.patches)if(original.field==='markdown'&&original.before!==original.after&&slice(original.start,original.end)===original.after){
    const pieces:RevisionPatch[]=original.display_patches?.length?original.display_patches.map(p=>({...p,start:original.start+p.start,end:original.start+p.end,field:'markdown',removed:original.removed||[],added:[]})):[original]
    for(const p of pieces){
      const touched=blocks.filter(b=>p.start===p.end?b.start<p.start&&b.end>p.start:b.start<p.end&&b.end>p.start)
      ranges.push({start:touched[0]?.start??p.start,end:touched.at(-1)?.end??p.end,groupId:g.id,patch:p})
    }
  }
  const regions:{start:number;end:number;items:typeof ranges}[]=[]
  for(const r of ranges.sort((a,b)=>a.start-b.start||a.end-b.end)){
    const last=regions.at(-1)
    if(last&&r.start<last.end){last.end=Math.max(last.end,r.end);last.items.push(r)}
    else regions.push({start:r.start,end:r.end,items:[r]})
  }
  const segments:TrackingSegment[]=[]
  const append=(kind:'plain'|'change',start:number,end:number,items:typeof ranges=[])=>{
    const body=slice(start,end)
    let before=body
    for(const {patch:p} of [...items].sort((a,b)=>b.patch.start-a.patch.start)){
      const original=Array.from(before)
      before=original.slice(0,p.start-start).join('')+p.before+original.slice(p.end-start).join('')
    }
    segments.push({kind,source:body,...(kind==='change'?{before}:{}),lineOffset:slice(0,start).split('\n').length-1,groupIds:[...new Set(items.map(i=>i.groupId))],patches:items.map(i=>i.patch)})
  }
  let cursor=0
  for(const r of regions){if(r.start>cursor)append('plain',cursor,r.start);append('change',r.start,r.end,r.items);cursor=r.end}
  if(cursor<chars.length)append('plain',cursor,chars.length)
  return segments
}
