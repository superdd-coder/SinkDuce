import {
  useState, useEffect, useLayoutEffect, useCallback, useRef,
} from "react"
import { useShallow } from "zustand/react/shallow"
import { cn } from "@/lib/utils"
import { useAppStore } from "@/stores/app-store"
import { useAudioRecorder } from "@/hooks/use-audio-recorder"
import { warmupDesktopSystemAudio } from "@/lib/desktop-system-audio"
import { useTranscription } from "@/hooks/use-transcription"
import {
  getMeetings, getMeeting, deleteMeeting, discardMeetingRecording,
  uploadMeetingAudio, appendRecordingPcm, finalizeMeetingRecording,
  transcribeMeeting, cancelTranscribeMeeting,
  getMeetingTranscript, updateMeeting, commitMeetingSpeakers,
  getRealtimeTranscriptionProviders, getFileTranscriptionProviders,
  getActiveProviderInfo,
  type Meeting, type TranscriptSegment, type LanguageHintOption,
} from "@/api/client"
import { toast } from "sonner"
import { useT } from "@/i18n/use-t"
import { formatApiError } from "@/api/http"
import {
  type SectionRailActions,
  type SectionRailModel,
} from "./meeting-tabs"
import { MeetingList } from "./meeting-list"
import { MeetingGroupStage } from "./meeting-group-stage"
import {
  listMeetingGroups,
  deleteMeetingGroup as apiDeleteMeetingGroup,
  type MeetingGroup,
} from "@/api/meeting"
import type { MediaBarHandle } from "./media-bar"

import {
  type MeetingQcSpinPhase,
} from "./meeting-quick-chat"
import { DEFAULT_LANGUAGE_HINTS } from "./language-hints-selector"
import {
  DEFAULT_TRANSLATION_TARGET,
  TRANSLATION_TARGET_LANGUAGES,
} from "./translation-selector"
import { clipLanguageHints } from "@/lib/language-hints"
import { startStream as startBlueprintStream } from "@/hooks/use-blueprint-stream"
import { type CaptureMiniPlayerHandle } from "./capture-mini-player"
import { MeetingCaptureStages } from "./meeting-capture-stages"
import { MeetingStudioStage } from "./meeting-studio-stage"
import { MeetingViewOverlays } from "./meeting-view-overlays"
import { useMeetingNotes } from "@/hooks/use-meeting-notes"

/** Keep live notes when a mutation response omitted notes_content (PUT/upload). */
function mergeMeetingUpdate(prev: Meeting | null, next: Meeting): Meeting {
  if (
    prev &&
    prev.id === next.id &&
    next.notes_content === undefined &&
    prev.notes_content !== undefined
  ) {
    return { ...next, notes_content: prev.notes_content }
  }
  return next
}

