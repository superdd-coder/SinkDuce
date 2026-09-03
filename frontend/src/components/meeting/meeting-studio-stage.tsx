import type { Dispatch, MutableRefObject, RefObject, SetStateAction } from "react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { Pencil, Check, X, Plus, PanelRightClose, PanelRightOpen, AlertCircle, Settings } from "lucide-react"
import { Tabs, TabsList, TabsTrigger, TabsIndicator } from "@/components/ui/tabs"
import {
  MeetingTabs,
  type SectionRailActions,
  type SectionRailModel,
} from "./meeting-tabs"
import { TranscriptTab, SpeakersTab } from "./transcript-panel"
import { MediaBar, type MediaBarHandle } from "./media-bar"
import {
  MeetingQuickChat,
  MeetingQcFab,
  type MeetingQcSpinPhase,
} from "./meeting-quick-chat"
import { type Meeting, type TranscriptSegment, type LanguageHintOption } from "@/api/client"
import { MeetingGroupsPanel } from "./meeting-groups-meta"
import type { SidebarView } from "@/stores/app-store"
import { useT } from "@/i18n/use-t"

export type MeetingStudioSideTab = "sections" | "transcript" | "speaker" | "groups"
export type MeetingStudioSideSurface = "tools" | "chat"

export type MeetingStudioFocusRef = { id: string; ts: number; fromChat?: boolean }

export type MeetingStudioSectionTip = {
  id: string
  text: string
  left: number
  top: number
  width: number
}

export interface MeetingStudioStageProps {
  meeting: Meeting
  collections: { id: string; name?: string }[]
  setActiveCollection: (id: string) => void
  setSidebarView: (view: SidebarView) => void
  mainAreaRef: RefObject<HTMLDivElement | null>
  meetingContentRef: RefObject<HTMLDivElement | null>
  mediaBarRef: RefObject<MediaBarHandle | null>
  txPeekOpen: boolean
  setTxPeekOpen: Dispatch<SetStateAction<boolean>>
  editingTitle: boolean
  setEditingTitle: Dispatch<SetStateAction<boolean>>
  titleDraft: string
  setTitleDraft: Dispatch<SetStateAction<string>>
  handleSaveTitle: () => void
  handleStartEditTitle: () => void
  metaCreated: string | undefined
  metaSpeakers: string
  hasFileProvider: boolean
  handleMeetingUpdate: (m: Meeting) => void
  notesContent: string
  onNotesChange: (value: string) => void
  handleSegmentClick: (start: number, end?: number) => void
  requestSideTab: (tab: MeetingStudioSideTab) => void
  setQuickChatOpen: Dispatch<SetStateAction<boolean>>
  setActiveSectionTag: Dispatch<SetStateAction<string>>
  displaySegments: TranscriptSegment[]
  setFocusRef: Dispatch<SetStateAction<MeetingStudioFocusRef | null>>
  partialText: string
  focusRef: MeetingStudioFocusRef | null
  activeSectionTag: string
  transcriptJumpCounter: number
  playbackTime: number | null
  selectedSummaryId: string
  setSelectedSummaryId: Dispatch<SetStateAction<string>>
  setSectionRailModel: Dispatch<SetStateAction<SectionRailModel | null>>
  bindSectionRailActions: (actions: SectionRailActions) => void
  setMainTab: Dispatch<SetStateAction<string>>
  audioVersion: number
  recordingMeetingId: string | null
  recorderDuration: number
  recorderLevels: number[] | null | undefined
  recorderIsRecording: boolean
  recorderIsPaused: boolean
  recorderError: string | null | undefined
  pauseRecording: () => void
  resumeRecording: () => void
  handleUploadAudio: (file: File) => void | Promise<void>
  handleStartRecording: () => void | Promise<void>
  handleStopRecording: () => void
  handleTranscribe: () => void | Promise<void>
  setRetranscribeConfirmOpen: Dispatch<SetStateAction<boolean>>
  handleCancelTranscribe: () => void | Promise<void>
  hasRealtimeProvider: boolean
  realtimeEnabled: boolean
  setRealtimeEnabled: Dispatch<SetStateAction<boolean>>
  activeHotWordsSupported: boolean
  handleSelectHotWordsLibraries: (ids: string[]) => void
  handleHotWordsDraftChange: (draft: string[] | undefined) => void
  languageHints: string[]
  supportedLanguageHints: LanguageHintOption[]
  updateLanguageHints: (next: string[]) => void
  maxLanguageHints: number
  setPlaybackTime: (t: number | null) => void
  handleDiscard: () => void | Promise<void>
  sideRailOpen: boolean
  sideSurfaceDisplay: MeetingStudioSideSurface
  sideSurfaceExiting: boolean
  sideTab: MeetingStudioSideTab
  setSideTab: Dispatch<SetStateAction<MeetingStudioSideTab>>
  onOpenGroup?: (groupId: string) => void
  onGroupsChanged?: () => void
  setSideRailOpenWithMotion: (open: boolean) => void
  setSideRailOpen: Dispatch<SetStateAction<boolean>>
  handleRefClick: (sentenceId: string) => void
  sectionRailCardRef: RefObject<HTMLDivElement | null>
  sectionRailModel: SectionRailModel | null
  sectionRailActionsRef: MutableRefObject<SectionRailActions | null>
  hideSectionTip: () => void
  activeSectionRailId: string | null
  sectionFocusReady: boolean
  sectionFocus: { top: number; height: number }
  sectionItemRefs: MutableRefObject<Map<string, HTMLElement>>
  showSectionTip: (id: string, text: string, anchorEl: HTMLElement | null) => void
  sectionTip: MeetingStudioSectionTip | null
  qcFabPark: "bottom" | "top"
  qcFabFading: boolean
  qcFabRideOut: boolean
  qcFabBottomFadeIn: boolean
  qcFabSpinPhase: MeetingQcSpinPhase
  quickChatOpen: boolean
}

