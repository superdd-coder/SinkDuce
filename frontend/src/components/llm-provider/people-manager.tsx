import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { Play, Trash2, Users, UserRound } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { FieldLabel } from "@/components/ui/field-label"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"
import { toast } from "sonner"
import {
  deletePerson,
  getPerson,
  getSpeakerPreview,
  listPeople,
  speakerPreviewAudioUrl,
  updatePerson,
  type SpeakerPerson,
  type SpeakerPersonDetail,
} from "@/api/client"
import { triggerMePersonRefresh } from "@/lib/me-person-refresh"

const MAIN_FADE_MS = 220

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  )
}

function waitMs(ms: number) {
  return new Promise<void>((resolve) => window.setTimeout(resolve, ms))
}

function personHue(id: string): string {
  let h = 0
  for (let i = 0; i < id.length; i++) h = (h * 33 + id.charCodeAt(i)) >>> 0
  return `hsl(${h % 360} 36% 38%)`
}

function initialOf(label: string): string {
  const t = label.trim()
  return t ? t[0]!.toUpperCase() : "?"
}

function formatSpeech(sec: number): string {
  if (!sec || sec < 1) return "0s"
  if (sec < 60) return `${Math.round(sec)}s`
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return s ? `${m}m ${s}s` : `${m}m`
}

function SlideConfirmDelete({
  onConfirm,
  label = "Delete",
}: {
  onConfirm: () => void
  label?: string
}) {
  const [armed, setArmed] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const btnRef = useRef<HTMLButtonElement>(null)

  const disarm = useCallback(() => {
    setArmed(false)
    if (timer.current) {
      clearTimeout(timer.current)
      timer.current = null
    }
  }, [])

  useEffect(() => {
    if (!armed) return
    const onDown = (ev: Event) => {
      if (btnRef.current?.contains(ev.target as Node)) return
      disarm()
    }
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") disarm()
    }
    const t = window.setTimeout(() => {
      document.addEventListener("pointerdown", onDown, true)
      document.addEventListener("keydown", onKey, true)
    }, 0)
    timer.current = setTimeout(disarm, 4000)
    return () => {
      window.clearTimeout(t)
      document.removeEventListener("pointerdown", onDown, true)
      document.removeEventListener("keydown", onKey, true)
    }
  }, [armed, disarm])

  return (
    <button
      ref={btnRef}
      type="button"
      className={cn("pm-people-pill", armed && "is-danger")}
      onClick={() => {
        if (!armed) {
          setArmed(true)
          return
        }
        disarm()
        onConfirm()
      }}
    >
      <Trash2 className="size-3.5" />
      {armed ? "Confirm delete" : label}
    </button>
  )
}

