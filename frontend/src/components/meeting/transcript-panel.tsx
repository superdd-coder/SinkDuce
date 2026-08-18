import { useState, useMemo, useEffect, useRef, useCallback } from "react"
import { ChevronRight, ChevronLeft, Clock, Search } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tabs, TabsList, TabsTrigger, TabsIndicator, TabsContent } from "@/components/ui/tabs"
import { useScrollEdgeFade } from "@/hooks/use-scroll-edge-fade"
import type { Meeting, SpeakerMatch, TranscriptSegment } from "@/api/client"
import { PeoplePicker } from "@/components/meeting/people-picker"

interface TranscriptPanelProps {
  open: boolean
  onToggle: () => void
  segments: TranscriptSegment[]
  partialText?: string
  onSegmentClick?: (startTime: number, endTime?: number) => void
  focusRef?: { id: string; ts: number } | null
  activeSectionTag?: string
  speakerNames?: Record<string, string>
  meetingId?: string
  speakerMatches?: Record<string, SpeakerMatch>
  onPersonAssigned?: (meeting: Meeting) => void
  isRealtime?: boolean
  tabs?: { tab_id: string; type?: string; md_file_path?: string }[]
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
  meetingId,
  speakerMatches,
  onPersonAssigned,
  isRealtime = false,
  tabs,
}: TranscriptPanelProps) {
  const [tab, setTab] = useState("transcript")

  return (
    <div
      className={cn(
        "flex flex-col shrink-0 transition-[width] duration-300 ease-[cubic-bezier(0.32,0.72,0,1)]",
        open ? "w-72" : "w-10",
      )}
    >
      <Button
        type="button"
        variant="ghost"
        className="h-8 w-full justify-center shrink-0 rounded-none"
        onClick={onToggle}
      >
        {open ? (
          <div className="flex items-center gap-2 w-full px-1">
            <span className="pm-label flex-1 text-left normal-case tracking-[0.08em]">Transcript</span>
            {isRealtime && (
              <span className="pm-meta flex items-center gap-1 text-[var(--pm-danger)]">
                <span className="h-1.5 w-1.5 rounded-full bg-[var(--pm-danger)] animate-pulse" />
                live
              </span>
            )}
            <span className="pm-meta">{segments.length}</span>
            <ChevronRight className="size-3.5 text-[var(--pm-faint)]" />
          </div>
        ) : (
          <ChevronLeft className="size-3.5 text-[var(--pm-faint)]" />
        )}
      </Button>
      {open && (
        <Tabs value={tab} onValueChange={setTab} className="flex flex-col flex-1 min-h-0">
          <TabsList className="relative w-full px-2">
            <TabsIndicator className="pm-tabs-indicator" renderBeforeHydration />
            <TabsTrigger value="transcript" className="flex-1">Transcript</TabsTrigger>
            <TabsTrigger value="speakers" className="flex-1">Speaker</TabsTrigger>
          </TabsList>

          <TabsContent key={`transcript-${tab}`} value="transcript" className="flex-1 min-h-0 overflow-y-auto animate-tab-in">
            <TranscriptTab
              segments={segments}
              partialText={partialText}
              onSegmentClick={onSegmentClick}
              focusRef={focusRef}
              activeSectionTag={activeSectionTag}
              speakerNames={speakerNames}
              tabs={tabs}
            />
          </TabsContent>
          <TabsContent key={`speakers-${tab}`} value="speakers" className="flex-1 min-h-0 overflow-y-auto animate-tab-in">
            <SpeakersTab
              segments={segments}
              speakerNames={speakerNames}
              meetingId={meetingId}
              speakerMatches={speakerMatches}
              onPersonAssigned={onPersonAssigned}
              onSegmentClick={onSegmentClick}
              activeSectionTag={activeSectionTag}
            />
          </TabsContent>
        </Tabs>
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
  tabs,
  showSearch = true,
  playbackTime = 0,
  /** Live capture: keep latest final/partial segment centered as captions stream in. */
  followLive = false,
}: {
  segments: TranscriptSegment[]
  partialText?: string
  onSegmentClick?: (startTime: number, endTime?: number) => void
  focusRef?: { id: string; ts: number } | null
  activeSectionTag?: string
  speakerNames: Record<string, string>
  tabs?: { tab_id: string; type?: string; md_file_path?: string }[]
  showSearch?: boolean
  playbackTime?: number
  followLive?: boolean
}) {
  const [search, setSearch] = useState("")
  const [focusedIdx, setFocusedIdx] = useState(-1)
  const [playingIdx, setPlayingIdx] = useState(-1)
  const containerRef = useRef<HTMLDivElement>(null)
  /** Live follow: stick until user scrolls away; re-stick when near bottom. */
  const stickToLatestRef = useRef(true)
  const ignoreLiveScrollRef = useRef(false)
  const query = search.toLowerCase().trim()

  const filtered = useMemo(() => {
    if (!query) return segments
    return segments.filter(
      (seg) =>
        seg.text.toLowerCase().includes(query) ||
        (seg.speaker_id && (speakerNames[seg.speaker_id] ?? `Speaker ${seg.speaker_id}`).toLowerCase().includes(query))
    )
  }, [segments, query, speakerNames])

  const edgeFade = useScrollEdgeFade(
    containerRef,
    `${filtered.length}:${partialText ? 1 : 0}`,
  )

  // Scroll to focused sentence when ref is clicked
  useEffect(() => {
    if (!focusRef?.id || !containerRef.current) return
    const container = containerRef.current
    const idx = focusRef.id.startsWith("_idx_")
      ? parseInt(focusRef.id.slice(5), 10)
      : segments.findIndex((seg) => seg.sentence_id?.endsWith(focusRef.id))
    if (idx === -1 || isNaN(idx)) return
    setFocusedIdx(idx)
    // Defer slightly so the layout (especially the floating panel width
    // transition) has a chance to settle before we measure positions.
    const raf = requestAnimationFrame(() => {
      const items = container.querySelectorAll("[data-seg-idx]")
      const el = items[idx] as HTMLElement | undefined
      if (!el) return
      // When the container has overflow (floating panel portal), use manual
      // scrollTo to avoid leaking scroll to the document viewport during
      // the width-transition animation.
      // When the container does NOT overflow (transcript tab — its flex parent
      // has auto height), fall back to scrollIntoView which targets the nearest
      // scrollable ancestor (the meeting content scroll area).
      if (container.scrollHeight > container.clientHeight) {
        const containerTop = container.getBoundingClientRect().top
        const elTop = el.getBoundingClientRect().top
        const offset = elTop - containerTop + container.scrollTop
          - container.clientHeight / 2 + el.offsetHeight / 2
        container.scrollTo({ top: offset, behavior: "smooth" })
      } else {
        el.scrollIntoView({ behavior: "smooth", block: "center" })
      }
    })
    // Clear highlight after 2s
    const timer = setTimeout(() => setFocusedIdx(-1), 2000)
    return () => { cancelAnimationFrame(raf); clearTimeout(timer) }
  }, [focusRef?.ts, focusRef?.id, segments])

  const LIVE_BOTTOM_PX = 96
  const ignoreClearTimerRef = useRef(0)

  const centerLiveLatest = useCallback(() => {
    if (!followLive || !stickToLatestRef.current) return
    const container = containerRef.current
    if (!container) return
    const target =
      (container.querySelector("[data-seg-live]") as HTMLElement | null) ??
      ([...container.querySelectorAll("[data-seg-idx]")].at(-1) as
        | HTMLElement
        | undefined) ??
      null
    if (!target) return
    if (container.scrollHeight <= container.clientHeight + 2) return
    const containerTop = container.getBoundingClientRect().top
    const elTop = target.getBoundingClientRect().top
    // Center latest content (instant — partials update often)
    const offset =
      elTop -
      containerTop +
      container.scrollTop -
      container.clientHeight / 2 +
      target.offsetHeight / 2
    ignoreLiveScrollRef.current = true
    container.scrollTo({ top: Math.max(0, offset), behavior: "auto" })
    if (ignoreClearTimerRef.current) window.clearTimeout(ignoreClearTimerRef.current)
    ignoreClearTimerRef.current = window.setTimeout(() => {
      ignoreLiveScrollRef.current = false
      ignoreClearTimerRef.current = 0
    }, 80)
  }, [followLive])

  const unlockLiveStick = useCallback(() => {
    if (!followLive) return
    if (ignoreLiveScrollRef.current) return
    stickToLatestRef.current = false
  }, [followLive])

  // User intent: wheel / touch detaches; scroll-near-bottom re-attaches
  useEffect(() => {
    if (!followLive) return
    const el = containerRef.current
    if (!el) return

    const onWheel = () => unlockLiveStick()
    const onTouch = () => unlockLiveStick()
    const onScroll = () => {
      if (ignoreLiveScrollRef.current) return
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight
      if (dist <= LIVE_BOTTOM_PX) {
        const wasStuck = stickToLatestRef.current
        stickToLatestRef.current = true
        // Re-attach: snap latest to center immediately (don't wait for next caption)
        if (!wasStuck) {
          requestAnimationFrame(() => centerLiveLatest())
        }
      } else {
        stickToLatestRef.current = false
      }
    }

    el.addEventListener("wheel", onWheel, { passive: true })
    el.addEventListener("touchmove", onTouch, { passive: true })
    el.addEventListener("scroll", onScroll, { passive: true })
    return () => {
      el.removeEventListener("wheel", onWheel)
      el.removeEventListener("touchmove", onTouch)
      el.removeEventListener("scroll", onScroll)
    }
  }, [followLive, unlockLiveStick, centerLiveLatest])

  // Reset stick when entering live follow mode
  useEffect(() => {
    if (followLive) stickToLatestRef.current = true
  }, [followLive])

  // Live transcript: keep newest final / partial centered while stuck
  useEffect(() => {
    if (!followLive) return
    if (!stickToLatestRef.current) return
    if (filtered.length === 0 && !partialText) return

    let raf = 0
    let raf2 = 0
    // Double rAF: wait for segment/partial DOM paint before measuring
    raf = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(centerLiveLatest)
    })
    return () => {
      cancelAnimationFrame(raf)
      cancelAnimationFrame(raf2)
    }
  }, [followLive, filtered.length, partialText, segments, centerLiveLatest])

  // Highlight + auto-scroll current segment during continuous playback
  const lastAutoScrollRef = useRef(0)
  const lastPlayingIdxRef = useRef(-1)
  useEffect(() => {
    if (followLive) return
    if (playbackTime == null || playbackTime <= 0) {
      setPlayingIdx(-1)
      lastPlayingIdxRef.current = -1
      return
    }
    // Find segment at current time (prefer containing; fallback last started)
    let idx = segments.findIndex(
      (seg) => seg.start <= playbackTime && playbackTime < seg.end,
    )
    if (idx === -1) {
      for (let i = segments.length - 1; i >= 0; i--) {
        if (segments[i].start <= playbackTime) {
          idx = i
          break
        }
      }
    }
    if (idx === -1) {
      setPlayingIdx(-1)
      lastPlayingIdxRef.current = -1
      return
    }
    setPlayingIdx(idx)

    // Auto-scroll only when the active sentence changes (throttled)
    if (idx === lastPlayingIdxRef.current) return
    lastPlayingIdxRef.current = idx
    const now = Date.now()
    if (now - lastAutoScrollRef.current < 400) return
    lastAutoScrollRef.current = now
    const container = containerRef.current
    if (!container) return
    const items = container.querySelectorAll("[data-seg-idx]")
    const el = items[idx] as HTMLElement | undefined
    if (!el) return
    const containerTop = container.getBoundingClientRect().top
    const elTop = el.getBoundingClientRect().top
    const offset =
      elTop - containerTop + container.scrollTop - container.clientHeight / 3
    container.scrollTo({ top: offset, behavior: "smooth" })
  }, [playbackTime, segments, followLive])

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
    <div className="flex flex-col h-full min-h-0">
      {showSearch && (
        <div className="px-2 pt-2 pb-1 shrink-0">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-[var(--pm-faint)] pointer-events-none" />
            <Input
              type="text"
              placeholder="Search transcript…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="h-8 pl-8 rounded-full"
            />
          </div>
          {query && (
            <p className="pm-meta mt-1">{filtered.length} of {segments.length} segments</p>
          )}
        </div>
      )}

      <div className="pm-panel-scroll-shell">
        <div ref={containerRef} className="flex-1 min-h-0 overflow-auto p-2 space-y-2">
          {filtered.length === 0 && !partialText && (
            <p className="pm-meta text-center py-8">
              {query ? "No matching segments" : "No transcript yet"}
            </p>
          )}
          {filtered.map((seg, i) => {
            const displayName = seg.speaker_id
              ? speakerNames[seg.speaker_id] ?? `Speaker ${seg.speaker_id}`
              : null
            const sentNum: number | null = (() => {
              const id = seg.sentence_id
              if (!id) return null
              const m = id.match(/stt_0*(\d+)/)
              return m ? parseInt(m[1], 10) : null
            })()
            const origIdx = segments.indexOf(seg)
            return (
              <div
                key={`${seg.start}-${i}`}
                data-seg-idx={origIdx}
                className={cn(
                  "pm-meeting-seg",
                  onSegmentClick && "is-clickable",
                  query && seg.text.toLowerCase().includes(query) && "is-focused",
                  origIdx === focusedIdx && "is-focused",
                  origIdx === playingIdx && focusedIdx !== origIdx && "is-playing",
                )}
                onClick={() => onSegmentClick?.(seg.start, seg.end)}
              >
                <div className="flex items-center gap-2 mb-1 flex-wrap">
                  {sentNum != null && (
                    <span className="pm-meta t-mono-family w-7 shrink-0 text-center">
                      {sentNum}
                    </span>
                  )}
                  {displayName && (
                    <span className="pm-meeting-seg-speaker">
                      {highlight(displayName)}
                    </span>
                  )}
                  {seg.section_tags && seg.section_tags.length > 0 && (
                    <span className="flex items-center gap-1">
                      {seg.section_tags.map((tag) => {
                        const label = sectionTagLabel(tag, tabs)
                        if (!label) return null
                        const isActive = activeSectionTag === tag
                        return (
                          <span
                            key={tag}
                            className={cn("pm-meeting-seg-tag", isActive && "is-active")}
                            title={tag}
                          >
                            {label}
                          </span>
                        )
                      })}
                    </span>
                  )}
                  <span className="pm-meta flex items-center gap-1">
                    <Clock className="size-3" />
                    {formatTime(seg.start)} – {formatTime(seg.end)}
                  </span>
                </div>
                <p className="pm-meeting-seg-body">
                  {highlight(seg.text)}
                </p>
              </div>
            )
          })}
          {partialText && (
            <div
              className="pm-meeting-seg pm-meeting-seg--partial"
              data-seg-live
              aria-live="polite"
            >
              <div className="pm-meeting-seg-partial-label">
                <span className="pm-meeting-seg-partial-dot" aria-hidden />
                Live
              </div>
              <p className="pm-meeting-seg-partial-text">{partialText}</p>
            </div>
          )}
        </div>
        <div
          className={cn(
            "pm-rail-edge-fade pm-rail-edge-fade--top",
            edgeFade.top && "is-visible",
          )}
          aria-hidden
        />
        <div
          className={cn(
            "pm-rail-edge-fade pm-rail-edge-fade--bottom",
            edgeFade.bottom && "is-visible",
          )}
          aria-hidden
        />
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
  meetingId,
  speakerMatches,
  onPersonAssigned,
  slotsStatus,
  slotsMs,
  onSegmentClick,
  activeSectionTag,
}: {
  segments: TranscriptSegment[]
  speakerNames: Record<string, string>
  meetingId?: string
  speakerMatches?: Record<string, SpeakerMatch>
  onPersonAssigned?: (meeting: Meeting) => void
  slotsStatus?: Meeting["speaker_slots_status"]
  slotsMs?: number | null
  onSegmentClick?: (startTime: number, endTime?: number) => void
  activeSectionTag?: string
}) {
  const scrollRef = useRef<HTMLDivElement>(null)

  // Unique speakers + up to 5 sample sentences each (stable order — never Math.random)
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
        // Prefer longer utterances; keep chronological order for stable UI
        const byStart = [...segs].sort((a, b) => a.start - b.start)
        const longEnough = byStart.filter((s) => s.end - s.start >= 3)
        const pool = longEnough.length >= 1 ? longEnough : byStart
        // Evenly sample up to 5 from the pool (deterministic, no shuffle)
        const n = Math.min(5, pool.length)
        const samples =
          n <= 0
            ? []
            : n === pool.length
              ? pool
              : Array.from({ length: n }, (_, i) => {
                  const idx = Math.round((i * (pool.length - 1)) / Math.max(1, n - 1))
                  return pool[idx]
                })
        return { id, segments: segs, samples }
      })
  }, [segments])

  const edgeFade = useScrollEdgeFade(scrollRef, speakers.length)

  if (speakers.length === 0) {
    return (
      <p className="pm-meta text-center py-8">
        No speakers identified
      </p>
    )
  }

  return (
    <div className="pm-panel-scroll-shell h-full min-h-0">
      <div ref={scrollRef} className="flex-1 min-h-0 overflow-auto p-2 space-y-3 pt-4">
        {slotsStatus === "computing" && (
          <p className="pm-meta px-1">Computing voiceprints…</p>
        )}
        {slotsStatus === "ready" && slotsMs != null && (
          <p className="pm-meta px-1">Voiceprints ready · {(slotsMs / 1000).toFixed(1)}s</p>
        )}
        {slotsStatus === "unavailable" && (
          <p className="pm-meta px-1">Voiceprints unavailable</p>
        )}
        {speakers.map((speaker) => (
          <SpeakerCard
            key={speaker.id}
            speakerId={speaker.id}
            displayName={speakerNames[speaker.id]}
            segmentCount={speaker.segments.length}
            samples={speaker.samples}
            meetingId={meetingId}
            match={speakerMatches?.[speaker.id]}
            onPersonAssigned={onPersonAssigned}
            onSegmentClick={onSegmentClick}
            activeSectionTag={activeSectionTag}
          />
        ))}
      </div>
      <div
        className={cn(
          "pm-rail-edge-fade pm-rail-edge-fade--top",
          edgeFade.top && "is-visible",
        )}
        aria-hidden
      />
      <div
        className={cn(
          "pm-rail-edge-fade pm-rail-edge-fade--bottom",
          edgeFade.bottom && "is-visible",
        )}
        aria-hidden
      />
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
  meetingId,
  match,
  onPersonAssigned,
  onSegmentClick,
  activeSectionTag,
}: {
  speakerId: string
  displayName?: string
  segmentCount: number
  samples: TranscriptSegment[]
  meetingId?: string
  match?: SpeakerMatch
  onPersonAssigned?: (meeting: Meeting) => void
  activeSectionTag?: string
  onSegmentClick?: (startTime: number, endTime?: number) => void
}) {
  return (
    <div className="pm-meeting-nested space-y-2">
      <div className="flex items-center gap-2">
        <span className="pm-meeting-seg-speaker">{speakerId}</span>
        {meetingId && onPersonAssigned ? (
          <PeoplePicker
            meetingId={meetingId}
            speakerId={speakerId}
            displayName={displayName}
            match={match}
            onAssigned={onPersonAssigned}
          />
        ) : (
          <span className="pm-title truncate flex-1 min-w-0">
            {displayName ?? `Speaker ${speakerId}`}
          </span>
        )}
        <span className="pm-meta shrink-0">
          {segmentCount} segments
        </span>
      </div>

      <div className="space-y-1">
        {samples.map((seg, i) => (
          <div
            key={i}
            className={cn(
              "pm-meeting-seg pm-meeting-seg--sample flex items-center gap-1.5 min-w-0",
              onSegmentClick && "is-clickable",
            )}
            onClick={() => onSegmentClick?.(seg.start, seg.end)}
            title={seg.text}
          >
            <span className="pm-meta shrink-0 tabular-nums">{formatTime(seg.start)}</span>
            <span className="pm-meeting-seg-body pm-meeting-seg-text flex-1 min-w-0">
              {seg.text}
            </span>
            {seg.section_tags && seg.section_tags.length > 0 && (
              <span className="flex items-center gap-0.5 shrink-0">
                {seg.section_tags.map((tag) => {
                  const isActive = activeSectionTag === tag
                  return (
                    <span key={tag} className={cn("pm-meeting-seg-tag", isActive && "is-active")}>
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

/** Convert tab_(sec|blue|cus)_NN → T1, T2, ...
 *
 * When `tabs` is provided, uses the same dynamic sequential indexing as
 * `tabShortLabel()` so transcript tags match Section Summary "(Topic N)".
 * Without tabs, falls back to extracting the number from the tab ID suffix. */
export function sectionTagLabel(tag: string, tabs?: { tab_id: string; type?: string; md_file_path?: string }[]): string {
  if (tabs) {
    const sections = tabs.filter(t => t.type === "section")
    const idx = sections.findIndex(t => t.tab_id === tag)
    if (idx >= 0) return `T${idx + 1}`
    // Tag not found in current tabs — stale, hide it
    return ""
  }
  const m = tag.match(/^tab_(?:(?:sec|blue|cus)_)?(\d+)$/)
  if (m) return `T${parseInt(m[1], 10)}`
  return tag
}
