import { useState, useEffect, useCallback, useRef } from "react"
import { createPortal } from "react-dom"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Pencil, Check, X } from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import { useAudioRecorder } from "@/hooks/use-audio-recorder"
import { useTranscription } from "@/hooks/use-transcription"
import {
  getMeetings, getMeeting, deleteMeeting,
  uploadMeetingAudio, transcribeMeeting, cancelTranscribeMeeting,
  getMeetingTranscript, updateMeeting,
  getRealtimeTranscriptionProviders, getFileTranscriptionProviders,
  getActiveProviderInfo, getHotWordsLibraries,
  type Meeting, type TranscriptSegment, type LanguageHintOption, type HotWordsLibrarySummary,
} from "@/api/client"
import { toast } from "sonner"
import { MeetingTabs } from "./meeting-tabs"
import { TranscriptTab } from "./transcript-panel"
import { AlertCircle, Settings, X as XIcon } from "lucide-react"
import { MeetingList } from "./meeting-list"
import { MediaBar } from "./media-bar"
import type { MediaBarHandle } from "./media-bar"

import { DEFAULT_LANGUAGE_HINTS } from "./language-hints-selector"

export function MeetingView() {
  const { activeMeeting, setActiveMeeting, setSidebarView, fetchCollections, collections, setActiveCollection } = useAppStore()

  // Data
  const [meetings, setMeetings] = useState<Meeting[]>([])
  const [meeting, setMeeting] = useState<Meeting | null>(null)
  const meetingContentRef = useRef<HTMLDivElement>(null)
  const [transcript, setTranscript] = useState<TranscriptSegment[]>([])

  // Guard against stale fetchMeeting results after activeMeeting changes
  const fetchMeetingIdRef = useRef<string | null>(null)

  // UI state
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [realtimeEnabled, setRealtimeEnabled] = useState(false)
  const [hasRealtimeProvider, setHasRealtimeProvider] = useState(false)
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
  const [focusRef, setFocusRef] = useState<{ id: string; ts: number } | null>(null)
  const [activeSectionTag, setActiveSectionTag] = useState("")
  const [floatingOpen, setFloatingOpen] = useState(false)
  const floatingPanelRef = useRef<HTMLDivElement>(null)
  const [playbackTime, setPlaybackTime] = useState(0)

  // When the main content area is wide enough, we left-shift the centered column
  // and absolutely position the floating panel (current "balanced" design).
  // When too narrow, the panel becomes a flex sibling so the content column
  // can yield/compress instead of overflowing.
  const mainAreaRef = useRef<HTMLDivElement>(null)
  const [canShift, setCanShift] = useState(true)
  // Hide the metadata block (CREATED/SPEAKERS/COLLECTIONS) when the main area
  // is too narrow to fit it next to the title without crowding.
  const [showMetadata, setShowMetadata] = useState(true)
  useEffect(() => {
    const el = mainAreaRef.current
    if (!el) return
    const update = () => {
      const w = el.clientWidth
      setCanShift(w >= 1000)
      setShowMetadata(w >= 900)
    }
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [meeting?.id])

  // Open floating transcript when sentence reference is clicked
  useEffect(() => {
    if (focusRef) setFloatingOpen(true)
  }, [focusRef?.ts])

  // Close floating panel when switching meetings
  useEffect(() => {
    setFloatingOpen(false)
  }, [activeMeeting])

  // Track floating panel position on scroll — direct DOM manipulation for smooth 60fps
  useEffect(() => {
    if (!floatingOpen || !canShift) return
    let raf = 0
    const panel = floatingPanelRef.current
    const update = () => {
      if (raf || !panel) return
      raf = requestAnimationFrame(() => {
        raf = 0
        const contentRect = meetingContentRef.current?.getBoundingClientRect()
        if (!contentRect || !panel) return
        const titleBottom = document.querySelector('[data-meeting-title]')?.getBoundingClientRect().bottom ?? 56
        const tabBarBottom = titleBottom + 36
        const top = Math.max(contentRect.top, tabBarBottom)
        panel.style.top = `${top}px`
        panel.style.left = `${contentRect.right + 20}px`
      })
    }
    update()
    window.addEventListener("scroll", update, { passive: true, capture: true })
    return () => window.removeEventListener("scroll", update, { capture: true })
  }, [floatingOpen, canShift])

  // Hooks
  const transcription = useTranscription(activeMeeting)
  const recorder = useAudioRecorder(realtimeEnabled && hasRealtimeProvider ? transcription.sendAudioData : undefined)
  const mediaBarRef = useRef<MediaBarHandle>(null)

  // Keep transcription.durationRef in sync with recording duration so
  // toggle-reopen timestamps land at current position, not at 0.
  transcription.durationRef.current = recorder.duration

  // Auto-open floating transcript panel when live transcription starts
  const prevIsTranscribingRef = useRef(false)
  useEffect(() => {
    if (realtimeEnabled && hasRealtimeProvider) {
      // Open on the rising edge: false → true transition
      if (transcription.isTranscribing && !prevIsTranscribingRef.current) {
        setFloatingOpen(true)
      }
    }
    prevIsTranscribingRef.current = transcription.isTranscribing
  }, [realtimeEnabled, hasRealtimeProvider, transcription.isTranscribing])

  // When realtime transcription finalizes (user stops recording), the hook
  // persists segments to the backend. Refetch the meeting so the new
  // transcript_path / status flip the Summarize + Allocate buttons visible.
  const transcriptionRef = useRef(transcription)
  transcriptionRef.current = transcription
  useEffect(() => {
    if (!activeMeeting) return
    transcriptionRef.current.setOnFinalized(() => {
      fetchMeeting(activeMeeting)
      fetchMeetings()
    })
    return () => { transcriptionRef.current.setOnFinalized(null) }
  }, [activeMeeting]) // eslint-disable-line react-hooks/exhaustive-deps

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
  useEffect(() => {
    if (!hasRealtimeProvider) return

    const shouldTranscribe = recorder.isRecording && realtimeEnabled

    if (shouldTranscribe && !transcription.isTranscribing) {
      transcription.startTranscription(["auto"])
    } else if (!shouldTranscribe && transcription.isTranscribing) {
      transcription.stopTranscription()
    }
  }, [recorder.isRecording, hasRealtimeProvider, realtimeEnabled, transcription.isTranscribing])

  // Fetch meetings list
  const fetchMeetings = useCallback(async () => {
    try {
      const list = await getMeetings()
      setMeetings(list)
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
      const busy = m.processing_state && m.processing_state !== "idle"
      if (busy) {
        if (pollingRef.current) clearInterval(pollingRef.current)
        pollingRef.current = setInterval(async () => {
          try {
            const updated = await getMeeting(id)
            // Guard: user may have switched meetings while polling
            if (fetchMeetingIdRef.current !== id) { clearInterval(pollingRef.current!); return }
            setMeeting(updated)
            const stillBusy = updated.processing_state && updated.processing_state !== "idle"
            if (!stillBusy) {
              clearInterval(pollingRef.current!)
              pollingRef.current = null
              fetchMeetings()
              // Re-fetch transcript to pick up section_tags from extract
              fetchTranscript(id)
            }
          } catch { /* ignore */ }
        }, 2000)
      }
    } catch { /* ignore */ }
  }, [])

  // Fetch transcript
  const fetchTranscript = useCallback(async (id: string) => {
    try {
      const res = await getMeetingTranscript(id)
      setTranscript(res.segments)
    } catch {
      setTranscript([])
    }
  }, [])

  // Check for transcription providers on mount
  useEffect(() => {
    getRealtimeTranscriptionProviders()
      .then((providers) => setHasRealtimeProvider(providers.some((p) => p.is_active)))
      .catch(() => setHasRealtimeProvider(false))
    getFileTranscriptionProviders()
      .then((providers) => setHasFileProvider(providers.some((p) => p.is_active)))
      .catch(() => setHasFileProvider(false))
  }, [])

  // Load meetings and hot words on mount
  useEffect(() => {
    fetchMeetings()
    getHotWordsLibraries()
      .then(setHotWordsLibraries)
      .catch(() => setHotWordsLibraries([]))
  }, [fetchMeetings])

  // Load meeting detail when active changes
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
      setMeeting(null)
      setTranscript([])
      fetchMeeting(activeMeeting)
      fetchTranscript(activeMeeting)
    } else {
      setMeeting(null)
      setTranscript([])
    }
  }, [activeMeeting, fetchMeeting, fetchTranscript])

  // Poll for status changes during transcribing
  useEffect(() => {
    if (meeting?.status === "transcribing" && activeMeeting) {
      pollingRef.current = setInterval(() => {
        fetchMeeting(activeMeeting)
        fetchTranscript(activeMeeting)
      }, 2000)
      return () => { if (pollingRef.current) clearInterval(pollingRef.current) }
    }
    if (pollingRef.current) {
      clearInterval(pollingRef.current)
      pollingRef.current = null
    }
  }, [meeting?.status, activeMeeting, fetchMeeting, fetchTranscript])

  // Fetch transcript when transcription completes
  useEffect(() => {
    if (meeting?.status === "completed" && activeMeeting) {
      fetchTranscript(activeMeeting)
    }
  }, [meeting?.status, activeMeeting, fetchTranscript])

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
  useEffect(() => {
    if (recorder.audioBlob && activeMeeting) {
      const file = new File([recorder.audioBlob], "recording.webm", { type: recorder.audioBlob.type })
      uploadMeetingAudio(activeMeeting, file)
        .then((m) => {
          setMeeting(m)
          setAudioVersion((v) => v + 1)
          toast.success("Audio uploaded")
          recorder.reset()
          fetchMeetings()

          // Auto-trigger file transcription if a cloud/file provider is configured.
          // This replaces the realtime WebSocket segments with a full-quality
          // transcription (speaker diarization, punctuation, etc.) from the
          // configured cloud API, then proceeds to Summary & Section generation.
          if (hasFileProvider) {
            transcribeMeeting(activeMeeting, languageHintsRef.current)
              .then(() => {
                toast.info("File transcription started")
                fetchMeeting(activeMeeting)
              })
              .catch((err) => toast.error(`Transcription failed: ${err instanceof Error ? err.message : String(err)}`))
          }
        })
        .catch((err) => toast.error(`Upload failed: ${err instanceof Error ? err.message : String(err)}`))
    }
  }, [recorder.audioBlob])

  // Handlers
  const handleUploadAudio = async (file: File) => {
    if (!activeMeeting) return
    try {
      const m = await uploadMeetingAudio(activeMeeting, file)
      setMeeting(m)
      setAudioVersion((v) => v + 1)
      toast.success("Audio uploaded")
      fetchMeetings()
    } catch (err) {
      toast.error(`Upload failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleTranscribe = async () => {
    if (!activeMeeting) return
    if (!hasFileProvider) {
      toast.error("No transcription provider configured. Go to Settings → Transcription to set one up.", {
        action: { label: "Settings", onClick: () => setSidebarView("llm_provider") },
      })
      return
    }
    // Clear realtime segments so new transcript shows after completion
    transcription.setSegments([])
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
      fetchMeeting(activeMeeting)
      toast.info("Transcription cancelled")
    } catch (err) {
      toast.error(`Cancel failed: ${err instanceof Error ? err.message : String(err)}`)
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

  const handleSegmentClick = (startTime: number) => {
    mediaBarRef.current?.seekTo(startTime)
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

  return (
    <div className="h-full flex [&_button]:font-[350] [&_button]:uppercase [&_button]:tracking-[0.08em]">
      <MeetingList
        meetings={meetings}
        activeMeeting={activeMeeting}
        onSelect={handleSelectMeeting}
        onCreated={(id) => { fetchMeetings(); setActiveMeeting(id) }}
        onDelete={handleDelete}
      />

      <div ref={mainAreaRef} className="flex-1 overflow-hidden" key={activeMeeting || "empty"}>
        {meeting ? (
          <div className="h-full flex flex-col animate-tab-in">
            {/* Header + Media Bar + Content share the same centered width and left-shift (wide mode only). */}
            <div className={cn(
              "flex-1 flex flex-col min-h-0 max-w-[800px] mx-auto w-full transition-transform duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]",
              floatingOpen && canShift ? "-translate-x-[196px]" : "translate-x-0",
            )}>
              {/* Header — sticky title on the left, metadata (CREATED/SPEAKERS/COLLECTIONS) on the right */}
              <div data-meeting-title className="flex items-start justify-between gap-4 px-4 pt-3 shrink-0 sticky top-0 z-20 bg-background">
              {editingTitle ? (
                <div className="flex items-center gap-1 flex-1 min-w-0">
                  <input
                    className="flex-1 text-sm font-light bg-transparent border-b border-primary outline-none px-0 py-0.5 min-w-0"
                    value={titleDraft}
                    onChange={(e) => setTitleDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleSaveTitle()
                      if (e.key === "Escape") setEditingTitle(false)
                    }}
                    autoFocus
                  />
                  <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0" onClick={handleSaveTitle}>
                    <Check className="h-4 w-4" />
                  </Button>
                  <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0" onClick={() => setEditingTitle(false)}>
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              ) : (
                <div className="flex items-start gap-1 min-w-0 flex-1">
                  <h2
                    className="t-body-family"
                    style={{
                      fontSize: "clamp(20px, 2vw, 24px)",
                      fontWeight: 300,
                      letterSpacing: "-0.01em",
                      lineHeight: 1.35,
                      color: "var(--ze-ink)",
                    }}
                  >
                    {meeting.title}
                  </h2>
                  <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0 opacity-60 hover:opacity-100 mt-0.5" onClick={handleStartEditTitle}>
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                </div>
              )}

              {/* Metadata stack on the right side of the title row */}
              {showMetadata && (
              <div className="flex flex-col gap-0.5 text-[11px] text-muted-foreground text-right shrink-0 pt-[8px]">
                <div className="flex items-center justify-end gap-2">
                  <span className="font-semibold uppercase tracking-[0.12em] text-foreground/50 text-[10px]">CREATED</span>
                  <span>
                    {meeting.created_at
                      ? new Date(meeting.created_at).toLocaleDateString("en-US", {
                          month: "short", day: "numeric", year: "numeric",
                          hour: "2-digit", minute: "2-digit",
                        })
                      : "—"}
                  </span>
                </div>
                <div className="flex items-center justify-end gap-2">
                  <span className="font-semibold uppercase tracking-[0.12em] text-foreground/50 text-[10px]">SPEAKERS</span>
                  <span>
                    {(() => {
                      const named = meeting.speaker_names ? Object.values(meeting.speaker_names).filter(Boolean) : []
                      if (named.length > 0) return named.join(", ")
                      const count = new Set(transcript.map(s => s.speaker_id).filter(Boolean)).size
                      return `${count || 0} speaker${count !== 1 ? "s" : ""}`
                    })()}
                  </span>
                </div>
                <div className="flex items-center justify-end gap-2">
                  <span className="font-semibold uppercase tracking-[0.12em] text-foreground/50 text-[10px]">COLLECTIONS</span>
                  <span>
                    {(() => {
                      const cols = meeting.allocated_collections
                      if (!cols || cols.length === 0) return <span className="text-muted-foreground">—</span>
                      return [...new Set(cols)].map((id, i) => (
                        <span key={id}>
                          <button
                            className="hover:text-primary hover:underline"
                            onClick={() => {
                              setActiveCollection(id)
                              setSidebarView("database")
                              setTimeout(() => window.dispatchEvent(new CustomEvent("show-meeting-log")), 100)
                            }}
                          >
                            {collections.find((x: any) => x.id === id)?.name || id}
                          </button>
                          {i < [...new Set(cols)].length - 1 ? ", " : ""}
                        </span>
                      ))
                    })()}
                  </span>
                </div>
              </div>
              )}
            </div>

            {/* ── Scroll container: everything below the sticky title scrolls together ── */}
            <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden">

            {/* TOPICS — own row below the title row, left-aligned, width capped to heading+button */}
            <div className="flex items-start justify-between gap-4 px-4 pb-1 pt-4 shrink-0 text-[11px] text-muted-foreground">
              <span className="flex flex-wrap gap-x-1.5 gap-y-0.5 flex-1 min-w-0 text-left">
                {(() => {
                  const blueprint = meeting.blueprint ?? []
                  const filtered = blueprint.filter((b: any) => b.tab_name?.toLowerCase() !== "other")
                  if (filtered.length === 0) return <span className="text-muted-foreground">—</span>
                  return filtered.map((b: any, i: number) => (
                    <span key={b.blueprint_id} className="whitespace-nowrap">
                      {b.tab_name}{i < filtered.length - 1 ? " |" : ""}
                    </span>
                  ))
                })()}
              </span>
              {/* Invisible spacer matching metadata width so topics stays within heading+button area */}
              {showMetadata && (
                <div className="shrink-0 invisible flex flex-col gap-0.5 text-right" aria-hidden="true">
                  <div className="flex items-center justify-end gap-2">
                    <span className="font-semibold uppercase tracking-[0.12em] text-foreground/50 text-[10px]">CREATED</span>
                    <span>
                      {meeting.created_at
                        ? new Date(meeting.created_at).toLocaleDateString("en-US", {
                            month: "short", day: "numeric", year: "numeric",
                            hour: "2-digit", minute: "2-digit",
                          })
                        : "—"}
                    </span>
                  </div>
                  <div className="flex items-center justify-end gap-2">
                    <span className="font-semibold uppercase tracking-[0.12em] text-foreground/50 text-[10px]">SPEAKERS</span>
                    <span>
                      {(() => {
                        const named = meeting.speaker_names ? Object.values(meeting.speaker_names).filter(Boolean) : []
                        if (named.length > 0) return named.join(", ")
                        const count = new Set(transcript.map(s => s.speaker_id).filter(Boolean)).size
                        return `${count || 0} speaker${count !== 1 ? "s" : ""}`
                      })()}
                    </span>
                  </div>
                  <div className="flex items-center justify-end gap-2">
                    <span className="font-semibold uppercase tracking-[0.12em] text-foreground/50 text-[10px]">COLLECTIONS</span>
                    <span>
                      {(() => {
                        const cols = meeting.allocated_collections
                        if (!cols || cols.length === 0) return <span className="text-muted-foreground">—</span>
                        return [...new Set(cols)].map((id, i) => (
                          <span key={id}>
                            {collections.find((x: any) => x.id === id)?.name || id}
                            {i < [...new Set(cols)].length - 1 ? ", " : ""}
                          </span>
                        ))
                      })()}
                    </span>
                  </div>
                </div>
              )}
            </div>

            {/* Media Bar — full width */}
            <div className="px-4 pt-1 pb-2">
              <MediaBar
                ref={mediaBarRef}
                meetingId={meeting.id}
                status={meeting.status}
                hasAudio={!!meeting.audio_path}
                audioPath={meeting.audio_path}
                audioUrl={meeting.audio_path ? `/api/meetings/${meeting.id}/audio?v=${audioVersion}` : null}
                audioVersion={audioVersion}
                duration={recorder.duration}
                isRecording={recorder.isRecording}
                isPaused={recorder.isPaused}
                transcriptionError={meeting.transcription_error}
                onUploadAudio={handleUploadAudio}
                onStartRecord={recorder.startRecording}
                onStopRecord={recorder.stopRecording}
                onPauseRecord={recorder.pauseRecording}
                onResumeRecord={recorder.resumeRecording}
                onTranscribe={handleTranscribe}
                onReTranscribe={(transcript.length > 0 || meeting.transcript_path || transcription.segments.length > 0) ? () => {
                  setRetranscribeConfirmOpen(true)
                } : undefined}
                onCancelTranscribe={meeting.status === "transcribing" ? handleCancelTranscribe : undefined}
                hasRealtimeProvider={hasRealtimeProvider}
                realtimeEnabled={realtimeEnabled}
                onToggleRealtime={() => setRealtimeEnabled(v => !v)}
                hasTranscript={transcript.length > 0 || transcription.segments.length > 0}
                hotWordsLibraryId={meeting.hot_words_library_id}
                hotWordsLibraries={hotWordsLibraries}
                onSelectHotWords={handleSelectHotWordsLibrary}
                languageHints={languageHints}
                languageHintOptions={supportedLanguageHints}
                onChangeLanguageHints={updateLanguageHints}
                showLanguageSelector={!!meeting.audio_path}
                onTimeUpdate={setPlaybackTime}
                recorderError={recorder.error}
              />
            </div>

              {/* Provider warning */}
            {!hasFileProvider && meeting.audio_path && (
              <div className="mx-4 mt-1 flex items-center gap-2 px-3 py-2 text-sm border border-amber-200 dark:border-amber-800 rounded-lg text-amber-700 dark:text-amber-300">
                <AlertCircle className="h-4 w-4 shrink-0" />
                <span className="flex-1">No transcription provider configured.</span>
                <Button variant="outline" size="sm" className="h-7 text-xs" onClick={() => setSidebarView("llm_provider")}>
                  <Settings className="h-3 w-3 mr-1" /> Settings
                </Button>
              </div>
            )}

            {/* Content: MeetingTabs + narrow floating panel */}
            <div ref={meetingContentRef} className="flex w-full">
              <MeetingTabs
                key={meeting.id}
                meetingId={meeting.id}
                meeting={meeting}
                notesContent={meeting.notes_content ?? ""}
                onMeetingUpdate={handleMeetingUpdate}
                onSeekTo={handleSegmentClick}
                onFocusSentence={(id) => { setFocusRef({ id, ts: Date.now() }); setFloatingOpen(true) }}
                onActiveTabChange={setActiveSectionTag}
                transcriptSegments={transcription.segments.length > 0 ? transcription.segments : transcript}
                partialText={transcription.currentPartial}
                focusRef={focusRef}
                activeSectionTag={activeSectionTag}
                floatingPanelOpen={floatingOpen}
                canShift={canShift}
                playbackTime={playbackTime}
                className="flex-1 min-w-0"
              />
              {/* Narrow mode: floating panel as a flex sibling so the content column yields. */}
              {floatingOpen && !canShift && (
                <div className="shrink-0 flex flex-col animate-slide-up py-5 pl-5" style={{ width: "min(320px, 55vw)" }}>
                  <div className="flex flex-col flex-1 min-h-0 border-l border-primary/45 bg-transparent">
                    <div className="flex items-center justify-between px-3 h-9 pb-2 shrink-0">
                      <span className="text-xs font-light uppercase tracking-[0.15em] text-muted-foreground whitespace-nowrap">Transcript</span>
                      <button
                        onClick={() => setFloatingOpen(false)}
                        className="h-7 w-7 flex items-center justify-center rounded-sm text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                      >
                        <XIcon className="h-3.5 w-3.5" />
                      </button>
                    </div>
                    <div className="flex-1 min-h-0 overflow-y-auto">
                      <TranscriptTab
                        segments={transcription.segments.length > 0 ? transcription.segments : transcript}
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
                </div>
              )}
            </div>
            </div>
            </div>

            {/* Wide-mode floating transcript panel — Portal to avoid scroll-container clipping */}
            {floatingOpen && canShift && createPortal(
              <div ref={floatingPanelRef} className="fixed z-30 flex flex-col animate-slide-up py-5" style={{
                width: "min(320px, calc(100vw - 520px))",
                top: 0,
                left: 0,
                bottom: 0,
              }}>
                <div className="flex flex-col flex-1 min-h-0 border-l border-primary/45 bg-background/95 backdrop-blur-sm">
                  <div className="flex items-center justify-between px-3 h-9 pb-2 shrink-0">
                    <span className="text-xs font-light uppercase tracking-[0.15em] text-muted-foreground whitespace-nowrap">Transcript</span>
                    <button
                      onClick={() => setFloatingOpen(false)}
                      className="h-7 w-7 flex items-center justify-center rounded-sm text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                    >
                      <XIcon className="h-3.5 w-3.5" />
                    </button>
                  </div>
                  <div className="flex-1 min-h-0 overflow-y-auto">
                    <TranscriptTab
                      segments={transcription.segments.length > 0 ? transcription.segments : transcript}
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
              </div>, document.body
            )}

          </div>
        ) : (
          <div className="flex items-center justify-center h-full text-muted-foreground animate-tab-in">
            <div className="text-center">
              <p className="text-sm t-body-family">Select a meeting or create one</p>
            </div>
          </div>
        )}
      </div>

      {/* Dialogs */}

      <Dialog open={!!deleteTarget} onOpenChange={(v) => !v && setDeleteTarget(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete Meeting</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Are you sure you want to delete this meeting?
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>Cancel</Button>
            <Button variant="destructive" onClick={confirmDelete}>Delete</Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={retranscribeConfirmOpen} onOpenChange={setRetranscribeConfirmOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Re-transcribe Meeting</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Re-transcribing will overwrite the existing transcript and speaker names.
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setRetranscribeConfirmOpen(false)}>Cancel</Button>
            <Button onClick={() => { setRetranscribeConfirmOpen(false); handleTranscribe() }}>Continue</Button>
          </div>
        </DialogContent>
      </Dialog>

    </div>
  )
}
