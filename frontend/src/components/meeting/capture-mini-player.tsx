import {
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  forwardRef,
  type ReactNode,
} from "react"
import { Pause, Play } from "lucide-react"

export type CaptureMiniPlayerHandle = {
  /** Jump to start and play; if end is set, auto-pause at that time (one sentence). */
  seekTo: (start: number, end?: number) => void
}

/** Compact / review capture player; optional segment end-stop */
export const CaptureMiniPlayer = forwardRef<
  CaptureMiniPlayerHandle,
  {
    audioUrl: string
    audioVersion: number
    /** compact = chip player; review = progress under cards + play in footer row */
    variant?: "compact" | "review"
    onTimeUpdate?: (time: number) => void
    /** review only: right-side action (e.g. Summarize) on same row as play */
    footerSlot?: ReactNode
    /** review only: left tools (hot words / language / re-transcribe) */
    footerLeftSlot?: ReactNode
  }
>(function CaptureMiniPlayer(
  { audioUrl, audioVersion, variant = "compact", onTimeUpdate, footerSlot, footerLeftSlot },
  ref,
) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const trackRef = useRef<HTMLDivElement>(null)
  const segmentEndRef = useRef<number | null>(null)
  const [playing, setPlaying] = useState(false)
  const [current, setCurrent] = useState(0)
  const [duration, setDuration] = useState(0)

  const fmt = (s: number) => {
    const m = Math.floor(s / 60)
    const sec = Math.floor(s % 60)
    return `${m}:${sec.toString().padStart(2, "0")}`
  }

  useEffect(() => {
    segmentEndRef.current = null
    setPlaying(false)
    setCurrent(0)
    setDuration(0)
    onTimeUpdate?.(0)
  }, [audioUrl, audioVersion, onTimeUpdate])

  useImperativeHandle(ref, () => ({
    seekTo(start: number, end?: number) {
      const el = audioRef.current
      if (!el) return
      const t = Math.max(0, start)
      // Only stop at end when explicitly provided (speaker samples)
      segmentEndRef.current =
        end != null && Number.isFinite(end) && end > t ? end : null
      el.currentTime = t
      setCurrent(t)
      onTimeUpdate?.(t)
      void el.play().catch(() => {})
    },
  }))

  const clearSegmentEnd = () => {
    segmentEndRef.current = null
  }

  const onTimeUpdateInternal = () => {
    const el = audioRef.current
    if (!el) return
    const t = el.currentTime
    setCurrent(t)
    onTimeUpdate?.(t)
    const stopAt = segmentEndRef.current
    if (stopAt != null && t >= stopAt - 0.02) {
      segmentEndRef.current = null
      el.pause()
      if (el.currentTime > stopAt) {
        el.currentTime = stopAt
        setCurrent(stopAt)
        onTimeUpdate?.(stopAt)
      }
    }
  }

  const toggle = () => {
    const el = audioRef.current
    if (!el) return
    clearSegmentEnd()
    if (el.paused) {
      void el.play().catch(() => {})
      setPlaying(true)
    } else {
      el.pause()
      setPlaying(false)
    }
  }

  const seek = (clientX: number) => {
    const el = audioRef.current
    const track = trackRef.current
    if (!el || !track || !duration) return
    clearSegmentEnd()
    const r = track.getBoundingClientRect()
    const ratio = Math.min(1, Math.max(0, (clientX - r.left) / r.width))
    el.currentTime = ratio * duration
    setCurrent(el.currentTime)
    onTimeUpdate?.(el.currentTime)
  }

  const ratio = duration > 0 ? Math.min(1, current / duration) : 0

  const audioEl = (
    <audio
      key={`capture-audio-${audioVersion}`}
      ref={audioRef}
      src={audioUrl}
      preload="metadata"
      className="sr-only"
      onTimeUpdate={onTimeUpdateInternal}
      onLoadedMetadata={() => setDuration(audioRef.current?.duration || 0)}
      onEnded={() => {
        segmentEndRef.current = null
        setPlaying(false)
      }}
      onPlay={() => setPlaying(true)}
      onPause={() => setPlaying(false)}
    />
  )

  const progressEl = (
    <div
      ref={trackRef}
      className={
        variant === "review"
          ? "pm-meeting-review-progress"
          : "pm-meeting-player-progress"
      }
      role="slider"
      tabIndex={0}
      aria-label="Seek"
      aria-valuemin={0}
      aria-valuemax={Math.floor(duration || 0)}
      aria-valuenow={Math.floor(current || 0)}
      onClick={(e) => seek(e.clientX)}
    >
      <div
        className={
          variant === "review"
            ? "pm-meeting-review-progress-fill"
            : "pm-meeting-player-progress-fill"
        }
        style={{ width: `${ratio * 100}%` }}
      />
    </div>
  )

  if (variant === "review") {
    return (
      <div className="pm-meeting-review-dock">
        {audioEl}
        <div className="pm-meeting-review-progress-wrap">
          {progressEl}
          <span className="pm-meeting-review-time t-mono-family">
            {fmt(current)}
            <span className="pm-meeting-player-time-sep">/</span>
            {fmt(duration)}
          </span>
        </div>
        <div className="pm-meeting-f-controls pm-meeting-speaker-gate-actions pm-meeting-review-actions">
          {footerLeftSlot ? (
            <div className="pm-meeting-review-tools">{footerLeftSlot}</div>
          ) : (
            <div className="pm-meeting-review-tools" aria-hidden />
          )}
          <button
            type="button"
            className="pm-meeting-review-play"
            onClick={toggle}
            aria-label={playing ? "Pause" : "Play"}
          >
            {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
          </button>
          <div className="pm-meeting-review-footer-right">{footerSlot}</div>
        </div>
      </div>
    )
  }

  return (
    <div className="pm-meeting-capture-player">
      {audioEl}
      <button
        type="button"
        className="pm-meeting-player-play"
        onClick={toggle}
        aria-label={playing ? "Pause" : "Play"}
      >
        {playing ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
      </button>
      {progressEl}
      <span className="pm-meeting-player-time t-mono-family">
        {fmt(current)}
        <span className="pm-meeting-player-time-sep">/</span>
        {fmt(duration)}
      </span>
    </div>
  )
})
