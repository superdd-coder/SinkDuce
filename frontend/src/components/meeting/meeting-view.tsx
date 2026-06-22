import { useState, useEffect, useCallback, useRef } from "react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Mic, Share, Pencil, Check, X, Loader2 } from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import { useAudioRecorder } from "@/hooks/use-audio-recorder"
import { useTranscription } from "@/hooks/use-transcription"
import {
  getMeetings, getMeeting, deleteMeeting,
  uploadMeetingAudio, transcribeMeeting, cancelTranscribeMeeting,
  generateMeetingSummary,
  getMeetingTranscript, updateMeeting,
  getRealtimeTranscriptionProviders, getFileTranscriptionProviders,
  getActiveProviderInfo,
  type Meeting, type TranscriptSegment, type TodoItem, type LanguageHintOption,
} from "@/api/client"
import { toast } from "sonner"
import { AlertCircle, Settings } from "lucide-react"
import { MeetingList } from "./meeting-list"
import { MediaBar } from "./media-bar"
import type { MediaBarHandle } from "./media-bar"
import { NotesEditor } from "./notes-editor"
import { TranscriptPanel } from "./transcript-panel"
import { MultiIngestDialog } from "./multi-ingest-dialog"
import { HotWordsSelector } from "./hot-words-selector"
import { LanguageHintsSelector, DEFAULT_LANGUAGE_HINTS } from "./language-hints-selector"

