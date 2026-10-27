import type {ChatReport} from '../api/chat-reports.ts'
import type {RevisionSuggestion,ReportChange} from './report-review.ts'
import {trackingSegments} from './report-revision-tracking.ts'

/** Historical rounds describe the actual save, not its inherited rejection range. */
export function discussionChanges(report:Pick<ChatReport,'revision'|'changes'|'suggestions'>&Partial<Pick<ChatReport,'markdown'|'blocks'>>,round:RevisionSuggestion):ReportChange[] {
  if(report.revision!==round.result_revision)return []
  const stored=report.suggestions?.find(g=>g.id===round.id)||round
  if(stored.decision==='answered'||!stored.patches.length)return []
  if(stored.location_id&&report.markdown!==undefined){
    return trackingSegments(report.markdown,report.blocks||[],[{...stored,decision:'pending'}]).filter(s=>s.kind==='change').map((segment,index)=>({
      id:`${stored.id}:${index}`,field:'markdown',kind:!segment.before?'insert':!segment.source?'delete':'replace',
      old_start:segment.lineOffset+1,old_end:segment.lineOffset+(segment.before||'').split('\n').length,
      new_start:segment.lineOffset+1,new_end:segment.lineOffset+segment.source.split('\n').length,
      before:segment.before||'',after:segment.source,
    })).concat(stored.patches.filter(p=>p.field==='title').map((p,index)=>({id:`${stored.id}:title:${index}`,field:'title',kind:'replace',old_start:1,old_end:1,new_start:1,new_end:1,before:p.before,after:p.after}))) as ReportChange[]
  }
  const ids=new Set(stored.results.flatMap(result=>result.change_ids||[]))
  if(ids.size)return (report.changes||[]).filter(change=>ids.has(change.id))
  // Legacy direct saves have no comment results; one group owns that save's delta.
  const peers=report.suggestions?.filter(g=>g.result_revision===report.revision&&g.decision!=='answered'&&g.patches.length>0)||[]
  return peers.length===1&&peers[0].id===stored.id?report.changes||[]:[]
}

export function comparisonEvidence(before:Pick<ChatReport,'citations'|'markdown'>,after:Pick<ChatReport,'citations'|'markdown'>) {
  return {citations:[...new Map([...before.citations,...after.citations].map(e=>[e.id,e])).values()],source:before.markdown+'\n'+after.markdown}
}

function headingAt(source:string,at:number) {
  const lines=source.split('\n')
  let fence='',title='',lineNumber=0
  for(const [index,line] of lines.slice(0,at||1).entries()){
    const marker=line.match(/^\s*(`{3,}|~{3,})/)
    if(marker){fence=fence===marker[1][0]?'':marker[1][0];continue}
    if(fence)continue
    const match=line.match(/^#{1,6}\s+(.+?)\s*#*$/)
    if(match){title=match[1].replace(/[*_`]/g,'');lineNumber=index+1}
  }
  return {title,lineNumber}
}

export function discussionChangeLocation(change:ReportChange,before:string,after:string):string {
  return change.after?headingAt(after,change.new_start).title:headingAt(before,change.old_start).title
}

export function discussionSections(changes:ReportChange[],before:string,after:string) {
  const sections:{key:string;title:string;changes:ReportChange[]}[]=[],byKey=new Map<string,typeof sections[number]>()
  for(const change of changes){
    const old=headingAt(before,change.old_start),current=headingAt(after,change.new_start)
    const useCurrent=!!change.after||old.title===current.title
    const heading=useCurrent?current:old
    const key=`${useCurrent?'current':'removed'}:${heading.lineNumber}:${heading.title}`
    let section=byKey.get(key)
    if(!section){section={key,title:heading.title,changes:[]};byKey.set(key,section);sections.push(section)}
    section.changes.push(change)
  }
  return sections
}

export function discussionChangeKind(change:ReportChange) {
  const plain=(text:string)=>text.replace(/\[cite:[^\]]+\]/g,'').replace(/\s+/g,'')
  return change.before!==change.after&&plain(change.before)===plain(change.after)?'citation':change.kind
}
