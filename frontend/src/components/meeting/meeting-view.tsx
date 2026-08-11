import {
  useState, useEffect, useLayoutEffect, useCallback, useRef, forwardRef, useImperativeHandle,
  type ReactNode,
} from "react"
import { createPortal } from "react-dom"
import { useShallow } from "zustand/react/shallow"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { Pencil, Check, X, Plus, PanelRightClose, PanelRightOpen, Mic, Upload, Pause, Square, Loader2, Users, Play, Sparkles } from "lucide-react"
import { Tabs, TabsList, TabsTrigger, TabsIndicator } from "@/components/ui/tabs"
import { useAppStore } from "@/stores/app-store"
import { useAudioRecorder } from "@/hooks/use-audio-recorder"
import { useTranscription } from "@/hooks/use-transcription"
import {
  getMeetings, getMeeting, deleteMeeting, discardMeetingRecording,
  uploadMeetingAudio, transcribeMeeting, cancelTranscribeMeeting,
  getMeetingTranscript, updateMeeting,
  getRealtimeTranscriptionProviders, getFileTranscriptionProviders,
  getActiveProviderInfo, getHotWordsLibraries,
  type Meeting, type TranscriptSegment, type LanguageHintOption, type HotWordsLibrarySummary,
} from "@/api/client"
import { toast } from "sonner"
import {
  MeetingTabs,
  type SectionRailActions,
  type SectionRailModel,
} from "./meeting-tabs"
import { TranscriptTab, SpeakersTab } from "./transcript-panel"
import { AlertCircle, Settings } from "lucide-react"
import { MeetingList } from "./meeting-list"
import { MediaBar } from "./media-bar"
import type { MediaBarHandle } from "./media-bar"

import {
  MeetingQuickChat,
  MeetingQcFab,
  type MeetingQcSpinPhase,
} from "./meeting-quick-chat"
import { DEFAULT_LANGUAGE_HINTS, LanguageHintsSelector } from "./language-hints-selector"
import { HotWordsSelector } from "./hot-words-selector"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { LiveCaptureControlCard } from "./live-capture-control-card"
import { startStream as startBlueprintStream } from "@/hooks/use-blueprint-stream"

export type CaptureMiniPlayerHandle = {
  /** Jump to start and play; if end is set, auto-pause at that time (one sentence). */
  seekTo: (start: number, end?: number) => void
}

/** Compact / review capture player; optional segment end-stop */
const CaptureMiniPlayer = forwardRef<
  CaptureMiniPlayerHandle,
  {
    audioUrl: string
    audioVersion: number
    /** compact = chip player; review = progress under cards + play in footer row */
    variant?: "compact" | "review"
    onTimeUpdate?: (time: number) => void
    /** review only: right-side action (e.g. Summarize) on same row as play */
    footerSlot?: ReactNode
  }
