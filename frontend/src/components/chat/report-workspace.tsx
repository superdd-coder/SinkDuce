import { useState, type CSSProperties, type ReactNode } from "react"
import type { ReportReference } from "@/api/client"
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog"
import { useT } from "@/i18n/use-t"
import { ReportViewer } from "./report-viewer"
import { ReportResizeHandle } from "./report-resize-handle"
import { useAppStore } from "@/stores/app-store"

/** Keep the live Quick Chat controls and state beside the report. */
export function ReportWorkspace({ report, chat, onClose }: { report: ReportReference; chat: ReactNode; onClose: () => void }) {
  const t = useT()
  const [open, setOpen] = useState(true)
  const ratio = useAppStore((state) => state.reportChatRatio)
  return <Dialog open={open} onOpenChange={setOpen} onOpenChangeComplete={(opened) => { if (!opened) onClose() }}>
    <DialogContent className="pm-report-workspace" style={{ "--pm-report-left-w": `${ratio * 100}%` } as CSSProperties} showCloseButton={false} forceOverlay>
      <DialogTitle className="sr-only">{report.title}</DialogTitle>
      <DialogDescription className="sr-only">{t("chat.reports.split")}</DialogDescription>
      <div className="pm-report-workspace-chat">{chat}</div>
      <ReportResizeHandle />
      <ReportViewer report={report} embedded dialogHosted active={open} onClose={() => setOpen(false)} />
    </DialogContent>
  </Dialog>
}
