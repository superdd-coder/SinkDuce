import { useId, useState } from 'react'
import { ArrowUpRight, AudioLines, ChevronDown, ChevronRight, FileText, Globe, MessagesSquare } from 'lucide-react'
import { useAppStore, type Source } from '@/stores/app-store'
import { useT } from '@/i18n/use-t'
import { formatGroupCiteDate, type GroupMeetingSource } from '@/lib/group-cites'
import { sourceEvidence } from '@/lib/source-evidence'
import { formatTranscriptTime } from '@/lib/transcript-playback'
import { useCitations } from './citation-context'
import './sources-card.css'

/** Every source entry opens the same evidence viewer as a sentence citation. */
export function SourcesCard({ sources, meetingSources = [] }: { sources: Source[]; meetingSources?: GroupMeetingSource[] }) {
  const t = useT()
  const [expanded, setExpanded] = useState(false)
  const listId = useId()
  const collections = useAppStore(state => state.collections)
  const locale = useAppStore(state => state.locale)
  const citations = useCitations()
  const byKey = new Map<string, Source>()
  for (const [index, source] of (sources || []).entries()) {
    const meta = source.metadata || {}
    const key = JSON.stringify([meta.collection || '', meta.version_id || '', meta.qa_snapshot_id || meta.chunk_id || meta.id || meta.url || index])
    if (!byKey.has(key) || source.score >= byKey.get(key)!.score) byKey.set(key, source)
  }
  const list = [...byKey.values()].sort((a,b) => (Number(b.score) || 0) - (Number(a.score) || 0))
  const sourceCount = list.length + meetingSources.length
  if (!sourceCount) return null
  return <div className="pm-chat-sources pm-unified-sources">
    <button type="button" className="pm-chat-sources-toggle" aria-expanded={expanded} aria-controls={listId} onClick={() => setExpanded(value => !value)}>
      <span>{t('chat.sourcesCount', {n:sourceCount})}</span><ChevronDown size={12} aria-hidden="true"/>
    </button>
    <div id={listId} className={`pm-chat-sources-list${expanded ? ' is-open' : ''}`} aria-hidden={!expanded} inert={!expanded}>
      <div>{meetingSources.map((meeting) => {
        const title = meeting.title || t('common.transcript')
        return <div key={meeting.id} className="pm-chat-meeting-source" role="group" aria-label={title}>
          <div className="pm-chat-meeting-source-heading">
            <AudioLines size={16} aria-hidden="true"/>
            <span className="pm-chat-source-name">{title}</span>
            {meeting.date && <time className="pm-chat-source-context" dateTime={meeting.date}>{formatGroupCiteDate(meeting.date, locale)}</time>}
          </div>
          {meeting.excerpts.map((evidence, index) => {
            const start = evidence.location.start_time
            const time = typeof start === 'number' && Number.isFinite(start) && start >= 0 ? formatTranscriptTime(start) : ''
            const label = t('meeting.citedPassage', { n: index + 1 })
            return <button type="button" key={evidence.id} className="pm-chat-meeting-passage"
              aria-label={[label, time, evidence.quote].filter(Boolean).join(' · ')} onClick={() => citations.open(evidence)}>
              <span>{label}</span>
              {time && <span className="pm-chat-passage-time">{time}</span>}
              {evidence.quote && <span className="pm-chat-source-snippet">{evidence.quote}</span>}
              <ChevronRight size={14} aria-hidden="true"/>
            </button>
          })}
        </div>
      })}{list.map((source,index) => {
        const evidence = sourceEvidence(source,index,citations.records)
        const collection = String(evidence.source.collection || evidence.source.collection_name || '')
        const collectionName = collections.find(item => item.id === collection || item.name === collection)?.name
        const page = evidence.location.page_number
        return <button type="button" key={index} className="pm-chat-source-row" title={evidence.title} onClick={() => citations.open(evidence)}>
          {evidence.kind === 'qa' ? <MessagesSquare size={16} aria-hidden="true"/> : evidence.kind === 'web' ? <Globe size={16} aria-hidden="true"/> : <FileText size={16} aria-hidden="true"/>}
          <span className="pm-chat-source-copy"><span className="pm-chat-source-name">{evidence.title}</span>
            <span className="pm-chat-source-snippet">{evidence.quote}</span>
            {(collectionName || page != null) && <span className="pm-chat-source-context">{[collectionName,page != null && t('chat.reports.page',{n:String(page)})].filter(Boolean).join(' · ')}</span>}
          </span><ArrowUpRight size={13} className="pm-chat-source-arrow" aria-hidden="true"/>
        </button>
      })}</div>
    </div>
  </div>
}
