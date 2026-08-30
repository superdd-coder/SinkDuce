import type { Dispatch, ReactNode, RefObject, SetStateAction } from "react"
import { useEffect, useRef, useState } from "react"
import { Loader2, Mic, Play, RotateCcw, Sparkles, Square, Upload, Users } from "lucide-react"
import { cn } from "@/lib/utils"
import { type Meeting, type TranscriptSegment, type LanguageHintOption, type LiveSummaryState } from "@/api/client"
import { TranscriptTab, SpeakersTab } from "./transcript-panel"
import { CaptureMiniPlayer, type CaptureMiniPlayerHandle } from "./capture-mini-player"
import { LanguageHintsSelector } from "./language-hints-selector"
import { HotWordsSelector } from "./hot-words-selector"
import { LiveCaptureControlCard } from "./live-capture-control-card"
import { MeetingNotesCard } from "./meeting-notes-card"
import { LiveSummaryPanel, LiveSummaryTail } from "./live-summary-panel"
import type { MeetingNotesStatus } from "@/hooks/use-meeting-notes"
import { useT } from "@/i18n/use-t"

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
  setPlaybackTime: (t: number | null) => void
  playbackTime: number | null
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
  liveSummaryEnabled: boolean
  liveSummaryState: LiveSummaryState | null
  liveSummaryError: string | null
  liveSummaryEngine: string
  onToggleLiveSummary: (enabled: boolean) => void
  onResetLiveSummary: () => void
  handleSegmentClick: (start: number, end?: number) => void
  liveNotes: string
  handleLiveNotesChange: (value: string) => void
  notesStatus: MeetingNotesStatus
  notesRailOpen: boolean
  onToggleNotesRail: () => void
  pauseRecording: () => void
  resumeRecording: () => void
}

function PrepStage({
  mode,
  meetingId,
  notesOpen,
  onToggleNotes,
  notes,
  onNotesChange,
  notesStatus,
  children,
}: {
  mode: "empty" | "audio-ready" | "transcribing"
  meetingId: string
  notesOpen: boolean
  onToggleNotes: () => void
  notes: string
  onNotesChange: (value: string) => void
  notesStatus: MeetingNotesStatus
  children: ReactNode
}) {
  const t = useT()
  return (
    <div
      className={cn("pm-meeting-mode-empty", notesOpen && "is-notes-open")}
      data-meeting-mode={mode}
    >
      <div className="pm-meeting-e-main">{children}</div>
      <aside
        id={`meeting-notes-rail-${meetingId}`}
        className="pm-meeting-notes-rail"
        aria-hidden={!notesOpen}
      >
        <div className="pm-meeting-notes-dock">
          <button
            type="button"
            className={cn("pm-meeting-notes-handle", !!notes.trim() && "has-content")}
            aria-expanded={notesOpen}
            aria-controls={`meeting-notes-rail-${meetingId}`}
            aria-label={t("meeting.notesHandle")}
            onClick={onToggleNotes}
          >
            <span className="pm-meeting-notes-handle-label">{t("common.notes")}</span>
            {!!notes.trim() && <span className="pm-meeting-notes-handle-dot" aria-hidden />}
          </button>
          <MeetingNotesCard
            meetingId={meetingId}
            value={notes}
            onChange={onNotesChange}
            status={notesStatus}
            placeholder={t("meeting.writeNotesPrep")}
          />
        </div>
      </aside>
    </div>
  )
}

