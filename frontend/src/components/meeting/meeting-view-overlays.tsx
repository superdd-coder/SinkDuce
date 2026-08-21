import { createPortal } from "react-dom"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { useT } from "@/i18n/use-t"

export type MeetingSectionTip = {
  id: string
  text: string
  left: number
  top: number
  width: number
}

export interface MeetingViewOverlaysProps {
  deleteTarget: string | null
  setDeleteTarget: (id: string | null) => void
  confirmDelete: () => void
  retranscribeConfirmOpen: boolean
  setRetranscribeConfirmOpen: (open: boolean) => void
  handleTranscribe: () => void
  sectionTip: MeetingSectionTip | null
  sideRailOpen: boolean
  sideTab: "sections" | "transcript" | "speaker"
}

export function MeetingViewOverlays({
  deleteTarget,
  setDeleteTarget,
  confirmDelete,
  retranscribeConfirmOpen,
  setRetranscribeConfirmOpen,
  handleTranscribe,
  sectionTip,
  sideRailOpen,
  sideTab,
}: MeetingViewOverlaysProps) {
  const t = useT()
  return (
    <>
      {/* Dialogs */}

      <Dialog open={!!deleteTarget} onOpenChange={(v) => !v && setDeleteTarget(null)}>
        <DialogContent
          className="pm-dialog pm-dialog--silk sm:max-w-[280px]"
          showCloseButton={false}
          overlayClassName="pm-dialog-overlay--silk"
        >
          <DialogHeader>
            <DialogKicker>{t("nav.meeting")}</DialogKicker>
            <DialogTitle>{t("meeting.deleteMeetingQ")}</DialogTitle>
            <DialogDescription>
              {t("meeting.deleteMeetingBody")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" size="sm" onClick={() => setDeleteTarget(null)}>
              {t("common.cancel")}
            </Button>
            <Button type="button" variant="destructive-solid" size="sm" onClick={confirmDelete}>
              {t("common.delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={retranscribeConfirmOpen} onOpenChange={setRetranscribeConfirmOpen}>
        <DialogContent
          className="pm-dialog pm-dialog--silk sm:max-w-sm"
          showCloseButton={false}
          overlayClassName="pm-dialog-overlay--silk"
        >
          <DialogHeader>
            <DialogKicker>{t("common.transcript")}</DialogKicker>
            <DialogTitle>{t("meeting.reTranscribeQ")}</DialogTitle>
            <DialogDescription>
              {t("meeting.reTranscribeBody")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" size="sm" onClick={() => setRetranscribeConfirmOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              type="button"
              variant="default"
              size="sm"
              onClick={() => { setRetranscribeConfirmOpen(false); handleTranscribe() }}
            >
              {t("common.continue")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Section description: under the hovered item */}
      {sectionTip &&
        sideRailOpen &&
        sideTab === "sections" &&
        createPortal(
          <div
            id={`section-tip-${sectionTip.id}`}
            role="tooltip"
            className="pm-meeting-section-tip"
            style={{
              left: sectionTip.left,
              top: sectionTip.top,
              width: sectionTip.width,
            }}
          >
            {sectionTip.text}
          </div>,
          document.body,
        )}

    </>
  )
}
