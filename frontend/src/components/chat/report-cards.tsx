import { useRef, useState } from "react"
import { ArrowUpRight } from "lucide-react"
import type { ReportReference } from "@/api/client"
import { useT } from "@/i18n/use-t"
import { ReportViewer } from "./report-viewer"
import { DocumentMark } from './document-mark'
import { documentDate } from '@/lib/chat-document-display'
import { useAppStore } from '@/stores/app-store'
import "./chat-reports.css"

export function ReportCards({ reports = [], onOpenReport }: { reports?: ReportReference[]; onOpenReport?: (report: ReportReference) => void }) {
  const t = useT()
  const locale = useAppStore(state=>state.locale)
  const [selected, setSelected] = useState<ReportReference | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  if (!reports.length) return null
  return <div className="pm-report-cards">
    {reports.map((report) => <button key={report.id} type="button" className="pm-report-card" title={report.title} onClick={(event) => {
      triggerRef.current = event.currentTarget
      if (onOpenReport) onOpenReport(report)
      else setSelected(report)
    }}>
      <DocumentMark kind="report" large/>
      <span className="pm-report-card-body"><span className="pm-report-card-title">{report.title}</span>
        <span className="pm-report-card-meta"><span>{t("chat.reports.report")}</span>
          {documentDate(report.updated_at,locale)&&<><span className="pm-artifact-meta-dot" aria-hidden="true"/><time dateTime={report.updated_at}>{documentDate(report.updated_at,locale)}</time></>}
        </span></span>
      <span className="pm-report-card-open" aria-hidden="true"><ArrowUpRight size={15} strokeWidth={1.7}/></span>
    </button>)}
    {selected && <ReportViewer key={selected.id} report={selected} triggerRef={triggerRef} onClose={() => setSelected(null)} />}
  </div>
}
