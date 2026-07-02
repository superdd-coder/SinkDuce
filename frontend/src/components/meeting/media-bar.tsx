import { useRef, useEffect, useState, forwardRef, useImperativeHandle } from "react"
import { createPortal } from "react-dom"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip"
import { Upload, Mic, Square, Pause, Loader2, FileAudio, RefreshCw, Play, AlertCircle, BookOpen, Languages, Check } from "lucide-react"
import type { MeetingStatus, HotWordsLibrarySummary, LanguageHintOption } from "@/api/client"

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
  hotWordsLibraryId?: string | null
  hotWordsLibraries?: HotWordsLibrarySummary[]
  onSelectHotWords?: (libraryId: string | null) => void
  languageHints?: string[]
  languageHintOptions?: LanguageHintOption[]
  onChangeLanguageHints?: (hints: string[]) => void
  showLanguageSelector?: boolean
  onTimeUpdate?: (time: number) => void
}

export interface MediaBarHandle {
  seekTo: (time: number) => void
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
  hotWordsLibraryId,
  hotWordsLibraries = [],
  onSelectHotWords,
  languageHints = [],
  languageHintOptions = [],
  onChangeLanguageHints,
  showLanguageSelector,
  onTimeUpdate,
}, ref) {
  const inputRef = useRef<HTMLInputElement>(null)
  const audioRef = useRef<HTMLAudioElement>(null)

  // Hot Words dropdown
  const [hwOpen, setHwOpen] = useState(false)
  const hwBtnRef = useRef<HTMLButtonElement>(null)
  const hwDropdownRef = useRef<HTMLDivElement>(null)
  const hwMenuRef = useRef<HTMLDivElement>(null)

  // Language dropdown
  const [langOpen, setLangOpen] = useState(false)
  const langBtnRef = useRef<HTMLButtonElement>(null)
  const langDropdownRef = useRef<HTMLDivElement>(null)
  const langMenuRef = useRef<HTMLDivElement>(null)

  // Click outside to close dropdowns (portal-aware)
  useEffect(() => {
    if (!hwOpen && !langOpen) return
    const handler = (e: MouseEvent) => {
      if (hwOpen) {
        const hitMenu = hwMenuRef.current?.contains(e.target as Node)
        const hitDropdown = hwDropdownRef.current?.contains(e.target as Node)
        if (!hitMenu && !hitDropdown) setHwOpen(false)
      }
      if (langOpen) {
        const hitMenu = langMenuRef.current?.contains(e.target as Node)
        const hitDropdown = langDropdownRef.current?.contains(e.target as Node)
        if (!hitMenu && !hitDropdown) setLangOpen(false)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [hwOpen, langOpen])

  // Pause audio when meeting changes to prevent crackling
  useEffect(() => {
    return () => {
      const el = audioRef.current
      if (el) {
        el.pause()
      }
    }
  }, [meetingId])

  // Emit timeupdate for transcript auto-scroll
  useEffect(() => {
    const el = audioRef.current
    if (!el || !onTimeUpdate) return
    const handler = () => onTimeUpdate(el.currentTime)
    el.addEventListener("timeupdate", handler)
    return () => el.removeEventListener("timeupdate", handler)
  }, [onTimeUpdate, audioUrl])

  useImperativeHandle(ref, () => ({
    seekTo(time: number) {
      const el = audioRef.current
      if (el) {
        el.currentTime = time
        el.play().catch(() => {})  // AbortError when interrupted by pause/unmount
      }
    },
  }))

  // --- Dropdown position helpers ---
  const hwDropdownStyle = hwBtnRef.current
    ? {
        top: hwBtnRef.current.getBoundingClientRect().bottom + 4,
        left: hwBtnRef.current.getBoundingClientRect().right - 180,
      }
    : {}

  const langDropdownStyle = langBtnRef.current
    ? {
        top: langBtnRef.current.getBoundingClientRect().bottom + 4,
        left: langBtnRef.current.getBoundingClientRect().right - 200,
      }
    : {}

  // Recording state
  if (isRecording || isPaused) {
    return (
      <div className="flex items-center gap-3 py-3 px-0">
        <div className="h-3 w-3 rounded-full bg-red-500 animate-pulse" />
        <span className="font-mono text-sm tabular-nums">{formatDuration(duration)}</span>
        <div className="flex-1 flex items-center gap-1">
          {Array.from({ length: 20 }).map((_, i) => (
            <div
              key={i}
              className="w-1 bg-primary/40 rounded-full animate-pulse"
              style={{ height: `${Math.random() * 16 + 4}px`, animationDelay: `${i * 50}ms` }}
            />
          ))}
        </div>
        <Button variant="outline" size="sm" onClick={isPaused ? onResumeRecord : onPauseRecord}>
          <Pause className="h-4 w-4 mr-1" />
          {isPaused ? "Resume" : "Pause"}
        </Button>
        <Button variant="destructive" size="sm" onClick={onStopRecord}>
          <Square className="h-4 w-4 mr-1" />
          Stop
        </Button>
      </div>
    )
  }

  // Transcribing state
  if (status === "transcribing") {
    return (
      <div className="flex flex-col gap-2">
        {transcriptionError && (
          <div className="flex items-center gap-2 p-3 border border-destructive/50 rounded-lg bg-destructive/10 text-destructive">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span className="text-sm flex-1">Transcription failed: {transcriptionError}</span>
          </div>
        )}
        <div className="flex items-center gap-3 py-3 px-0">
          {/* Audio player during transcription */}
          {audioUrl ? (
            <audio key={`transcribing-${audioVersion}`} ref={audioRef} controls src={audioUrl} preload="metadata" className="flex-1 h-7 styled-audio">
              <track kind="captions" />
            </audio>
          ) : (
            <>
              <Loader2 className="h-4 w-4 animate-spin text-primary" />
              <span className="text-sm">Transcribing...</span>
            </>
          )}
          <span className="text-sm text-muted-foreground shrink-0">Transcribing...</span>
          {onCancelTranscribe && (
            <Button variant="destructive" size="sm" onClick={onCancelTranscribe}>
              <Square className="h-4 w-4 mr-1" />
              Stop
            </Button>
          )}
        </div>
        <ProcessingBar label="Transcribing audio..." />
      </div>
    )
  }

  // Has audio — always show player + action buttons
  if (hasAudio) {
    const hwSelectedLabel = hotWordsLibraries.find((l) => l.id === hotWordsLibraryId)?.name
    const langCount = languageHints.length
    const langCustomized = langCount > 0 && !(langCount === 1 && languageHints[0] === "auto")

    return (
      <div className="flex flex-col gap-2">
        {transcriptionError && (
          <div className="flex items-center gap-2 p-3 border border-destructive/50 rounded-lg bg-destructive/10 text-destructive">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span className="text-sm flex-1">Transcription failed: {transcriptionError}</span>
          </div>
        )}
        <div className="flex items-center gap-3 py-3 px-0">
          {audioUrl ? (
            <audio key={`player-${audioVersion}`} ref={audioRef} controls src={audioUrl} preload="metadata" className="flex-1 h-7 styled-audio">
              <track kind="captions" />
            </audio>
          ) : (
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <FileAudio className="h-4 w-4 text-muted-foreground shrink-0" />
              <span className="text-sm text-muted-foreground truncate" title={audioPath}>
                {audioPath ? audioPath.split("/").pop() : "Audio uploaded"}
              </span>
            </div>
          )}
          <div className="flex items-center gap-2 shrink-0">
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
                <Button variant="outline" size="sm" onClick={() => inputRef.current?.click()}>
                  <RefreshCw className="h-3 w-3 mr-1" />
                  Replace
                </Button>
              </>
            )}
            {!hasTranscript && (
              <Button size="sm" onClick={onTranscribe}>
                <Play className="h-4 w-4 mr-1" />
                Transcribe
              </Button>
            )}
            {hasTranscript && onReTranscribe && (
              <Button variant="outline" size="sm" onClick={onReTranscribe}>
                Re-transcribe
              </Button>
            )}

            {/* Hot Words — dropdown */}
            {onSelectHotWords && (
              <div ref={hwMenuRef} className="relative">
                <Tooltip>
                  <TooltipTrigger
                    render={
                      <button
                        ref={hwBtnRef}
                        type="button"
                        onClick={() => { setHwOpen(!hwOpen); setLangOpen(false) }}
                        className={cn(
                          "h-7 w-7 flex items-center justify-center rounded-sm transition-colors",
                          hotWordsLibraryId
                            ? "text-primary hover:bg-primary/10"
                            : "text-muted-foreground hover:text-foreground hover:bg-accent"
                        )}
                      >
                        <BookOpen className="h-3.5 w-3.5" />
                      </button>
                    }
                  />
                  <TooltipContent side="top" className="px-2.5 py-1.5 text-[11px] bg-[#0A120E] text-[#FAFAF7] rounded-[3px]">
                    Hot Words{hotWordsLibraryId ? ` · ${hwSelectedLabel}` : ""}
                  </TooltipContent>
                </Tooltip>

                {createPortal(
                  <div
                    ref={hwDropdownRef}
                    className={`fixed z-[100] flex-col items-center overflow-hidden rounded border border-primary/30 bg-popover/60 backdrop-blur-md shadow-lg transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] ${
                      hwOpen
                        ? "opacity-100 visible translate-y-0 pointer-events-auto"
                        : "opacity-0 invisible translate-y-3 pointer-events-none"
                    }`}
                    style={{
                      width: 180,
                      top: hwDropdownStyle.top ?? 0,
                      left: hwDropdownStyle.left ?? 0,
                    }}
                  >
                    <button
                      type="button"
                      onClick={() => { onSelectHotWords(null); setHwOpen(false) }}
                      className="relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group"
                    >
                      <span className="relative z-10 flex items-center gap-2 px-2 py-2 w-full text-[10px]">
                        <span className={`sk-diamond ${!hotWordsLibraryId ? "on" : ""}`} aria-hidden />
                        <span>None</span>
                      </span>
                      <span className="absolute inset-0 z-0 bg-primary transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
                    </button>
                    {hotWordsLibraries.map((lib) => (
                      <button
                        key={lib.id}
                        type="button"
                        onClick={() => { onSelectHotWords(lib.id); setHwOpen(false) }}
                        className="relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group"
                      >
                        <span className="relative z-10 flex items-center gap-2 px-2 py-2 w-full text-[10px]">
                          <span className={`sk-diamond ${hotWordsLibraryId === lib.id ? "on" : ""}`} aria-hidden />
                          <span className="flex-1 whitespace-normal break-words min-w-0 leading-snug">{lib.name}</span>
                          <span className="text-[9px] text-muted-foreground/60 shrink-0 ml-1">{lib.word_count}w</span>
                        </span>
                        <span className="absolute inset-0 z-0 bg-primary transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
                      </button>
                    ))}
                    {hotWordsLibraries.length === 0 && (
                      <div className="px-2 py-3 text-[10px] text-muted-foreground text-center">No hot word libraries</div>
                    )}
                  </div>,
                  document.body
                )}
              </div>
            )}

            {/* Language — multi-select dropdown */}
            {showLanguageSelector && (
              <div ref={langMenuRef} className="relative">
                <Tooltip>
                  <TooltipTrigger
                    render={
                      <button
                        ref={langBtnRef}
                        type="button"
                        onClick={() => { setLangOpen(!langOpen); setHwOpen(false) }}
                        className={cn(
                          "h-7 w-7 flex items-center justify-center rounded-sm transition-colors",
                          langCustomized
                            ? "text-primary hover:bg-primary/10"
                            : "text-muted-foreground hover:text-foreground hover:bg-accent"
                        )}
                      >
                        <Languages className="h-3.5 w-3.5" />
                      </button>
                    }
                  />
                  <TooltipContent side="top" className="px-2.5 py-1.5 text-[11px] bg-[#0A120E] text-[#FAFAF7] rounded-[3px]">
                    Language{langCustomized ? ` · ${langCount} selected` : " · auto"}
                  </TooltipContent>
                </Tooltip>

                {createPortal(
                  <div
                    ref={langDropdownRef}
                    className={`fixed z-[100] flex-col items-center overflow-hidden rounded border border-primary/30 bg-popover/60 backdrop-blur-md shadow-lg transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] ${
                      langOpen
                        ? "opacity-100 visible translate-y-0 pointer-events-auto"
                        : "opacity-0 invisible translate-y-3 pointer-events-none"
                    }`}
                    style={{
                      width: 200,
                      top: langDropdownStyle.top ?? 0,
                      left: langDropdownStyle.left ?? 0,
                    }}
                  >
                    {languageHintOptions.map(({ code, label }) => {
                      const isSelected = languageHints.includes(code)
                      return (
                        <button
                          key={code}
                          type="button"
                          onClick={() => {
                            if (isSelected) {
                              onChangeLanguageHints?.(languageHints.filter((c) => c !== code))
                            } else {
                              onChangeLanguageHints?.([...languageHints, code])
                            }
                          }}
                          className="relative flex items-center gap-2 w-full cursor-pointer overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] text-muted-foreground hover:text-primary-foreground group"
                        >
                          <span className="relative z-10 flex items-center gap-2 px-2 py-2 w-full text-[10px]">
                            <span className={cn(
                              "h-3.5 w-3.5 rounded border-2 flex items-center justify-center shrink-0 transition-colors",
                              isSelected
                                ? "bg-primary border-primary"
                                : "border-muted-foreground/40"
                            )}>
                              {isSelected && (
                                <Check className="h-2.5 w-2.5 text-primary-foreground" strokeWidth={4} />
                              )}
                            </span>
                            <span className="flex-1 whitespace-normal break-words min-w-0 leading-snug">{label}</span>
                            <span className="text-[9px] text-muted-foreground/60 shrink-0 ml-1 font-mono">{code}</span>
                          </span>
                          <span className="absolute inset-0 z-0 bg-primary transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] scale-x-0 origin-left group-hover:scale-x-100 group-hover:origin-right" />
                        </button>
                      )
                    })}
                  </div>,
                  document.body
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  // No audio — upload / record
  return (
    <div className="flex items-center gap-2 py-3 px-0">
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
      <Button variant="outline" size="sm" onClick={() => inputRef.current?.click()}>
        <Upload className="h-4 w-4 mr-1" />
        Audio
      </Button>
      <Button variant="outline" size="sm" onClick={onStartRecord}>
        <Mic className="h-4 w-4 mr-1" />
        Record
      </Button>
      {hasRealtimeProvider && onToggleRealtime && (
        <button
          type="button"
          onClick={onToggleRealtime}
          className={cn(
            "flex items-center gap-1.5 text-xs px-2 py-1 rounded-md border transition-colors ml-auto",
            realtimeEnabled
              ? "border-primary/30 text-primary"
              : "border-border text-muted-foreground"
          )}
          title={realtimeEnabled ? "Live captions ON" : "Live captions OFF"}
        >
          <div className={cn("w-2 h-2 rounded-full", realtimeEnabled ? "bg-green-500" : "bg-muted-foreground/30")} />
          Live captions
        </button>
      )}
    </div>
  )
})

function ProcessingBar({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 px-1">
      <Loader2 className="h-3 w-3 animate-spin text-primary shrink-0" />
      {label && <span className="text-xs text-muted-foreground">{label}</span>}
      <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
        <div className="h-full bg-primary/70 rounded-full animate-progress" style={{ width: "40%" }} />
      </div>
    </div>
  )
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`
}
