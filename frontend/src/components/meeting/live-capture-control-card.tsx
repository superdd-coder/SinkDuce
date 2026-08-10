/**
 * Live capture control card — speaker-gate-style banner (not a danger strip).
 * Real waveform bars + Live captions + Pause / Stop / Discard.
 * No Chat on this surface.
 */
import { useState } from "react"
import { Pause, Play, Square, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"

export interface LiveCaptureControlCardProps {
  /** May be empty/undefined after HMR — UI falls back to flat bars. */
  levels?: number[] | null
  durationLabel: string
  isPaused: boolean
  hasRealtimeProvider: boolean
  realtimeEnabled: boolean
  onToggleRealtime: () => void
  onPause: () => void
  onResume: () => void
  onStop: () => void
  onDiscard: () => void
}

export function LiveCaptureControlCard({
  levels,
  durationLabel,
  isPaused,
  hasRealtimeProvider,
  realtimeEnabled,
  onToggleRealtime,
  onPause,
  onResume,
  onStop,
  onDiscard,
}: LiveCaptureControlCardProps) {
  const [discardOpen, setDiscardOpen] = useState(false)
  // Guard HMR / partial state where levels may be missing
  const bars = Array.isArray(levels) && levels.length > 0 ? levels : Array.from({ length: 24 }, () => 0)

  return (
    <div className="pm-meeting-live-control-card" role="region" aria-label="Recording controls">
      <div className="pm-meeting-live-control-main">
        <div
          className={cn("pm-meeting-live-wave", isPaused && "is-paused")}
          aria-hidden
        >
          {bars.map((v, i) => (
            <span
              key={i}
              className="pm-meeting-live-wave-bar"
              style={{ height: `${Math.max(8, Math.round((v || 0) * 100))}%` }}
            />
          ))}
        </div>

        <div className="pm-meeting-live-control-status">
          <span
            className={cn(
              "pm-meeting-live-control-dot",
              isPaused ? "is-paused" : "is-live",
            )}
            aria-hidden
          />
          <strong className="pm-meeting-live-control-label">
            {isPaused ? "Paused" : "Recording"}
          </strong>
          <span className="pm-meeting-live-control-time t-mono-family">
            {durationLabel}
          </span>
        </div>
      </div>

      <div className="pm-meeting-live-control-actions">
        {hasRealtimeProvider && (
          <Button
            type="button"
            variant={realtimeEnabled ? "secondary" : "ghost"}
            size="sm"
            className="pm-meeting-live-captions-btn"
            onClick={onToggleRealtime}
            title={realtimeEnabled ? "Live captions ON" : "Live captions OFF"}
          >
            <span
              className={cn(
                "w-1.5 h-1.5 rounded-full mr-1.5 shrink-0",
                realtimeEnabled ? "bg-[var(--pm-green)]" : "bg-[var(--pm-faint)]",
              )}
            />
            Live captions
          </Button>
        )}

        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={isPaused ? onResume : onPause}
        >
          {isPaused ? (
            <Play className="size-3.5 mr-1" />
          ) : (
            <Pause className="size-3.5 mr-1" />
          )}
          {isPaused ? "Resume" : "Pause"}
        </Button>

        <Button
          type="button"
          variant="default"
          size="sm"
          className="pm-meeting-f-stop"
          onClick={onStop}
        >
          <Square className="size-3.5 mr-1" />
          Stop
        </Button>

        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setDiscardOpen(true)}
        >
          <Trash2 className="size-3.5 mr-1" />
          Discard
        </Button>
      </div>

      <Dialog open={discardOpen} onOpenChange={setDiscardOpen}>
        <DialogContent
          className="pm-dialog pm-dialog--silk sm:max-w-sm"
          showCloseButton={false}
          overlayClassName="pm-dialog-overlay--silk"
        >
          <DialogHeader>
            <DialogTitle>Discard recording?</DialogTitle>
            <DialogDescription>
              This will stop the recording and permanently delete all audio, captions, and transcript data.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setDiscardOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive-solid"
              size="sm"
              onClick={() => {
                setDiscardOpen(false)
                onDiscard()
              }}
            >
              Discard
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
