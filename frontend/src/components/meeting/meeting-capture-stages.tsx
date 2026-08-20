import type { Dispatch, RefObject, SetStateAction } from "react"
import { Loader2, Mic, Play, Sparkles, Square, Upload, Users } from "lucide-react"
import { cn } from "@/lib/utils"
import { uploadMeetingImage, type Meeting, type TranscriptSegment, type LanguageHintOption } from "@/api/client"
import { TranscriptTab, SpeakersTab } from "./transcript-panel"
import { CaptureMiniPlayer, type CaptureMiniPlayerHandle } from "./capture-mini-player"
import { LanguageHintsSelector } from "./language-hints-selector"
import { HotWordsSelector } from "./hot-words-selector"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { LiveCaptureControlCard } from "./live-capture-control-card"

export type MeetingCaptureMode = "setup" | "audio" | "transcribing" | "speakers" | "live"

export interface MeetingCaptureStagesProps {
  mode: MeetingCaptureMode
  meeting: Meeting
  languageHints: string[]
  updateLanguageHints: (next: string[]) => void
  supportedLanguageHints: LanguageHintOption[]
  maxLanguageHints: number
  activeHotWordsSupported: boolean
  handleSelectHotWordsLibraries: (ids: string[]) => void
  emptyUploadRef: RefObject<HTMLInputElement | null>
  handleUploadAudio: (file: File) => void | Promise<void>
  startLiveChipOpen: boolean
  hasRealtimeProvider: boolean
  openStartLiveChip: () => void
  scheduleCloseStartLiveChip: () => void
  handleStartRecording: () => void | Promise<void>
  realtimeEnabled: boolean
  setRealtimeEnabled: Dispatch<SetStateAction<boolean>>
  recorderError: string | null | undefined
  captureAudioUrl: string | null
  audioVersion: number
  handleTranscribe: () => void | Promise<void>
  hasFileProvider: boolean
  handleCancelTranscribe: () => void | Promise<void>
  speakersNeedNames: boolean
  namedSpeakerCount: number
  speakerIdsInTranscript: string[]
  displaySegments: TranscriptSegment[]
  handleTranscriptSegmentClick: (start: number, end?: number) => void
  handleSpeakerSampleClick: (start: number, end?: number) => void
  onPersonAssigned: (meeting: Meeting) => void
  capturePlayerRef: RefObject<CaptureMiniPlayerHandle | null>
  setPlaybackTime: (t: number) => void
  playbackTime: number
  handleHotWordsDraftChange: (draft: string[] | undefined) => void
  handleEnterStudio: () => void
  recorderLevels: number[]
  recorderDuration: number
  recorderIsPaused: boolean
  formatRecTime: (seconds: number) => string
  handleStopRecording: () => void
  handleDiscard: () => void | Promise<void>
  liveSegments: TranscriptSegment[]
  livePartial: string
  handleSegmentClick: (start: number, end?: number) => void
  liveNotes: string
  handleLiveNotesChange: (value: string) => void
  pauseRecording: () => void
  resumeRecording: () => void
}