export function MeetingView({ active = true }: { active?: boolean }) {
  const t = useT()
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
  const [railTab, setRailTab] = useState<"meetings" | "groups">("meetings")
  const [groups, setGroups] = useState<MeetingGroup[]>([])
  const [activeGroup, setActiveGroup] = useState<string | null>(null)
  const [meeting, setMeeting] = useState<Meeting | null>(null)
  const applyMeeting = useCallback((m: Meeting) => {
    setMeeting((prev) => mergeMeetingUpdate(prev, m))
  }, [])
  const notes = useMeetingNotes({
    meetingId: meeting?.id ?? activeMeeting,
    serverContent: meeting?.notes_content ?? "",
    onSaved: applyMeeting,
  })
  const notesRailOpenRef = useRef<Map<string, boolean>>(new Map())
  const [notesRailOpen, setNotesRailOpen] = useState(false)
  useEffect(() => {
    const id = meeting?.id
    if (!id) {
      setNotesRailOpen(false)
      return
    }
    setNotesRailOpen(notesRailOpenRef.current.get(id) ?? false)
  }, [meeting?.id])
  const toggleNotesRail = useCallback(() => {
    const id = meeting?.id
    if (!id) return
    setNotesRailOpen((prev) => {
      const next = !prev
      notesRailOpenRef.current.set(id, next)
      return next
    })
  }, [meeting?.id])
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
  /** Studio ↔ group stage — sequential opacity, same clock as meeting-mode fade */
  const [kindOpaque, setKindOpaque] = useState(true)
  const [paintGroupStage, setPaintGroupStage] = useState(false)
  const [paintGroupId, setPaintGroupId] = useState<string | null>(null)
  const kindGenRef = useRef(0)
  const KIND_OUT_MS = 180

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
  const levelsRef = useRef<number[]>([])
  /** Serializes live PCM POSTs so disk order matches capture order. */
  const persistChainRef = useRef(Promise.resolve())
  /** Meetings this window instance has captured in, since page load. A
   * status='recording' meeting outside this set survived a page refresh —
   * offer recovery instead of a stuck "Recording" badge. */
  const sessionOwnedRecordingIdsRef = useRef<Set<string>>(new Set())
  const [hasFileProvider, setHasFileProvider] = useState(true) // optimistic — avoids flash on remount; config check corrects if needed
  const [supportedLanguageHints, setSupportedLanguageHints] = useState<LanguageHintOption[]>([])
  const [maxLanguageHints, setMaxLanguageHints] = useState(1)
  /** Active adapter hot-words capability (from registry class flag) */
  const [fileSupportsHotWords, setFileSupportsHotWords] = useState(false)
  const [rtSupportsHotWords, setRtSupportsHotWords] = useState(false)

  // Per-meeting + path language hints (`${meetingId}:rt` | `${meetingId}:file`)
  const perMeetingLanguageHints = useRef<Map<string, string[]>>(new Map())
  /** Which model path the language selector is bound to right now */
  const languagePathRef = useRef<"rt" | "file">("rt")
  const [languageHints, setLanguageHints] = useState<string[]>([...DEFAULT_LANGUAGE_HINTS])

  // Live bilingual captions (DashScope LiveTranslate) — realtime-only
  const [supportsTranslation, setSupportsTranslation] = useState(false)
  const [translationEnabled, setTranslationEnabled] = useState(false)
  const [translationTarget, setTranslationTarget] = useState<string>(DEFAULT_TRANSLATION_TARGET)
  const perMeetingTranslation = useRef<Map<string, { enabled: boolean; target: string }>>(new Map())
  const translationEnabledRef = useRef(false)
  translationEnabledRef.current = translationEnabled
  const translationTargetRef = useRef<string>(DEFAULT_TRANSLATION_TARGET)
  translationTargetRef.current = translationTarget
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState("")
  const [audioVersion, setAudioVersion] = useState(0)
  const [retranscribeConfirmOpen, setRetranscribeConfirmOpen] = useState(false)
  const [deleteGroupTarget, setDeleteGroupTarget] = useState<string | null>(null)
  const [focusRef, setFocusRef] = useState<{ id: string; ts: number; fromChat?: boolean } | null>(null)
  const [activeSectionTag, setActiveSectionTag] = useState("")
  const discardingRef = useRef(false)
  /** 3-min silent-capture hint timer; cancelled on stop/discard/unmount */
  const noAudioWarnTimerRef = useRef<number | null>(null)
  const [playbackTime, setPlaybackTime] = useState<number | null>(null)
  const [quickChatOpen, setQuickChatOpen] = useState(false)
  const [transcriptJumpCounter, setTranscriptJumpCounter] = useState(0)
  /** Summary section selection (synced with MeetingTabs + side Sections tab) */
  const [selectedSummaryId, setSelectedSummaryId] = useState("tab_general")
  const [, setMainTab] = useState("summary")
  /** Right analysis rail: Sections | Transcript | Speaker | Group */
  const [sideTab, setSideTab] = useState<"sections" | "transcript" | "speaker" | "groups">("sections")
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
  const requestSideTab = useCallback((tab: "sections" | "transcript" | "speaker" | "groups") => {
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
  const recorder = useAudioRecorder((pcm) => {
    const owner = captureOwnerRef.current
    if (owner) {
      const copy = pcm.slice(0)
      persistChainRef.current = persistChainRef.current
        .then(() => appendRecordingPcm(owner, copy))
        .catch(() => undefined)
    }
    if (realtimeEnabled && hasRealtimeProvider) {
      transcription.sendAudioData(pcm)
    }
  })
  levelsRef.current = recorder.levels
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
    const next = clipLanguageHints(hints, maxLanguageHints)
    setLanguageHints(next)
    if (activeMeeting) {
      perMeetingLanguageHints.current.set(
        `${activeMeeting}:${languagePathRef.current}`,
        next,
      )
    }
  }

  // Per-meeting live-translation prefs (enabled + target language).
  // While recording, changes hot-swap the engine via a graceful reconnect
  // (audio recording itself is never interrupted — it is a separate PCM
  // consumer); before recording they only update state for the next session.
  const updateTranslationEnabled = useCallback(
    (v: boolean) => {
      setTranslationEnabled(v)
      // Sync the ref NOW — the paired target callback may have just updated
      // it in the same tick (first-enable flow: pick language → enable).
      translationEnabledRef.current = v
      if (activeMeeting) {
        perMeetingTranslation.current.set(activeMeeting, {
          enabled: v,
          target: translationTargetRef.current,
        })
      }
      if (transcription.isTranscribing) {
        toast.message(t("meeting.liveTranslationSwitching"))
        transcription.reconfigureTranslation(v ? translationTargetRef.current : null)
      }
    },
    [activeMeeting, transcription.isTranscribing, transcription.reconfigureTranslation, t],
  )
  const updateTranslationTarget = useCallback(
    (code: string) => {
      const valid = TRANSLATION_TARGET_LANGUAGES.some((l) => l.code === code)
      const next = valid ? code : DEFAULT_TRANSLATION_TARGET
      setTranslationTarget(next)
      // Sync immediately so a same-tick enable callback reads the new value.
      translationTargetRef.current = next
      if (activeMeeting) {
        perMeetingTranslation.current.set(activeMeeting, {
          enabled: translationEnabledRef.current,
          target: next,
        })
      }
      // Engine swap only when translation is (or just stays) on — a bare
      // language pick while OFF must not reconnect; the enable callback
      // right after it connects with this fresh target.
      if (transcription.isTranscribing && translationEnabledRef.current) {
        toast.message(t("meeting.liveTranslationSwitching"))
        transcription.reconfigureTranslation(next)
      }
    },
    [activeMeeting, transcription.isTranscribing, transcription.reconfigureTranslation, t],
  )

  // Restore translation prefs when switching meetings
  useEffect(() => {
    if (!activeMeeting) return
    const stored = perMeetingTranslation.current.get(activeMeeting)
    if (stored) {
      setTranslationEnabled(stored.enabled && supportsTranslation)
      setTranslationTarget(stored.target)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeMeeting])

  /**
   * Language options follow the path-appropriate default model:
   * - Setup / live capture → active **realtime** model
   * - Audio ready / file re-tx → active **file** model
   * Hot words stay shared (meeting-level library).
   */
  const refreshLanguageHintsFromActiveProvider = useCallback(
    async (opts?: { preferRealtime?: boolean; meetingId?: string | null }) => {
      try {
        const info = await getActiveProviderInfo()
        const preferRt = !!opts?.preferRealtime
        const fileHints = info.file?.supported_language_hints ?? []
        const rtHints = info.realtime?.supported_language_hints ?? []
        const useRt = preferRt && rtHints.length > 0
        const hints = (useRt ? rtHints : fileHints).length
          ? (useRt ? rtHints : fileHints)
          : fileHints.length
            ? fileHints
            : rtHints

        setSupportedLanguageHints(hints)
        languagePathRef.current = useRt ? "rt" : "file"
        setFileSupportsHotWords(!!info.file?.supports_hot_words)
        setRtSupportsHotWords(!!info.realtime?.supports_hot_words)
        // Live bilingual captions are a realtime capability.
        const rtTranslation = !!info.realtime?.supports_realtime_translation
        setSupportsTranslation(rtTranslation)
        if (!rtTranslation) setTranslationEnabled(false)
        const side = useRt ? info.realtime : info.file
        const maxHints = Math.max(1, side?.max_language_hints ?? 1)
        setMaxLanguageHints(maxHints)

        const supportedCodes = new Set(hints.map((h) => h.code))
        const mid = opts?.meetingId ?? activeMeeting
        const storeKey = mid
          ? `${mid}:${useRt ? "rt" : "file"}`
          : useRt
            ? "rt"
            : "file"
        const stored = perMeetingLanguageHints.current.get(storeKey)
        const pick = (codes: string[]) => {
          const filtered = codes.filter((c) => supportedCodes.has(c))
          if (filtered.length > 0) return clipLanguageHints(filtered, maxHints)
          if (supportedCodes.has("auto")) return ["auto"]
          return hints[0]?.code ? [hints[0].code] : [...DEFAULT_LANGUAGE_HINTS]
        }
        const next = pick(stored ?? languageHintsRef.current)
        setLanguageHints(next)
        perMeetingLanguageHints.current.set(storeKey, next)
      } catch {
        /* keep previous options */
      }
    },
    [activeMeeting],
  )

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
      const target = translationEnabledRef.current
        ? translationTargetRef.current
        : undefined
      startTranscriptionRef.current(languageHintsRef.current, target)
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

  const fetchGroups = useCallback(async () => {
    try {
      setGroups(await listMeetingGroups())
    } catch { /* ignore */ }
  }, [])

  // Load collections for ID -> name mapping
  useEffect(() => {
    fetchCollections()
    void fetchGroups()
  }, [fetchCollections, fetchGroups])

  // Fetch single meeting detail
  const fetchMeeting = useCallback(async (id: string) => {
    fetchMeetingIdRef.current = id
    try {
      const m = await getMeeting(id)
      // Guard: if activeMeeting changed while fetching, discard stale result
      if (fetchMeetingIdRef.current !== id) return
      applyMeeting(m)
      // If a background task is in progress, resume polling.
      // Update meeting on every poll tick so children (MeetingTabs) stay in sync.
      // Polling for busy processing is started by the active-view effect below
      // (avoids network churn while Meeting sidebar is hidden).
    } catch { /* ignore */ }
  }, [applyMeeting])

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

  useEffect(() => {
    if (!active) return
    void warmupDesktopSystemAudio()
  }, [active])

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

  // Load meetings on mount
  useEffect(() => {
    fetchMeetings()
  }, [fetchMeetings])

  // Load meeting detail when active changes — keep previous paint (no blank flash)
  useEffect(() => {
    if (activeMeeting) {
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
    // Group ↔ studio is owned by the kind fade — don't double-fade.
    if (paintGroupStage) {
      setMeetingSoftFaded(false)
      return
    }
    meetingSoftGenRef.current += 1
    setMeetingSoftFaded(true)
    setEditingTitle(false)
    setPlaybackTime(null)
  }, [activeMeeting, paintGroupStage])

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

  const wantGroupStage = !!(activeGroup && railTab === "groups" && !activeMeeting)
  useEffect(() => {
    const targetId = wantGroupStage ? activeGroup : null
    const paintedId = paintGroupStage ? paintGroupId : null
    if (wantGroupStage === paintGroupStage && targetId === paintedId) return
    setKindOpaque(false)
    const gen = ++kindGenRef.current
    const t = window.setTimeout(() => {
      if (kindGenRef.current !== gen) return
      setPaintGroupStage(wantGroupStage)
      setPaintGroupId(wantGroupStage ? activeGroup : paintedId)
    }, KIND_OUT_MS)
    return () => window.clearTimeout(t)
  }, [wantGroupStage, activeGroup, paintGroupStage, paintGroupId])
  useEffect(() => {
    const targetId = wantGroupStage ? activeGroup : null
    const paintedId = paintGroupStage ? paintGroupId : null
    if (wantGroupStage !== paintGroupStage || targetId !== paintedId || kindOpaque) return
    let inner = 0
    const outer = requestAnimationFrame(() => {
      inner = requestAnimationFrame(() => setKindOpaque(true))
    })
    return () => {
      cancelAnimationFrame(outer)
      cancelAnimationFrame(inner)
    }
  }, [wantGroupStage, activeGroup, paintGroupStage, paintGroupId, kindOpaque])

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
    const slotsBusy = meeting?.speaker_slots_status === "computing"
    if (!transcribing && !processingBusy && !slotsBusy) {
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
    meeting?.speaker_slots_status,
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
      toast.success(t("meeting.fileTxReady"))
      return
    }
    // Failed file-tx: backend sets status back to "created" + transcription_error.
    // Must clear postLiveFileTx lock or Capture stays stuck on "transcribing" chrome.
    if (
      sameMeeting &&
      prevStatus === "transcribing" &&
      curr !== "transcribing" &&
      curr !== "completed"
    ) {
      setPostLiveFileTxMeetingId((mid) => (mid === paintedId ? null : mid))
      const err = (meeting?.transcription_error || "").trim()
      const friendly = /ASR_RESPONSE_HAVE_NO_WORDS|no.?words/i.test(err)
        ? t("meeting.noSpeechDetected")
        : err
          ? t("meeting.transcriptionFailed", { error: err })
          : t("meeting.transcriptionFailedShort")
      toast.error(friendly)
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
    meeting?.transcription_error,
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
    // A pending Discard consumes the blob regardless of owner resolution —
    // handleDiscard nulls captureOwnerRef before the ~800ms-late blob lands,
    // so gating this branch on ownerId left discardingRef stuck true and the
    // NEXT recording's auto file-transcription was silently swallowed.
    if (recorder.audioBlob && discardingRef.current) {
      discardingRef.current = false
      captureOwnerRef.current = null
      recorder.reset()
      return
    }
    const ownerId = captureOwnerRef.current
    if (recorder.audioBlob && ownerId) {
      const blobType = recorder.audioBlob.type || "audio/wav"
      const ext = blobType.includes("wav")
        ? "wav"
        : blobType.includes("mp4")
          ? "m4a"
          : blobType.includes("webm")
            ? "webm"
            : "wav"
      const file = new File([recorder.audioBlob], `recording.${ext}`, { type: blobType })
      const uploadTo = ownerId
      captureOwnerRef.current = null
      uploadMeetingAudio(uploadTo, file)
        .then((m) => {
          // Only replace detail panel if user is still viewing that meeting
          if (fetchMeetingIdRef.current === uploadTo || activeMeeting === uploadTo) {
            applyMeeting(m)
            setAudioVersion((v) => v + 1)
          }
          toast.success(t("meeting.audioUploaded"))
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
                toast.info(t("meeting.fileTxStarted"))
                fetchMeeting(uploadTo)
              })
              .catch((err) => {
                setPostLiveFileTxMeetingId((mid) => (mid === uploadTo ? null : mid))
                toast.error(
                  t("meeting.transcriptionFailed", { error: formatApiError(err, t) }),
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
          toast.error(t("chat.uploadFailed", { error: formatApiError(err, t) }))
        })
    }
  }, [recorder.audioBlob])

  // Handlers
  const handleUploadAudio = async (file: File) => {
    if (!activeMeeting) return
    notes.flush(activeMeeting)
    try {
      const m = await uploadMeetingAudio(activeMeeting, file)
      applyMeeting(m)
      setAudioVersion((v) => v + 1)
      toast.success(t("meeting.audioReadyReview"))
      fetchMeetings()
      // Do NOT auto-transcribe on upload; user clicks Transcribe on capture page
    } catch (err) {
      toast.error(t("chat.uploadFailed", { error: formatApiError(err, t) }))
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
    if (noAudioWarnTimerRef.current) window.clearTimeout(noAudioWarnTimerRef.current)
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
      toast.error(t("meeting.alreadyRecordingOther"), {
        description: t("meeting.stopDiscardFirst"),
      })
      return
    }
    if (recorder.isRecording || recorder.isPaused) {
      toast.message(t("meeting.recordingInProgress"))
      return
    }
    toast.message(t("meeting.startingCapture"))
    notes.flush(ownerId)
    // Bind owner before start so the first PCM chunks persist to this meeting.
    if (ownerId) {
      captureOwnerRef.current = ownerId
      setRecordingMeetingId(ownerId)
      sessionOwnedRecordingIdsRef.current.add(ownerId)
    }
    // Permission / share dialog first — stay on setup if denied / cancelled
    const startError = await recorder.startRecording()
    if (startError) {
      captureOwnerRef.current = null
      setRecordingMeetingId(null)
      toast.error(startError, { duration: 6500 })
      return
    }
    // Silent-capture hint: only after a sustained 3 minutes of silence — an
    // early check fired while macOS permission prompts were still open.
    if (noAudioWarnTimerRef.current) window.clearTimeout(noAudioWarnTimerRef.current)
    noAudioWarnTimerRef.current = window.setTimeout(() => {
      noAudioWarnTimerRef.current = null
      if (!captureOwnerRef.current) return // stopped/discarded meanwhile
      const peak = Math.max(0, ...(levelsRef.current ?? [0]))
      if (peak < 0.02) {
        toast.warning(
          t("meeting.noAudioDetected"),
          { duration: 8000 },
        )
      }
    }, 3 * 60_000)
    // Do not force realtime on — respect pre-start preference (hover chip / default)
  }

  const clearRecordingOwner = useCallback(() => {
    setRecordingMeetingId(null)
    // keep captureOwnerRef until upload effect consumes audioBlob
  }, [])

  const handleStopRecording = useCallback(() => {
    // Keep captureOwnerRef for upload target even if user is viewing another meeting
    const owner = captureOwnerRef.current ?? recordingMeetingId
    if (noAudioWarnTimerRef.current) {
      window.clearTimeout(noAudioWarnTimerRef.current)
      noAudioWarnTimerRef.current = null
    }
    // Flush live notes immediately so Studio/Summarize does not mount on a stale empty field
    notes.flush(owner)
    // Lock Capture on "Transcribing" UI before realtime save sets status=completed
    // (otherwise speakers gate flashes until file-tx starts).
    if (owner && hasFileProvider) {
      setPostLiveFileTxMeetingId(owner)
    }
    transcription.stopTranscription()
    recorder.stopRecording()
    setRealtimeEnabled(hasRealtimeProvider)
    if (owner) {
      void persistChainRef.current
        .then(() => finalizeMeetingRecording(owner))
        .then((m) => applyMeeting(m))
        .catch(() => undefined)
    }
    clearRecordingOwner()
  }, [
    transcription,
    recorder,
    hasRealtimeProvider,
    clearRecordingOwner,
    recordingMeetingId,
    hasFileProvider,
    applyMeeting,
    notes.flush,
  ])

  /** Uncommitted hot-words pick when a transcript already exists (cleared on leave / refresh). */
  const hotWordsDraftRef = useRef<string[] | undefined>(undefined)

  const handleHotWordsDraftChange = useCallback((draft: string[] | undefined) => {
    hotWordsDraftRef.current = draft
  }, [])

  const handleTranscribe = async () => {
    if (!activeMeeting) return
    notes.flush(activeMeeting)
    if (!hasFileProvider) {
      toast.error(t("meeting.noProviderSetup"), {
        action: { label: t("nav.settings"), onClick: () => setSidebarView("llm_provider") },
      })
      return
    }
    // Commit hot-words draft (if any) before starting file transcription
    const draft = hotWordsDraftRef.current
    if (draft !== undefined) {
      try {
        const m = await updateMeeting(activeMeeting, {
          hot_words_library_ids: draft,
          hot_words_library_id: draft[0] ?? null,
        })
        applyMeeting(m)
        hotWordsDraftRef.current = undefined
      } catch (err) {
        toast.error(
          t("meeting.hotWordsUpdateFailed", { error: formatApiError(err, t) }),
        )
        return
      }
    }
    // Clear realtime segments so new file transcript shows after completion
    transcription.setSegments([])
    // Re-tx must return to Speakers after complete (not stay in Studio)
    lockBackToCapture(activeMeeting)
    try {
      await transcribeMeeting(activeMeeting, languageHints)
      toast.info(t("meeting.transcriptionStarted"))
      fetchMeeting(activeMeeting)
    } catch (err) {
      const raw = err instanceof Error ? err.message : String(err)
      const msg = /No audio/i.test(raw)
        ? t("meeting.noAudioFile")
        : /No active file transcription provider/i.test(raw)
          ? t("meeting.noFileProviderDefault")
          : raw.replace(/^API \d+:\s*/, "")
      toast.error(t("meeting.transcriptionFailed", { error: msg }))
    }
  }

  const handleCancelTranscribe = async () => {
    if (!activeMeeting) return
    try {
      await cancelTranscribeMeeting(activeMeeting)
      setPostLiveFileTxMeetingId((mid) => (mid === activeMeeting ? null : mid))
      fetchMeeting(activeMeeting)
      toast.info(t("meeting.transcriptionCancelled"))
    } catch (err) {
      toast.error(t("meeting.cancelFailed", { error: formatApiError(err, t) }))
    }
  }

  const handleDiscard = async () => {
    const owner = captureOwnerRef.current ?? recordingMeetingId ?? activeMeeting
    if (!owner) return
    if (noAudioWarnTimerRef.current) {
      window.clearTimeout(noAudioWarnTimerRef.current)
      noAudioWarnTimerRef.current = null
    }
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
      toast.success(t("meeting.recordingDiscarded"))
      if (activeMeeting === owner) fetchMeeting(owner)
      fetchMeetings()
    } catch (err) {
      toast.error(t("meeting.discardFailed", { error: formatApiError(err, t) }))
    } finally {
      // Deterministic flag clear — an empty recording produces no blob, so
      // the audioBlob effect may never run the discard branch.
      discardingRef.current = false
      recorder.reset()
    }
  }

  const handleDelete = (id: string) => {
    setDeleteTarget(id)
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    const target =
      meetings.find((m) => m.id === deleteTarget) ||
      (meeting?.id === deleteTarget ? meeting : null)
    const colIds = new Set<string>()
    for (const col of target?.allocated_collections || []) {
      if (col) colIds.add(col)
    }
    for (const t of target?.tabs || []) {
      const col = t.associated_collection_id
      if (col) colIds.add(col)
    }
    try {
      await deleteMeeting(deleteTarget)
      if (activeMeeting === deleteTarget) setActiveMeeting(null)
      setDeleteTarget(null)
      fetchMeetings()
      if (colIds.size > 0) {
        try {
          const { useFileMgmtStore } = await import("@/stores/file-mgmt-store")
          const store = useFileMgmtStore.getState()
          for (const col of colIds) {
            await store.refreshLibrarySurfaces(col)
          }
        } catch { /* Database view may be unmounted */ }
      }
      toast.success(t("meeting.meetingDeleted"))
    } catch {
      toast.error(t("settings.deleteFailed"))
    }
  }

  const confirmDeleteGroup = async () => {
    if (!deleteGroupTarget) return
    const id = deleteGroupTarget
    try {
      await apiDeleteMeetingGroup(id)
      if (activeGroup === id) setActiveGroup(null)
      setDeleteGroupTarget(null)
      await fetchGroups()
      toast.success(t("meeting.groupDeleted"))
    } catch (err) {
      toast.error(t("common.failedWithError", { error: formatApiError(err, t) }))
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
    applyMeeting(m)
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
      applyMeeting(m)
      setEditingTitle(false)
      fetchMeetings()
    } catch (err) {
      toast.error(t("meeting.renameFailed", { error: formatApiError(err, t) }))
    }
  }

  const handleSelectHotWordsLibraries = async (libraryIds: string[]) => {
    if (!activeMeeting) return
    try {
      const m = await updateMeeting(activeMeeting, {
        hot_words_library_ids: libraryIds,
        hot_words_library_id: libraryIds[0] ?? null,
      })
      applyMeeting(m)
    } catch (err) {
      toast.error(t("meeting.hotWordsUpdateFailed", { error: formatApiError(err, t) }))
    }
  }

  const metaCreated = meeting?.created_at
    ? new Date(meeting.created_at).toLocaleDateString(undefined, {
        month: "short", day: "numeric", year: "numeric",
        hour: "2-digit", minute: "2-digit",
      })
    : "—"

  const metaSpeakers = (() => {
    if (!meeting) return "—"
    const named = meeting.speaker_names ? Object.values(meeting.speaker_names).filter(Boolean) : []
    if (named.length > 0) return named.join(", ")
    const count = new Set(transcript.map((s) => s.speaker_id).filter(Boolean)).size
    return count === 1
      ? t("meeting.nSpeaker", { n: count || 0 })
      : t("meeting.nSpeakers", { n: count || 0 })
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
   * Stage mode for capture ↔ speakers ↔ studio (and live / audio-ready / transcribing).
   * Paint lags target via sequential fade so layout doesn't hard-cut.
   * audio vs transcribing are separate so Transcribe click fades out → in.
   */
  type StageMode =
    | "setup"
    | "audio"
    | "transcribing"
    | "speakers"
    | "live"
    | "studio"
    | "empty"
  const targetStageMode: StageMode = !meeting
    ? "empty"
    : isCaptureSetup
      ? "setup"
      : isFileTranscribing
        ? "transcribing"
        : isCaptureAudioReady
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

  // Language options: setup/live → realtime model; audio-ready (after upload) → file model
  useEffect(() => {
    if (!active) return
    const preferRealtime =
      displayStageMode === "setup" ||
      displayStageMode === "live" ||
      displayStageMode === "empty"
    void refreshLanguageHintsFromActiveProvider({
      preferRealtime,
      meetingId: activeMeeting,
    })
  }, [
    active,
    activeMeeting,
    displayStageMode,
    refreshLanguageHintsFromActiveProvider,
  ])

  /**
   * Hot words follow the path-appropriate adapter (same as language hints).
   * Setup / live / empty → realtime model. Audio / re-tx / studio → file model.
   * Local ONNX adapters set supports_hot_words=false; do not OR the other path.
   */
  const activeHotWordsSupported =
    displayStageMode === "setup" ||
    displayStageMode === "empty" ||
    displayStageMode === "live"
      ? rtSupportsHotWords
      : fileSupportsHotWords

  const captureAudioUrl =
    meeting?.audio_path
      ? `/api/meetings/${meeting.id}/audio?v=${audioVersion}`
      : null

  /** LIVE chip only while this meeting is actually capturing with realtime. */
  const studioPartialText =
    !!recorder.isRecording &&
    recordingMeetingId === meeting?.id &&
    realtimeEnabled
      ? transcription.currentPartial
      : ""

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
    const start = () => {
      setStudioUnlocked((prev) => ({ ...prev, [meeting.id]: true }))
      const hasExistingSummary =
        !!meeting.tabs?.some((t) => !!(t as { md_file_path?: string }).md_file_path) ||
        !!(meeting.blueprint && meeting.blueprint.length > 0)
      if (!hasExistingSummary) {
        startBlueprintStream(meeting.id)
      }
    }
    void commitMeetingSpeakers(meeting.id)
      .then((m) => {
        applyMeeting(m)
        start()
      })
      .catch(() => start())
  }, [meeting, applyMeeting])

  const handlePersonAssigned = useCallback(
    (m: Meeting) => {
      applyMeeting(m)
      void import("@/components/ui/tiptap-editor").then((mod) => {
        mod.invalidateMeetingSpeakerCache(m.id)
      })
    },
    [],
  )

  const emptyUploadRef = useRef<HTMLInputElement>(null)
  const formatRecTime = (seconds: number) => {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`
  }

  // ── Interrupted-capture auto-recovery (page refreshed mid-recording) ──
  // The PCM is already fsynced server-side every 500ms and the transcript
  // checkpoints via partial save. On load, finalize the orphan immediately
  // (recovery also resets status → created): the meeting then reopens in the
  // audio-ready state — same UI as an uploaded recording, user decides whether
  // to transcribe. No banner, no manual "finish" step.
  const recoveryAttemptedRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (recordingMeetingId) return
    const orphan = meetings.find(
      (m) =>
        !sessionOwnedRecordingIdsRef.current.has(m.id) &&
        !m.audio_path &&
        !recoveryAttemptedRef.current.has(m.id) &&
        (m.status === "recording" ||
          // Orphans captured before the server marked status='recording':
          // the PCM + transcript checkpoint landed but status stayed
          // 'created' (audio only ever appears via finalize/upload).
          (m.status === "created" && !!m.transcript_path)),
    )
    if (!orphan) return
    recoveryAttemptedRef.current.add(orphan.id)
    void finalizeMeetingRecording(orphan.id, true)
      .then((m) => {
        applyMeeting(m)
        void fetchMeetings()
      })
      .catch(() => {
        // Leave it visible for the next page load to retry.
        recoveryAttemptedRef.current.delete(orphan.id)
      })
  }, [meetings, recordingMeetingId, applyMeeting, fetchMeetings])

  return (
    <div className="pm-meeting">
      <div className="pm-meeting-body">
        <MeetingList
          meetings={meetings}
          activeMeeting={activeMeeting}
          recordingMeetingId={recordingMeetingId}
          keepFocusOnCreate={!!(recorder.isRecording || recorder.isPaused)}
          onSelect={(id: string) => {
            setActiveGroup(null)
            setRailTab("meetings")
            handleSelectMeeting(id)
          }}
          onCreated={(id: string, opts?: { stayOnCurrent?: boolean }) => {
            void fetchMeetings()
            setRailTab("meetings")
            // While capturing, never switch the stage onto the new meeting —
            // that looked like the live session was “overwritten”.
            if (opts?.stayOnCurrent || recorder.isRecording || recorder.isPaused) {
              return
            }
            setActiveGroup(null)
            setActiveMeeting(id)
          }}
          onDelete={handleDelete}
          railTab={railTab}
          onRailTab={(tab) => {
            setRailTab(tab)
            if (tab === "groups") void fetchGroups()
          }}
          groups={groups}
          activeGroup={activeGroup}
          onSelectGroup={(id) => {
            setRailTab("groups")
            setActiveGroup(id)
            setActiveMeeting(null)
          }}
          onDeleteGroup={(id) => {
            setDeleteGroupTarget(id)
          }}
        />

        <div className="pm-meeting-stage">
          <div className={cn("pm-meeting-stage-kind", !kindOpaque && "is-exiting")}>
          {paintGroupId ? (
            <div
              className={cn(
                "h-full min-h-0 flex-1",
                !paintGroupStage && "hidden",
              )}
              aria-hidden={!paintGroupStage}
            >
            <MeetingGroupStage
              groupId={paintGroupId}
              meetings={meetings}
              onOpenMeeting={handleSelectMeeting}
              onGroupChanged={() => { void fetchGroups() }}
              onMeetingsChanged={fetchMeetings}
            />
            </div>
          ) : null}
          {!paintGroupStage ? (
          <>
          {/*
            Steady stage:
              Left: title chrome + Summary/Notes content card
              Right: player card + side pills (Sections | Transcript | Speaker | Group) / QC
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
              (displayStageMode === "setup" ||
                displayStageMode === "audio" ||
                displayStageMode === "transcribing") && "is-mode-empty",
              (displayStageMode === "speakers" || displayStageMode === "live") && "is-mode-live",
              (displayStageMode === "setup" ||
                displayStageMode === "audio" ||
                displayStageMode === "transcribing" ||
                displayStageMode === "speakers" ||
                displayStageMode === "live") && "is-mode-capture",
            )}
          >
            {meeting && displayStageMode !== "studio" && displayStageMode !== "empty" ? (
              <MeetingCaptureStages
                mode={displayStageMode}
                meeting={meeting}
                languageHints={languageHints}
                updateLanguageHints={updateLanguageHints}
                supportedLanguageHints={supportedLanguageHints}
                maxLanguageHints={maxLanguageHints}
                supportsTranslation={supportsTranslation}
                translationEnabled={translationEnabled}
                translationTarget={translationTarget}
                setTranslationEnabled={updateTranslationEnabled}
                setTranslationTarget={updateTranslationTarget}
                activeHotWordsSupported={activeHotWordsSupported}
                handleSelectHotWordsLibraries={handleSelectHotWordsLibraries}
                emptyUploadRef={emptyUploadRef}
                handleUploadAudio={handleUploadAudio}
                startLiveChipOpen={startLiveChipOpen}
                hasRealtimeProvider={hasRealtimeProvider}
                openStartLiveChip={openStartLiveChip}
                scheduleCloseStartLiveChip={scheduleCloseStartLiveChip}
                handleStartRecording={handleStartRecording}
                realtimeEnabled={realtimeEnabled}
                setRealtimeEnabled={setRealtimeEnabled}
                recorderError={recorder.error}
                captureAudioUrl={captureAudioUrl}
                audioVersion={audioVersion}
                handleTranscribe={handleTranscribe}
                hasFileProvider={hasFileProvider}
                handleCancelTranscribe={handleCancelTranscribe}
                speakersNeedNames={speakersNeedNames}
                namedSpeakerCount={namedSpeakerCount}
                speakerIdsInTranscript={speakerIdsInTranscript}
                displaySegments={displaySegments}
                handleTranscriptSegmentClick={handleTranscriptSegmentClick}
                handleSpeakerSampleClick={handleSpeakerSampleClick}
                onPersonAssigned={handlePersonAssigned}
                capturePlayerRef={capturePlayerRef}
                setPlaybackTime={setPlaybackTime}
                playbackTime={playbackTime}
                handleHotWordsDraftChange={handleHotWordsDraftChange}
                handleEnterStudio={handleEnterStudio}
                recorderLevels={recorder.levels ?? []}
                recorderDuration={recorder.duration}
                recorderIsPaused={!!recorder.isPaused}
                formatRecTime={formatRecTime}
                handleStopRecording={handleStopRecording}
                handleDiscard={handleDiscard}
                liveSegments={transcription.segments}
                livePartial={transcription.currentPartial}
                livePartialTranslation={transcription.currentPartialTranslation}
                liveSummaryEnabled={transcription.liveSummaryEnabled}
                liveSummaryState={transcription.liveSummaryState}
                liveSummaryError={transcription.liveSummaryError}
                liveSummaryEngine={transcription.liveSummaryEngine}
                onToggleLiveSummary={transcription.setLiveSummaryEnabled}
                onResetLiveSummary={transcription.resetLiveSummary}
                handleSegmentClick={handleSegmentClick}
                liveNotes={notes.draft}
                handleLiveNotesChange={notes.change}
                notesStatus={notes.status}
                notesRailOpen={notesRailOpen}
                onToggleNotesRail={toggleNotesRail}
                pauseRecording={recorder.pauseRecording}
                resumeRecording={recorder.resumeRecording}
              />
            ) : meeting && displayStageMode === "studio" ? (
              <MeetingStudioStage
                meeting={meeting}
                collections={collections}
                setActiveCollection={setActiveCollection}
                setSidebarView={setSidebarView}
                mainAreaRef={mainAreaRef}
                meetingContentRef={meetingContentRef}
                mediaBarRef={mediaBarRef}
                txPeekOpen={txPeekOpen}
                setTxPeekOpen={setTxPeekOpen}
                editingTitle={editingTitle}
                setEditingTitle={setEditingTitle}
                titleDraft={titleDraft}
                setTitleDraft={setTitleDraft}
                handleSaveTitle={handleSaveTitle}
                handleStartEditTitle={handleStartEditTitle}
                metaCreated={metaCreated}
                metaSpeakers={metaSpeakers}
                hasFileProvider={hasFileProvider}
                handleMeetingUpdate={handleMeetingUpdate}
                notesContent={notes.draft}
                onNotesChange={notes.change}
                handleSegmentClick={handleSegmentClick}
                requestSideTab={requestSideTab}
                setQuickChatOpen={setQuickChatOpen}
                setActiveSectionTag={setActiveSectionTag}
                displaySegments={displaySegments}
                setFocusRef={setFocusRef}
                partialText={studioPartialText}
                focusRef={focusRef}
                activeSectionTag={activeSectionTag}
                transcriptJumpCounter={transcriptJumpCounter}
                playbackTime={playbackTime}
                selectedSummaryId={selectedSummaryId}
                setSelectedSummaryId={setSelectedSummaryId}
                setSectionRailModel={setSectionRailModel}
                bindSectionRailActions={bindSectionRailActions}
                setMainTab={setMainTab}
                audioVersion={audioVersion}
                recordingMeetingId={recordingMeetingId}
                recorderDuration={recorder.duration}
                recorderLevels={recorder.levels}
                recorderIsRecording={!!recorder.isRecording}
                recorderIsPaused={!!recorder.isPaused}
                recorderError={recorder.error}
                pauseRecording={recorder.pauseRecording}
                resumeRecording={recorder.resumeRecording}
                handleUploadAudio={handleUploadAudio}
                handleStartRecording={handleStartRecording}
                handleStopRecording={handleStopRecording}
                handleTranscribe={handleTranscribe}
                setRetranscribeConfirmOpen={setRetranscribeConfirmOpen}
                handleCancelTranscribe={handleCancelTranscribe}
                hasRealtimeProvider={hasRealtimeProvider}
                realtimeEnabled={realtimeEnabled}
                setRealtimeEnabled={setRealtimeEnabled}
                activeHotWordsSupported={activeHotWordsSupported}
                handleSelectHotWordsLibraries={handleSelectHotWordsLibraries}
                handleHotWordsDraftChange={handleHotWordsDraftChange}
                languageHints={languageHints}
                supportedLanguageHints={supportedLanguageHints}
                updateLanguageHints={updateLanguageHints}
                maxLanguageHints={maxLanguageHints}
                setPlaybackTime={setPlaybackTime}
                handleDiscard={handleDiscard}
                sideRailOpen={sideRailOpen}
                sideSurfaceDisplay={sideSurfaceDisplay}
                sideSurfaceExiting={sideSurfaceExiting}
                sideTab={sideTab}
                setSideTab={setSideTab}
                onOpenGroup={(id) => {
                  setRailTab("groups")
                  setActiveGroup(id)
                  setActiveMeeting(null)
                }}
                onGroupsChanged={() => void fetchGroups()}
                setSideRailOpenWithMotion={setSideRailOpenWithMotion}
                setSideRailOpen={setSideRailOpen}
                handleRefClick={handleRefClick}
                sectionRailCardRef={sectionRailCardRef}
                sectionRailModel={sectionRailModel}
                sectionRailActionsRef={sectionRailActionsRef}
                hideSectionTip={hideSectionTip}
                activeSectionRailId={activeSectionRailId}
                sectionFocusReady={sectionFocusReady}
                sectionFocus={sectionFocus}
                sectionItemRefs={sectionItemRefs}
                showSectionTip={showSectionTip}
                sectionTip={sectionTip}
                qcFabPark={qcFabPark}
                qcFabFading={qcFabFading}
                qcFabRideOut={qcFabRideOut}
                qcFabBottomFadeIn={qcFabBottomFadeIn}
                qcFabSpinPhase={qcFabSpinPhase}
                quickChatOpen={quickChatOpen}
              />
            ) : (
              <div className="pm-meeting-empty pm-meeting-empty--span">
                <p>{t("meeting.selectOrCreate")}</p>
              </div>
            )}
          </div>
          </>
          ) : null}
          </div>
        </div>
      </div>

      <MeetingViewOverlays
        deleteTarget={deleteTarget}
        setDeleteTarget={setDeleteTarget}
        confirmDelete={confirmDelete}
        deleteGroupTarget={deleteGroupTarget}
        setDeleteGroupTarget={setDeleteGroupTarget}
        confirmDeleteGroup={confirmDeleteGroup}
        retranscribeConfirmOpen={retranscribeConfirmOpen}
        setRetranscribeConfirmOpen={setRetranscribeConfirmOpen}
        handleTranscribe={handleTranscribe}
        sectionTip={sectionTip}
        sideRailOpen={sideRailOpen}
        sideTab={sideTab}
      />

    </div>
  )
}