export function MeetingView() {
  const { activeMeeting, setActiveMeeting, setSidebarView, setActiveCollection, setNavigationGuard, ingestMeetingId, ingestProgress, collections, fetchCollections } = useAppStore()
  const isIngesting = ingestMeetingId === activeMeeting && Object.values(ingestProgress).some((s) => s === "pending")

  // Data
  const [meetings, setMeetings] = useState<Meeting[]>([])
  const [meeting, setMeeting] = useState<Meeting | null>(null)
  const [transcript, setTranscript] = useState<TranscriptSegment[]>([])

  // UI state
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [transcriptOpen, setTranscriptOpen] = useState(true)
  const [realtimeEnabled, setRealtimeEnabled] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [generatingMeetingId, setGeneratingMeetingId] = useState<string | null>(null)
  const [notesDraft, setNotesDraft] = useState("")
  const [notesSavedContent, setNotesSavedContent] = useState("")
  const [isNotesDirty, setIsNotesDirty] = useState(false)
  const [multiIngestOpen, setMultiIngestOpen] = useState(false)
  const [hasRealtimeProvider, setHasRealtimeProvider] = useState(false)
  const [hasFileProvider, setHasFileProvider] = useState(false)
  const [providerSupportsHotWords, setProviderSupportsHotWords] = useState(false)
  const [supportedLanguageHints, setSupportedLanguageHints] = useState<LanguageHintOption[]>([])
  // Per-meeting language hints: keyed by meeting ID, persists across meeting switches during the session
  const perMeetingLanguageHints = useRef<Map<string, string[]>>(new Map())
  const [languageHints, setLanguageHints] = useState<string[]>([...DEFAULT_LANGUAGE_HINTS])
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState("")
  const [audioVersion, setAudioVersion] = useState(0)
  const [regenerateConfirmOpen, setRegenerateConfirmOpen] = useState(false)
  const [retranscribeConfirmOpen, setRetranscribeConfirmOpen] = useState(false)

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
  const notesDraftRef = useRef("")
  const notesSavedContentRef = useRef("")

  // Keep refs in sync with state for stale-closure-safe comparisons
  useEffect(() => { notesDraftRef.current = notesDraft }, [notesDraft])
  useEffect(() => { notesSavedContentRef.current = notesSavedContent }, [notesSavedContent])

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
    console.trace("[fetchMeeting]", id)
    try {
      const m = await getMeeting(id)
      setMeeting(m)
      // If a background summary is in progress, resume polling
      if (m.summarizing) {
        setGenerating(true)
        setGeneratingMeetingId(id)
        const poll = setInterval(async () => {
          try {
            const updated = await getMeeting(id)
            if (!updated.summarizing) {
              clearInterval(poll)
              setMeeting(updated)
              setGenerating(false)
              setGeneratingMeetingId(null)
              fetchMeetings()
            }
          } catch { /* ignore */ }
        }, 2000)
      }
      // Determine the server-side notes content
      const serverNotes: string = m.notes_content ?? ""
      // Only reset the draft when there are no unsaved edits
      if (notesDraftRef.current === notesSavedContentRef.current) {
        setNotesDraft(serverNotes)
        setNotesSavedContent(serverNotes)
      } else {
        // User has unsaved edits – just update the saved-content baseline
        setNotesSavedContent(serverNotes)
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

  const handleGenerate = () => {
    if (!activeMeeting) return
    if (meeting?.summary) {
      setRegenerateConfirmOpen(true)
      return
    }
    doGenerate()
  }

  const doGenerate = async () => {
    if (!activeMeeting) return
    setRegenerateConfirmOpen(false)
    setGenerating(true)
    setGeneratingMeetingId(activeMeeting)
    try {
      await generateMeetingSummary(activeMeeting)
      // Async via task queue: poll until done
      const poll = setInterval(async () => {
        try {
          const m = await getMeeting(activeMeeting)
          if (m && !m.summarizing) {
            clearInterval(poll)
            setMeeting(m)
            fetchMeetings()
            setGenerating(false)
            setGeneratingMeetingId(null)
            toast.success("Summary generated")
          }
        } catch { /* ignore poll errors */ }
      }, 2000)
    } catch (err) {
      setGenerating(false)
      setGeneratingMeetingId(null)
      toast.error(`Generation failed: ${err instanceof Error ? err.message : String(err)}`)
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

  const handleSaveNotes = async (content: string) => {
    if (!activeMeeting) return
    try {
      const m = await updateMeeting(activeMeeting, { notes: content })
      setMeeting(m)
      setNotesDraft(content)
      setNotesSavedContent(content)
      setIsNotesDirty(false)
      toast.success("Notes saved")
    } catch (err) {
      toast.error(`Save failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleDiscardNotes = () => {
    setNotesDraft(notesSavedContent)
    setIsNotesDirty(false)
  }

  const hasContent = !!(meeting?.summary || meeting?.detail || (meeting?.todos && meeting.todos.length > 0) || notesDraft)

  const handleReingest = () => {
    if (!activeMeeting) return
    setMultiIngestOpen(true)
  }

  const handleUpdateGenerated = async (data: { summary?: string; detail?: string; todos?: TodoItem[] }) => {
    if (!activeMeeting) return
    const key = data.summary !== undefined ? "summary" : "detail"
    const content = data.summary ?? data.detail ?? ""
    console.log(`[Save ${key}] sent:`, JSON.stringify(content.slice(0, 300)))
    try {
      const m = await updateMeeting(activeMeeting, data)
      const returned = key === "summary" ? m.summary : m.detail
      console.log(`[Save ${key}] returned:`, JSON.stringify((returned ?? "").slice(0, 300)))
      setMeeting(m)
      toast.success("Updated")
    } catch (err) {
      toast.error(`Update failed: ${err instanceof Error ? err.message : String(err)}`)
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

  // beforeunload guard when notes dirty
  useEffect(() => {
    if (!isNotesDirty) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
    }
    window.addEventListener("beforeunload", handler)
    return () => window.removeEventListener("beforeunload", handler)
  }, [isNotesDirty])

  // Sidebar navigation guard
  useEffect(() => {
    if (isNotesDirty) {
      setNavigationGuard(() => {
        const ok = window.confirm("You have unsaved notes. Discard changes and leave?")
        if (ok) setIsNotesDirty(false)
        return ok
      })
      return () => setNavigationGuard(null)
    } else {
      setNavigationGuard(null)
    }
  }, [isNotesDirty, setNavigationGuard])

  const handleSelectMeeting = useCallback((id: string) => {
    if (isNotesDirty) {
      if (!window.confirm("You have unsaved notes. Discard changes and switch meeting?")) return
      setIsNotesDirty(false)
    }
    setActiveMeeting(id)
  }, [isNotesDirty, setActiveMeeting])

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
    <div className="h-full flex [&_button]:font-light [&_button]:uppercase [&_button]:tracking-wider [&_button]:text-xs">
      <MeetingList
        meetings={meetings}
        activeMeeting={activeMeeting}
        onSelect={handleSelectMeeting}
        onCreated={(id) => { fetchMeetings(); setActiveMeeting(id) }}
        onDelete={handleDelete}
      />

      <div className="flex-1 overflow-hidden">
        {meeting ? (
          <div className="h-full flex flex-col">
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
                  <Button
                    variant={meeting?.allocated_collections?.length ? "outline" : "default"}
                    size="sm"
                    disabled={!hasContent || isIngesting}
                    title={!hasContent ? "Add notes or generate a summary first" : undefined}
                    onClick={meeting?.allocated_collections?.length ? handleReingest : () => setMultiIngestOpen(true)}
                  >
                    {isIngesting ? (
                      <><Loader2 className="h-4 w-4 mr-1 animate-spin" /> Ingesting...</>
                    ) : (
                      <><Share className="h-4 w-4 mr-1" /> {meeting?.allocated_collections?.length ? "Re-ingest" : "Ingest"}</>
                    )}
                  </Button>
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

            {/* Notes + Transcript */}
            <div className="flex-1 flex min-h-0">
              <div className="flex-1 min-h-0 px-2">
                <NotesEditor
                  meetingId={meeting.id}
                  notesContent={notesDraft}
                  detail={meeting.detail ?? null}
                  summary={meeting.summary ?? null}
                  todos={meeting.todos ?? null}
                  hasTranscript={
                    !!meeting.transcript_path ||
                    transcript.length > 0 ||
                    transcription.segments.length > 0
                  }
                  generating={generating && generatingMeetingId === activeMeeting}
                  onSaveNotes={handleSaveNotes}
                  onDiscardNotes={handleDiscardNotes}
                  onGenerate={handleGenerate}
                  onRegenerate={handleGenerate}
                  onUpdateGenerated={handleUpdateGenerated}
                  onNotesUploaded={(content) => { setNotesDraft(content); setNotesSavedContent(content); setIsNotesDirty(false) }}
                  onDirtyChange={setIsNotesDirty}
                />
              </div>
              <TranscriptPanel
                open={transcriptOpen}
                onToggle={() => setTranscriptOpen(!transcriptOpen)}
                segments={transcription.segments.length > 0 ? transcription.segments : transcript}
                partialText={transcription.currentPartial}
                onSegmentClick={handleSegmentClick}
                speakerNames={meeting.speaker_names ?? {}}
                onUpdateSpeakerName={handleUpdateSpeakerName}
                isRealtime={transcription.isTranscribing}
              />
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-center h-full text-muted-foreground">
            <div className="text-center">
              <Mic className="h-12 w-12 mx-auto mb-3 opacity-30" />
              <p>Select a meeting or create one</p>
            </div>
          </div>
        )}
      </div>

      {/* Dialogs */}

      <MultiIngestDialog
        open={multiIngestOpen}
        onOpenChange={setMultiIngestOpen}
        meetingId={activeMeeting ?? ""}
        isReingest={(meeting?.allocated_collections?.length ?? 0) > 0}
        allocatedCollections={meeting?.allocated_collections}
        allocatedFileIds={meeting?.allocated_file_ids}
        onComplete={async () => {
          if (activeMeeting) {
            await fetchMeeting(activeMeeting)
            fetchMeetings()
          }
        }}
      />

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

      <Dialog open={regenerateConfirmOpen} onOpenChange={setRegenerateConfirmOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Re-summarize Meeting</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Re-summarizing will overwrite the existing Summary, Detail, and TODO.
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setRegenerateConfirmOpen(false)}>Cancel</Button>
            <Button onClick={doGenerate}>Continue</Button>
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