export function MeetingCaptureStages(p: MeetingCaptureStagesProps) {
  const {
    mode,
    meeting,
    languageHints,
    updateLanguageHints,
    supportedLanguageHints,
    maxLanguageHints,
    activeHotWordsSupported,
    handleSelectHotWordsLibraries,
    emptyUploadRef,
    handleUploadAudio,
    startLiveChipOpen,
    hasRealtimeProvider,
    openStartLiveChip,
    scheduleCloseStartLiveChip,
    handleStartRecording,
    realtimeEnabled,
    setRealtimeEnabled,
    captureAudioUrl,
    audioVersion,
    handleTranscribe,
    hasFileProvider,
    handleCancelTranscribe,
    speakersNeedNames,
    namedSpeakerCount,
    speakerIdsInTranscript,
    displaySegments,
    handleTranscriptSegmentClick,
    handleSpeakerSampleClick,
    onPersonAssigned,
    capturePlayerRef,
    setPlaybackTime,
    playbackTime,
    handleHotWordsDraftChange,
    handleEnterStudio,
    recorderLevels,
    recorderDuration,
    recorderIsPaused,
    formatRecTime,
    handleStopRecording,
    handleDiscard,
    liveSegments,
    livePartial,
    handleSegmentClick,
    liveNotes,
    handleLiveNotesChange,
    pauseRecording,
    resumeRecording,
  } = p
  const recorder = {
    error: p.recorderError,
    levels: recorderLevels,
    duration: recorderDuration,
    isPaused: recorderIsPaused,
    pauseRecording,
    resumeRecording,
  }
  const transcription = {
    segments: liveSegments,
    currentPartial: livePartial,
  }

  return (
mode === "setup" ? (
              /* ═══ Capture · Setup
               * 1) Hot words (shared) + language (realtime model)
               * 2) Start recording
               * 3) or
               * 4) Upload audio → leaves setup; language becomes file model
               */
              <div className="pm-meeting-mode-empty" data-meeting-mode="empty">
                <div className="pm-meeting-e-stage">
                  <p className="pm-meeting-e-kicker">New meeting</p>
                  <h3 className="pm-meeting-e-title">Capture the conversation</h3>
                  <p className="pm-meeting-e-sub">
                    {activeHotWordsSupported
                      ? "Pick a hot-words library to reduce ambiguity. Select language for live caption."
                      : "Select language for live caption. The active local model does not support hot words."}
                  </p>

                  <div className="pm-meeting-e-config" aria-label="Transcription settings">
                    <HotWordsSelector
                      meetingId={meeting.id}
                      currentLibraryIds={meeting.hot_words_library_ids ?? (meeting.hot_words_library_id ? [meeting.hot_words_library_id] : [])}
                      hasTranscript={false}
                      providerSupportsHotWords={activeHotWordsSupported}
                      onSelectLibraries={handleSelectHotWordsLibraries}
                    />
                    {supportedLanguageHints.length > 0 && (
                      <LanguageHintsSelector
                        selected={languageHints}
                        onChange={updateLanguageHints}
                        options={supportedLanguageHints}
                        maxHints={maxLanguageHints}
                      />
                    )}
                  </div>

                  <div className="pm-meeting-e-actions pm-meeting-e-actions--stack">
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

                    <p className="pm-meeting-e-or" aria-hidden>
                      or
                    </p>

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
            ) : mode === "audio" ? (
              /* ═══ Capture · Audio ready (upload / post-live before file-tx) ═══ */
              <div className="pm-meeting-mode-empty" data-meeting-mode="audio-ready">
                <div className="pm-meeting-e-stage pm-meeting-e-stage--wide">
                  <p className="pm-meeting-e-kicker">Audio ready</p>
                  <h3 className="pm-meeting-e-title">Review audio</h3>
                  <p className="pm-meeting-e-sub">
                    {activeHotWordsSupported
                      ? "Pick a hot-words library to reduce ambiguity. Select language for file transcription."
                      : "Select language for file transcription. The active local model does not support hot words."}
                  </p>

                  <div className="pm-meeting-e-config" aria-label="Transcription settings">
                    <HotWordsSelector
                      meetingId={meeting.id}
                      currentLibraryIds={meeting.hot_words_library_ids ?? (meeting.hot_words_library_id ? [meeting.hot_words_library_id] : [])}
                      hasTranscript={false}
                      providerSupportsHotWords={activeHotWordsSupported}
                      onSelectLibraries={handleSelectHotWordsLibraries}
                    />
                    {supportedLanguageHints.length > 0 && (
                      <LanguageHintsSelector
                        selected={languageHints}
                        onChange={updateLanguageHints}
                        options={supportedLanguageHints}
                        maxHints={maxLanguageHints}
                        showTipBubble
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
                  </div>
                  {!hasFileProvider && (
                    <p className="pm-meeting-e-error">
                      No file transcription provider configured. Go to Settings → Transcription.
                    </p>
                  )}
                </div>
              </div>
            ) : mode === "transcribing" ? (
              /* ═══ Capture · File transcription in progress ═══ */
              <div className="pm-meeting-mode-empty" data-meeting-mode="transcribing">
                <div className="pm-meeting-e-stage pm-meeting-e-stage--wide">
                  <p className="pm-meeting-e-kicker">Transcribing</p>
                  <h3 className="pm-meeting-e-title">File transcription in progress</h3>
                  <p className="pm-meeting-e-sub">
                    Stay on this page — next you can name speakers and start Summary.
                  </p>

                  <div className="pm-meeting-e-config" aria-label="Transcription settings">
                    <HotWordsSelector
                      meetingId={meeting.id}
                      currentLibraryIds={meeting.hot_words_library_ids ?? (meeting.hot_words_library_id ? [meeting.hot_words_library_id] : [])}
                      hasTranscript={false}
                      providerSupportsHotWords={activeHotWordsSupported}
                      onSelectLibraries={handleSelectHotWordsLibraries}
                      disabled
                    />
                    {supportedLanguageHints.length > 0 && (
                      <LanguageHintsSelector
                        selected={languageHints}
                        onChange={updateLanguageHints}
                        options={supportedLanguageHints}
                        maxHints={maxLanguageHints}
                        disabled
                        showTipBubble={false}
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
                  </div>
                </div>
              </div>
            ) : mode === "speakers" ? (
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
                      <span className="pm-meeting-f-card-meta">
                        {meeting.speaker_slots_status === "computing"
                          ? "Computing voiceprints…"
                          : meeting.speaker_slots_status === "ready" &&
                              meeting.speaker_slots_ms != null
                            ? `Voiceprints ready · ${(meeting.speaker_slots_ms / 1000).toFixed(1)}s`
                            : meeting.speaker_slots_status === "unavailable"
                              ? "Voiceprints unavailable"
                              : "configure"}
                      </span>
                    </div>
                    <div className="pm-meeting-f-card-body">
                      <SpeakersTab
                        segments={displaySegments}
                        speakerNames={meeting.speaker_names ?? {}}
                        meetingId={meeting.id}
                        speakerMatches={meeting.speaker_matches}
                        slotsStatus={meeting.speaker_slots_status}
                        slotsMs={meeting.speaker_slots_ms}
                        onPersonAssigned={onPersonAssigned}
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
                    footerLeftSlot={
                      <>
                        <HotWordsSelector
                          meetingId={meeting.id}
                          currentLibraryIds={meeting.hot_words_library_ids ?? (meeting.hot_words_library_id ? [meeting.hot_words_library_id] : [])}
                          hasTranscript={displaySegments.length > 0}
                          providerSupportsHotWords={activeHotWordsSupported}
                          onSelectLibraries={handleSelectHotWordsLibraries}
                          onDraftChange={handleHotWordsDraftChange}
                          compact
                        />
                        {supportedLanguageHints.length > 0 && (
                          <LanguageHintsSelector
                            selected={languageHints}
                            onChange={updateLanguageHints}
                            options={supportedLanguageHints}
                            maxHints={maxLanguageHints}
                            compact
                            showTipBubble={false}
                          />
                        )}
                        <button
                          type="button"
                          className="pm-meeting-pill is-compact pm-meeting-review-retx"
                          onClick={() => void handleTranscribe()}
                          disabled={!hasFileProvider}
                          title="Re-run file transcription with current hot words and language"
                        >
                          <Play className="size-3.5 opacity-80" />
                          Re-transcribe
                        </button>
                      </>
                    }
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
                  <div className="pm-meeting-f-controls pm-meeting-speaker-gate-actions pm-meeting-review-actions">
                    <div className="pm-meeting-review-tools">
                      <HotWordsSelector
                        meetingId={meeting.id}
                        currentLibraryIds={meeting.hot_words_library_ids ?? (meeting.hot_words_library_id ? [meeting.hot_words_library_id] : [])}
                        hasTranscript={displaySegments.length > 0}
                        providerSupportsHotWords={activeHotWordsSupported}
                        onSelectLibraries={handleSelectHotWordsLibraries}
                        onDraftChange={handleHotWordsDraftChange}
                        compact
                      />
                      {supportedLanguageHints.length > 0 && (
                        <LanguageHintsSelector
                          selected={languageHints}
                          onChange={updateLanguageHints}
                          options={supportedLanguageHints}
                          maxHints={maxLanguageHints}
                          compact
                          showTipBubble={false}
                        />
                      )}
                      <button
                        type="button"
                        className="pm-meeting-pill is-compact pm-meeting-review-retx"
                        onClick={() => void handleTranscribe()}
                        disabled={!hasFileProvider}
                      >
                        <Play className="size-3.5 opacity-80" />
                        Re-transcribe
                      </button>
                    </div>
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
            ) : mode === "live" ? (
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
                        onImageUpload={async (file) => {
                          const result = await uploadMeetingImage(meeting.id, file)
                          return result.url
                        }}
                      />
                    </div>
                  </div>
                </div>
              </div>
    ) : null
  )
}