export function MeetingStudioStage(p: MeetingStudioStageProps) {
  const t = useT()
  const {
    meeting,
    collections,
    setActiveCollection,
    setSidebarView,
    mainAreaRef,
    meetingContentRef,
    mediaBarRef,
    txPeekOpen,
    setTxPeekOpen,
    editingTitle,
    setEditingTitle,
    titleDraft,
    setTitleDraft,
    handleSaveTitle,
    handleStartEditTitle,
    metaCreated,
    metaSpeakers,
    hasFileProvider,
    handleMeetingUpdate,
    notesContent,
    onNotesChange,
    handleSegmentClick,
    requestSideTab,
    setQuickChatOpen,
    setActiveSectionTag,
    displaySegments,
    setFocusRef,
    partialText,
    focusRef,
    activeSectionTag,
    transcriptJumpCounter,
    playbackTime,
    selectedSummaryId,
    setSelectedSummaryId,
    setSectionRailModel,
    bindSectionRailActions,
    setMainTab,
    audioVersion,
    recordingMeetingId,
    recorderDuration,
    recorderLevels,
    recorderIsRecording,
    recorderIsPaused,
    recorderError,
    pauseRecording,
    resumeRecording,
    handleUploadAudio,
    handleStartRecording,
    handleStopRecording,
    handleTranscribe,
    setRetranscribeConfirmOpen,
    handleCancelTranscribe,
    hasRealtimeProvider,
    realtimeEnabled,
    setRealtimeEnabled,
    activeHotWordsSupported,
    handleSelectHotWordsLibraries,
    handleHotWordsDraftChange,
    languageHints,
    supportedLanguageHints,
    updateLanguageHints,
    maxLanguageHints,
    setPlaybackTime,
    handleDiscard,
    sideRailOpen,
    sideSurfaceDisplay,
    sideSurfaceExiting,
    sideTab,
    setSideTab,
    setSideRailOpenWithMotion,
    setSideRailOpen,
    handleRefClick,
    sectionRailCardRef,
    sectionRailModel,
    sectionRailActionsRef,
    hideSectionTip,
    activeSectionRailId,
    sectionFocusReady,
    sectionFocus,
    sectionItemRefs,
    showSectionTip,
    sectionTip,
    qcFabPark,
    qcFabFading,
    qcFabRideOut,
    qcFabBottomFadeIn,
    qcFabSpinPhase,
    quickChatOpen,
  } = p

  const recorder = {
    duration: recorderDuration,
    levels: recorderLevels,
    isRecording: recorderIsRecording,
    isPaused: recorderIsPaused,
    error: recorderError,
    pauseRecording,
    resumeRecording,
  }

  return (
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
                          <Button variant="ghost" size="icon-sm" className="shrink-0" onClick={handleSaveTitle} aria-label={t("meeting.saveTitle")}>
                            <Check className="size-3.5" />
                          </Button>
                          <Button variant="ghost" size="icon-sm" className="shrink-0" onClick={() => setEditingTitle(false)} aria-label={t("meeting.cancelEdit")}>
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
                            aria-label={t("meeting.editTitle")}
                          >
                            <Pencil className="size-3.5" />
                          </Button>
                        </div>
                      )}

                      <div className="pm-meeting-meta-stack">
                        <div className="pm-meeting-meta-row">
                          <span className="pm-meeting-meta-key">{t("common.created")}</span>
                          <span className="pm-meeting-meta-val">{metaCreated}</span>
                        </div>
                        <div className="pm-meeting-meta-row">
                          <span className="pm-meeting-meta-key">{t("common.speakers")}</span>
                          <span className="pm-meeting-meta-val">{metaSpeakers}</span>
                        </div>
                        <div className="pm-meeting-meta-row">
                          <span className="pm-meeting-meta-key">{t("common.collections")}</span>
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
                      <span className="flex-1">{t("meeting.noTranscriptionProvider")}</span>
                      <Button variant="ghost" size="sm" onClick={() => setSidebarView("llm_provider")}>
                        <Settings className="size-3 mr-1" /> {t("nav.settings")}
                      </Button>
                    </div>
                  )}

                  <div ref={meetingContentRef} className="pm-meeting-content-card">
                    <MeetingTabs
                      meetingId={meeting.id}
                      meeting={meeting}
                      notesContent={notesContent}
                      onNotesChange={onNotesChange}
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
                      partialText={partialText}
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
                      hotWordsLibraryIds={meeting.hot_words_library_ids ?? (meeting.hot_words_library_id ? [meeting.hot_words_library_id] : [])}
                      hotWordsSupported={activeHotWordsSupported}
                      onSelectHotWords={handleSelectHotWordsLibraries}
                      onHotWordsDraftChange={handleHotWordsDraftChange}
                      languageHints={languageHints}
                      languageHintOptions={supportedLanguageHints}
                      onChangeLanguageHints={updateLanguageHints}
                      maxLanguageHints={maxLanguageHints}
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
                  aria-label={t("meeting.meetingSidePanel")}
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
                              <span className="pm-meeting-side-chat-title">{t("common.chat")}</span>
                              <span className="pm-meeting-side-chat-sub truncate" title={meeting.title ?? ""}>
                                {meeting.title || t("nav.meeting")}
                              </span>
                            </div>
                          ) : (
                            <Tabs
                              value={sideTab}
                              onValueChange={(v) => {
                                if (v === "sections" || v === "transcript" || v === "speaker" || v === "groups") {
                                  setSideTab(v)
                                }
                              }}
                              className="pm-meeting-side-tabs gap-0 min-w-0 flex-1"
                            >
                              <TabsList className="relative" aria-label={t("meeting.sidePanel")}>
                                <TabsIndicator className="pm-tabs-indicator" renderBeforeHydration />
                                <TabsTrigger value="sections" disabled={sideSurfaceExiting}>
                                  {t("meeting.sections")}
                                </TabsTrigger>
                                <TabsTrigger value="groups" disabled={sideSurfaceExiting}>
                                  {t("meeting.groupTab")}
                                </TabsTrigger>
                                <TabsTrigger value="transcript" disabled={sideSurfaceExiting}>
                                  {t("common.transcript")}
                                </TabsTrigger>
                                <TabsTrigger value="speaker" disabled={sideSurfaceExiting}>
                                  {t("meeting.speaker")}
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
                            title={t("meeting.collapseSidePanel")}
                            aria-label={t("meeting.collapseSidePanel")}
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
                          indexStatus={meeting.transcript_index_status}
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
                                    {sectionRailModel?.thinking ? t("meeting.building") : t("meeting.browse")}
                                  </span>
                                  <button
                                    type="button"
                                    className="pm-meeting-section-rail-add"
                                    disabled={!!sectionRailModel?.busy || !!sectionRailModel?.thinking}
                                    title={t("meeting.addSection")}
                                    aria-label={t("meeting.addSection")}
                                    onClick={() => sectionRailActionsRef.current?.openAddSection()}
                                  >
                                    <Plus className="size-3.5" />
                                    <span>{t("common.add")}</span>
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
                                    <p className="pm-meeting-section-rail-empty">{t("meeting.noSections")}</p>
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
                                      const isIngested =
                                        (item.kind === "section" || item.kind === "general") &&
                                        item.ingested === true
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
                                              isCustom && "is-custom",
                                              isCustom && item.selected === false && "is-preview",
                                            )}
                                            title={
                                              isStreaming
                                                ? t("meeting.streamingWatch")
                                                : isGenerating
                                                  ? t("meeting.generating")
                                                  : isIngested
                                                    ? t("meeting.ingestedOpen")
                                                    : isReady
                                                      ? t("library.openSection")
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
                                                if (!Number.isNaN(idx)) actions.toggleCustom(idx)
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
                                                  item.selected && "is-on",
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
                                                {t("meeting.streaming")}
                                              </span>
                                            ) : isGenerating ? (
                                              <span className="pm-meeting-section-card-badge">{t("common.generated")}</span>
                                            ) : isIngested ? (
                                              <span
                                                className="pm-meeting-section-card-ingested"
                                                title={t("meeting.ingestedToCollection")}
                                                aria-label={t("common.ingested")}
                                              >
                                                <Check className="size-2.5" strokeWidth={2.5} aria-hidden />
                                              </span>
                                            ) : null}
                                          </button>
                                          {isCustom && (
                                            <button
                                              type="button"
                                              className="pm-meeting-section-card-remove"
                                              disabled={sectionRailModel.busy}
                                              title={t("meeting.removeReceipt")}
                                              aria-label={t("meeting.removeReceipt")}
                                              onClick={(e) => {
                                                e.stopPropagation()
                                                const idx = Number(item.id.replace("custom:", ""))
                                                if (!Number.isNaN(idx)) {
                                                  sectionRailActionsRef.current?.removeCustom(idx)
                                                }
                                              }}
                                            >
                                              <X className="size-3" aria-hidden />
                                            </button>
                                          )}
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
                                        {sectionRailModel.busy ? t("meeting.extracting") : t("meeting.breakdown")}
                                      </button>
                                    </div>
                                  )}
                                </div>
                              </div>
                            </div>

                            <div
                              className={cn(
                                "pm-meeting-side-panel",
                                sideTab === "groups" && "is-active",
                              )}
                              aria-hidden={sideTab !== "groups"}
                            >
                              <MeetingGroupsPanel
                                meetingId={meeting.id}
                                onOpenGroup={p.onOpenGroup}
                                onGroupsChanged={p.onGroupsChanged}
                              />
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
                                partialText={partialText}
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
                                meetingId={meeting.id}
                                speakerMatches={meeting.speaker_matches}
                                slotsStatus={meeting.speaker_slots_status}
                                slotsMs={meeting.speaker_slots_ms}
                                onPersonAssigned={(m) => {
                                  handleMeetingUpdate(m)
                                  void import("@/components/ui/tiptap-editor").then((mod) => {
                                    mod.invalidateMeetingSpeakerCache(meeting.id)
                                  })
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
                    aria-label={t("meeting.openSidePanel")}
                  >
                    <PanelRightOpen className="size-3.5" />
                    {t("meeting.panel")}
                  </button>
                )}

                {/* Chat sentence-ref: overlay Transcript peek (covers main; side width fixed) */}
                {txPeekOpen && sideRailOpen && (
                  <aside
                    className="pm-meeting-tx-peek"
                    aria-label={t("meeting.transcriptRef")}
                  >
                    <div className="pm-meeting-tx-peek-card">
                      <div className="pm-meeting-tx-peek-head">
                        <span className="pm-meeting-tx-peek-title">{t("common.transcript")}</span>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-xs"
                          onClick={() => setTxPeekOpen(false)}
                          aria-label={t("meeting.closeTranscript")}
                        >
                          <X className="size-3.5" />
                        </Button>
                      </div>
                      <div className="pm-meeting-tx-peek-body">
                        <TranscriptTab
                          segments={displaySegments}
                          partialText={partialText}
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
  )
}
