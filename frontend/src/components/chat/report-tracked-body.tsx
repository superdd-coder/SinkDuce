import {useMemo} from 'react'
import type {ChatReport} from '@/api/client'
import {trackingSegments,type TrackingSegment} from '@/lib/report-revision-tracking'
import {AnswerMarkdown} from './answer-markdown'
import {CitationScope} from './citation-scope'

function TrackedPassage({report,segment}:{report:ChatReport;segment:TrackingSegment}) {
  const previousCitations=segment.groupIds.flatMap(id=>report.suggestions?.find(group=>group.id===id)?.citations||[])
  const citations=[...new Map([...previousCitations,...(report.citations||[])].map(citation=>[citation.id,citation])).values()]
  return <CitationScope citations={citations} source={report.markdown+'\n'+segment.before} sessionId={report.session_id}>
    <AnswerMarkdown source={segment.source} previousSource={segment.before} lineOffset={segment.lineOffset} blocks={report.blocks} report headingAnchors restrictImages reviewAnchors/>
  </CitationScope>
}

/** Historical text exists only in this display layer, with its original evidence. */
export function ReportTrackedBody({report,tracking}:{report:ChatReport;tracking:boolean}) {
  const segments=useMemo(()=>tracking?trackingSegments(report.markdown,report.blocks||[],report.suggestions||[]):[],[report,tracking])
  if(!tracking||!report.suggestions?.some(g=>g.decision==='pending'))return <AnswerMarkdown source={report.markdown} blocks={report.blocks} report headingAnchors restrictImages reviewAnchors/>
  return <>{segments.map((segment,index)=>{
    if(segment.kind==='plain')return <AnswerMarkdown key={`plain-${index}`} source={segment.source} lineOffset={segment.lineOffset} blocks={report.blocks} report headingAnchors restrictImages reviewAnchors/>
    return <div key={`change-${index}`} className="pm-review-inline-change" data-review-group={segment.groupIds.join(' ')} data-review-primary={segment.groupIds.filter(id=>{const g=report.suggestions?.find(g=>g.id===id);const primary=g?.focus||g?.patches[g.primary_patch||0];return primary&&segment.patches.some(p=>primary.start<=p.start&&p.end<=primary.end)}).join(' ')}>
      {segment.before&&segment.source?<TrackedPassage report={report} segment={segment}/>:<>
        {!!segment.before&&!segment.patches.every(p=>p.before==='')&&<div className="pm-review-deletion" data-review-deletion="true" data-review-group={segment.groupIds.join(' ')}>
          <CitationScope citations={segment.groupIds.flatMap(id=>report.suggestions?.find(g=>g.id===id)?.citations||[])} source={segment.before} sessionId={report.session_id}>
            <AnswerMarkdown source={segment.before} report restrictImages lineOffset={-1} reviewMarks={{kind:'removed',passages:segment.patches.flatMap(p=>p.removed||[]),all:!segment.source}}/>
          </CitationScope>
        </div>}
        {!!segment.source&&<div className="pm-review-candidate"><AnswerMarkdown source={segment.source} lineOffset={segment.lineOffset} blocks={report.blocks} report headingAnchors restrictImages reviewAnchors reviewMarks={{kind:'added',passages:segment.patches.flatMap(p=>p.added||[]),all:!segment.before}}/></div>}
      </>}
    </div>
  })}</>
}
