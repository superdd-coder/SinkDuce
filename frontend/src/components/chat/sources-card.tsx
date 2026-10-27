import { useId, useState } from 'react'
import { ArrowUpRight, ChevronDown, FileText, Globe } from 'lucide-react'
import { useAppStore, type Source } from '@/stores/app-store'
import { useT } from '@/i18n/use-t'
import { sourceEvidence } from '@/lib/source-evidence'
import { useCitations } from './citation-context'
import './sources-card.css'

/** Every source entry opens the same evidence viewer as a sentence citation. */
export function SourcesCard({ sources }: {sources: Source[]}) {
  const t = useT()
  const [expanded, setExpanded] = useState(false)
  const listId = useId()
  const collections = useAppStore(state => state.collections)
  const citations = useCitations()
  const byKey = new Map<string, Source>()
  for (const [index, source] of (sources || []).entries()) {
    const meta = source.metadata || {}
    const key = JSON.stringify([meta.collection || '', meta.version_id || '', meta.chunk_id || meta.id || meta.url || index])
    if (!byKey.has(key) || source.score >= byKey.get(key)!.score) byKey.set(key, source)
  }
  const list = [...byKey.values()].sort((a,b) => (Number(b.score) || 0) - (Number(a.score) || 0))
  if (!list.length) return null
  return <div className="pm-chat-sources pm-unified-sources">
    <button type="button" className="pm-chat-sources-toggle" aria-expanded={expanded} aria-controls={listId} onClick={() => setExpanded(value => !value)}>
      <span>{t('chat.sourcesCount', {n:list.length})}</span><ChevronDown size={12} aria-hidden="true"/>
    </button>
    <div id={listId} className={`pm-chat-sources-list${expanded ? ' is-open' : ''}`} aria-hidden={!expanded} inert={!expanded}>
      <div>{list.map((source,index) => {
        const evidence = sourceEvidence(source,index,citations.records)
        const collection = String(evidence.source.collection || evidence.source.collection_name || '')
        const collectionName = collections.find(item => item.id === collection || item.name === collection)?.name
        const page = evidence.location.page_number
        return <button type="button" key={index} className="pm-chat-source-row" title={evidence.title} onClick={() => citations.open(evidence)}>
          {evidence.kind === 'web' ? <Globe size={16} aria-hidden="true"/> : <FileText size={16} aria-hidden="true"/>}
          <span className="pm-chat-source-copy"><span className="pm-chat-source-name">{evidence.title}</span>
            <span className="pm-chat-source-snippet">{evidence.quote}</span>
            {(collectionName || page != null) && <span className="pm-chat-source-context">{[collectionName,page != null && t('chat.reports.page',{n:String(page)})].filter(Boolean).join(' · ')}</span>}
          </span><ArrowUpRight size={13} className="pm-chat-source-arrow" aria-hidden="true"/>
        </button>
      })}</div>
    </div>
  </div>
}
