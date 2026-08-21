import { useRef, useEffect, useState, forwardRef, useImperativeHandle, type RefObject } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { SoftMenu } from "@/components/ui/menu"
import { cn } from "@/lib/utils"
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip"
import { Upload, Mic, Square, Pause, Loader2, FileAudio, RefreshCw, Play, AlertCircle, BookOpen, Languages, Trash2, Download, Ban } from "lucide-react"
import type { MeetingStatus, HotWordsLibrarySummary, LanguageHintOption } from "@/api/client"
import { toggleLanguageHint } from "./language-hints-selector"
import { useT } from "@/i18n/use-t"

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
  /** Real AnalyserNode levels 0–1; never Math.random (re-renders would thrash). */
  levels?: number[] | null
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
  hotWordsLibraryIds?: string[]
  hotWordsLibraries?: HotWordsLibrarySummary[]
  /** File-model adapter flag. When false the chip is visible but disabled. */
  hotWordsSupported?: boolean
  onSelectHotWords?: (libraryIds: string[]) => void
  languageHints?: string[]
  languageHintOptions?: LanguageHintOption[]
  onChangeLanguageHints?: (hints: string[]) => void
  maxLanguageHints?: number
  showLanguageSelector?: boolean
  onTimeUpdate?: (time: number) => void
  recorderError?: string | null
  onDiscard?: () => void
}

