import {useEffect, useRef, useState} from 'react'
import {Download, Loader2} from 'lucide-react'
import {toast} from 'sonner'
import {downloadMeetingSummary, type MeetingSummaryFormat} from '@/api/client'
import {DropdownSelect} from '@/components/ui/dropdown-select'
import {downloadBlob} from '@/lib/download'
import {useT} from '@/i18n/use-t'

const FORMATS: MeetingSummaryFormat[] = ['docx', 'pdf', 'md']
const formatName = (format: MeetingSummaryFormat) => format === 'docx' ? 'Word' : format === 'md' ? 'Markdown' : 'PDF'

export function MeetingSummaryExport({meetingId, tabId, language, disabled = false}: {
  meetingId: string; tabId: string; language?: string | null; disabled?: boolean
}) {
  const t = useT()
  const [busy, setBusy] = useState<MeetingSummaryFormat | null>(null)
  const request = useRef<AbortController | null>(null)
  useEffect(() => {
    setBusy(null)
    return () => { request.current?.abort(); request.current = null }
  }, [meetingId, tabId, language])

  const exportSummary = async (value: string) => {
    const format = value as MeetingSummaryFormat
    if (disabled || request.current || !FORMATS.includes(format)) return
    const controller = new AbortController()
    request.current = controller
    setBusy(format)
    try {
      const result = await downloadMeetingSummary(meetingId, tabId, format, language, controller.signal)
      if (!controller.signal.aborted) downloadBlob(result.blob, result.filename)
    } catch (error) {
      if (!controller.signal.aborted) toast.error(error instanceof Error ? error.message : t('meeting.exportFailed'))
    } finally {
      if (request.current === controller) { request.current = null; setBusy(null) }
    }
  }

  return <div role="group" aria-label={t('meeting.exportSummary')} aria-busy={!!busy}>
    <DropdownSelect size="tag" chevron value="" disabled={disabled || !!busy}
      className="pm-meeting-summary-export min-w-0 shrink-0"
      placeholder={busy ? `${formatName(busy)} · ${t('chat.reports.preparing')}` : t('common.export')}
      leading={busy ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}
      options={FORMATS.map(format => ({value: format, label: formatName(format)}))}
      onChange={exportSummary} />
  </div>
}