>(function CaptureMiniPlayer(
  { audioUrl, audioVersion, variant = "compact", onTimeUpdate, footerSlot },
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
          <button
            type="button"
            className="pm-meeting-review-play"
            onClick={toggle}
            aria-label={playing ? "Pause" : "Play"}
          >
            {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
          </button>
          {footerSlot}
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

export function MeetingView({ active = true }: { active?: boolean }) {
  const {
    activeMeeting,
    setActiveMeeting,
    setSidebarView,
    fetchCollections,
    collections,
    setActiveCollection,
  } = useAppStore(
    useShallow((s) => ({
      activeMeeting: s.activeMeeting,
      setActiveMeeting: s.setActiveMeeting,
      setSidebarView: s.setSidebarView,
      fetchCollections: s.fetchCollections,
      collections: s.collections,
      setActiveCollection: s.setActiveCollection,
    }))
  )

  // Data
  const [meetings, setMeetings] = useState<Meeting[]>([])
  const [meeting, setMeeting] = useState<Meeting | null>(null)
  const meetingContentRef = useRef<HTMLDivElement>(null)
  const [transcript, setTranscript] = useState<TranscriptSegment[]>([])

  // Guard against stale fetchMeeting / transcript results after activeMeeting changes
  const fetchMeetingIdRef = useRef<string | null>(null)
  const fetchTranscriptIdRef = useRef<string | null>(null)
  /**
   * Soft-fade stage cards when switching meetings (no blank hard-cut).
   * Keep previous meeting painted until the next payload lands.
   */
  const [meetingSoftFaded, setMeetingSoftFaded] = useState(false)
  const meetingSoftSkipRef = useRef(true)
  const meetingSoftGenRef = useRef(0)

  // UI state
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [realtimeEnabled, setRealtimeEnabled] = useState(false)
  const [hasRealtimeProvider, setHasRealtimeProvider] = useState(false)
  /** Start-recording Live caption chip open (hover with close debounce) */
  const [startLiveChipOpen, setStartLiveChipOpen] = useState(false)
  const startLiveChipCloseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  /**
   * Which meeting owns the in-progress capture stream.
   * Recorder lives on MeetingView (shared) — must not paint recording UI on other meetings.
   */
  const [recordingMeetingId, setRecordingMeetingId] = useState<string | null>(null)
  /** Survives clearRecordingOwner until audioBlob upload finishes (may stop while viewing another meeting). */
  const captureOwnerRef = useRef<string | null>(null)
  const [hasFileProvider, setHasFileProvider] = useState(true) // optimistic — avoids flash on remount; config check corrects if needed
  const [supportedLanguageHints, setSupportedLanguageHints] = useState<LanguageHintOption[]>([])
  const [hotWordsLibraries, setHotWordsLibraries] = useState<HotWordsLibrarySummary[]>([])
  // Per-meeting language hints: keyed by meeting ID, persists across meeting switches during the session
  const perMeetingLanguageHints = useRef<Map<string, string[]>>(new Map())
  const [languageHints, setLanguageHints] = useState<string[]>([...DEFAULT_LANGUAGE_HINTS])
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState("")
  const [audioVersion, setAudioVersion] = useState(0)
  const [retranscribeConfirmOpen, setRetranscribeConfirmOpen] = useState(false)
  const [focusRef, setFocusRef] = useState<{ id: string; ts: number; fromChat?: boolean } | null>(null)
  const [activeSectionTag, setActiveSectionTag] = useState("")
  const discardingRef = useRef(false)
  const [playbackTime, setPlaybackTime] = useState(0)
  const [quickChatOpen, setQuickChatOpen] = useState(false)
  const [transcriptJumpCounter, setTranscriptJumpCounter] = useState(0)
  /** Summary section selection (synced with MeetingTabs + side Sections tab) */
  const [selectedSummaryId, setSelectedSummaryId] = useState("tab_general")
  const [, setMainTab] = useState("summary")
  /** Right analysis rail: Sections | Transcript | Speaker */
  const [sideTab, setSideTab] = useState<"sections" | "transcript" | "speaker">("sections")
  const [sideRailOpen, setSideRailOpen] = useState(true)
  /**
   * Only user-driven collapse/expand should silk-slide the main pad + side.
   * Meeting switch / capture→studio must snap — otherwise left body “shrinks back”.
   */
  const [sideRailMotion, setSideRailMotion] = useState(false)
  const sideRailMotionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const setSideRailOpenWithMotion = useCallback((open: boolean) => {
    setSideRailMotion(true)
    setSideRailOpen(open)
    if (sideRailMotionTimerRef.current) clearTimeout(sideRailMotionTimerRef.current)
    sideRailMotionTimerRef.current = setTimeout(() => {
      setSideRailMotion(false)
      sideRailMotionTimerRef.current = null
    }, 520)
  }, [])
  /**
   * Side surface: tools (Sections|Transcript|Speaker) vs Chat.
   * Only this tools↔chat swap fades the head + body.
   * Tab switches within tools are instant (sliding pill + content crossfade).
   */
  type SideSurface = "tools" | "chat"
  const sideSurfaceTarget: SideSurface = quickChatOpen ? "chat" : "tools"
  const [sideSurfaceDisplay, setSideSurfaceDisplay] = useState<SideSurface>(sideSurfaceTarget)
  const [sideSurfaceExiting, setSideSurfaceExiting] = useState(false)
  const sideSurfaceFadeRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  /**
   * QC diamond park:
   * Open (bottom→top): fade teleport + spin phases
   * Close chat (top→bottom): ride-out with side slide, then bottom fade-in
   */
  const [qcFabPark, setQcFabPark] = useState<"bottom" | "top">("bottom")
  const [qcFabFading, setQcFabFading] = useState(false)
  const [qcFabRideOut, setQcFabRideOut] = useState(false)
  const [qcFabBottomFadeIn, setQcFabBottomFadeIn] = useState(false)
  const [qcFabSpinPhase, setQcFabSpinPhase] = useState<MeetingQcSpinPhase>("idle")
  const qcFabParkRef = useRef<"bottom" | "top">("bottom")
  const qcFabTimersRef = useRef<ReturnType<typeof setTimeout>[]>([])
  /** Chat sentence-ref: same-width transcript overlay (does not shrink main) */
  const [txPeekOpen, setTxPeekOpen] = useState(false)
  const [sectionRailModel, setSectionRailModel] = useState<SectionRailModel | null>(null)
  const sectionRailActionsRef = useRef<SectionRailActions | null>(null)
  const sectionRailCardRef = useRef<HTMLDivElement>(null)
  const sectionItemRefs = useRef<Map<string, HTMLElement>>(new Map())
  const [sectionFocus, setSectionFocus] = useState({ top: 0, height: 0 })
  const [sectionFocusReady, setSectionFocusReady] = useState(false)
  const bindSectionRailActions = useCallback((actions: SectionRailActions) => {
    sectionRailActionsRef.current = actions
  }, [])
  const requestSideTab = useCallback((tab: "sections" | "transcript" | "speaker") => {
    setSideRailOpenWithMotion(true)
    setSideTab(tab)
  }, [setSideRailOpenWithMotion])
  /** Hover description: fixed under the hovered section item */
  const [sectionTip, setSectionTip] = useState<{
    id: string
    text: string
    left: number
    top: number
    width: number
  } | null>(null)
  const hideSectionTip = useCallback(() => setSectionTip(null), [])
  const showSectionTip = useCallback((
    id: string,
    text: string,
    anchorEl: HTMLElement | null,
  ) => {
    if (!text.trim() || !anchorEl) {
      setSectionTip(null)
      return
    }
    const r = anchorEl.getBoundingClientRect()
    // 3/5 of item width, right-aligned under the section button
    const width = Math.max(96, r.width * (3 / 5))
    setSectionTip({
      id,
      text,
      left: r.right - width,
      top: r.bottom + 6,
      width,
    })
  }, [])

  /* Sliding mint focus — same language as Meetings / Sessions rails */
  const activeSectionRailId =
    sectionRailModel?.items.find((i) => i.active)?.id ?? null
  useEffect(() => {
    if (!activeSectionRailId || sectionRailModel?.thinking) {
      setSectionFocusReady(false)
      return
    }
    const activeEl = sectionItemRefs.current.get(activeSectionRailId)
    if (!activeEl) return
    setSectionFocus({
      top: activeEl.offsetTop,
      height: activeEl.offsetHeight,
    })
    requestAnimationFrame(() => setSectionFocusReady(true))
  }, [activeSectionRailId, sectionRailModel?.items, sectionRailModel?.thinking])

  // Transcript right-rail panel and Meeting Chat are mutually exclusive
  // (both occupy stage-right card slot).

  const mainAreaRef = useRef<HTMLDivElement>(null)

  // Open side-rail Transcript when sentence reference is clicked (from Summary)
  useEffect(() => {
    if (focusRef && !focusRef.fromChat) {
      requestSideTab("transcript")
    }
  }, [focusRef?.ts, requestSideTab])

  // Reset side panels when switching meetings
  useEffect(() => {
    setQuickChatOpen(false)
    setTxPeekOpen(false)
    setTranscriptJumpCounter(0)
    setFocusRef(null)
    setSelectedSummaryId("tab_general")
    setMainTab("summary")
    setSideTab("sections")
    // Snap side open (no silk pad) — animated reopen looked like main area “shrinking”
    setSideRailMotion(false)
    if (sideRailMotionTimerRef.current) {
      clearTimeout(sideRailMotionTimerRef.current)
      sideRailMotionTimerRef.current = null
    }
    setSideRailOpen(true)
    setSideSurfaceDisplay("tools")
    setSideSurfaceExiting(false)
    if (sideSurfaceFadeRef.current) clearTimeout(sideSurfaceFadeRef.current)
    setSectionRailModel(null)
    setSectionTip(null)
    setQcFabPark("bottom")
    qcFabParkRef.current = "bottom"
    setQcFabFading(false)
    setQcFabRideOut(false)
    setQcFabBottomFadeIn(false)
    setQcFabSpinPhase("idle")
    qcFabTimersRef.current.forEach(clearTimeout)
    qcFabTimersRef.current = []
  }, [activeMeeting])

  /**
   * Side surface: sequential fade tools ↔ Chat (open + close).
   * 1) fade out current head + body
   * 2) swap display while still opacity 0
   * 3) double-rAF then fade / slide in (so open and close both animate)
   */
  useEffect(() => {
    if (sideSurfaceTarget === sideSurfaceDisplay) return
    if (sideSurfaceFadeRef.current) clearTimeout(sideSurfaceFadeRef.current)
    setSideSurfaceExiting(true)
    const OUT_MS = 200
    sideSurfaceFadeRef.current = setTimeout(() => {
      setSideSurfaceDisplay(sideSurfaceTarget)
      // Keep is-exiting one frame so the new tree paints at opacity 0, then enter
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          setSideSurfaceExiting(false)
          sideSurfaceFadeRef.current = null
        })
      })
    }, OUT_MS)
    return () => {
      if (sideSurfaceFadeRef.current) clearTimeout(sideSurfaceFadeRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only drive on target/display identity
  }, [sideSurfaceTarget, sideSurfaceDisplay])

  // Diamond park transitions
  useEffect(() => {
    const wantTop = quickChatOpen && sideRailOpen
    const target: "bottom" | "top" = wantTop ? "top" : "bottom"
    if (target === qcFabParkRef.current) return

    const from = qcFabParkRef.current
    qcFabTimersRef.current.forEach(clearTimeout)
    qcFabTimersRef.current = []
    const later = (ms: number, fn: () => void) => {
      const id = setTimeout(fn, ms)
      qcFabTimersRef.current.push(id)
    }

    const ENTER_FADE = 150
    const ENTER_DECEL_TOP = 420
    const ENTER_DECEL_BOTTOM = 520
    /** Match .pm-meeting-stage-right slide duration */
    const RIDE_OUT_MS = 420

    // Chat close (top → bottom): ride off with side, then fade in at bottom-right
    if (from === "top" && target === "bottom") {
      setQcFabBottomFadeIn(false)
      setQcFabFading(false)
      setQcFabRideOut(true)
      setQcFabSpinPhase("exiting")

      later(RIDE_OUT_MS, () => {
        qcFabParkRef.current = "bottom"
        setQcFabPark("bottom")
        setQcFabRideOut(false)
        setQcFabFading(true)
        setQcFabSpinPhase("enter-decel-bottom")
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            setQcFabFading(false)
            setQcFabBottomFadeIn(true)
          })
        })
        later(ENTER_DECEL_BOTTOM, () => {
          setQcFabSpinPhase("idle")
          setQcFabBottomFadeIn(false)
        })
      })

      return () => {
        qcFabTimersRef.current.forEach(clearTimeout)
        qcFabTimersRef.current = []
      }
    }

    // Open chat (bottom → top): fade teleport + spin enter
    const EXIT_TOTAL = 300
    const EXIT_FADE_AT = 150

    setQcFabRideOut(false)
    setQcFabBottomFadeIn(false)
    setQcFabFading(false)
    setQcFabSpinPhase("exiting")

    later(EXIT_FADE_AT, () => setQcFabFading(true))

    later(EXIT_TOTAL, () => {
      qcFabParkRef.current = target
      setQcFabPark(target)
      setQcFabFading(true)
      setQcFabSpinPhase("enter-hold")
      requestAnimationFrame(() => {
        requestAnimationFrame(() => setQcFabFading(false))
      })

      later(ENTER_FADE, () => {
        if (target === "top") {
          setQcFabSpinPhase("enter-decel-top")
          later(ENTER_DECEL_TOP, () => setQcFabSpinPhase("cruise"))
        } else {
          setQcFabSpinPhase("enter-decel-bottom")
          later(ENTER_DECEL_BOTTOM, () => setQcFabSpinPhase("idle"))
        }
      })
    })

    return () => {
      qcFabTimersRef.current.forEach(clearTimeout)
      qcFabTimersRef.current = []
    }
  }, [quickChatOpen, sideRailOpen])

  useEffect(() => {
    if (sideTab !== "sections" || !sideRailOpen) setSectionTip(null)
  }, [sideTab, sideRailOpen])

  // Hooks — pin transcription to the meeting that owns the capture stream so
  // switching the list selection does not reset WS / wipe live segments.
  const transcriptionMeetingId = recordingMeetingId ?? activeMeeting
  const transcription = useTranscription(transcriptionMeetingId)
  const recorder = useAudioRecorder(realtimeEnabled && hasRealtimeProvider ? transcription.sendAudioData : undefined)
  const mediaBarRef = useRef<MediaBarHandle>(null)
  const capturePlayerRef = useRef<CaptureMiniPlayerHandle>(null)
  /** Latest segments for resolving end times when only start is passed */
  const displaySegmentsRef = useRef<TranscriptSegment[]>([])

  // Keep transcription.durationRef in sync with recording duration so
  // toggle-reopen timestamps land at current position, not at 0.
  transcription.durationRef.current = recorder.duration

  // Auto-open side Transcript when live transcription starts (Steady path; F mode later)
  const prevIsTranscribingRef = useRef(false)
  useEffect(() => {
    if (realtimeEnabled && hasRealtimeProvider) {
      if (transcription.isTranscribing && !prevIsTranscribingRef.current) {
        requestSideTab("transcript")
      }
    }
    prevIsTranscribingRef.current = transcription.isTranscribing
  }, [realtimeEnabled, hasRealtimeProvider, transcription.isTranscribing, requestSideTab])

  // When realtime transcription finalizes (user stops recording), the hook
  // persists segments to the backend. Refetch the *recording* meeting.
  const transcriptionRef = useRef(transcription)
  transcriptionRef.current = transcription
  useEffect(() => {
    const mid = recordingMeetingId ?? activeMeeting
    if (!mid) return
    transcriptionRef.current.setOnFinalized(() => {
      fetchMeeting(mid)
      fetchMeetings()
    })
    return () => { transcriptionRef.current.setOnFinalized(null) }
  }, [recordingMeetingId, activeMeeting]) // eslint-disable-line react-hooks/exhaustive-deps

  // Tab close / refresh cannot restore MediaStream — warn while capture is live
  useEffect(() => {
    if (!recorder.isRecording && !recorder.isPaused) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ""
    }
    window.addEventListener("beforeunload", onBeforeUnload)
    return () => window.removeEventListener("beforeunload", onBeforeUnload)
  }, [recorder.isRecording, recorder.isPaused])

  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Keep languageHints in a ref so the recording effect always sees current value
  const languageHintsRef = useRef(languageHints)
  languageHintsRef.current = languageHints

  // Per-meeting setter: persists to map + updates state
  const updateLanguageHints = (hints: string[]) => {
    setLanguageHints(hints)
    if (activeMeeting) {
      perMeetingLanguageHints.current.set(activeMeeting, hints)
    }
  }

  // Start/stop realtime transcription when recording starts/stops,
  // and when realtimeEnabled is toggled during recording.
  // Do NOT depend on isTranscribing flips from WS reconnect — that used to
  // spawn parallel sessions and spam engine toasts.
  const startTranscriptionRef = useRef(transcription.startTranscription)
  const stopTranscriptionRef = useRef(transcription.stopTranscription)
  startTranscriptionRef.current = transcription.startTranscription
  stopTranscriptionRef.current = transcription.stopTranscription
  const isTranscribingRef = useRef(transcription.isTranscribing)
  isTranscribingRef.current = transcription.isTranscribing

  useEffect(() => {
    if (!hasRealtimeProvider) return

    const shouldTranscribe = recorder.isRecording && realtimeEnabled

    if (shouldTranscribe && !isTranscribingRef.current) {
      // Prefer explicit language for local Chinese streaming; "auto" is stripped server-side
      startTranscriptionRef.current(["zh"])
    } else if (!shouldTranscribe && isTranscribingRef.current) {
      stopTranscriptionRef.current()
    }
  }, [recorder.isRecording, hasRealtimeProvider, realtimeEnabled])

  // Fetch meetings list
  const fetchMeetings = useCallback(async () => {
    try {
      const list = await getMeetings()
      // Newest created first (API also sorts; keep client stable if order drifts)
      const sorted = [...list].sort((a, b) => {
        const ta = new Date(a.created_at || a.updated_at || 0).getTime()
        const tb = new Date(b.created_at || b.updated_at || 0).getTime()
        return tb - ta
      })
      setMeetings(sorted)
    } catch { /* ignore */ }
  }, [])

  // Load collections for ID -> name mapping
  useEffect(() => {
    fetchCollections()
  }, [fetchCollections])

  // Fetch single meeting detail
  const fetchMeeting = useCallback(async (id: string) => {
    fetchMeetingIdRef.current = id
    try {
      const m = await getMeeting(id)
      // Guard: if activeMeeting changed while fetching, discard stale result
      if (fetchMeetingIdRef.current !== id) return
      setMeeting(m)
      // If a background task is in progress, resume polling.
      // Update meeting on every poll tick so children (MeetingTabs) stay in sync.
      // Polling for busy processing is started by the active-view effect below
      // (avoids network churn while Meeting sidebar is hidden).
    } catch { /* ignore */ }
  }, [])

  // Fetch transcript (stale-safe — never clear previous segments mid-switch)
  const fetchTranscript = useCallback(async (id: string) => {
    fetchTranscriptIdRef.current = id
    try {
      const res = await getMeetingTranscript(id)
      if (fetchTranscriptIdRef.current !== id) return
      setTranscript(res.segments)
    } catch {
      if (fetchTranscriptIdRef.current !== id) return
      setTranscript([])
    }
  }, [])

  // Refresh transcription provider status when Meeting tab becomes active.
  // Views are keep-alive mounted, so a mount-only check would stay stale after
  // configuring providers in Settings and navigating back.
  useEffect(() => {
    if (!active) return
    getRealtimeTranscriptionProviders()
      .then((providers) => setHasRealtimeProvider(providers.some((p) => p.is_active)))
      .catch(() => setHasRealtimeProvider(false))
    getFileTranscriptionProviders()
      .then((providers) => setHasFileProvider(providers.some((p) => p.is_active)))
      .catch(() => setHasFileProvider(false))
  }, [active])

  // Load meetings and hot words on mount
  useEffect(() => {
    fetchMeetings()
    getHotWordsLibraries()
      .then(setHotWordsLibraries)
      .catch(() => setHotWordsLibraries([]))
  }, [fetchMeetings])

  // Load meeting detail when active changes — keep previous paint (no blank flash)
  useEffect(() => {
    if (activeMeeting) {
      // Refresh provider info in case active model was changed in Settings
      getActiveProviderInfo()
        .then((info) => {
          const hints = info.file.supported_language_hints
          setSupportedLanguageHints(hints)
          // Restore per-meeting language hints, or default filtered by supported codes
          const stored = perMeetingLanguageHints.current.get(activeMeeting)
          if (stored) {
            setLanguageHints(stored)
          } else {
            const supportedCodes = new Set(hints.map((h) => h.code))
            setLanguageHints(DEFAULT_LANGUAGE_HINTS.filter((c) => supportedCodes.has(c)))
          }
        })
        .catch(() => {})
      // Do NOT setMeeting(null) / setTranscript([]) — that blanked the stage and
      // thrashed player grid before the next payload arrived.
      fetchMeeting(activeMeeting)
      fetchTranscript(activeMeeting)
    } else {
      setMeeting(null)
      setTranscript([])
    }
  }, [activeMeeting, fetchMeeting, fetchTranscript])

  /**
   * Meeting switch soft-fade:
   * 1) fade out immediately (previous meeting still mounted)
   * 2) when meeting.id matches activeMeeting, fade in
   */
  useLayoutEffect(() => {
    if (meetingSoftSkipRef.current) {
      meetingSoftSkipRef.current = false
      return
    }
    if (!activeMeeting) {
      setMeetingSoftFaded(false)
      return
    }
    meetingSoftGenRef.current += 1
    setMeetingSoftFaded(true)
    setEditingTitle(false)
    setPlaybackTime(0)
  }, [activeMeeting])

  useEffect(() => {
    if (!meetingSoftFaded) return
    if (!activeMeeting) {
      setMeetingSoftFaded(false)
      return
    }
    const gen = meetingSoftGenRef.current
    // Wait until painted meeting matches selection, then fade in
    if (meeting?.id === activeMeeting) {
      const t = window.setTimeout(() => {
        if (meetingSoftGenRef.current !== gen) return
        setMeetingSoftFaded(false)
      }, 40)
      return () => window.clearTimeout(t)
    }
    // Fetch failed / stalled — don't leave the stage stuck transparent
    const failSafe = window.setTimeout(() => {
      if (meetingSoftGenRef.current !== gen) return
      setMeetingSoftFaded(false)
    }, 2500)
    return () => window.clearTimeout(failSafe)
  }, [meetingSoftFaded, meeting?.id, activeMeeting])

  // Poll while Meeting sidebar is open and work is in progress
  useEffect(() => {
    if (!active || !activeMeeting) {
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
        pollingRef.current = null
      }
      return
    }
    const transcribing = meeting?.status === "transcribing"
    const processingBusy =
      !!meeting?.processing_state && meeting.processing_state !== "idle"
    if (!transcribing && !processingBusy) {
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
        pollingRef.current = null
      }
      return
    }
    if (pollingRef.current) clearInterval(pollingRef.current)
    pollingRef.current = setInterval(() => {
      fetchMeeting(activeMeeting)
      if (transcribing) fetchTranscript(activeMeeting)
      // Keep rail status labels (Transcribing / Summarizing) in sync
      void fetchMeetings()
    }, 2000)
    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
        pollingRef.current = null
      }
    }
  }, [
    active,
    activeMeeting,
    meeting?.status,
    meeting?.processing_state,
    fetchMeeting,
    fetchTranscript,
    fetchMeetings,
  ])

  /**
   * List-wide poll: any meeting in the rail that is transcribing / summarizing
   * (not necessarily selected) still needs status updates on the list.
   */
  useEffect(() => {
    if (!active) return
    const listBusy = meetings.some(
      (m) =>
        m.status === "transcribing" ||
        m.status === "recording" ||
        (m.processing_state && m.processing_state !== "idle") ||
        recordingMeetingId === m.id,
    )
    if (!listBusy) return
    const id = window.setInterval(() => {
      void fetchMeetings()
    }, 2500)
    return () => window.clearInterval(id)
  }, [active, meetings, recordingMeetingId, fetchMeetings])

  /**
   * Studio unlock is sticky per meeting once the user passes the speaker gate
   * (or already has summary/blueprint work). File-complete alone does NOT unlock —
   * Capture shows Speakers first; "Summarize" unlocks Studio and starts Summary.
   * Re-transcribe clears this flag so Speakers returns.
   */
  const [studioUnlocked, setStudioUnlocked] = useState<Record<string, boolean>>({})
  /**
   * After Live stop we auto-start file transcription. Realtime save briefly sets
   * status=completed before upload+transcribe flips to "transcribing" — without
   * this lock, Capture flashes the speakers gate (前置页) in between.
   */
  const [postLiveFileTxMeetingId, setPostLiveFileTxMeetingId] = useState<string | null>(null)

  const lockBackToCapture = useCallback((meetingId: string) => {
    setStudioUnlocked((prev) => {
      if (!prev[meetingId]) return prev
      const next = { ...prev }
      delete next[meetingId]
      return next
    })
  }, [])

  // File / re-tx in progress → force Capture (Speakers after complete)
  useEffect(() => {
    if (!meeting?.id) return
    if (meeting.status === "transcribing") {
      lockBackToCapture(meeting.id)
    }
  }, [meeting?.id, meeting?.status, lockBackToCapture])

  useEffect(() => {
    if (!meeting || meeting.status !== "completed") return
    // Returning meetings that already have Studio work skip the speaker gate
    // (not after a fresh file-tx — backend clears tabs/blueprint on re-tx).
    const hasStudioWork =
      !!meeting.tabs?.some((t) => !!(t as { md_file_path?: string }).md_file_path) ||
      !!(meeting.blueprint && meeting.blueprint.length > 0)
    if (!hasStudioWork) return
    setStudioUnlocked((prev) =>
      prev[meeting.id] ? prev : { ...prev, [meeting.id]: true },
    )
  }, [meeting?.id, meeting?.status, meeting?.tabs, meeting?.blueprint])

  // File transcription finished: drop live draft + load file result once per transition.
  // Soft-switch keeps the previous `meeting` painted while `activeMeeting` already
  // changed — only interpret transitions when painted id matches selection, or we
  // stamp the old "transcribing" onto the new id and toast on a completed meeting.
  const prevMeetingStatusRef = useRef<{ id: string | null; status?: string }>({
    id: null,
    status: undefined,
  })
  const setSegments = transcription.setSegments
  useEffect(() => {
    const paintedId = meeting?.id ?? null
    const curr = meeting?.status
    // Soft-fade: ignore until detail payload matches the selected meeting
    if (!activeMeeting || !paintedId || paintedId !== activeMeeting || !curr) return

    const prev = prevMeetingStatusRef.current
    const sameMeeting = prev.id === paintedId
    const prevStatus = sameMeeting ? prev.status : undefined
    prevMeetingStatusRef.current = { id: paintedId, status: curr }

    // Same-meeting only: real transcribing → completed (not a switch onto completed)
    if (sameMeeting && prevStatus === "transcribing" && curr === "completed") {
      setPostLiveFileTxMeetingId((mid) => (mid === paintedId ? null : mid))
      setSegments([])
      void fetchTranscript(paintedId)
      toast.success("File transcription ready")
      return
    }
    // First land on completed for this meeting (prev different or meeting switch)
    if (curr === "completed" && (!sameMeeting || prevStatus !== "completed")) {
      // Don't clear live draft while we're bridging into auto file-tx after Live
      if (postLiveFileTxMeetingId === paintedId) return
      setSegments([])
      void fetchTranscript(paintedId)
    }
  }, [
    meeting?.id,
    meeting?.status,
    activeMeeting,
    fetchTranscript,
    setSegments,
    postLiveFileTxMeetingId,
  ])

  // Re-fetch transcript when processing_state goes idle (extract/regenerate complete)
  // so section_tags from sentences.json appear on transcript sentences.
  const prevProcessingRef = useRef(meeting?.processing_state)
  useEffect(() => {
    const prev = prevProcessingRef.current
    const curr = meeting?.processing_state
    prevProcessingRef.current = curr
    if (prev && prev !== "idle" && curr === "idle" && activeMeeting) {
      fetchTranscript(activeMeeting)
    }
  }, [meeting?.processing_state, activeMeeting, fetchTranscript])

  // When recording stops, upload audio then auto-trigger file transcription
  // Target is always the capture owner (not necessarily the meeting currently on screen).
  useEffect(() => {
    const ownerId = captureOwnerRef.current
    if (recorder.audioBlob && ownerId) {
      // Skip upload if user clicked Discard
      if (discardingRef.current) {
        discardingRef.current = false
        captureOwnerRef.current = null
        recorder.reset()
        return
      }
      const file = new File([recorder.audioBlob], "recording.webm", { type: recorder.audioBlob.type })
      const uploadTo = ownerId
      captureOwnerRef.current = null
      uploadMeetingAudio(uploadTo, file)
        .then((m) => {
          // Only replace detail panel if user is still viewing that meeting
          if (fetchMeetingIdRef.current === uploadTo || activeMeeting === uploadTo) {
            setMeeting(m)
            setAudioVersion((v) => v + 1)
          }
          toast.success("Audio uploaded")
          recorder.reset()
          fetchMeetings()

          // Auto-trigger file transcription if a cloud/file provider is configured.
          // Stay on Capture until file completes — do not enter Studio yet.
          if (hasFileProvider) {
            // Keep live draft visible on Capture while file runs; clear on completed.
            // postLiveFileTxMeetingId already set on stop — hold Transcribing UI.
            setPostLiveFileTxMeetingId(uploadTo)
            lockBackToCapture(uploadTo)
            transcribeMeeting(uploadTo, languageHintsRef.current)
              .then(() => {
                toast.info("File transcription started")
                fetchMeeting(uploadTo)
              })
              .catch((err) => {
                setPostLiveFileTxMeetingId((mid) => (mid === uploadTo ? null : mid))
                toast.error(
                  `Transcription failed: ${err instanceof Error ? err.message : String(err)}`,
                )
              })
          } else {
            // No file provider: live-saved transcript is final → speaker gate next
            setPostLiveFileTxMeetingId((mid) => (mid === uploadTo ? null : mid))
            void fetchTranscript(uploadTo)
            void fetchMeeting(uploadTo)
          }
        })
        .catch((err) => {
          setPostLiveFileTxMeetingId((mid) => (mid === uploadTo ? null : mid))
          toast.error(`Upload failed: ${err instanceof Error ? err.message : String(err)}`)
        })
    }
  }, [recorder.audioBlob])

  // Handlers
  const handleUploadAudio = async (file: File) => {
    if (!activeMeeting) return
    try {
      const m = await uploadMeetingAudio(activeMeeting, file)
      setMeeting(m)
      setAudioVersion((v) => v + 1)
      toast.success("Audio ready — review then Transcribe")
      fetchMeetings()
      // Do NOT auto-transcribe on upload; user clicks Transcribe on capture page
    } catch (err) {
      toast.error(`Upload failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  // Default Live captions on when a realtime provider is available (user can turn off before/during record)
  useEffect(() => {
    if (hasRealtimeProvider) setRealtimeEnabled(true)
  }, [hasRealtimeProvider])

  /** Open Live-caption chip immediately; close only after leave debounce so it stays clickable. */
  const openStartLiveChip = useCallback(() => {
    if (startLiveChipCloseTimerRef.current) {
      clearTimeout(startLiveChipCloseTimerRef.current)
      startLiveChipCloseTimerRef.current = null
    }
    setStartLiveChipOpen(true)
  }, [])
  const scheduleCloseStartLiveChip = useCallback(() => {
    if (startLiveChipCloseTimerRef.current) clearTimeout(startLiveChipCloseTimerRef.current)
    startLiveChipCloseTimerRef.current = setTimeout(() => {
      setStartLiveChipOpen(false)
      startLiveChipCloseTimerRef.current = null
    }, 280)
  }, [])
  useEffect(() => () => {
    if (startLiveChipCloseTimerRef.current) clearTimeout(startLiveChipCloseTimerRef.current)
  }, [])

  const handleStartRecording = async () => {
    const ownerId = activeMeeting
    // One capture stream at a time — never hijack another meeting’s recording
    if (
      (recorder.isRecording || recorder.isPaused) &&
      recordingMeetingId &&
      ownerId &&
      recordingMeetingId !== ownerId
    ) {
      toast.error("Already recording another meeting", {
        description: "Stop or discard that recording before starting a new one.",
      })
      return
    }
    if (recorder.isRecording || recorder.isPaused) {
      toast.message("Recording already in progress")
      return
    }
    // Permission / share dialog first — stay on setup if denied / cancelled
    const ok = await recorder.startRecording()
    if (!ok) {
      toast.error(
        "Microphone and system audio permission are required to record. Allow access (and Share audio) then try again.",
        { duration: 6500 },
      )
      return
    }
    // Bind stream to this meeting so other meetings do not show live waveform / pause chrome
    if (ownerId) {
      setRecordingMeetingId(ownerId)
      captureOwnerRef.current = ownerId
    }
    // Do not force realtime on — respect pre-start preference (hover chip / default)
  }

  const clearRecordingOwner = useCallback(() => {
    setRecordingMeetingId(null)
    // keep captureOwnerRef until upload effect consumes audioBlob
  }, [])

  const handleStopRecording = useCallback(() => {
    // Keep captureOwnerRef for upload target even if user is viewing another meeting
    const owner = captureOwnerRef.current ?? recordingMeetingId
    // Lock Capture on "Transcribing" UI before realtime save sets status=completed
    // (otherwise speakers gate flashes until file-tx starts).
    if (owner && hasFileProvider) {
      setPostLiveFileTxMeetingId(owner)
    }
    transcription.stopTranscription()
    recorder.stopRecording()
    setRealtimeEnabled(hasRealtimeProvider)
    clearRecordingOwner()
  }, [
    transcription,
    recorder,
    hasRealtimeProvider,
    clearRecordingOwner,
    recordingMeetingId,
    hasFileProvider,
  ])

  const handleTranscribe = async () => {
    if (!activeMeeting) return
    if (!hasFileProvider) {
      toast.error("No transcription provider configured. Go to Settings → Transcription to set one up.", {
        action: { label: "Settings", onClick: () => setSidebarView("llm_provider") },
      })
      return
    }
    // Clear realtime segments so new file transcript shows after completion
    transcription.setSegments([])
    // Re-tx must return to Speakers after complete (not stay in Studio)
    lockBackToCapture(activeMeeting)
    try {
      await transcribeMeeting(activeMeeting, languageHints)
      toast.info("Transcription started")
      fetchMeeting(activeMeeting)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      toast.error(`Transcription failed: ${msg}`)
    }
  }

  const handleCancelTranscribe = async () => {
    if (!activeMeeting) return
    try {
      await cancelTranscribeMeeting(activeMeeting)
      setPostLiveFileTxMeetingId((mid) => (mid === activeMeeting ? null : mid))
      fetchMeeting(activeMeeting)
      toast.info("Transcription cancelled")
    } catch (err) {
      toast.error(`Cancel failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleDiscard = async () => {
    const owner = captureOwnerRef.current ?? recordingMeetingId ?? activeMeeting
    if (!owner) return
    // Set flag BEFORE stopping recorder so the audioBlob effect skips upload
    discardingRef.current = true
    setPostLiveFileTxMeetingId((mid) => (mid === owner ? null : mid))
    // Stop realtime transcription first (discard: clears segments, closes WS, skips save)
    transcription.stopTranscription({ discard: true })
    // Then stop the recorder
    recorder.stopRecording()
    setRealtimeEnabled(hasRealtimeProvider)
    clearRecordingOwner()
    captureOwnerRef.current = null
    try {
      await discardMeetingRecording(owner)
      toast.success("Recording discarded")
      if (activeMeeting === owner) fetchMeeting(owner)
      fetchMeetings()
    } catch (err) {
      toast.error(`Discard failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleDelete = (id: string) => {
    setDeleteTarget(id)
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    try {
      await deleteMeeting(deleteTarget)
      if (activeMeeting === deleteTarget) setActiveMeeting(null)
      setDeleteTarget(null)
      fetchMeetings()
      toast.success("Meeting deleted")
    } catch {
      toast.error("Delete failed")
    }
  }

  /**
   * Transcript click: pass only start → continuous play.
   * Speaker sample click: pass start+end → stop at sentence end.
   * Studio MediaBar: same rules.
   */
  const handleSegmentClick = (startTime: number, endTime?: number) => {
    const end =
      endTime != null && Number.isFinite(endTime) && endTime > startTime
        ? endTime
        : undefined
    capturePlayerRef.current?.seekTo(startTime, end)
    mediaBarRef.current?.seekTo(startTime, end)
  }

  /** Transcript rows: continuous from this sentence */
  const handleTranscriptSegmentClick = (startTime: number) => {
    handleSegmentClick(startTime)
  }

  /** Speaker samples: play one sentence only */
  const handleSpeakerSampleClick = (startTime: number, endTime?: number) => {
    handleSegmentClick(startTime, endTime)
  }

  const focusTranscriptSegment = useCallback((sentenceId: string, fromChat: boolean) => {
    setActiveSectionTag("")
    // Prefer file transcript once completed (speaker gate + Studio)
    const allSegments =
      meeting?.status === "completed" || (meeting && studioUnlocked[meeting.id])
        ? transcript.length > 0
          ? transcript
          : transcription.segments
        : transcription.segments.length > 0
          ? transcription.segments
          : transcript
    let idx = allSegments.findIndex((seg: any) => seg.sentence_id === sentenceId)
    if (idx === -1) {
      const numMatch = sentenceId.match(/^stt_(\d+)$/)
      if (numMatch) idx = parseInt(numMatch[1], 10) - 1
    }
    if (idx >= 0 && idx < allSegments.length) {
      handleSegmentClick(allSegments[idx].start)
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          setFocusRef({ id: "_idx_" + idx, ts: Date.now(), fromChat })
        })
      })
    } else {
      setFocusRef({ id: sentenceId, ts: Date.now(), fromChat })
    }
  }, [transcription.segments, transcript, meeting, studioUnlocked])

  const handleRefClick = (sentenceId: string) => {
    // Chat open → overlay Transcript peek (main width unchanged, Chat stays)
    if (quickChatOpen) {
      if (!sideRailOpen) setSideRailOpenWithMotion(true)
      else setSideRailOpen(true)
      setTxPeekOpen(true)
      setTranscriptJumpCounter((c) => c + 1)
      focusTranscriptSegment(sentenceId, true)
      return
    }
    // Otherwise: switch side tab to Transcript
    requestSideTab("transcript")
    setTxPeekOpen(false)
    setTranscriptJumpCounter((c) => c + 1)
    focusTranscriptSegment(sentenceId, false)
  }


  const handleMeetingUpdate = useCallback((m: Meeting) => {
    setMeeting(m)
    if (activeMeeting) {
      fetchMeetings()
      // If meeting just became busy (Summarize/Extract/Regenerate triggered),
      // start polling so children (MeetingTabs) receive updates without manual refresh.
      const busy = m.processing_state && m.processing_state !== "idle"
      if (busy) fetchMeeting(activeMeeting)
    }
  }, [activeMeeting, fetchMeetings, fetchMeeting])

  const handleSelectMeeting = useCallback((id: string) => {
    setActiveMeeting(id)
  }, [setActiveMeeting])

  const handleStartEditTitle = () => {
    if (!meeting) return
    setTitleDraft(meeting.title)
    setEditingTitle(true)
  }

  const handleSaveTitle = async () => {
    if (!activeMeeting || !titleDraft.trim()) { setEditingTitle(false); return }
    try {
      const m = await updateMeeting(activeMeeting, { title: titleDraft.trim() })
      setMeeting(m)
      setEditingTitle(false)
      fetchMeetings()
    } catch (err) {
      toast.error(`Rename failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleSelectHotWordsLibrary = async (libraryId: string | null) => {
    if (!activeMeeting) return
    try {
      const m = await updateMeeting(activeMeeting, { hot_words_library_id: libraryId })
      setMeeting(m)
    } catch (err) {
      toast.error(`Failed to update hot words: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const metaCreated = meeting?.created_at
    ? new Date(meeting.created_at).toLocaleDateString("en-US", {
        month: "short", day: "numeric", year: "numeric",
        hour: "2-digit", minute: "2-digit",
      })
    : "—"

  const metaSpeakers = (() => {
    if (!meeting) return "—"
    const named = meeting.speaker_names ? Object.values(meeting.speaker_names).filter(Boolean) : []
    if (named.length > 0) return named.join(", ")
    const count = new Set(transcript.map((s) => s.speaker_id).filter(Boolean)).size
    return `${count || 0} speaker${count !== 1 ? "s" : ""}`
  })()

  /**
   * Capture vs Studio:
   * setup → (upload) audio-ready → transcribe → speakers → Summarize
   * setup → live → auto file-tx (same UI as transcribe) → speakers → Summarize
   * Studio only after Summarize (or existing summary work).
   */
  const hasStudioAnchor = !!(meeting && studioUnlocked[meeting.id])
  const isCapturePhase = !!meeting && !hasStudioAnchor
  /** Recording chrome only on the meeting that started the stream */
  const isRecordingThisMeeting = !!(
    recordingMeetingId &&
    activeMeeting === recordingMeetingId &&
    (recorder.isRecording || recorder.isPaused)
  )
  const isLiveStage = !!(isCapturePhase && isRecordingThisMeeting)
  /** Bridging Live → auto file-tx (or status already "transcribing") */
  const postLiveFileTxPending = !!(
    meeting &&
    postLiveFileTxMeetingId === meeting.id
  )
  const isFileTranscribing = !!(
    isCapturePhase &&
    !isLiveStage &&
    (meeting?.status === "transcribing" || postLiveFileTxPending)
  )
  /** File done — left transcript + right speakers + Summarize */
  const isCaptureSpeakers = !!(
    isCapturePhase &&
    !isLiveStage &&
    !postLiveFileTxPending &&
    meeting?.status === "completed"
  )
  /** Has audio, not yet / not currently transcribing — mini player + Transcribe */
  const isCaptureAudioReady = !!(
    isCapturePhase &&
    !isLiveStage &&
    !isFileTranscribing &&
    !isCaptureSpeakers &&
    !!meeting?.audio_path
  )
  const isCaptureSetup = !!(
    isCapturePhase &&
    !isLiveStage &&
    !isFileTranscribing &&
    !isCaptureAudioReady &&
    !isCaptureSpeakers
  )

  /**
   * Stage mode for capture ↔ speakers ↔ studio (and live / audio-ready).
   * Paint lags target via sequential fade so layout doesn't hard-cut.
   */
  type StageMode = "setup" | "audio" | "speakers" | "live" | "studio" | "empty"
  const targetStageMode: StageMode = !meeting
    ? "empty"
    : isCaptureSetup
      ? "setup"
      : isCaptureAudioReady || isFileTranscribing
        ? "audio"
        : isCaptureSpeakers
          ? "speakers"
          : isLiveStage
            ? "live"
            : "studio"
  const [displayStageMode, setDisplayStageMode] = useState<StageMode>(targetStageMode)
  const [stageModePhase, setStageModePhase] = useState<"shown" | "hiding">("shown")
  const stageModeGenRef = useRef(0)
  const STAGE_MODE_OUT_MS = 160

  useEffect(() => {
    if (targetStageMode === displayStageMode) {
      if (stageModePhase === "hiding") setStageModePhase("shown")
      return
    }
    // Under meeting-level soft-fade the stage is already opacity 0 — swap instantly
    if (meetingSoftFaded) {
      setDisplayStageMode(targetStageMode)
      setStageModePhase("shown")
      return
    }
    const gen = ++stageModeGenRef.current
    setStageModePhase("hiding")
    const t = window.setTimeout(() => {
      if (stageModeGenRef.current !== gen) return
      setDisplayStageMode(targetStageMode)
      // Double rAF: paint new mode at opacity 0, then fade in
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          if (stageModeGenRef.current !== gen) return
          setStageModePhase("shown")
        })
      })
    }, STAGE_MODE_OUT_MS)
    return () => window.clearTimeout(t)
  }, [targetStageMode, displayStageMode, meetingSoftFaded, stageModePhase])

  const captureAudioUrl =
    meeting?.audio_path
      ? `/api/meetings/${meeting.id}/audio?v=${audioVersion}`
      : null

  /** Prefer file/API transcript once completed; live draft during Capture live/wait */
  const displaySegments =
    meeting?.status === "completed" || hasStudioAnchor
      ? transcript.length > 0
        ? transcript
        : transcription.segments
      : transcription.segments.length > 0
        ? transcription.segments
        : transcript
  displaySegmentsRef.current = displaySegments

  const speakerIdsInTranscript = (() => {
    const ids = new Set<string>()
    for (const seg of displaySegments) {
      if (seg.speaker_id) ids.add(seg.speaker_id)
    }
    return [...ids].sort((a, b) => Number(a) - Number(b))
  })()
  const namedSpeakerCount = speakerIdsInTranscript.filter((id) =>
    !!(meeting?.speaker_names?.[id]?.trim()),
  ).length
  const speakersNeedNames =
    speakerIdsInTranscript.length > 0 &&
    namedSpeakerCount < speakerIdsInTranscript.length

  const handleEnterStudio = useCallback(() => {
    if (!meeting) return
    // Speakers → Summarize: unlock Studio and start summary stream (no auto-start
    // on mere transcript ready — that skipped Speakers and jumped into summarizing).
    setStudioUnlocked((prev) => ({ ...prev, [meeting.id]: true }))
    const hasExistingSummary =
      !!meeting.tabs?.some((t) => !!(t as { md_file_path?: string }).md_file_path) ||
      !!(meeting.blueprint && meeting.blueprint.length > 0)
    if (!hasExistingSummary) {
      startBlueprintStream(meeting.id)
    }
  }, [meeting])

  const handleUpdateSpeakerName = useCallback(
    (speakerId: string, name: string) => {
      if (!meeting) return
      const updated = { ...(meeting.speaker_names ?? {}), [speakerId]: name }
      updateMeeting(meeting.id, { speaker_names: updated })
        .then((m) => {
          setMeeting(m)
          void import("@/components/ui/tiptap-editor").then((mod) => {
            mod.invalidateMeetingSpeakerCache(meeting.id)
          })
        })
        .catch(() => {
          toast.error("Failed to save speaker name")
        })
    },
    [meeting],
  )

  const emptyUploadRef = useRef<HTMLInputElement>(null)
  const [liveNotes, setLiveNotes] = useState(meeting?.notes_content ?? "")
  const liveNotesTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** Draft notes while recording survive switching away and back */
  const liveNotesDraftsRef = useRef<Map<string, string>>(new Map())
  useEffect(() => {
    if (!meeting?.id) return
    const draft = liveNotesDraftsRef.current.get(meeting.id)
    setLiveNotes(draft !== undefined ? draft : (meeting.notes_content ?? ""))
  }, [meeting?.id, meeting?.notes_content])
  const handleLiveNotesChange = (value: string) => {
    setLiveNotes(value)
    if (!activeMeeting) return
    liveNotesDraftsRef.current.set(activeMeeting, value)
    if (liveNotesTimer.current) clearTimeout(liveNotesTimer.current)
    liveNotesTimer.current = setTimeout(() => {
      updateMeeting(activeMeeting, { notes: value })
        .then((m) => setMeeting(m))
        .catch(() => {})
    }, 800)
  }
  const formatRecTime = (seconds: number) => {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`
  }

  return (
    <div className="pm-meeting">
      <div className="pm-meeting-body">
        <MeetingList
          meetings={meetings}
          activeMeeting={activeMeeting}
          recordingMeetingId={recordingMeetingId}
          keepFocusOnCreate={!!(recorder.isRecording || recorder.isPaused)}
          onSelect={handleSelectMeeting}
          onCreated={(id: string, opts?: { stayOnCurrent?: boolean }) => {
            void fetchMeetings()
            // While capturing, never switch the stage onto the new meeting —
            // that looked like the live session was “overwritten”.
            if (opts?.stayOnCurrent || recorder.isRecording || recorder.isPaused) {
              return
            }
            setActiveMeeting(id)
          }}
          onDelete={handleDelete}
        />

        <div className="pm-meeting-stage">
          {/*
            Steady stage:
              Left: title chrome + Summary/Notes content card
              Right: player card + side pills (Sections | Transcript | Speaker) / QC
            No key=activeMeeting — remount caused blank flash + player layout thrash.
          */}
          <div
            className={cn(
              "pm-meeting-stage-surface",
              "pm-meeting-soft-fade",
              meetingSoftFaded && "is-faded",
              stageModePhase === "hiding" && "is-mode-exiting",
              sideRailMotion && "is-side-motion",
              /* Grid shell follows painted mode (not target) so layout eases with fade */
              !sideRailOpen && displayStageMode === "studio" && "is-side-collapsed",
              (displayStageMode === "setup" || displayStageMode === "audio") && "is-mode-empty",
              (displayStageMode === "speakers" || displayStageMode === "live") && "is-mode-live",
              (displayStageMode === "setup" ||
                displayStageMode === "audio" ||
                displayStageMode === "speakers" ||
                displayStageMode === "live") && "is-mode-capture",
            )}
          >
            {meeting && displayStageMode === "setup" ? (
              /* ═══ Capture · Setup — config before record/upload ═══ */
              <div className="pm-meeting-mode-empty" data-meeting-mode="empty">
                <div className="pm-meeting-e-stage">
                  <p className="pm-meeting-e-kicker">New meeting</p>
                  <h3 className="pm-meeting-e-title">Capture the conversation</h3>
                  <p className="pm-meeting-e-sub">
                    Choose hot words and language, then record live or upload audio.
                  </p>

                  <div className="pm-meeting-e-config" aria-label="Transcription settings">
                    <HotWordsSelector
                      meetingId={meeting.id}
                      currentLibraryId={meeting.hot_words_library_id}
                      hasTranscript={false}
                      providerSupportsHotWords
                      onSelectLibrary={handleSelectHotWordsLibrary}
                      onRetranscribe={() => {}}
                    />
                    {supportedLanguageHints.length > 0 && (
                      <LanguageHintsSelector
                        selected={languageHints}
                        onChange={updateLanguageHints}
                        options={supportedLanguageHints}
                      />
                    )}
                  </div>

                  <div className="pm-meeting-e-actions">
                    <input
                      ref={emptyUploadRef}
                      type="file"
                      accept="audio/*"
                      className="hidden"
                      onChange={(e) => {
                        const file = e.target.files?.[0]
                        if (file) void handleUploadAudio(file)
                        e.target.value = ""
                      }}
                    />
                    <div
                      className={cn(
                        "pm-meeting-e-start-group",
                        startLiveChipOpen && hasRealtimeProvider && "is-open",
                      )}
                      onMouseEnter={hasRealtimeProvider ? openStartLiveChip : undefined}
                      onMouseLeave={hasRealtimeProvider ? scheduleCloseStartLiveChip : undefined}
                      onFocusCapture={hasRealtimeProvider ? openStartLiveChip : undefined}
                      onBlurCapture={(e) => {
                        if (!hasRealtimeProvider) return
                        const next = e.relatedTarget as Node | null
                        if (next && e.currentTarget.contains(next)) return
                        scheduleCloseStartLiveChip()
                      }}
                    >
                      <button
                        type="button"
                        className="pm-meeting-e-cta is-primary"
                        onClick={() => void handleStartRecording()}
                      >
                        <Mic className="size-3.5" />
                        Start recording
                      </button>
                      {hasRealtimeProvider && (
                        <button
                          type="button"
                          className={cn(
                            "pm-meeting-e-realtime-chip",
                            realtimeEnabled ? "is-on" : "is-off",
                          )}
                          tabIndex={startLiveChipOpen ? 0 : -1}
                          onClick={(e) => {
                            e.preventDefault()
                            e.stopPropagation()
                            setRealtimeEnabled((v) => !v)
                            openStartLiveChip()
                          }}
                          title={
                            realtimeEnabled
                              ? "Live caption on — click to turn off"
                              : "Live caption off — click to turn on"
                          }
                        >
                          <span
                            className={cn(
                              "pm-meeting-e-realtime-dot",
                              realtimeEnabled && "is-on",
                            )}
                          />
                          Live caption · {realtimeEnabled ? "On" : "Off"}
                        </button>
                      )}
                    </div>
                    <button
                      type="button"
                      className="pm-meeting-e-cta is-secondary"
                      onClick={() => emptyUploadRef.current?.click()}
                    >
                      <Upload className="size-3.5" />
                      Upload audio
                    </button>
                  </div>
                  {recorder.error && (
                    <p className="pm-meeting-e-error">{recorder.error}</p>
                  )}
                </div>
              </div>
            ) : meeting && displayStageMode === "audio" ? (
              /* ═══ Capture · Audio ready / Transcribing (same shell for upload + post-live file tx) ═══ */
              <div className="pm-meeting-mode-empty" data-meeting-mode={isFileTranscribing ? "transcribing" : "audio-ready"}>
                <div className="pm-meeting-e-stage pm-meeting-e-stage--wide">
                  <p className="pm-meeting-e-kicker">
                    {isFileTranscribing ? "Transcribing" : "Audio ready"}
                  </p>
                  <h3 className="pm-meeting-e-title">
                    {isFileTranscribing ? "File transcription in progress" : "Review audio"}
                  </h3>
                  <p className="pm-meeting-e-sub">
                    {isFileTranscribing
                      ? "Stay on this page — next you can name speakers and start Summary."
                      : "Play back the recording, then start transcription when ready."}
                  </p>

                  <div className="pm-meeting-e-config" aria-label="Transcription settings">
                    <HotWordsSelector
                      meetingId={meeting.id}
                      currentLibraryId={meeting.hot_words_library_id}
                      hasTranscript={false}
                      providerSupportsHotWords
                      onSelectLibrary={handleSelectHotWordsLibrary}
                      onRetranscribe={() => {}}
                    />
                    {supportedLanguageHints.length > 0 && (
                      <LanguageHintsSelector
                        selected={languageHints}
                        onChange={updateLanguageHints}
                        options={supportedLanguageHints}
                      />
                    )}
                  </div>

                  {captureAudioUrl && (
                    <CaptureMiniPlayer
                      audioUrl={captureAudioUrl}
                      audioVersion={audioVersion}
                    />
                  )}

                  <div className="pm-meeting-e-actions">
                    {isFileTranscribing ? (
                      <>
                        <button
                          type="button"
                          className="pm-meeting-e-cta is-primary"
                          disabled
                        >
                          <Loader2 className="size-3.5 animate-spin" />
                          Transcribing…
                        </button>
                        <button
                          type="button"
                          className="pm-meeting-e-cta is-secondary"
                          onClick={() => void handleCancelTranscribe()}
                        >
                          <Square className="size-3.5" />
                          Cancel
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          type="button"
                          className="pm-meeting-e-cta is-primary"
                          onClick={() => void handleTranscribe()}
                          disabled={!hasFileProvider}
                        >
                          <Play className="size-3.5" />
                          Transcribe
                        </button>
                        <button
                          type="button"
                          className="pm-meeting-e-cta is-secondary"
                          onClick={() => emptyUploadRef.current?.click()}
                        >
                          <Upload className="size-3.5" />
                          Replace audio
                        </button>
                        <input
                          ref={emptyUploadRef}
                          type="file"
                          accept="audio/*"
                          className="hidden"
                          onChange={(e) => {
                            const file = e.target.files?.[0]
                            if (file) void handleUploadAudio(file)
                            e.target.value = ""
                          }}
                        />
                      </>
                    )}
                  </div>
                  {!hasFileProvider && !isFileTranscribing && (
                    <p className="pm-meeting-e-error">
                      No file transcription provider configured. Go to Settings → Transcription.
                    </p>
                  )}
                </div>
              </div>
            ) : meeting && displayStageMode === "speakers" ? (
              /* ═══ Capture · Review: transcript + speakers, then Summarize → Studio ═══ */
              <div className="pm-meeting-mode-live" data-meeting-mode="speakers">
                <div className="pm-meeting-speaker-gate-hint" role="status">
                  <Users className="size-3.5 shrink-0" />
                  <p>
                    {speakersNeedNames ? (
                      <>
                        <strong>Review transcript</strong>
                        {" · "}
                        {namedSpeakerCount}/{speakerIdsInTranscript.length || 0} speakers named.
                        {" "}
                        Name speakers on the right so Summary uses real names
                        (e.g. &ldquo;Alex&rdquo; instead of &ldquo;Speaker 0&rdquo;).
                      </>
                    ) : speakerIdsInTranscript.length === 0 ? (
                      <>
                        <strong>Review transcript</strong>
                        {" · "}
                        No speaker labels — you can continue to Summary.
                      </>
                    ) : (
                      <>
                        <strong>Review transcript</strong>
                        {" · "}
                        Speakers named · ready for Summary.
                      </>
                    )}
                  </p>
                </div>

                <div className="pm-meeting-f-grid">
                  <div className="pm-meeting-f-card">
                    <div className="pm-meeting-f-card-h">
                      <span className="pm-meeting-f-card-label">Transcript</span>
                      <span className="pm-meeting-f-card-meta">
                        {displaySegments.length} segments
                      </span>
                    </div>
                    <div className="pm-meeting-f-card-body">
                      <TranscriptTab
                        segments={displaySegments}
                        onSegmentClick={handleTranscriptSegmentClick}
                        speakerNames={meeting.speaker_names ?? {}}
                        showSearch={false}
                        playbackTime={playbackTime}
                      />
                    </div>
                  </div>
                  <div className="pm-meeting-f-card">
                    <div className="pm-meeting-f-card-h">
                      <span className="pm-meeting-f-card-label">Speakers</span>
                      <span className="pm-meeting-f-card-meta">configure</span>
                    </div>
                    <div className="pm-meeting-f-card-body">
                      <SpeakersTab
                        segments={displaySegments}
                        speakerNames={meeting.speaker_names ?? {}}
                        onUpdateSpeakerName={handleUpdateSpeakerName}
                        onSegmentClick={handleSpeakerSampleClick}
                      />
                    </div>
                  </div>
                </div>

                {captureAudioUrl ? (
                  <CaptureMiniPlayer
                    ref={capturePlayerRef}
                    variant="review"
                    audioUrl={captureAudioUrl}
                    audioVersion={audioVersion}
                    onTimeUpdate={setPlaybackTime}
                    footerSlot={
                      <button
                        type="button"
                        className="pm-meeting-e-cta is-primary"
                        onClick={handleEnterStudio}
                      >
                        <Sparkles className="size-3.5" />
                        Summarize
                      </button>
                    }
                  />
                ) : (
                  <div className="pm-meeting-f-controls pm-meeting-speaker-gate-actions">
                    <button
                      type="button"
                      className="pm-meeting-e-cta is-primary"
                      onClick={handleEnterStudio}
                    >
                      <Sparkles className="size-3.5" />
                      Summarize
                    </button>
                  </div>
                )}
              </div>
            ) : meeting && displayStageMode === "live" ? (
              /* ═══ F · Live record + Notes (independent of Steady) ═══ */
              <div className="pm-meeting-mode-live" data-meeting-mode="live">
                <LiveCaptureControlCard
                  levels={recorder.levels ?? []}
                  durationLabel={formatRecTime(recorder.duration)}
                  isPaused={!!recorder.isPaused}
                  hasRealtimeProvider={hasRealtimeProvider}
                  realtimeEnabled={realtimeEnabled}
                  onToggleRealtime={() => setRealtimeEnabled((v) => !v)}
                  onPause={recorder.pauseRecording}
                  onResume={recorder.resumeRecording}
                  onStop={handleStopRecording}
                  onDiscard={() => void handleDiscard()}
                />
                <div className="pm-meeting-f-grid">
                  <div className="pm-meeting-f-card">
                    <div className="pm-meeting-f-card-h">
                      <span className="pm-meeting-f-card-label">Live transcript</span>
                      <span className="pm-meeting-f-card-meta">
                        {realtimeEnabled && hasRealtimeProvider ? "live captions" : "auto-scroll"}
                      </span>
                    </div>
                    <div className="pm-meeting-f-card-body">
                      <TranscriptTab
                        segments={transcription.segments}
                        partialText={transcription.currentPartial}
                        onSegmentClick={handleSegmentClick}
                        speakerNames={meeting.speaker_names ?? {}}
                        showSearch={false}
                        followLive
                      />
                    </div>
                  </div>
                  <div className="pm-meeting-f-card">
                    <div className="pm-meeting-f-card-h">
                      <span className="pm-meeting-f-card-label">Notes</span>
                      <span className="pm-meeting-f-card-meta">saved live</span>
                    </div>
                    <div className="pm-meeting-f-card-body pm-meeting-f-notes">
                      <MarkdownEditor
                        value={liveNotes}
                        onChange={handleLiveNotesChange}
                        minHeight="200px"
                        placeholder="Write notes while recording…"
                      />
                    </div>
                  </div>
                </div>
              </div>
            ) : meeting && displayStageMode === "studio" ? (
              <div className="pm-meeting-mode-studio" data-meeting-mode="studio">
                {/* ── Steady · R1C1: title + meta ── */}
                <div
                  ref={mainAreaRef}
                  className="pm-meeting-left-chrome"
                  onPointerDownCapture={() => {
                    if (txPeekOpen) setTxPeekOpen(false)
                  }}
                >
                  <div data-meeting-title className="pm-meeting-title-card">
                    <div className="pm-meeting-head">
                      {editingTitle ? (
                        <div className="flex items-center gap-1 flex-1 min-w-0">
                          <input
                            className="pm-meeting-title-input"
                            value={titleDraft}
                            onChange={(e) => setTitleDraft(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") handleSaveTitle()
                              if (e.key === "Escape") setEditingTitle(false)
                            }}
                            autoFocus
                          />
                          <Button variant="ghost" size="icon-sm" className="shrink-0" onClick={handleSaveTitle} aria-label="Save title">
                            <Check className="size-3.5" />
                          </Button>
                          <Button variant="ghost" size="icon-sm" className="shrink-0" onClick={() => setEditingTitle(false)} aria-label="Cancel edit">
                            <X className="size-3.5" />
                          </Button>
                        </div>
                      ) : (
                        <div className="flex items-start gap-1 min-w-0 flex-1">
                          <h2 className="pm-meeting-title">{meeting.title}</h2>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            className="shrink-0 opacity-50 hover:opacity-100 mt-0.5"
                            onClick={handleStartEditTitle}
                            aria-label="Edit title"
                          >
                            <Pencil className="size-3.5" />
                          </Button>
                        </div>
                      )}

                      <div className="pm-meeting-meta-stack">
                        <div className="pm-meeting-meta-row">
                          <span className="pm-meeting-meta-key">Created</span>
                          <span className="pm-meeting-meta-val">{metaCreated}</span>
                        </div>
                        <div className="pm-meeting-meta-row">
                          <span className="pm-meeting-meta-key">Speakers</span>
                          <span className="pm-meeting-meta-val">{metaSpeakers}</span>
                        </div>
                        <div className="pm-meeting-meta-row">
                          <span className="pm-meeting-meta-key">Collections</span>
                          <span className="pm-meeting-meta-val">
                            {(() => {
                              const cols = meeting.allocated_collections
                              if (!cols || cols.length === 0) return <span>—</span>
                              return [...new Set(cols)].map((id, i, arr) => (
                                <span key={id}>
                                  <button
                                    type="button"
                                    onClick={() => {
                                      setActiveCollection(id)
                                      setSidebarView("database")
                                      setTimeout(() => window.dispatchEvent(new CustomEvent("show-meeting-log")), 100)
                                    }}
                                  >
                                    {collections.find((x: any) => x.id === id)?.name || id}
                                  </button>
                                  {i < arr.length - 1 ? ", " : ""}
                                </span>
                              ))
                            })()}
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                {/* ── R2C1: fixed-height content card (tabs + tools inside; body scrolls) ── */}
                <div
                  className="pm-meeting-left-body"
                  onPointerDownCapture={() => {
                    if (txPeekOpen) setTxPeekOpen(false)
                  }}
                >
                  {!hasFileProvider && meeting.audio_path && (
                    <div className="pm-meeting-warn mx-3 mt-2 mb-0">
                      <AlertCircle className="size-3.5 shrink-0" />
                      <span className="flex-1">No transcription provider configured.</span>
                      <Button variant="ghost" size="sm" onClick={() => setSidebarView("llm_provider")}>
                        <Settings className="size-3 mr-1" /> Settings
                      </Button>
                    </div>
                  )}

                  <div ref={meetingContentRef} className="pm-meeting-content-card">
                    <MeetingTabs
                      meetingId={meeting.id}
                      meeting={meeting}
                      notesContent={meeting.notes_content ?? ""}
                      onMeetingUpdate={handleMeetingUpdate}
                      onSeekTo={handleSegmentClick}
                      onFocusSentence={(id) => {
                        // Summary sentence-ref → single side Transcript (no floating panel)
                        requestSideTab("transcript")
                        setQuickChatOpen(false)
                        setActiveSectionTag("")
                        const allSegments =
                          displaySegments
                        let idx = allSegments.findIndex((seg: any) => seg.sentence_id === id)
                        if (idx === -1) {
                          const numMatch = id.match(/^stt_(\d+)$/)
                          if (numMatch) idx = parseInt(numMatch[1], 10) - 1
                        }
                        if (idx >= 0 && idx < allSegments.length) {
                          handleSegmentClick(allSegments[idx].start)
                          requestAnimationFrame(() => {
                            requestAnimationFrame(() => {
                              setFocusRef({ id: "_idx_" + idx, ts: Date.now() })
                            })
                          })
                        } else {
                          setFocusRef({ id, ts: Date.now() })
                        }
                      }}
                      onActiveTabChange={setActiveSectionTag}
                      transcriptSegments={displaySegments}
                      partialText={transcription.currentPartial}
                      focusRef={focusRef}
                      activeSectionTag={activeSectionTag}
                      floatingPanelOpen={false}
                      forceTranscriptTab={transcriptJumpCounter}
                      tabBarOffset={0}
                      canShift={false}
                      playbackTime={playbackTime}
                      selectedSummaryId={selectedSummaryId}
                      onSelectedSummaryIdChange={setSelectedSummaryId}
                      onSectionRailModelChange={setSectionRailModel}
                      onBindSectionRailActions={bindSectionRailActions}
                      onMainTabChange={setMainTab}
                      onRequestSideTab={requestSideTab}
                      hostTranscriptInParent
                      className="pm-meeting-tabs-shell"
                    />
                  </div>
                </div>

                {/* ── R1C2: player always stays (does not collapse with side) ── */}
                <div className="pm-meeting-player-zone">
                  <div className="pm-meeting-player-zone-card">
                    <MediaBar
                      ref={mediaBarRef}
                      meetingId={meeting.id}
                      status={meeting.status}
                      hasAudio={!!meeting.audio_path}
                      audioPath={meeting.audio_path}
                      audioUrl={meeting.audio_path ? `/api/meetings/${meeting.id}/audio?v=${audioVersion}` : null}
                      audioVersion={audioVersion}
                      duration={
                        recordingMeetingId === meeting.id ? recorder.duration : 0
                      }
                      levels={
                        recordingMeetingId === meeting.id
                          ? recorder.levels
                          : null
                      }
                      isRecording={
                        !!recorder.isRecording &&
                        recordingMeetingId === meeting.id
                      }
                      isPaused={
                        !!recorder.isPaused && recordingMeetingId === meeting.id
                      }
                      transcriptionError={meeting.transcription_error}
                      onUploadAudio={handleUploadAudio}
                      onStartRecord={() => void handleStartRecording()}
                      onStopRecord={handleStopRecording}
                      onPauseRecord={recorder.pauseRecording}
                      onResumeRecord={recorder.resumeRecording}
                      onTranscribe={handleTranscribe}
                      onReTranscribe={(displaySegments.length > 0 || meeting.transcript_path) ? () => {
                        setRetranscribeConfirmOpen(true)
                      } : undefined}
                      onCancelTranscribe={meeting.status === "transcribing" ? handleCancelTranscribe : undefined}
                      hasRealtimeProvider={hasRealtimeProvider}
                      realtimeEnabled={realtimeEnabled}
                      onToggleRealtime={() => setRealtimeEnabled((v) => !v)}
                      hasTranscript={displaySegments.length > 0}
                      hotWordsLibraryId={meeting.hot_words_library_id}
                      hotWordsLibraries={hotWordsLibraries}
                      onSelectHotWords={handleSelectHotWordsLibrary}
                      languageHints={languageHints}
                      languageHintOptions={supportedLanguageHints}
                      onChangeLanguageHints={updateLanguageHints}
                      showLanguageSelector={!!meeting.audio_path}
                      onTimeUpdate={setPlaybackTime}
                      recorderError={recorder.error}
                      onDiscard={handleDiscard}
                    />
                  </div>
                </div>

                {/* ── R2C2: analysis side only (collapses; main body expands right) ── */}
                <aside
                  className={cn(
                    "pm-meeting-stage-right",
                    !sideRailOpen && "is-collapsed",
                  )}
                  aria-label="Meeting side panel"
                  aria-hidden={!sideRailOpen}
                >
                    <div className="pm-meeting-stage-right-card">
                      {/* Head: sliding pill tabs / Chat label + collapse. Diamond parks absolutely. */}
                      <div
                        className={cn(
                          "pm-meeting-side-head",
                          sideSurfaceDisplay === "chat" && "is-chat-mode",
                        )}
                      >
                        <div
                          className={cn(
                            "pm-meeting-side-head-main",
                            sideSurfaceExiting && "is-exiting",
                            !sideSurfaceExiting && "is-entering",
                          )}
                          key={sideSurfaceDisplay === "chat" ? "chat-head" : "tabs-head"}
                        >
                          {sideSurfaceDisplay === "chat" ? (
                            <div className="pm-meeting-side-chat-label">
                              <span className="pm-meeting-side-chat-title">Chat</span>
                              <span className="pm-meeting-side-chat-sub truncate" title={meeting.title ?? ""}>
                                {meeting.title || "Meeting"}
                              </span>
                            </div>
                          ) : (
                            <Tabs
                              value={sideTab}
                              onValueChange={(v) => {
                                if (v === "sections" || v === "transcript" || v === "speaker") {
                                  setSideTab(v)
                                }
                              }}
                              className="pm-meeting-side-tabs gap-0 min-w-0 flex-1"
                            >
                              <TabsList className="relative" aria-label="Side panel">
                                <TabsIndicator className="pm-tabs-indicator" renderBeforeHydration />
                                <TabsTrigger value="sections" disabled={sideSurfaceExiting}>
                                  Sections
                                </TabsTrigger>
                                <TabsTrigger value="transcript" disabled={sideSurfaceExiting}>
                                  Transcript
                                </TabsTrigger>
                                <TabsTrigger value="speaker" disabled={sideSurfaceExiting}>
                                  Speaker
                                </TabsTrigger>
                              </TabsList>
                            </Tabs>
                          )}
                        </div>
                        {/* Spacer so diamond parks over this gap when top; collapse only in tools mode */}
                        <span className="pm-meeting-side-fab-slot" aria-hidden />
                        {sideSurfaceDisplay !== "chat" && (
                          <button
                            type="button"
                            className="pm-meeting-side-collapse"
                            title="Collapse side panel"
                            aria-label="Collapse side panel"
                            onClick={() => setSideRailOpenWithMotion(false)}
                          >
                            <PanelRightClose className="size-3.5" />
                          </button>
                        )}
                      </div>

                      <div
                        className={cn(
                          "pm-meeting-side-slot",
                          sideSurfaceExiting && "is-exiting",
                          !sideSurfaceExiting && "is-entering",
                        )}
                        key={sideSurfaceDisplay}
                      >
                      {sideSurfaceDisplay === "chat" ? (
                        <MeetingQuickChat
                          meetingId={meeting.id}
                          meetingTitle={meeting.title ?? ""}
                          open
                          onOpen={() => {
                            if (!sideRailOpen) setSideRailOpenWithMotion(true)
                            else setSideRailOpen(true)
                            setQuickChatOpen(true)
                          }}
                          onClose={() => {
                            /* Back to tools tabs — keep side rail open */
                            setQuickChatOpen(false)
                            setTxPeekOpen(false)
                          }}
                          onRefClick={handleRefClick}
                          layout="dock"
                        />
                      ) : (
                        <>
                          <div className="pm-meeting-side-body">
                            {/* Keep panels mounted; CSS crossfade on tab change (no remount / hard cut) */}
                            <div
                              className={cn(
                                "pm-meeting-side-panel",
                                sideTab === "sections" && "is-active",
                              )}
                              aria-hidden={sideTab !== "sections"}
                            >
                              <div
                                ref={sectionRailCardRef}
                                className={cn(
                                  "pm-meeting-section-rail-card",
                                  sectionRailModel?.thinking && "sk-thinking-flow",
                                )}
                              >
                                <div className="pm-meeting-section-rail-head">
                                  <span className="pm-meeting-section-rail-title">
                                    {sectionRailModel?.thinking ? "Building…" : "Browse"}
                                  </span>
                                  <button
                                    type="button"
                                    className="pm-meeting-section-rail-add"
                                    disabled={!!sectionRailModel?.busy || !!sectionRailModel?.thinking}
                                    title="Add section"
                                    aria-label="Add section"
                                    onClick={() => sectionRailActionsRef.current?.openAddSection()}
                                  >
                                    <Plus className="size-3.5" />
                                    <span>Add</span>
                                  </button>
                                </div>
                                <div
                                  className={cn(
                                    "pm-meeting-section-rail-list",
                                    sectionRailModel?.thinking && "is-building",
                                  )}
                                  onScroll={hideSectionTip}
                                >
                                  {/* Sliding focus pill — moves between General / sections */}
                                  {activeSectionRailId && !sectionRailModel?.thinking ? (
                                    <div
                                      className={cn(
                                        "pm-meeting-section-focus",
                                        sectionFocusReady && "is-ready",
                                      )}
                                      style={{
                                        transform: `translateY(${sectionFocus.top}px)`,
                                        height: sectionFocus.height,
                                      }}
                                      aria-hidden
                                    />
                                  ) : null}
                                  {/* Building: only solid skeleton fence — never show real General/sections underneath */}
                                  {sectionRailModel?.thinking ? (
                                    <div className="pm-meeting-section-fence-overlay" aria-hidden>
                                      <div className="pm-meeting-section-fence-line" />
                                      <div className="pm-meeting-section-fence-line" />
                                      <div className="pm-meeting-section-fence-line" />
                                      <div className="pm-meeting-section-fence-line" />
                                    </div>
                                  ) : !sectionRailModel || sectionRailModel.items.length === 0 ? (
                                    <p className="pm-meeting-section-rail-empty">No sections yet</p>
                                  ) : (
                                    sectionRailModel.items.map((item) => {
                                      if (item.kind === "skeleton") {
                                        return (
                                          <div
                                            key={item.id}
                                            className="pm-meeting-section-card is-skeleton"
                                            aria-hidden
                                          >
                                            <span
                                              className="pm-meeting-section-skel"
                                              style={{ width: `${52 + (item.id.charCodeAt(3) % 3) * 14}%` }}
                                            />
                                          </div>
                                        )
                                      }

                                      const isNav = item.kind === "general" || item.kind === "section"
                                      const isPick = item.kind === "blueprint"
                                      const isCustom = item.kind === "custom"
                                      const isEarly = item.kind === "early"
                                      /** md on disk OR live gen/stream — can open */
                                      const isReady = isNav && item.ready === true
                                      /** Tokens flowing (open to watch stream) */
                                      const isStreaming = isNav && item.streaming === true
                                      /**
                                       * Work in flight before first token (server generating / SSE prefilling)
                                       * OR not yet openable. Show Generating badge.
                                       */
                                      const isGenerating =
                                        isNav &&
                                        (item.generating === true || item.ready === false) &&
                                        !isStreaming
                                      const isIngested = item.kind === "section" && item.ingested === true
                                      const clickable =
                                        (isNav && (isReady || isStreaming || isGenerating)) ||
                                        ((isPick || isCustom) && !sectionRailModel.busy)
                                      const desc = item.hint?.trim() || ""

                                      return (
                                        <div
                                          key={item.id}
                                          ref={(el) => {
                                            if (el) sectionItemRefs.current.set(item.id, el)
                                            else sectionItemRefs.current.delete(item.id)
                                          }}
                                          className="pm-meeting-section-card-wrap"
                                          onMouseEnter={(e) => {
                                            if (desc) {
                                              showSectionTip(
                                                item.id,
                                                desc,
                                                e.currentTarget,
                                              )
                                            }
                                          }}
                                          onMouseLeave={hideSectionTip}
                                        >
                                          <button
                                            type="button"
                                            disabled={!clickable}
                                            className={cn(
                                              "pm-meeting-section-card",
                                              item.active && isReady && "is-active",
                                              item.selected && "is-picked",
                                              isReady && !isStreaming && !isGenerating && "is-ready",
                                              isStreaming && "is-streaming",
                                              isGenerating && "is-pending",
                                              isIngested && "is-ingested",
                                              (isEarly || (item.kind === "blueprint" && !item.selected)) && "is-preview",
                                              isCustom && "is-picked",
                                            )}
                                            title={
                                              isStreaming
                                                ? "Streaming — click to watch"
                                                : isGenerating
                                                  ? "Generating…"
                                                  : isIngested
                                                    ? "Ingested · open section"
                                                    : isReady
                                                      ? "Open section"
                                                      : undefined
                                            }
                                            onClick={() => {
                                              const actions = sectionRailActionsRef.current
                                              if (!actions) return
                                              if (isNav) {
                                                // Enter ready + live streaming sections without
                                                // dismissing SSE (stream state is global).
                                                if (!isReady && !isStreaming && !isGenerating) return
                                                setSelectedSummaryId(item.id)
                                                actions.selectSection(item.id)
                                                setMainTab("summary")
                                                return
                                              }
                                              if (isPick) {
                                                actions.toggleBlueprint(item.id)
                                                return
                                              }
                                              if (isCustom) {
                                                const idx = Number(item.id.replace("custom:", ""))
                                                if (!Number.isNaN(idx)) actions.removeCustom(idx)
                                              }
                                            }}
                                            onFocus={(e) => {
                                              if (desc) {
                                                const wrap = e.currentTarget.closest(
                                                  ".pm-meeting-section-card-wrap",
                                                ) as HTMLElement | null
                                                showSectionTip(item.id, desc, wrap)
                                              }
                                            }}
                                            onBlur={hideSectionTip}
                                            aria-describedby={
                                              desc && sectionTip?.id === item.id
                                                ? `section-tip-${item.id}`
                                                : undefined
                                            }
                                          >
                                            {(isPick || isCustom) && (
                                              <span
                                                className={cn(
                                                  "pm-meeting-section-card-dot",
                                                  (item.selected || isCustom) && "is-on",
                                                )}
                                                aria-hidden
                                              />
                                            )}
                                            {/* Ready / streaming / generating status mark */}
                                            {isNav && (
                                              <span
                                                className={cn(
                                                  "pm-meeting-section-card-status",
                                                  isStreaming
                                                    ? "is-streaming"
                                                    : isGenerating
                                                      ? "is-pending"
                                                      : isReady
                                                        ? "is-ready"
                                                        : "is-pending",
                                                )}
                                                aria-hidden
                                              />
                                            )}
                                            {item.shortLabel ? (
                                              <span className="pm-meeting-section-card-t">{item.shortLabel}</span>
                                            ) : null}
                                            <span className="pm-meeting-section-card-name">{item.label}</span>
                                            {isStreaming ? (
                                              <span className="pm-meeting-section-card-badge is-streaming">
                                                Streaming
                                              </span>
                                            ) : isGenerating ? (
                                              <span className="pm-meeting-section-card-badge">Generating</span>
                                            ) : isIngested ? (
                                              <span
                                                className="pm-meeting-section-card-ingested"
                                                title="Ingested to collection"
                                                aria-label="Ingested"
                                              >
                                                <Check className="size-2.5" strokeWidth={2.5} aria-hidden />
                                              </span>
                                            ) : null}
                                            {isCustom ? (
                                              <X className="pm-meeting-section-card-x size-3" aria-hidden />
                                            ) : null}
                                          </button>
                                        </div>
                                      )
                                    })
                                  )}
                                  {/* Breakdown sits under the last list item (not a separate footer bar) */}
                                  {sectionRailModel?.canBreakdown && (
                                    <div className="pm-meeting-section-rail-breakdown-wrap">
                                      <button
                                        type="button"
                                        className={cn(
                                          "pm-meeting-section-rail-breakdown",
                                          sectionRailModel.busy && "sk-thinking-flow",
                                        )}
                                        disabled={sectionRailModel.busy}
                                        onClick={() => sectionRailActionsRef.current?.breakdown()}
                                      >
                                        {sectionRailModel.busy ? "Extracting…" : "Breakdown"}
                                      </button>
                                    </div>
                                  )}
                                </div>
                              </div>
                            </div>

                            <div
                              className={cn(
                                "pm-meeting-side-panel",
                                sideTab === "transcript" && "is-active",
                              )}
                              aria-hidden={sideTab !== "transcript"}
                            >
                              <TranscriptTab
                                segments={displaySegments}
                                partialText={transcription.currentPartial}
                                onSegmentClick={handleSegmentClick}
                                focusRef={focusRef}
                                activeSectionTag={activeSectionTag}
                                speakerNames={meeting.speaker_names ?? {}}
                                tabs={meeting?.tabs}
                                showSearch
                                playbackTime={playbackTime}
                              />
                            </div>

                            <div
                              className={cn(
                                "pm-meeting-side-panel",
                                sideTab === "speaker" && "is-active",
                              )}
                              aria-hidden={sideTab !== "speaker"}
                            >
                              <SpeakersTab
                                segments={displaySegments}
                                speakerNames={meeting.speaker_names ?? {}}
                                onUpdateSpeakerName={(id, name) => {
                                  const updated = { ...meeting.speaker_names, [id]: name }
                                  updateMeeting(meeting.id, { speaker_names: updated }).then((m) => {
                                    handleMeetingUpdate(m)
                                    void import("@/components/ui/tiptap-editor").then((mod) => {
                                      mod.invalidateMeetingSpeakerCache(meeting.id)
                                    })
                                  }).catch(() => {})
                                }}
                                onSegmentClick={handleSegmentClick}
                                activeSectionTag={activeSectionTag}
                              />
                            </div>
                          </div>
                        </>
                      )}
                      </div>
                    </div>
                  </aside>

                {/*
                  Single QC diamond: bottom park by default; Chat open → fade out,
                  snap to top, fade in (no slide). Side collapse keeps bottom park.
                */}
                <div
                  className={cn(
                    "pm-meeting-qc-fab-host",
                    qcFabPark === "top" ? "is-park-top" : "is-park-bottom",
                    qcFabFading && "is-fading",
                    qcFabRideOut && "is-ride-out",
                    qcFabBottomFadeIn && "is-fade-in",
                  )}
                >
                  <MeetingQcFab
                    open={
                      quickChatOpen &&
                      sideRailOpen &&
                      qcFabPark === "top" &&
                      (qcFabSpinPhase === "cruise" ||
                        qcFabSpinPhase === "enter-hold" ||
                        qcFabSpinPhase === "enter-decel-top")
                    }
                    spinPhase={qcFabSpinPhase}
                    onOpen={() => {
                      if (!sideRailOpen) setSideRailOpenWithMotion(true)
                      else setSideRailOpen(true)
                      setQuickChatOpen(true)
                      setTxPeekOpen(false)
                    }}
                    onClose={() => {
                      /* Leave Chat → tools tabs; do not collapse the side rail */
                      setQuickChatOpen(false)
                      setTxPeekOpen(false)
                    }}
                  />
                </div>

                {!sideRailOpen && (
                  <button
                    type="button"
                    className="pm-meeting-side-reopen"
                    onClick={() => {
                      setSideRailOpenWithMotion(true)
                      /* reopen to last non-chat tools view */
                    }}
                    aria-label="Open side panel"
                  >
                    <PanelRightOpen className="size-3.5" />
                    Panel
                  </button>
                )}

                {/* Chat sentence-ref: overlay Transcript peek (covers main; side width fixed) */}
                {txPeekOpen && sideRailOpen && (
                  <aside
                    className="pm-meeting-tx-peek"
                    aria-label="Transcript reference"
                  >
                    <div className="pm-meeting-tx-peek-card">
                      <div className="pm-meeting-tx-peek-head">
                        <span className="pm-meeting-tx-peek-title">Transcript</span>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-xs"
                          onClick={() => setTxPeekOpen(false)}
                          aria-label="Close transcript"
                        >
                          <X className="size-3.5" />
                        </Button>
                      </div>
                      <div className="pm-meeting-tx-peek-body">
                        <TranscriptTab
                          segments={displaySegments}
                          partialText={transcription.currentPartial}
                          onSegmentClick={handleSegmentClick}
                          focusRef={focusRef}
                          activeSectionTag={activeSectionTag}
                          speakerNames={meeting.speaker_names ?? {}}
                          tabs={meeting?.tabs}
                          showSearch={false}
                          playbackTime={playbackTime}
                        />
                      </div>
                    </div>
                  </aside>
                )}
              </div>
            ) : (
              <div className="pm-meeting-empty pm-meeting-empty--span">
                <p>Select a meeting or create one</p>
              </div>
            )}
          </div>
        </div>
      </div>

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

    </div>
  )
}
