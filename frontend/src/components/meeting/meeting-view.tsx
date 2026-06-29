import { useState, useEffect, useCallback, useRef } from "react"
import { Button } from "@/components/ui/button"
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
  getActiveProviderInfo,
  type Meeting, type TranscriptSegment, type LanguageHintOption,
} from "@/api/client"
import { toast } from "sonner"
import { MeetingTabs } from "./meeting-tabs"
import { AlertCircle, Settings } from "lucide-react"
import { MeetingList } from "./meeting-list"
import { MediaBar } from "./media-bar"
import type { MediaBarHandle } from "./media-bar"
import { TranscriptPanel } from "./transcript-panel"
import { HotWordsSelector } from "./hot-words-selector"
import { LanguageHintsSelector, DEFAULT_LANGUAGE_HINTS } from "./language-hints-selector"

export function MeetingView() {
  const { activeMeeting, setActiveMeeting, setSidebarView, setActiveCollection, collections, fetchCollections } = useAppStore()

  // Data
  const [meetings, setMeetings] = useState<Meeting[]>([])
  const [meeting, setMeeting] = useState<Meeting | null>(null)
  const [transcript, setTranscript] = useState<TranscriptSegment[]>([])

  // Guard against stale fetchMeeting results after activeMeeting changes
  const fetchMeetingIdRef = useRef<string | null>(null)

  // UI state
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [transcriptOpen, setTranscriptOpen] = useState(true)
  const [realtimeEnabled, setRealtimeEnabled] = useState(false)
  const [hasRealtimeProvider, setHasRealtimeProvider] = useState(false)
  const [hasFileProvider, setHasFileProvider] = useState(true) // optimistic — avoids flash on remount; config check corrects if needed
  const [providerSupportsHotWords, setProviderSupportsHotWords] = useState(false)
  const [supportedLanguageHints, setSupportedLanguageHints] = useState<LanguageHintOption[]>([])
  // Per-meeting language hints: keyed by meeting ID, persists across meeting switches during the session
  const perMeetingLanguageHints = useRef<Map<string, string[]>>(new Map())
  const [languageHints, setLanguageHints] = useState<string[]>([...DEFAULT_LANGUAGE_HINTS])
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState("")
  const [audioVersion, setAudioVersion] = useState(0)
  const [retranscribeConfirmOpen, setRetranscribeConfirmOpen] = useState(false)
  const [focusRef, setFocusRef] = useState<{ id: string; ts: number } | null>(null)
  const [activeSectionTag, setActiveSectionTag] = useState("")

  // Hooks
  const transcription = useTranscription(activeMeeting)
  const recorder = useAudioRecorder(realtimeEnabled && hasRealtimeProvider ? transcription.sendAudioData : undefined)
  const mediaBarRef = useRef<MediaBarHandle>(null)

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

  // Start/stop realtime transcription when recording starts/stops
  const prevRecordingRef = useRef(false)
  useEffect(() => {
    const wasRecording = prevRecordingRef.current
    prevRecordingRef.current = recorder.isRecording
    if (!hasRealtimeProvider || !realtimeEnabled) return
    if (recorder.isRecording && !wasRecording) {
      transcription.startTranscription(["auto"])
    } else if (!recorder.isRecording && wasRecording) {
      transcription.stopTranscription()
    }
  }, [recorder.isRecording, hasRealtimeProvider, realtimeEnabled])

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
      // If a background task is in progress, resume polling
      const busy = m.processing_state && m.processing_state !== "idle"
      if (busy) {
        const poll = setInterval(async () => {
          try {
            const updated = await getMeeting(id)
            // Guard: user may have switched meetings while polling
            if (fetchMeetingIdRef.current !== id) { clearInterval(poll); return }
            const stillBusy = updated.processing_state && updated.processing_state !== "idle"
            if (!stillBusy) {
              clearInterval(poll)
              setMeeting(updated)
              fetchMeetings()
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

  // Load meetings on mount
  useEffect(() => {
    fetchMeetings()
  }, [fetchMeetings])

  // Load meeting detail when active changes
  useEffect(() => {
    if (activeMeeting) {
      // Refresh provider info in case active model was changed in Settings
      getActiveProviderInfo()
        .then((info) => {
          setProviderSupportsHotWords(info.file.supports_hot_words)
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
        .catch(() => setProviderSupportsHotWords(false))
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

  // When recording stops, upload audio
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
        })
        .catch((err) => toast.error(`Upload failed: ${err}`))
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
    if (activeMeeting) fetchMeetings()
  }, [activeMeeting, fetchMeetings])

  const handleSelectMeeting = useCallback((id: string) => {
    setActiveMeeting(id)
  }, [setActiveMeeting])

  const handleUpdateSpeakerName = async (speakerId: string, name: string) => {
    if (!activeMeeting || !meeting) return
    const updated = { ...(meeting.speaker_names ?? {}), [speakerId]: name }
    try {
      const m = await updateMeeting(activeMeeting, { speaker_names: updated })
      setMeeting(m)
      toast.success(`Speaker ${speakerId} renamed to "${name}"`)
    } catch (err) {
      toast.error(`Failed to update speaker name: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

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

      <div className="flex-1 overflow-hidden" key={activeMeeting || "empty"}>
        {meeting ? (
          <div className="h-full flex flex-col animate-tab-in">
            {/* Header */}
            <div className="flex items-center justify-between px-4 h-12 border-b border-border">
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
                <div className="flex items-center gap-1 min-w-0">
                  <h2 className="text-sm font-light truncate">{meeting.title}</h2>
                  <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0 opacity-60 hover:opacity-100" onClick={handleStartEditTitle}>
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                </div>
              )}
              <div className="flex items-center gap-2">
                <HotWordsSelector
                  meetingId={meeting.id}
                  currentLibraryId={meeting.hot_words_library_id}
                  hasTranscript={!!(meeting.transcript_path || transcript.length > 0)}
                  providerSupportsHotWords={providerSupportsHotWords}
                  onSelectLibrary={handleSelectHotWordsLibrary}
                  onRetranscribe={handleTranscribe}
                />
                {meeting.audio_path && (
                  <LanguageHintsSelector
                    selected={languageHints}
                    onChange={updateLanguageHints}
                    options={supportedLanguageHints}
                  />
                )}
                {meeting.allocated_collections?.length > 0 && (
                  <span className="text-xs text-muted-foreground flex items-center gap-1">
                    In:
                    {[...new Set(meeting.allocated_collections)].map((col) => (
                      <button
                        key={col}
                        className="font-medium text-foreground hover:text-primary hover:underline"
                        onClick={() => {
                          setActiveCollection(col)
                          setSidebarView("database")
                          setTimeout(() => window.dispatchEvent(new CustomEvent("show-meeting-log")), 100)
                        }}
                      >
                        {collections.find(c => c.id === col)?.name || col}
                      </button>
                    ))}
                  </span>
                )}
              </div>
            </div>

            {/* Media Bar */}
            <div className="px-4 py-2">
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

            {/* Content: MeetingTabs + transcript */}
            <div className="flex-1 flex min-h-0">
              <MeetingTabs
                key={meeting.id}
                meetingId={meeting.id}
                meeting={meeting}
                notesContent={meeting.notes_content ?? ""}
                onMeetingUpdate={handleMeetingUpdate}
                onSeekTo={handleSegmentClick}
                onFocusSentence={(id) => setFocusRef({ id, ts: Date.now() })}
                onActiveTabChange={setActiveSectionTag}
                transcriptSegments={transcription.segments.length > 0 ? transcription.segments : transcript}
              />
              <TranscriptPanel
                key={meeting.id}
                open={transcriptOpen}
                onToggle={() => setTranscriptOpen(!transcriptOpen)}
                segments={transcription.segments.length > 0 ? transcription.segments : transcript}
                partialText={transcription.currentPartial}
                onSegmentClick={handleSegmentClick}
                focusRef={focusRef}
                activeSectionTag={activeSectionTag}
                speakerNames={meeting.speaker_names ?? {}}
                onUpdateSpeakerName={handleUpdateSpeakerName}
                isRealtime={transcription.isTranscribing}
              />
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-center h-full text-muted-foreground animate-tab-in">
            <div className="text-center">
              <p className="text-sm" style={{ fontFamily: "var(--font-serif)" }}>Select a meeting or create one</p>
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
