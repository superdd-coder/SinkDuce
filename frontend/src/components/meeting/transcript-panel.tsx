import { useState, useMemo, useEffect, useRef } from "react"
import { ChevronRight, ChevronLeft, Clock, Pencil, Check, Search } from "lucide-react"
import { cn } from "@/lib/utils"
import { Tabs, TabsList, TabsTrigger, TabsIndicator, TabsContent } from "@/components/ui/tabs"
import type { TranscriptSegment } from "@/api/client"

interface TranscriptPanelProps {
  open: boolean
  onToggle: () => void
  segments: TranscriptSegment[]
  partialText?: string
  onSegmentClick?: (startTime: number) => void
  focusRef?: { id: string; ts: number } | null
  activeSectionTag?: string
  speakerNames?: Record<string, string>
  onUpdateSpeakerName?: (speakerId: string, name: string) => void
  isRealtime?: boolean
}

export function TranscriptPanel({
  open,
  onToggle,
  segments,
  partialText,
  onSegmentClick,
  focusRef,
  activeSectionTag,
  speakerNames = {},
  onUpdateSpeakerName,
  isRealtime = false,
}: TranscriptPanelProps) {
  const [tab, setTab] = useState("transcript")

  return (
    <div
      className={cn(
        "border-l border-border flex flex-col shrink-0 transition-all duration-200",
        open ? "w-72" : "w-10"
      )}
    >
      <button
        className="flex items-center justify-center h-8 hover:bg-accent transition-colors shrink-0"
        onClick={onToggle}
      >
        {open ? (
          <div className="flex items-center gap-2 text-sm font-light uppercase tracking-wider w-full px-3">
            <span className="flex-1 text-left">Transcript</span>
            {isRealtime && (
              <span className="flex items-center gap-1 text-xs text-red-500">
                <span className="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse" />
                live
              </span>
            )}
            <span className="text-xs text-muted-foreground">{segments.length}</span>
            <ChevronRight className="h-4 w-4 text-muted-foreground" />
          </div>
        ) : (
          <ChevronLeft className="h-4 w-4 text-muted-foreground" />
        )}
      </button>
      {open && (
        <>
          {/* Tab bar */}
          <Tabs value={tab} onValueChange={setTab} className="flex flex-col flex-1 min-h-0">
            <TabsList className="relative w-full px-1" variant="line">
              <TabsIndicator renderBeforeHydration />
              <TabsTrigger value="transcript" className="flex-1 font-light uppercase tracking-wider after:!opacity-0">TRANSCRIPT</TabsTrigger>
              <TabsTrigger value="speakers" className="flex-1 font-light uppercase tracking-wider after:!opacity-0">SPEAKER</TabsTrigger>
            </TabsList>

            <TabsContent key={`transcript-${tab}`} value="transcript" className="flex-1 min-h-0 overflow-y-auto animate-tab-in">
              <TranscriptTab
                segments={segments}
                partialText={partialText}
                onSegmentClick={onSegmentClick}
                focusRef={focusRef}
                activeSectionTag={activeSectionTag}
                speakerNames={speakerNames}
              />
            </TabsContent>
            <TabsContent key={`speakers-${tab}`} value="speakers" className="flex-1 min-h-0 overflow-y-auto animate-tab-in">
              <SpeakersTab
                segments={segments}
                speakerNames={speakerNames}
                onUpdateSpeakerName={onUpdateSpeakerName}
                onSegmentClick={onSegmentClick}
                activeSectionTag={activeSectionTag}
              />
            </TabsContent>
          </Tabs>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Transcript tab
// ---------------------------------------------------------------------------

export function TranscriptTab({
  segments,
  partialText,
  onSegmentClick,
  focusRef,
  activeSectionTag,
  speakerNames,
  showSearch = true,
}: {
  segments: TranscriptSegment[]
  partialText?: string
  onSegmentClick?: (startTime: number) => void
  focusRef?: { id: string; ts: number } | null
  activeSectionTag?: string
  speakerNames: Record<string, string>
  showSearch?: boolean
}) {
  const [search, setSearch] = useState("")
  const [focusedIdx, setFocusedIdx] = useState(-1)
  const containerRef = useRef<HTMLDivElement>(null)
  const query = search.toLowerCase().trim()

  const filtered = useMemo(() => {
    if (!query) return segments
    return segments.filter(
      (seg) =>
        seg.text.toLowerCase().includes(query) ||
        (seg.speaker_id && (speakerNames[seg.speaker_id] ?? `Speaker ${seg.speaker_id}`).toLowerCase().includes(query))
    )
  }, [segments, query, speakerNames])

  // Scroll to focused sentence when ref is clicked
  useEffect(() => {
    if (!focusRef?.id || !containerRef.current) return
    const container = containerRef.current
    const idx = segments.findIndex((seg) => seg.sentence_id?.endsWith(focusRef.id))
    if (idx === -1) return
    setFocusedIdx(idx)
    // Defer slightly so the layout (especially the floating panel width
    // transition) has a chance to settle before we measure positions.
    const raf = requestAnimationFrame(() => {
      const items = container.querySelectorAll("[data-seg-idx]")
      const el = items[idx] as HTMLElement | undefined
      if (!el) return
      // Manual scrollTop — never propagates to document viewport,
      // unlike scrollIntoView which can leak when the container is mid-animation.
      const containerTop = container.getBoundingClientRect().top
      const elTop = el.getBoundingClientRect().top
      const offset = elTop - containerTop + container.scrollTop
        - container.clientHeight / 2 + el.offsetHeight / 2
      container.scrollTo({ top: offset, behavior: "smooth" })
    })
    // Clear highlight after 2s
    const timer = setTimeout(() => setFocusedIdx(-1), 2000)
    return () => { cancelAnimationFrame(raf); clearTimeout(timer) }
  }, [focusRef?.ts, focusRef?.id, segments])

  const highlight = (text: string) => {
    if (!query) return text
    const idx = text.toLowerCase().indexOf(query)
    if (idx === -1) return text
    return (
      <>
        {text.slice(0, idx)}
        <mark className="bg-yellow-200 dark:bg-yellow-800 rounded px-0.5">{text.slice(idx, idx + query.length)}</mark>
        {text.slice(idx + query.length)}
      </>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Search bar */}
      {showSearch && (
        <div className="px-2 pt-2 pb-1">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
            <input
              type="text"
              placeholder="Search transcript..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full h-7 pl-7 pr-2 text-xs rounded-md border border-input bg-background"
            />
          </div>
          {query && (
            <p className="text-[10px] text-muted-foreground mt-1">{filtered.length} of {segments.length} segments</p>
          )}
        </div>
      )}

      <div ref={containerRef} className="flex-1 overflow-auto p-2 space-y-2.5">
        {filtered.length === 0 && !partialText && (
          <p className="text-xs text-muted-foreground text-center py-8">
            {query ? "No matching segments" : "No transcript yet"}
          </p>
        )}
        {filtered.map((seg, i) => {
          const displayName = seg.speaker_id
            ? speakerNames[seg.speaker_id] ?? `Speaker ${seg.speaker_id}`
            : null
          // Find original index in full segments array for highlight matching
          const origIdx = segments.indexOf(seg)
          return (
            <div
              key={`${seg.start}-${i}`}
              data-seg-idx={origIdx}
              className={cn(
                "rounded-md px-2 py-1.5 -mx-1 transition-colors",
                onSegmentClick && "cursor-pointer hover:bg-accent",
                query && seg.text.toLowerCase().includes(query) && "ring-1 ring-primary/30",
                origIdx === focusedIdx && "ring-2 ring-primary bg-primary/5"
              )}
              onClick={() => onSegmentClick?.(seg.start)}
            >
              <div className="flex items-center gap-2 mb-1">
                {displayName && (
                  <span className="text-xs font-light text-primary bg-primary/10 px-1.5 py-0.5 rounded">
                    {highlight(displayName)}
                  </span>
                )}
                {seg.section_tags && seg.section_tags.length > 0 && (
                  <span className="flex items-center gap-1">
                    {seg.section_tags.map((tag) => {
                      const isActive = activeSectionTag === tag
                      return (
                        <span
                          key={tag}
                          className={cn(
                            "text-[10px] font-light px-1 py-0.5 rounded border",
                            isActive
                              ? "border-green-500/40 text-green-600 bg-green-500/10"
                              : "border-border text-muted-foreground bg-muted/50"
                          )}
                          title={tag}
                        >
                          {sectionTagLabel(tag)}
                        </span>
                      )
                    })}
                  </span>
                )}
                <span className="flex items-center gap-1 text-xs text-muted-foreground">
                  <Clock className="h-3 w-3" />
                  {formatTime(seg.start)} – {formatTime(seg.end)}
                </span>
              </div>
              <p className="text-xs text-foreground leading-relaxed pl-0">
                {highlight(seg.text)}
              </p>
            </div>
          )
        })}
        {partialText && (
          <div className="rounded-md px-2 py-1.5 -mx-1 border border-primary/20">
            <p className="text-xs text-foreground/80 italic">{partialText}</p>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Speakers tab
// ---------------------------------------------------------------------------

export function SpeakersTab({
  segments,
  speakerNames,
  onUpdateSpeakerName,
  onSegmentClick,
  activeSectionTag,
}: {
  segments: TranscriptSegment[]
  speakerNames: Record<string, string>
  onUpdateSpeakerName?: (speakerId: string, name: string) => void
  onSegmentClick?: (startTime: number) => void
  activeSectionTag?: string
}) {
  // Extract unique speakers and pick 5 random samples each
  const speakers = useMemo(() => {
    const grouped: Record<string, TranscriptSegment[]> = {}
    for (const seg of segments) {
      const id = seg.speaker_id ?? "unknown"
      if (!grouped[id]) grouped[id] = []
      grouped[id].push(seg)
    }
    return Object.entries(grouped)
      .sort(([a], [b]) => Number(a) - Number(b))
      .map(([id, segs]) => {
        // Pick 5 random samples that are at least 3 seconds long
        const longEnough = segs.filter((s) => s.end - s.start >= 3)
        const pool = longEnough.length >= 5 ? longEnough : segs
        const shuffled = [...pool].sort(() => Math.random() - 0.5)
        return { id, segments: segs, samples: shuffled.slice(0, 5) }
      })
  }, [segments])

  if (speakers.length === 0) {
    return (
      <p className="text-xs text-muted-foreground text-center py-8">
        No speakers identified
      </p>
    )
  }

  return (
    <div className="p-2 space-y-4">
      {speakers.map((speaker) => (
        <SpeakerCard
          key={speaker.id}
          speakerId={speaker.id}
          displayName={speakerNames[speaker.id]}
          segmentCount={speaker.segments.length}
          samples={speaker.samples}
          onUpdateName={(name) => onUpdateSpeakerName?.(speaker.id, name)}
          onSegmentClick={onSegmentClick}
          activeSectionTag={activeSectionTag}
        />
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Speaker card with inline editing
// ---------------------------------------------------------------------------

function SpeakerCard({
  speakerId,
  displayName,
  segmentCount,
  samples,
  onUpdateName,
  onSegmentClick,
  activeSectionTag,
}: {
  speakerId: string
  displayName?: string
  segmentCount: number
  samples: TranscriptSegment[]
  activeSectionTag?: string
  onUpdateName: (name: string) => void
  onSegmentClick?: (startTime: number) => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(displayName ?? `Speaker ${speakerId}`)

  const label = displayName ?? `Speaker ${speakerId}`

  const handleSave = () => {
    if (draft.trim()) {
      onUpdateName(draft.trim())
    }
    setEditing(false)
  }

  return (
    <div className="border border-border rounded-lg p-2 space-y-2">
      {/* Speaker header */}
      <div className="flex items-center gap-2">
        <span className="text-xs font-light text-primary bg-primary/10 px-2 py-1 rounded">
          {speakerId}
        </span>
        {editing ? (
          <div className="flex items-center gap-1 flex-1 min-w-0">
            <input
              className="flex-1 text-sm font-medium bg-transparent border-b border-primary outline-none px-0 py-0.5 min-w-0"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSave()
                if (e.key === "Escape") { setDraft(label); setEditing(false) }
              }}
              autoFocus
            />
            <button
              className="p-1 rounded hover:bg-accent text-primary"
              onClick={handleSave}
            >
              <Check className="h-3.5 w-3.5" />
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-1 flex-1 min-w-0">
            <span className="text-sm font-medium truncate">{label}</span>
            <button
              className="p-1 rounded hover:bg-accent text-muted-foreground opacity-0 group-hover:opacity-100"
              style={{ opacity: 1 }}
              onClick={() => { setDraft(label); setEditing(true) }}
            >
              <Pencil className="h-3 w-3" />
            </button>
          </div>
        )}
        <span className="text-xs text-muted-foreground shrink-0">
          {segmentCount} segments
        </span>
      </div>

      {/* Sample segments */}
      <div className="space-y-1">
        {samples.map((seg, i) => (
          <div
            key={i}
            className={cn(
              "text-xs px-2 py-1.5 rounded transition-colors flex items-start gap-1.5",
              onSegmentClick && "cursor-pointer hover:bg-accent"
            )}
            onClick={() => onSegmentClick?.(seg.start)}
          >
            <span className="text-muted-foreground shrink-0">{formatTime(seg.start)}</span>
            <span className="text-foreground flex-1">{seg.text.length > 80 ? seg.text.slice(0, 80) + "..." : seg.text}</span>
            {seg.section_tags && seg.section_tags.length > 0 && (
              <span className="flex items-center gap-0.5 shrink-0">
                {seg.section_tags.map((tag) => {
                  const isActive = activeSectionTag === tag
                  return (
                    <span key={tag} className={cn(
                      "text-[10px] font-light px-1 py-0.5 rounded border",
                      isActive
                        ? "border-green-500/40 text-green-600 bg-green-500/10"
                        : "border-border text-muted-foreground bg-muted/50"
                    )}>
                      {sectionTagLabel(tag)}
                    </span>
                  )
                })}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, "0")}`
}

/** Convert tab_sec_01 → T1, tab_sec_02 → T2 */
export function sectionTagLabel(tag: string): string {
  const m = tag.match(/^tab_sec_(\d+)$/)
  if (m) return `T${parseInt(m[1], 10)}`
  return tag
}