export function MeetingCaptureStages(p: MeetingCaptureStagesProps) {
  const t = useT()
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
    liveSummaryEnabled,
    liveSummaryState,
    liveSummaryError,
    liveSummaryEngine,
    onToggleLiveSummary,
    onResetLiveSummary,
    handleSegmentClick,
    liveNotes,
    handleLiveNotesChange,
    notesStatus,
    notesRailOpen,
    onToggleNotesRail,
    pauseRecording,
    resumeRecording,
  } = p
  const summaryAvailable = realtimeEnabled && hasRealtimeProvider
  const [liveTab, setLiveTab] = useState<"transcript" | "summary">("transcript")
  const liveTabMountedRef = useRef(false)
  useEffect(() => {
    // Flip to the summary view when the USER turns it on — skip the first
    // run so the auto-enable at recording start keeps the transcript view.
    if (!liveTabMountedRef.current) {
      liveTabMountedRef.current = true
      return
    }
    if (liveSummaryEnabled) setLiveTab("summary")
  }, [liveSummaryEnabled])
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
              <PrepStage
                mode="empty"
                meetingId={meeting.id}
                notesOpen={notesRailOpen}
                onToggleNotes={onToggleNotesRail}
                notes={liveNotes}
                onNotesChange={handleLiveNotesChange}
                notesStatus={notesStatus}
              >
                <div className="pm-meeting-e-stage">
                  <p className="pm-meeting-e-kicker">{t("meeting.newMeeting")}</p>
                  <h3 className="pm-meeting-e-title">{t("meeting.capture")}</h3>
                  <p className="pm-meeting-e-sub">
                    {activeHotWordsSupported
                      ? t("meeting.setupHotWordsSub")
                      : t("meeting.setupNoHotWordsSub")}
                  </p>

                  <div className="pm-meeting-e-config" aria-label={t("meeting.transcriptionSettings")}>
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
                        {t("meeting.startRecording")}
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
                              ? t("meeting.liveCaptionOnClick")
                              : t("meeting.liveCaptionOffClick")
                          }
                        >
                          <span
                            className={cn(
                              "pm-meeting-e-realtime-dot",
                              realtimeEnabled && "is-on",
                            )}
                          />
                          {realtimeEnabled ? t("meeting.liveCaptionOn") : t("meeting.liveCaptionOff")}
                        </button>
                      )}
                    </div>

                    <p className="pm-meeting-e-or" aria-hidden>
                      {t("common.or")}
                    </p>

                    <button
                      type="button"
                      className="pm-meeting-e-cta is-secondary"
                      onClick={() => emptyUploadRef.current?.click()}
                    >
                      <Upload className="size-3.5" />
                      {t("meeting.uploadAudio")}
                    </button>
                  </div>
                  {recorder.error && (
                    <p className="pm-meeting-e-error">{recorder.error}</p>
                  )}
                </div>
              </PrepStage>
            ) : mode === "audio" ? (
              /* ═══ Capture · Audio ready (upload / post-live before file-tx) ═══ */
              <PrepStage
                mode="audio-ready"
                meetingId={meeting.id}
                notesOpen={notesRailOpen}
                onToggleNotes={onToggleNotesRail}
                notes={liveNotes}
                onNotesChange={handleLiveNotesChange}
                notesStatus={notesStatus}
              >
                <div className="pm-meeting-e-stage pm-meeting-e-stage--wide">
                  <p className="pm-meeting-e-kicker">{t("meeting.audioReady")}</p>
                  <h3 className="pm-meeting-e-title">{t("meeting.reviewAudio")}</h3>
                  <p className="pm-meeting-e-sub">
                    {activeHotWordsSupported
                      ? t("meeting.audioReadyFileHw")
                      : t("meeting.audioReadyFileNoHw")}
                  </p>

                  <div className="pm-meeting-e-config" aria-label={t("meeting.transcriptionSettings")}>
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
                      {t("common.transcribe")}
                    </button>
                    <button
                      type="button"
                      className="pm-meeting-e-cta is-secondary"
                      onClick={() => emptyUploadRef.current?.click()}
                    >
                      <Upload className="size-3.5" />
                      {t("meeting.replaceAudio")}
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
                      {t("meeting.noFileProviderSettings")}
                    </p>
                  )}
                </div>
              </PrepStage>
            ) : mode === "transcribing" ? (
              /* ═══ Capture · File transcription in progress ═══ */
              <PrepStage
                mode="transcribing"
                meetingId={meeting.id}
                notesOpen={notesRailOpen}
                onToggleNotes={onToggleNotesRail}
                notes={liveNotes}
                onNotesChange={handleLiveNotesChange}
                notesStatus={notesStatus}
              >
                <div className="pm-meeting-e-stage pm-meeting-e-stage--wide">
                  <p className="pm-meeting-e-kicker">{t("meeting.transcribing")}</p>
                  <h3 className="pm-meeting-e-title">{t("meeting.fileTranscribing")}</h3>
                  <p className="pm-meeting-e-sub">
                    {t("meeting.transcribingStay")}
                  </p>

                  <div className="pm-meeting-e-config" aria-label={t("meeting.transcriptionSettings")}>
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
                      {t("meeting.transcribing")}
                    </button>
                    <button
                      type="button"
                      className="pm-meeting-e-cta is-secondary"
                      onClick={() => void handleCancelTranscribe()}
                    >
                      <Square className="size-3.5" />
                      {t("common.cancel")}
                    </button>
                  </div>
                </div>
              </PrepStage>
            ) : mode === "speakers" ? (
              /* ═══ Capture · Review: transcript + speakers, then Summarize → Studio ═══ */
              <div className="pm-meeting-mode-live" data-meeting-mode="speakers">
                <div className="pm-meeting-speaker-gate-hint" role="status">
                  <Users className="size-3.5 shrink-0" />
                  <p>
                    {speakersNeedNames ? (
                      <>
                        <strong>{t("meeting.reviewTranscript")}</strong>
                        {" · "}
                        {t("meeting.speakersNamedHint", {
                          named: namedSpeakerCount,
                          total: speakerIdsInTranscript.length || 0,
                        })}
                      </>
                    ) : speakerIdsInTranscript.length === 0 ? (
                      <>
                        <strong>{t("meeting.reviewTranscript")}</strong>
                        {" · "}
                        {t("meeting.noSpeakerLabels")}
                      </>
                    ) : (
                      <>
                        <strong>{t("meeting.reviewTranscript")}</strong>
                        {" · "}
                        {t("meeting.speakersReady")}
                      </>
                    )}
                  </p>
                </div>

                <div className="pm-meeting-f-grid">
                  <div className="pm-meeting-f-card">
                    <div className="pm-meeting-f-card-h">
                      <span className="pm-meeting-f-card-label">{t("common.transcript")}</span>
                      <span className="pm-meeting-f-card-meta">
                        {t("meeting.nSegments", { n: displaySegments.length })}
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
                      <span className="pm-meeting-f-card-label">{t("common.speakers")}</span>
                      <span className="pm-meeting-f-card-meta">
                        {meeting.speaker_slots_status === "computing"
                          ? t("meeting.computingVoiceprints")
                          : meeting.speaker_slots_status === "ready" &&
                              meeting.speaker_slots_ms != null
                            ? t("meeting.voiceprintsReady", { s: (meeting.speaker_slots_ms / 1000).toFixed(1) })
                            : meeting.speaker_slots_status === "unavailable"
                              ? t("meeting.voiceprintsUnavailable")
                              : t("meeting.configure")}
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
                          title={t("meeting.rerunFileAsr")}
                        >
                          <Play className="size-3.5 opacity-80" />
                          {t("meeting.reTranscribe")}
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
                        {t("meeting.summarize")}
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
                        {t("meeting.reTranscribe")}
                      </button>
                    </div>
                    <button
                      type="button"
                      className="pm-meeting-e-cta is-primary"
                      onClick={handleEnterStudio}
                    >
                      <Sparkles className="size-3.5" />
                      {t("meeting.summarize")}
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
                      {summaryAvailable ? (
                        <div className="pm-live-tabs" role="tablist">
                          <button
                            type="button"
                            role="tab"
                            aria-selected={liveTab === "transcript"}
                            className={cn("pm-live-tab", liveTab === "transcript" && "is-active")}
                            onClick={() => setLiveTab("transcript")}
                          >
                            {t("meeting.liveTranscript")}
                          </button>
                          <button
                            type="button"
                            role="tab"
                            aria-selected={liveTab === "summary"}
                            className={cn("pm-live-tab", liveTab === "summary" && "is-active")}
                            onClick={() => setLiveTab("summary")}
                          >
                            {t("meeting.liveSummaryTab")}
                            {liveSummaryEngine === "running" && (
                              <span className="pm-live-tab-pulse" aria-hidden />
                            )}
                          </button>
                        </div>
                      ) : (
                        <span className="pm-meeting-f-card-label">{t("meeting.liveTranscript")}</span>
                      )}
                      {liveTab === "summary" && summaryAvailable ? (
                        <span className="pm-meeting-f-card-meta pm-live-head-meta">
                          <button
                            type="button"
                            className="pm-live-summary-toggle"
                            onClick={() => onToggleLiveSummary(!liveSummaryEnabled)}
                            title={
                              liveSummaryEnabled
                                ? t("meeting.liveSummaryOffClick")
                                : t("meeting.liveSummaryOnClick")
                            }
                          >
                            <span
                              className={cn(
                                "pm-live-summary-dot",
                                liveSummaryEnabled && "is-on",
                              )}
                              aria-hidden
                            />
                            {liveSummaryEnabled
                              ? t("meeting.liveSummaryOn")
                              : t("meeting.liveSummaryOff")}
                          </button>
                          {liveSummaryEnabled && (
                            <button
                              type="button"
                              className="pm-live-summary-reset"
                              onClick={onResetLiveSummary}
                              title={t("meeting.liveSummaryReset")}
                            >
                              <RotateCcw className="size-3" />
                            </button>
                          )}
                        </span>
                      ) : (
                        <span className="pm-meeting-f-card-meta">
                          {realtimeEnabled && hasRealtimeProvider
                            ? t("meeting.liveCaptionsMeta")
                            : t("meeting.autoScroll")}
                        </span>
                      )}
                    </div>
                    <div className="pm-meeting-f-card-body">
                      {liveTab === "summary" && summaryAvailable ? (
                        <div className="pm-live-summary-wrap">
                          <LiveSummaryPanel
                            state={liveSummaryState}
                            error={liveSummaryError}
                            paused={liveSummaryEnabled && !realtimeEnabled}
                            speakerNames={meeting.speaker_names ?? {}}
                          />
                          <LiveSummaryTail
                            segments={transcription.segments}
                            partial={transcription.currentPartial}
                            tailFromT={liveSummaryState?.tail_from_t ?? 0}
                          />
                        </div>
                      ) : (
                        <TranscriptTab
                          segments={transcription.segments}
                          partialText={transcription.currentPartial}
                          onSegmentClick={handleSegmentClick}
                          speakerNames={meeting.speaker_names ?? {}}
                          showSearch={false}
                          followLive
                        />
                      )}
                    </div>
                  </div>
                  <MeetingNotesCard
                    meetingId={meeting.id}
                    value={liveNotes}
                    onChange={handleLiveNotesChange}
                    status={notesStatus}
                    placeholder={t("meeting.writeNotesWhile")}
                    idleMeta={t("meeting.savedLive")}
                  />
                </div>
              </div>
    ) : null
  )
}