export function PeopleManager({
  open,
  onOpenChange,
  nested = false,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  nested?: boolean
}) {
  const [people, setPeople] = useState<SpeakerPerson[]>([])
  const [query, setQuery] = useState("")
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<SpeakerPersonDetail | null>(null)
  const [mainIn, setMainIn] = useState(true)
  const [playing, setPlaying] = useState(false)
  const lastClip = useRef<{ meeting_id: string; start: number } | null>(null)
  const [indicator, setIndicator] = useState({ top: 0, height: 0 })
  const [indicatorReady, setIndicatorReady] = useState(false)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const itemRefs = useRef<Map<string, HTMLButtonElement>>(new Map())

  const loadList = useCallback(async () => {
    try {
      const rows = await listPeople(query || undefined)
      setPeople(rows)
      setSelectedId((cur) => {
        if (cur && rows.some((p) => p.id === cur)) return cur
        return rows[0]?.id ?? null
      })
    } catch {
      setPeople([])
    }
  }, [query])

  useEffect(() => {
    if (open) void loadList()
  }, [open, loadList])

  useEffect(() => {
    let cancelled = false
    let fadeInRaf1 = 0
    let fadeInRaf2 = 0

    if (!open) return

    if (!selectedId) {
      setMainIn(false)
      ;(async () => {
        if (!prefersReducedMotion()) await waitMs(MAIN_FADE_MS)
        if (cancelled) return
        setDetail(null)
        fadeInRaf1 = requestAnimationFrame(() => {
          fadeInRaf2 = requestAnimationFrame(() => {
            if (!cancelled) setMainIn(true)
          })
        })
      })()
      return () => {
        cancelled = true
        if (fadeInRaf1) cancelAnimationFrame(fadeInRaf1)
        if (fadeInRaf2) cancelAnimationFrame(fadeInRaf2)
      }
    }

    setMainIn(false)
    ;(async () => {
      await new Promise<void>((r) => {
        requestAnimationFrame(() => requestAnimationFrame(() => r()))
      })
      if (cancelled) return
      if (!prefersReducedMotion()) await waitMs(MAIN_FADE_MS)
      if (cancelled) return
      try {
        const next = await getPerson(selectedId)
        if (cancelled) return
        setDetail(next)
        fadeInRaf1 = requestAnimationFrame(() => {
          fadeInRaf2 = requestAnimationFrame(() => {
            if (!cancelled) setMainIn(true)
          })
        })
      } catch {
        if (!cancelled) {
          setDetail(null)
          setMainIn(true)
        }
      }
    })()

    return () => {
      cancelled = true
      if (fadeInRaf1) cancelAnimationFrame(fadeInRaf1)
      if (fadeInRaf2) cancelAnimationFrame(fadeInRaf2)
    }
  }, [open, selectedId])

  useLayoutEffect(() => {
    if (!selectedId) {
      setIndicatorReady(false)
      return
    }
    const el = itemRefs.current.get(selectedId)
    if (!el) return
    setIndicator({ top: el.offsetTop, height: el.offsetHeight })
    requestAnimationFrame(() => setIndicatorReady(true))
  }, [selectedId, people])

  useEffect(() => () => {
    audioRef.current?.pause()
    if (saveTimer.current) clearTimeout(saveTimer.current)
  }, [])

  const scheduleSave = (patch: { display_name?: string; disambiguator?: string }) => {
    if (!selectedId) return
    setDetail((d) => (d ? { ...d, ...patch } : d))
    if (saveTimer.current) clearTimeout(saveTimer.current)
    const id = selectedId
    saveTimer.current = setTimeout(() => {
      void updatePerson(id, patch)
        .then(() => void loadList())
        .catch(() => toast.error("Failed to save"))
    }, 420)
  }

  const handlePlay = async () => {
    if (!detail) return
    audioRef.current?.pause()
    try {
      const clip = await getSpeakerPreview(detail.id, {
        exclude_meeting: lastClip.current?.meeting_id,
        exclude_start: lastClip.current?.start,
      })
      lastClip.current = { meeting_id: clip.meeting_id, start: clip.start }
      const el = new Audio(speakerPreviewAudioUrl(detail.id, clip))
      audioRef.current = el
      setPlaying(true)
      el.addEventListener("ended", () => setPlaying(false))
      el.addEventListener("error", () => {
        setPlaying(false)
        toast.error("No playable clip")
      })
      await el.play()
    } catch {
      setPlaying(false)
      toast.error("No playable clip")
    }
  }

  const handleToggleMe = async () => {
    if (!detail) return
    const next = !detail.is_me
    try {
      const updated = await updatePerson(detail.id, { is_me: next })
      setDetail((d) => (d ? { ...d, is_me: updated.is_me } : d))
      setPeople((prev) =>
        prev.map((p) => ({
          ...p,
          is_me: !!(updated.is_me && p.id === updated.id),
        })),
      )
      triggerMePersonRefresh(updated.is_me ? updated.id : null)
      await loadList()
      toast.success(updated.is_me ? "Marked as you" : "Unmarked")
    } catch {
      toast.error("Failed to update Me")
    }
  }

  const handleDelete = async () => {
    if (!detail) return
    try {
      const wasMe = !!detail.is_me
      await deletePerson(detail.id)
      setSelectedId(null)
      setDetail(null)
      if (wasMe) triggerMePersonRefresh(null)
      await loadList()
    } catch {
      toast.error("Failed to delete person")
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "pm-dialog pm-dialog--silk pm-settings-hw-dialog",
          "sm:max-w-6xl h-[80vh]",
          "!animate-none data-open:!animate-none data-closed:!animate-none",
          nested && "pm-dialog-layer-nested",
        )}
        overlayClassName={cn(
          "pm-dialog-overlay--silk",
          nested && "pm-dialog-layer-nested-overlay",
        )}
      >
        <DialogHeader className="shrink-0">
          <DialogKicker>Settings</DialogKicker>
          <DialogTitle>People</DialogTitle>
        </DialogHeader>

        <div className="pm-settings-hw pm-people-shell">
          <div className="pm-settings-hw-rail">
            <div className="pm-settings-hw-rail-head">
              <span className="pm-label text-[var(--pm-ink)]">Directory</span>
              <span className="pm-settings-hw-count">{people.length}</span>
            </div>
            <div className="px-3 pb-2">
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search"
                className="pm-settings-hw-input"
              />
            </div>
            <ScrollArea className="flex-1 min-h-0">
              <div ref={listRef} className="pm-settings-hw-lib-list">
                {selectedId ? (
                  <div
                    className={cn(
                      "pm-settings-hw-indicator",
                      indicatorReady && "is-ready",
                    )}
                    style={{
                      transform: `translateY(${indicator.top}px)`,
                      height: indicator.height,
                    }}
                    aria-hidden
                  />
                ) : null}
                {people.map((p) => (
                  <button
                    key={p.id}
                    ref={(el) => {
                      if (el) itemRefs.current.set(p.id, el)
                      else itemRefs.current.delete(p.id)
                    }}
                    type="button"
                    className={cn(
                      "group pm-settings-hw-lib pm-people-rail-row",
                      selectedId === p.id && "is-active",
                    )}
                    onClick={() => setSelectedId(p.id)}
                  >
                    <span
                      className="pm-people-avatar"
                      style={{ background: personHue(p.id) }}
                      aria-hidden
                    >
                      {initialOf(p.label || p.display_name)}
                    </span>
                    <span className="min-w-0 flex-1 text-left">
                      <span className="pm-people-rail-name">
                        {p.label}
                        {p.is_me && <span className="pm-people-me-tag">Me</span>}
                      </span>
                      <span className="pm-people-rail-meta">
                        {p.has_voiceprint ? formatSpeech(p.speech_sec) : "No print"}
                      </span>
                    </span>
                    {p.has_voiceprint && <span className="pm-people-dot" aria-hidden />}
                  </button>
                ))}
                {people.length === 0 && (
                  <p className="pm-meta p-2 text-center">No people yet</p>
                )}
              </div>
            </ScrollArea>
          </div>

          <div className="pm-settings-hw-main">
            <div className={cn("pm-settings-hw-main-body", mainIn && "is-in")}>
              {detail ? (
                <>
                  <section className="pm-people-identity" aria-label="Person">
                    <div className="pm-people-identity-top">
                      <span
                        className="pm-people-avatar pm-people-avatar--lg"
                        style={{ background: personHue(detail.id) }}
                        aria-hidden
                      >
                        {initialOf(detail.display_name)}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="pm-people-kicker">
                          {detail.has_voiceprint ? "Enrolled" : "Unenrolled"}
                          {detail.has_voiceprint
                            ? ` · ${formatSpeech(detail.speech_sec)}`
                            : ""}
                        </p>
                        <h3 className="pm-people-display">{detail.display_name || "Unnamed"}</h3>
                      </div>
                      <div className="pm-people-pills">
                        <button
                          type="button"
                          className={cn("pm-people-pill", detail.is_me && "is-on")}
                          title={detail.is_me ? "Unmark as you" : "Mark as you"}
                          onClick={() => void handleToggleMe()}
                        >
                          <UserRound className="size-3.5" />
                          Me
                        </button>
                        <button
                          type="button"
                          className={cn("pm-people-pill", playing && "is-on")}
                          onClick={() => void handlePlay()}
                        >
                          <Play className="size-3.5" />
                          {playing ? "Next clip" : "Listen"}
                        </button>
                        <SlideConfirmDelete onConfirm={() => void handleDelete()} />
                      </div>
                    </div>
                    <div className="pm-people-fields">
                      <div className="pm-settings-hw-field pm-settings-hw-field--name">
                        <FieldLabel className="pm-settings-hw-field-label">Name</FieldLabel>
                        <Input
                          className="pm-settings-hw-input"
                          value={detail.display_name}
                          onChange={(e) => scheduleSave({ display_name: e.target.value })}
                        />
                      </div>
                      <div className="pm-settings-hw-field">
                        <FieldLabel className="pm-settings-hw-field-label">Note</FieldLabel>
                        <Input
                          className="pm-settings-hw-input"
                          value={detail.disambiguator}
                          placeholder="Engineering, Client…"
                          onChange={(e) => scheduleSave({ disambiguator: e.target.value })}
                        />
                      </div>
                    </div>
                  </section>

                  <section className="pm-people-history" aria-label="Meetings">
                    <div className="pm-settings-hw-words-head">
                      <div className="pm-settings-hw-words-title">
                        <span className="pm-settings-hw-words-label">Meetings</span>
                        <span className="pm-settings-hw-count">{detail.meetings.length}</span>
                      </div>
                    </div>
                    {detail.meetings.length === 0 ? (
                      <div className="pm-settings-hw-words-empty">
                        <p className="pm-settings-hw-words-empty-title">No meetings yet</p>
                        <p className="pm-settings-hw-words-empty-hint">
                          Assign this person on a Speakers card to start a history.
                        </p>
                      </div>
                    ) : (
                      <ul className="pm-people-meetings">
                        {detail.meetings.map((row) => (
                          <li key={row.meeting_id} className="pm-people-meeting-card">
                            <span className="pm-people-meeting-title">{row.title}</span>
                            <span className="pm-meta">
                              {row.speaker_id ? `Slot ${row.speaker_id}` : "Seen"}
                              {row.speech_sec ? ` · ${formatSpeech(row.speech_sec)}` : ""}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>
                </>
              ) : (
                <div className="pm-settings-hw-empty">
                  <div className="pm-settings-hw-empty-icon" aria-hidden>
                    <Users className="size-5" />
                  </div>
                  <p className="pm-settings-hw-empty-title">People directory</p>
                  <p className="pm-settings-hw-empty-hint">
                    Names assigned in Meetings live here. Rename, listen, or remove.
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
