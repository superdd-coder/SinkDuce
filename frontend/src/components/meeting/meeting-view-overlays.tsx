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
            <DialogKicker>Meeting</DialogKicker>
            <DialogTitle>Delete meeting?</DialogTitle>
            <DialogDescription>
              This meeting and its audio will be permanently removed.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" size="sm" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
            <Button type="button" variant="destructive-solid" size="sm" onClick={confirmDelete}>
              Delete
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
            <DialogKicker>Transcript</DialogKicker>
            <DialogTitle>Re-transcribe meeting?</DialogTitle>
            <DialogDescription>
              Re-transcribing will overwrite the existing transcript and speaker names.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" size="sm" onClick={() => setRetranscribeConfirmOpen(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="default"
              size="sm"
              onClick={() => { setRetranscribeConfirmOpen(false); handleTranscribe() }}
            >
              Continue
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