export interface MediaBarHandle {
  /** Jump to start and play; optional end auto-pauses (one sentence). */
  seekTo: (time: number, end?: number) => void
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
  levels,
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
  hotWordsLibraryIds = [],
  hotWordsLibraries = [],
  hotWordsSupported = true,
  onSelectHotWords,
  languageHints = [],
  languageHintOptions = [],
  onChangeLanguageHints,
  maxLanguageHints = 1,
  showLanguageSelector,
  onTimeUpdate,
  recorderError,
  onDiscard,
}, ref) {
  const t = useT()
  const inputRef = useRef<HTMLInputElement>(null)
  const audioRef = useRef<HTMLAudioElement>(null)
  const progressTrackRef = useRef<HTMLDivElement>(null)
  const segmentEndRef = useRef<number | null>(null)

  const [discardConfirmOpen, setDiscardConfirmOpen] = useState(false)
  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [audioDuration, setAudioDuration] = useState(0)
  const [scrubbing, setScrubbing] = useState(false)
  const [scrubRatio, setScrubRatio] = useState(0)
  const scrubbingRef = useRef(false)

  // Hot Words / Language menus (SoftMenu + portal)
  const [hwOpen, setHwOpen] = useState(false)
  const hwBtnRef = useRef<HTMLElement>(null)
  const [langOpen, setLangOpen] = useState(false)
  const langBtnRef = useRef<HTMLElement>(null)

  useEffect(() => {
    if (!hotWordsSupported) setHwOpen(false)
  }, [hotWordsSupported])

  // Click outside closes SoftMenu (portal items are still under document)
  useEffect(() => {
    if (!hwOpen && !langOpen) return
    const handler = (e: MouseEvent) => {
      const t = e.target as Node
      if (hwOpen) {
        const hitBtn = hwBtnRef.current?.contains(t)
        const hitMenu = (e.target as Element)?.closest?.("[data-slot='menu']")
        if (!hitBtn && !hitMenu) setHwOpen(false)
      }
      if (langOpen) {
        const hitBtn = langBtnRef.current?.contains(t)
        const hitMenu = (e.target as Element)?.closest?.("[data-slot='menu']")
        if (!hitBtn && !hitMenu) setLangOpen(false)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [hwOpen, langOpen])

  // Pause + reset local player chrome when meeting changes (no parent remount)
  useEffect(() => {
    const el = audioRef.current
    if (el) {
      el.pause()
      try {
        el.currentTime = 0
      } catch { /* ignore */ }
    }
    setIsPlaying(false)
    setCurrentTime(0)
    setAudioDuration(0)
    setScrubbing(false)
    setScrubRatio(0)
    segmentEndRef.current = null
    return () => {
      const a = audioRef.current
      if (a) a.pause()
    }
  }, [meetingId])

  // Emit timeupdate for transcript auto-scroll + local progress state
  useEffect(() => {
    const el = audioRef.current
    if (!el) return
    const onTime = () => {
      const t = el.currentTime
      if (!scrubbing) setCurrentTime(t)
      onTimeUpdate?.(t)
      const stopAt = segmentEndRef.current
      if (stopAt != null && t >= stopAt - 0.02) {
        segmentEndRef.current = null
        el.pause()
        if (el.currentTime > stopAt) {
          el.currentTime = stopAt
          setCurrentTime(stopAt)
        }
      }
    }
    const onMeta = () => setAudioDuration(el.duration || 0)
    const onPlay = () => setIsPlaying(true)
    const onPause = () => setIsPlaying(false)
    const onEnded = () => {
      segmentEndRef.current = null
      setIsPlaying(false)
    }
    el.addEventListener("timeupdate", onTime)
    el.addEventListener("loadedmetadata", onMeta)
    el.addEventListener("durationchange", onMeta)
    el.addEventListener("play", onPlay)
    el.addEventListener("pause", onPause)
    el.addEventListener("ended", onEnded)
    return () => {
      el.removeEventListener("timeupdate", onTime)
      el.removeEventListener("loadedmetadata", onMeta)
      el.removeEventListener("durationchange", onMeta)
      el.removeEventListener("play", onPlay)
      el.removeEventListener("pause", onPause)
      el.removeEventListener("ended", onEnded)
    }
  }, [onTimeUpdate, audioUrl, scrubbing])

  // Reset transport when audio source changes
  useEffect(() => {
    segmentEndRef.current = null
    setIsPlaying(false)
    setCurrentTime(0)
    setAudioDuration(0)
    setScrubbing(false)
  }, [audioUrl, audioVersion])

  useImperativeHandle(ref, () => ({
    seekTo(time: number, end?: number) {
      const el = audioRef.current
      if (!el) return
      const t = Math.max(0, time)
      segmentEndRef.current =
        end != null && Number.isFinite(end) && end > t ? end : null
      el.currentTime = t
      setCurrentTime(t)
      el.play().catch(() => {})  // AbortError when interrupted by pause/unmount
    },
  }))

  const seekFromClientX = (clientX: number) => {
    const track = progressTrackRef.current
    const el = audioRef.current
    if (!track || !el) return
    segmentEndRef.current = null
    const rect = track.getBoundingClientRect()
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / Math.max(rect.width, 1)))
    setScrubRatio(ratio)
    const dur = el.duration || audioDuration || 0
    if (dur > 0 && Number.isFinite(dur)) {
      const t = ratio * dur
      el.currentTime = t
      setCurrentTime(t)
      onTimeUpdate?.(t)
    }
  }

  const beginScrub = (clientX: number) => {
    scrubbingRef.current = true
    setScrubbing(true)
    seekFromClientX(clientX)
  }
  const moveScrub = (clientX: number) => {
    if (!scrubbingRef.current) return
    seekFromClientX(clientX)
  }
  const endScrub = (clientX?: number) => {
    if (clientX != null) seekFromClientX(clientX)
    scrubbingRef.current = false
    setScrubbing(false)
  }

  const togglePlay = () => {
    const el = audioRef.current
    if (!el) return
    // Manual play/pause leaves free-play mode
    segmentEndRef.current = null
    if (el.paused) el.play().catch(() => {})
    else el.pause()
  }

  // Recording state — real levels only (no Math.random: parent re-renders would thrash bars)
  if (isRecording || isPaused) {
    const barCount = 20
    const rawLevels = Array.isArray(levels) && levels.length > 0 ? levels : null
    const waveBars = Array.from({ length: barCount }, (_, i) => {
      if (isPaused || !rawLevels) return 0.08
      const src = rawLevels[Math.floor((i * rawLevels.length) / barCount)] ?? 0
      return Math.min(1, Math.max(0.06, src))
    })
    return (
      <div className="flex flex-col gap-2 py-1">
        <div className="flex items-center gap-3">
          <div
            className={cn(
              "h-2.5 w-2.5 rounded-full shrink-0",
              isPaused
                ? "bg-[var(--pm-faint,#969c96)]"
                : "bg-[var(--pm-danger,#b42318)] animate-pulse",
            )}
          />
          <span className="pm-meta tabular-nums t-mono-family">{formatDuration(duration)}</span>
          <div
            className={cn(
              "flex-1 flex items-end gap-0.5 h-5 min-w-0",
              isPaused && "opacity-45",
            )}
            aria-hidden
          >
            {waveBars.map((v, i) => (
              <div
                key={i}
                className="w-1 min-w-[3px] flex-1 max-w-[5px] rounded-full"
                style={{
                  height: `${Math.round(4 + v * 16)}px`,
                  background:
                    "color-mix(in srgb, var(--pm-green, #1a5e3d) 55%, transparent)",
                  transition: "height 80ms linear",
                }}
              />
            ))}
          </div>
          <Button variant="ghost" size="sm" onClick={isPaused ? onResumeRecord : onPauseRecord}>
            <Pause className="size-3.5 mr-1" />
            {isPaused ? t("common.resume") : t("common.pause")}
          </Button>
          <Button variant="default" size="sm" onClick={onStopRecord}>
            <Square className="size-3.5 mr-1" />
            {t("meeting.finish")}
          </Button>
          {onDiscard && (
            <Button variant="destructive" size="sm" onClick={() => setDiscardConfirmOpen(true)}>
              <Trash2 className="size-3.5 mr-1" />
              {t("common.discard")}
            </Button>
          )}
        </div>
        {hasRealtimeProvider && onToggleRealtime && (
          <Button
            type="button"
            variant={realtimeEnabled ? "secondary" : "ghost"}
            size="sm"
            className="self-start"
            onClick={onToggleRealtime}
          >
            <span
              className={cn(
                "w-1.5 h-1.5 rounded-full mr-1.5",
                realtimeEnabled ? "bg-[var(--pm-green)]" : "bg-[var(--pm-faint)]",
              )}
            />
            {t("meeting.liveCaptions")}
          </Button>
        )}
        {onDiscard && (
          <Dialog open={discardConfirmOpen} onOpenChange={setDiscardConfirmOpen}>
            <DialogContent
              className="pm-dialog pm-dialog--silk sm:max-w-sm"
              showCloseButton={false}
              overlayClassName="pm-dialog-overlay--silk"
            >
              <DialogHeader>
                <DialogTitle>{t("meeting.discardRecordingQ")}</DialogTitle>
                <DialogDescription>
                  {t("meeting.discardRecordingBody")}
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button type="button" variant="ghost" size="sm" onClick={() => setDiscardConfirmOpen(false)}>
                  {t("common.cancel")}
                </Button>
                <Button
                  type="button"
                  variant="destructive-solid"
                  size="sm"
                  onClick={() => { setDiscardConfirmOpen(false); onDiscard() }}
                >
                  {t("common.discard")}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        )}
      </div>
    )
  }

  // Transcribing state — same custom player shell as ready state (no native audio chrome)
  if (status === "transcribing") {
    const dur = audioDuration > 0 && Number.isFinite(audioDuration) ? audioDuration : 0
    const ratio = scrubbing
      ? scrubRatio
      : dur > 0
        ? Math.min(1, Math.max(0, currentTime / dur))
        : 0
    const displayTime = scrubbing && dur > 0 ? scrubRatio * dur : currentTime

    return (
      <div className="pm-meeting-player">
        {transcriptionError && (
          <div className="pm-meeting-warn">
            <AlertCircle className="size-3.5 shrink-0" />
            <span className="flex-1">{t("meeting.transcriptionFailed", { error: transcriptionError })}</span>
          </div>
        )}

        {audioUrl ? (
          <audio
            key={`transcribing-${meetingId}-${audioVersion}`}
            ref={audioRef}
            src={audioUrl}
            preload="metadata"
            className="sr-only"
            data-meeting-audio=""
            aria-hidden
          >
            <track kind="captions" />
          </audio>
        ) : null}

        {/* Row 1: progress only */}
        <div className="pm-meeting-player-progress-row">
          {audioUrl ? (
            <div
              ref={progressTrackRef}
              className={cn("pm-meeting-player-progress", scrubbing && "is-scrubbing")}
              role="slider"
              tabIndex={0}
              aria-label={t("common.seek")}
              aria-valuemin={0}
              aria-valuemax={Math.floor(dur || 0)}
              aria-valuenow={Math.floor(displayTime || 0)}
              onPointerDown={(e) => {
                e.currentTarget.setPointerCapture(e.pointerId)
                beginScrub(e.clientX)
              }}
              onPointerMove={(e) => moveScrub(e.clientX)}
              onPointerUp={(e) => {
                endScrub(e.clientX)
                try { e.currentTarget.releasePointerCapture(e.pointerId) } catch { /* */ }
              }}
              onPointerCancel={() => endScrub()}
              onKeyDown={(e) => {
                const el = audioRef.current
                if (!el || !dur) return
                const step = e.shiftKey ? 10 : 5
                if (e.key === "ArrowRight" || e.key === "ArrowUp") {
                  e.preventDefault()
                  const t = Math.min(dur, (el.currentTime || 0) + step)
                  el.currentTime = t
                  setCurrentTime(t)
                } else if (e.key === "ArrowLeft" || e.key === "ArrowDown") {
                  e.preventDefault()
                  const t = Math.max(0, (el.currentTime || 0) - step)
                  el.currentTime = t
                  setCurrentTime(t)
                } else if (e.key === "Home") {
                  e.preventDefault()
                  el.currentTime = 0
                  setCurrentTime(0)
                } else if (e.key === "End") {
                  e.preventDefault()
                  el.currentTime = dur
                  setCurrentTime(dur)
                }
              }}
            >
              <div className="pm-meeting-player-progress-fill" style={{ width: `${ratio * 100}%` }} />
              <div className="pm-meeting-player-progress-thumb" style={{ left: `${ratio * 100}%` }} />
            </div>
          ) : (
            <div className="pm-meeting-player-progress pm-meeting-player-progress--busy" aria-hidden>
              <div className="pm-meeting-player-progress-fill animate-progress" style={{ width: "40%" }} />
            </div>
          )}
        </div>

        {/* Row 2: play + time centered as one group */}
        <div className="pm-meeting-player-main">
          <span className="pm-meeting-player-status pm-meta inline-flex items-center gap-1.5 min-w-0 truncate">
            <Loader2 className="size-3 animate-spin text-[var(--pm-green)] shrink-0" />
            {t("meeting.transcribing")}
          </span>
          {audioUrl ? (
            <button
              type="button"
              className="pm-meeting-player-play"
              onClick={togglePlay}
              aria-label={isPlaying ? t("common.pause") : t("common.play")}
            >
              {isPlaying ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
            </button>
          ) : (
            <span className="pm-meeting-player-play-slot">
              <Loader2 className="size-3.5 animate-spin text-[var(--pm-green)] shrink-0" />
            </span>
          )}
          <span className="pm-meeting-player-time t-mono-family" aria-live="off">
            {formatDuration(Math.floor(displayTime || 0))}
            <span className="pm-meeting-player-time-sep">/</span>
            {formatDuration(Math.floor(dur || 0))}
          </span>
        </div>

        {/* Row 3: cancel (centered chip tray) */}
        {onCancelTranscribe ? (
          <div className="pm-meeting-player-tools">
            <Button variant="destructive" size="sm" onClick={onCancelTranscribe}>
              <Square className="size-3.5 mr-1" />
              {t("common.stop")}
            </Button>
          </div>
        ) : null}
      </div>
    )
  }

  // Has audio — custom player card (tools row + scrubbable progress row)
  if (hasAudio) {
    const hwSelectedLabel = hotWordsLibraryIds
      .map((id) => hotWordsLibraries.find((l) => l.id === id)?.name)
      .filter(Boolean)
      .join(" · ")
    const langCount = languageHints.length
    const langCustomized = langCount > 0 && !(langCount === 1 && languageHints[0] === "auto")
    const dur = audioDuration > 0 && Number.isFinite(audioDuration) ? audioDuration : 0
    const ratio = scrubbing
      ? scrubRatio
      : dur > 0
        ? Math.min(1, Math.max(0, currentTime / dur))
        : 0
    const displayTime = scrubbing && dur > 0 ? scrubRatio * dur : currentTime

    return (
      <div className="pm-meeting-player">
        {transcriptionError && (
          <div className="pm-meeting-warn">
            <AlertCircle className="size-3.5 shrink-0" />
            <span className="flex-1">{t("meeting.transcriptionFailed", { error: transcriptionError })}</span>
          </div>
        )}

        {audioUrl ? (
          <audio
            key={`player-${meetingId}-${audioVersion}`}
            ref={audioRef}
            src={audioUrl}
            preload="metadata"
            className="sr-only"
            data-meeting-audio=""
            aria-hidden
          >
            <track kind="captions" />
          </audio>
        ) : null}

        {/* Row 1: progress only (full width) */}
        <div className="pm-meeting-player-progress-row">
          {audioUrl ? (
            <div
              ref={progressTrackRef}
              className={cn("pm-meeting-player-progress", scrubbing && "is-scrubbing")}
              role="slider"
              tabIndex={0}
              aria-label={t("common.seek")}
              aria-valuemin={0}
              aria-valuemax={Math.floor(dur || 0)}
              aria-valuenow={Math.floor(displayTime || 0)}
              onPointerDown={(e) => {
                e.currentTarget.setPointerCapture(e.pointerId)
                beginScrub(e.clientX)
              }}
              onPointerMove={(e) => moveScrub(e.clientX)}
              onPointerUp={(e) => {
                endScrub(e.clientX)
                try { e.currentTarget.releasePointerCapture(e.pointerId) } catch { /* */ }
              }}
              onPointerCancel={() => endScrub()}
              onKeyDown={(e) => {
                const el = audioRef.current
                if (!el || !dur) return
                const step = e.shiftKey ? 10 : 5
                if (e.key === "ArrowRight" || e.key === "ArrowUp") {
                  e.preventDefault()
                  const t = Math.min(dur, (el.currentTime || 0) + step)
                  el.currentTime = t
                  setCurrentTime(t)
                } else if (e.key === "ArrowLeft" || e.key === "ArrowDown") {
                  e.preventDefault()
                  const t = Math.max(0, (el.currentTime || 0) - step)
                  el.currentTime = t
                  setCurrentTime(t)
                } else if (e.key === "Home") {
                  e.preventDefault()
                  el.currentTime = 0
                  setCurrentTime(0)
                } else if (e.key === "End") {
                  e.preventDefault()
                  el.currentTime = dur
                  setCurrentTime(dur)
                }
              }}
            >
              <div className="pm-meeting-player-progress-fill" style={{ width: `${ratio * 100}%` }} />
              <div className="pm-meeting-player-progress-thumb" style={{ left: `${ratio * 100}%` }} />
            </div>
          ) : (
            <p className="pm-meta truncate w-full min-w-0" title={audioPath}>
              {audioPath ? audioPath.split("/").pop() : t("meeting.audioUploaded")}
            </p>
          )}
        </div>

        {/* Row 2: play + time centered as one group */}
        <div className="pm-meeting-player-main">
          {audioUrl ? (
            <button
              type="button"
              className="pm-meeting-player-play"
              onClick={togglePlay}
              aria-label={isPlaying ? t("common.pause") : t("common.play")}
            >
              {isPlaying ? (
                <Pause className="size-3.5" strokeWidth={2.25} />
              ) : (
                <Play className="size-3.5 pm-meeting-player-play-icon" strokeWidth={2.25} />
              )}
            </button>
          ) : (
            <span className="pm-meeting-player-play-slot">
              <FileAudio className="size-3.5 text-[var(--pm-faint)] shrink-0" />
            </span>
          )}
          <span className="pm-meeting-player-time t-mono-family" aria-live="off">
            {formatDuration(Math.floor(displayTime || 0))}
            <span className="pm-meeting-player-time-sep">/</span>
            {formatDuration(Math.floor(dur || 0))}
          </span>
        </div>

        {/* Row 3: tool buttons — hug content, centered */}
        <div className="pm-meeting-player-tools">
          {audioUrl && (
            <Tooltip>
              <TooltipTrigger
                render={
                  <button
                    type="button"
                    className="pm-meeting-player-chip"
                    aria-label={t("meeting.downloadAudio")}
                    onClick={() => {
                      const a = document.createElement("a")
                      a.href = audioUrl
                      a.download = audioPath?.split("/").pop() || `meeting-${meetingId}-audio`
                      a.rel = "noopener"
                      document.body.appendChild(a)
                      a.click()
                      a.remove()
                    }}
                  >
                    <Download className="size-3.5" strokeWidth={1.75} />
                  </button>
                }
              />
              <TooltipContent side="top">{t("meeting.downloadAudio")}</TooltipContent>
            </Tooltip>
          )}

          {onSelectHotWords && (
            <div className="relative" ref={hwBtnRef as RefObject<HTMLDivElement>}>
              <Tooltip>
                <TooltipTrigger
                  render={
                    <button
                      type="button"
                      disabled={!hotWordsSupported}
                      className={cn(
                        "pm-meeting-player-chip",
                        hotWordsSupported && hotWordsLibraryIds.length > 0 && "is-active",
                      )}
                      onClick={() => {
                        if (!hotWordsSupported) return
                        setHwOpen(!hwOpen)
                        setLangOpen(false)
                      }}
                      aria-label={t("meeting.hotWords")}
                    >
                      <BookOpen className="size-3.5" strokeWidth={1.75} />
                    </button>
                  }
                />
                <TooltipContent side="top">
                  {hotWordsSupported
                    ? hwSelectedLabel
                      ? `${t("meeting.hotWords")} · ${hwSelectedLabel}`
                      : t("meeting.hotWords")
                    : t("settings.hotWordsUnavailableTitle")}
                </TooltipContent>
              </Tooltip>
              <SoftMenu
                open={hotWordsSupported && hwOpen}
                portal
                anchorRef={hwBtnRef}
                align="end"
                className="pm-hw-menu min-w-[240px]"
              >
                <div className="pm-hw-list pm-hw-list--menu" role="listbox" aria-label={t("meeting.hotWordLibraries")}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={hotWordsLibraryIds.length === 0}
                    className={cn("pm-hw-option", hotWordsLibraryIds.length === 0 ? "is-on" : "is-off")}
                    onClick={() => { onSelectHotWords([]) }}
                  >
                    <span className="pm-hw-option-icon" aria-hidden>
                      <Ban className="size-3.5" />
                    </span>
                    <span className="pm-hw-option-body">
                      <span className="pm-hw-option-name">{t("common.none")}</span>
                      <span className="pm-hw-option-meta">{t("meeting.noVocabBoost")}</span>
                    </span>
                  </button>
                  {hotWordsLibraries.map((lib) => {
                    const isOn = hotWordsLibraryIds.includes(lib.id)
                    return (
                      <button
                        key={lib.id}
                        type="button"
                        role="option"
                        aria-selected={isOn}
                        className={cn("pm-hw-option", isOn ? "is-on" : "is-off")}
                        onClick={() => {
                          onSelectHotWords(
                            isOn
                              ? hotWordsLibraryIds.filter((id) => id !== lib.id)
                              : [...hotWordsLibraryIds, lib.id],
                          )
                        }}
                      >
                        <span className="pm-hw-option-icon" aria-hidden>
                          <BookOpen className="size-3.5" />
                        </span>
                        <span className="pm-hw-option-body">
                          <span className="pm-hw-option-name">{lib.name}</span>
                          <span className="pm-hw-option-meta">
                            {lib.word_count === 1
                              ? t("meeting.nWord", { n: lib.word_count })
                              : t("meeting.nWords", { n: lib.word_count })}
                          </span>
                        </span>
                      </button>
                    )
                  })}
                  {hotWordsLibraries.length === 0 && (
                    <p className="pm-hw-empty">{t("meeting.noHotWordLibs")}</p>
                  )}
                </div>
              </SoftMenu>
            </div>
          )}

          {showLanguageSelector && (
            <div className="relative" ref={langBtnRef as RefObject<HTMLDivElement>}>
              <Tooltip>
                <TooltipTrigger
                  render={
                    <button
                      type="button"
                      className={cn(
                        "pm-meeting-player-chip",
                        langCustomized && "is-active",
                      )}
                      onClick={() => { setLangOpen(!langOpen); setHwOpen(false) }}
                      aria-label={t("meeting.language")}
                    >
                      <Languages className="size-3.5" strokeWidth={1.75} />
                    </button>
                  }
                />
                <TooltipContent side="top">
                  {langCustomized
                    ? t("meeting.langCountSelected", { n: langCount })
                    : t("meeting.languageAuto")}
                </TooltipContent>
              </Tooltip>
              <SoftMenu
                open={langOpen}
                portal
                anchorRef={langBtnRef}
                align="end"
                className="pm-lang-menu min-w-[220px]"
              >
                <p className="pm-meta px-1 pb-1.5">
                  {maxLanguageHints <= 1
                    ? t("meeting.oneLanguage")
                    : t("meeting.upToLanguages", { n: maxLanguageHints })}
                </p>
                <div className="pm-lang-pills pm-lang-pills--menu" role="group" aria-label={t("meeting.languageHints")}>
                  {(() => {
                    const opts = languageHintOptions.some((o) => o.code === "auto")
                      ? languageHintOptions
                      : [{ code: "auto", label: t("meeting.auto") }, ...languageHintOptions]
                    const isAutoOnly =
                      languageHints.length === 0 ||
                      (languageHints.length === 1 && languageHints[0] === "auto")
                    return opts.map(({ code, label }) => {
                      const isSelected =
                        code === "auto" ? isAutoOnly : languageHints.includes(code)
                      return (
                        <button
                          key={code}
                          type="button"
                          className={cn("pm-lang-pill", isSelected ? "is-on" : "is-off")}
                          aria-pressed={isSelected}
                          onClick={() => {
                            onChangeLanguageHints?.(
                              toggleLanguageHint(languageHints, code, maxLanguageHints),
                            )
                          }}
                        >
                          <span className="pm-lang-pill-label">{code === "auto" ? t("meeting.auto") : label}</span>
                          {code !== "auto" && (
                            <span className="pm-lang-pill-code t-mono-family">{code}</span>
                          )}
                        </button>
                      )
                    })
                  })()}
                </div>
              </SoftMenu>
            </div>
          )}

          {/* Rightmost: Replace / Transcribe (pre-tx) or Re-transcribe (post-tx) */}
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
              <button
                type="button"
                className="pm-meeting-player-chip pm-meeting-player-chip--label"
                onClick={() => inputRef.current?.click()}
              >
                <RefreshCw className="size-3" strokeWidth={1.75} />
                <span>{t("common.replace")}</span>
              </button>
              <button
                type="button"
                className="pm-meeting-player-chip pm-meeting-player-chip--primary-label"
                onClick={onTranscribe}
              >
                <Play className="size-3 pm-meeting-player-play-icon" strokeWidth={2} />
                <span>{t("common.transcribe")}</span>
              </button>
            </>
          )}
          {hasTranscript && onReTranscribe && (
            <Tooltip>
              <TooltipTrigger
                render={
                  <button
                    type="button"
                    className="pm-meeting-player-chip pm-meeting-player-chip--label"
                    onClick={onReTranscribe}
                    aria-label={t("meeting.reTranscribe")}
                  >
                    <RefreshCw className="size-3" strokeWidth={1.75} />
                    <span>{t("meeting.reTranscribe")}</span>
                  </button>
                }
              />
              <TooltipContent side="top">{t("meeting.reTranscribe")}</TooltipContent>
            </Tooltip>
          )}
        </div>{/* /.pm-meeting-player-tools */}
      </div>
    )
  }

  // No audio — upload / record
  return (
    <div className="flex flex-col gap-2 py-1">
      {recorderError && (
        <div className="pm-meeting-warn">
          <AlertCircle className="size-3.5 shrink-0" />
          <span className="flex-1">{recorderError}</span>
        </div>
      )}
      <div className="flex items-center gap-2">
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
        <Button variant="ghost" size="sm" onClick={() => inputRef.current?.click()}>
          <Upload className="size-3.5 mr-1" />
          {t("common.audio")}
        </Button>
        <Button variant="default" size="sm" onClick={onStartRecord}>
          <Mic className="size-3.5 mr-1" />
          {t("meeting.record")}
        </Button>
        {hasRealtimeProvider && onToggleRealtime && (
          <Button
            type="button"
            variant={realtimeEnabled ? "secondary" : "ghost"}
            size="sm"
            className="ml-auto"
            onClick={onToggleRealtime}
            title={realtimeEnabled ? t("meeting.liveCaptionsOn") : t("meeting.liveCaptionsOff")}
          >
            <span
              className={cn(
                "w-1.5 h-1.5 rounded-full mr-1.5",
                realtimeEnabled ? "bg-[var(--pm-green)]" : "bg-[var(--pm-faint)]",
              )}
            />
            {t("meeting.liveCaptions")}
          </Button>
        )}
      </div>
    </div>
  )
})

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`
}
