import { useRef, useEffect, forwardRef, useImperativeHandle } from "react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { Upload, Mic, Square, Pause, Loader2, FileAudio, RefreshCw, Play, AlertCircle } from "lucide-react"
import type { MeetingStatus } from "@/api/client"

interface MediaBarProps {
  meetingId: string
  status: MeetingStatus
  hasAudio: boolean
  audioPath?: string
  audioUrl: string | null
  audioVersion: number
  duration: number
  isRecording: boolean
  isPaused: boolean
  transcriptionProgress?: number
  transcriptionError?: string | null
  onUploadAudio: (file: File) => void
  onStartRecord: () => void
  onStopRecord: () => void
  onPauseRecord: () => void
  onResumeRecord: () => void
  onTranscribe: () => void
  onReTranscribe?: () => void
  onCancelTranscribe?: () => void
  hasRealtimeProvider: boolean
  realtimeEnabled?: boolean
  onToggleRealtime?: () => void
  hasTranscript?: boolean
}

export interface MediaBarHandle {
  seekTo: (time: number) => void
}

export const MediaBar = forwardRef<MediaBarHandle, MediaBarProps>(function MediaBar({
  meetingId,
  status,
  hasAudio,
  audioPath,
  audioUrl,
  audioVersion,
  duration,
  isRecording,
  isPaused,
  transcriptionError,
  onUploadAudio,
  onStartRecord,
  onStopRecord,
  onPauseRecord,
  onResumeRecord,
  onTranscribe,
  onReTranscribe,
  onCancelTranscribe,
  hasRealtimeProvider,
  realtimeEnabled,
  onToggleRealtime,
  hasTranscript,
}, ref) {
  const inputRef = useRef<HTMLInputElement>(null)
  const audioRef = useRef<HTMLAudioElement>(null)

  // Pause audio when meeting changes to prevent crackling
  useEffect(() => {
    return () => {
      const el = audioRef.current
      if (el) {
        el.pause()
      }
    }
  }, [meetingId])

  useImperativeHandle(ref, () => ({
    seekTo(time: number) {
      const el = audioRef.current
      if (el) {
        el.currentTime = time
        el.play().catch(() => {})  // AbortError when interrupted by pause/unmount
      }
    },
  }))

  // Recording state
  if (isRecording || isPaused) {
    return (
      <div className="flex items-center gap-3 py-3 px-0">
        <div className="h-3 w-3 rounded-full bg-red-500 animate-pulse" />
        <span className="font-mono text-sm tabular-nums">{formatDuration(duration)}</span>
        <div className="flex-1 flex items-center gap-1">
          {Array.from({ length: 20 }).map((_, i) => (
            <div
              key={i}
              className="w-1 bg-primary/40 rounded-full animate-pulse"
              style={{ height: `${Math.random() * 16 + 4}px`, animationDelay: `${i * 50}ms` }}
            />
          ))}
        </div>
        <Button variant="outline" size="sm" onClick={isPaused ? onResumeRecord : onPauseRecord}>
          <Pause className="h-4 w-4 mr-1" />
          {isPaused ? "Resume" : "Pause"}
        </Button>
        <Button variant="destructive" size="sm" onClick={onStopRecord}>
          <Square className="h-4 w-4 mr-1" />
          Stop
        </Button>
      </div>
    )
  }

  // Transcribing state
  if (status === "transcribing") {
    return (
      <div className="flex flex-col gap-2">
        {transcriptionError && (
          <div className="flex items-center gap-2 p-3 border border-destructive/50 rounded-lg bg-destructive/10 text-destructive">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span className="text-sm flex-1">Transcription failed: {transcriptionError}</span>
          </div>
        )}
        <div className="flex items-center gap-3 py-3 px-0">
          {/* Audio player during transcription */}
          {audioUrl ? (
            <audio key={`transcribing-${audioVersion}`} ref={audioRef} controls src={audioUrl} preload="metadata" className="flex-1 h-7 styled-audio">
              <track kind="captions" />
            </audio>
          ) : (
            <>
              <Loader2 className="h-4 w-4 animate-spin text-primary" />
              <span className="text-sm">Transcribing...</span>
            </>
          )}
          <span className="text-sm text-muted-foreground shrink-0">Transcribing...</span>
          {onCancelTranscribe && (
            <Button variant="destructive" size="sm" onClick={onCancelTranscribe}>
              <Square className="h-4 w-4 mr-1" />
              Stop
            </Button>
          )}
        </div>
        <ProcessingBar label="Transcribing audio..." />
      </div>
    )
  }

  // Has audio — always show player + action buttons
  if (hasAudio) {
    return (
      <div className="flex flex-col gap-2">
        {transcriptionError && (
          <div className="flex items-center gap-2 p-3 border border-destructive/50 rounded-lg bg-destructive/10 text-destructive">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span className="text-sm flex-1">Transcription failed: {transcriptionError}</span>
          </div>
        )}
        <div className="flex items-center gap-3 py-3 px-0">
          {audioUrl ? (
            <audio key={`player-${audioVersion}`} ref={audioRef} controls src={audioUrl} preload="metadata" className="flex-1 h-7 styled-audio">
              <track kind="captions" />
            </audio>
          ) : (
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <FileAudio className="h-4 w-4 text-muted-foreground shrink-0" />
              <span className="text-sm text-muted-foreground truncate" title={audioPath}>
                {audioPath ? audioPath.split("/").pop() : "Audio uploaded"}
              </span>
            </div>
          )}
          <div className="flex items-center gap-2 shrink-0">
            {!hasTranscript && (
              <>
                <input
                  ref={inputRef}
                  type="file"
                  accept="audio/*"
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0]
                    if (file) onUploadAudio(file)
                    e.target.value = ""
                  }}
                />
                <Button variant="outline" size="sm" onClick={() => inputRef.current?.click()}>
                  <RefreshCw className="h-3 w-3 mr-1" />
                  Replace
                </Button>
              </>
            )}
            {!hasTranscript && (
              <Button size="sm" onClick={onTranscribe}>
                <Play className="h-4 w-4 mr-1" />
                Transcribe
              </Button>
            )}
            {hasTranscript && onReTranscribe && (
              <Button variant="outline" size="sm" onClick={onReTranscribe}>
                Re-transcribe
              </Button>
            )}
          </div>
        </div>
      </div>
    )
  }

  // No audio — upload / record
  return (
    <div className="flex items-center gap-2 py-3 px-0">
      <input
        ref={inputRef}
        type="file"
        accept="audio/*"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) onUploadAudio(file)
          e.target.value = ""
        }}
      />
      <Button variant="outline" size="sm" onClick={() => inputRef.current?.click()}>
        <Upload className="h-4 w-4 mr-1" />
        Audio
      </Button>
      <Button variant="outline" size="sm" onClick={onStartRecord}>
        <Mic className="h-4 w-4 mr-1" />
        Record
      </Button>
      {hasRealtimeProvider && onToggleRealtime && (
        <button
          type="button"
          onClick={onToggleRealtime}
          className={cn(
            "flex items-center gap-1.5 text-xs px-2 py-1 rounded-md border transition-colors ml-auto",
            realtimeEnabled
              ? "border-primary/30 text-primary"
              : "border-border text-muted-foreground"
          )}
          title={realtimeEnabled ? "Live captions ON" : "Live captions OFF"}
        >
          <div className={cn("w-2 h-2 rounded-full", realtimeEnabled ? "bg-green-500" : "bg-muted-foreground/30")} />
          Live captions
        </button>
      )}
    </div>
  )
})

function ProcessingBar({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 px-1">
      <Loader2 className="h-3 w-3 animate-spin text-primary shrink-0" />
      {label && <span className="text-xs text-muted-foreground">{label}</span>}
      <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
        <div className="h-full bg-primary/70 rounded-full animate-progress" style={{ width: "40%" }} />
      </div>
    </div>
  )
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`
}
