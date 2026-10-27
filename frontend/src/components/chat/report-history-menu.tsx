import { useState } from 'react'
import { Check, ChevronDown, Clock3, Loader2, RotateCcw } from 'lucide-react'
import type { ReportHistoryEntry } from '@/api/client'
import { Popover } from '@/components/ui/popover'
import { useT } from '@/i18n/use-t'

export function ReportHistoryMenu({entries, currentRevision, selectedRevision, onSelect, onRestore, restoring}: {
  entries: ReportHistoryEntry[]; currentRevision: number; selectedRevision: number
  onSelect: (revision: number) => void; onRestore: () => void; restoring: boolean
}) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const historical = selectedRevision !== currentRevision
  const date = (value: string) => {
    const time = new Date(value)
    return Number.isNaN(time.getTime()) ? value : time.toLocaleString(undefined, {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'})
  }
  return <Popover.Root open={open} onOpenChange={setOpen}>
    <Popover.Trigger className="pm-report-history-trigger" disabled={!entries.length} data-historical={historical || undefined} aria-label={t('chat.reports.history')}>
      <Clock3 size={15}/><span>{t(historical?'chat.reports.viewingHistory':'chat.reports.history')}</span><ChevronDown size={12}/>
    </Popover.Trigger>
    <Popover.Portal><Popover.Positioner side="bottom" align="end" sideOffset={10} className="pm-report-history-positioner">
      <Popover.Popup className="pm-report-history-menu">
        <Popover.Title className="pm-report-history-heading">{t('chat.reports.history')}</Popover.Title>
        <div className="pm-report-history-list">
          {entries.map(entry=><button key={entry.revision} type="button" data-history-revision={entry.revision}
            className={`pm-report-history-entry${entry.revision===selectedRevision?' is-selected':''}`}
            aria-current={entry.revision===selectedRevision?'true':undefined} disabled={restoring}
            onClick={()=>{onSelect(entry.revision);setOpen(false)}}>
            <span className="pm-report-history-dot"/>
            <span className="pm-report-history-entry-body">
              <span className="pm-report-history-entry-title">{entry.decision?t(`chat.reports.historyDecision.${entry.decision}`,{label:entry.label||t('chat.review.directRevision')}):t(`chat.reports.historyKind.${entry.kind || (entry.revision===1?'create':'edit')}`)}
                {entry.revision===currentRevision&&<small>{t('chat.reports.currentDraft')}</small>}
              </span>
              {!entry.decision&&entry.summaries?.length>0&&<span className="pm-report-history-summary">{entry.summaries.join(' ')}</span>}
              <time dateTime={entry.updated_at}>{date(entry.updated_at)}</time>
            </span>
            {entry.revision===selectedRevision&&<Check size={14} className="pm-report-history-check"/>}
          </button>)}
        </div>
        {historical&&<div className="pm-report-history-actions">
          <button type="button" disabled={restoring} onClick={()=>{onSelect(currentRevision);setOpen(false)}}>{t('chat.reports.backToCurrent')}</button>
          <button type="button" className="pm-report-history-restore" disabled={restoring} onClick={onRestore}>
            {restoring?<Loader2 size={14} className="animate-spin"/>:<RotateCcw size={14}/>}{t('chat.reports.restoreState')}
          </button>
        </div>}
      </Popover.Popup>
    </Popover.Positioner></Popover.Portal>
  </Popover.Root>
}
