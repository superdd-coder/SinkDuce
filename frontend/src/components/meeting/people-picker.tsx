import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { ChevronDown, Play, Plus, Search, UserMinus, Users } from "lucide-react"
import { cn } from "@/lib/utils"
import { SoftMenu } from "@/components/ui/menu"
import { toast } from "sonner"
import { PeopleManager } from "@/components/llm-provider/people-manager"
import {
  assignMeetingSpeaker,
  getSpeakerPreview,
  listPeople,
  speakerPreviewAudioUrl,
  type Meeting,
  type SpeakerMatch,
  type SpeakerPerson,
} from "@/api/client"

function personHue(id: string): string {
  let h = 0
  for (let i = 0; i < id.length; i++) h = (h * 33 + id.charCodeAt(i)) >>> 0
  return `hsl(${h % 360} 38% 42%)`
}

function initialOf(label: string): string {
  const t = label.trim()
  return t ? t[0]!.toUpperCase() : "?"
}

export function PeoplePicker({
  meetingId,
  speakerId,
  displayName,
  match,
  onAssigned,
}: {
  meetingId: string
  speakerId: string
  displayName?: string
  match?: SpeakerMatch
  onAssigned: (meeting: Meeting) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [people, setPeople] = useState<SpeakerPerson[]>([])
  const [busy, setBusy] = useState(false)
  const [needDisambiguator, setNeedDisambiguator] = useState(false)
  const [disambiguator, setDisambiguator] = useState("")
  const [playing, setPlaying] = useState<string | null>(null)
  const [managerOpen, setManagerOpen] = useState(false)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const lastClip = useRef<Record<string, { meeting_id: string; start: number }>>({})
  const anchorRef = useRef<HTMLButtonElement>(null)
  const noteRef = useRef<HTMLInputElement>(null)

  const label = displayName?.trim() || `Speaker ${speakerId}`
  const assigned = Boolean(displayName?.trim())

  const load = useCallback(async () => {
    try {
      setPeople(await listPeople())
    } catch {
      setPeople([])
    }
  }, [])

  useEffect(() => {
    if (open) void load()
  }, [open, load])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (anchorRef.current?.contains(t)) return
      const menu = document.querySelector("[data-people-picker-menu]")
      if (menu?.contains(t)) return
      if ((e.target as HTMLElement | null)?.closest?.("[data-slot='dialog-content']")) return
      setOpen(false)
    }
    const id = window.setTimeout(() => {
      document.addEventListener("mousedown", onDown)
    }, 0)
    return () => {
      window.clearTimeout(id)
      document.removeEventListener("mousedown", onDown)
    }
  }, [open])

  const stopPreview = useCallback(() => {
    const el = audioRef.current
    if (el) {
      el.pause()
      el.removeAttribute("src")
      el.load()
    }
    audioRef.current = null
    setPlaying(null)
  }, [])

  useEffect(() => () => {
    audioRef.current?.pause()
  }, [])

  useEffect(() => {
    if (open) return
    stopPreview()
    setNeedDisambiguator(false)
    setDisambiguator("")
  }, [open, stopPreview])

  useEffect(() => {
    if (!needDisambiguator) return
    noteRef.current?.focus()
  }, [needDisambiguator])

  const byId = useMemo(() => new Map(people.map((p) => [p.id, p])), [people])
  const q = query.trim().toLowerCase()

  const topIds = (match?.top ?? []).map((t) => t.person_id)
  const topRows = (match?.top ?? [])
    .map((t) => {
      const person = byId.get(t.person_id)
      if (!person) return null
      if (q && !`${person.display_name} ${person.disambiguator}`.toLowerCase().includes(q)) {
        return null
      }
      return { person, score: t.score }
    })
    .filter((row): row is { person: SpeakerPerson; score: number } => row !== null)

  const rest = people
    .filter((p) => !topIds.includes(p.id))
    .filter((p) => !q || `${p.display_name} ${p.disambiguator}`.toLowerCase().includes(q))
    .slice()
    .sort((a, b) => a.display_name.localeCompare(b.display_name, undefined, { sensitivity: "base" }))

  const nameTaken = people.some(
    (p) => p.display_name.trim().toLowerCase() === query.trim().toLowerCase(),
  )

  const assign = async (
    body: { person_id: string | null } | { new_person: { display_name: string; disambiguator?: string } },
  ) => {
    setBusy(true)
    try {
      const meeting = await assignMeetingSpeaker(meetingId, speakerId, body)
      onAssigned(meeting)
      setOpen(false)
      setQuery("")
      setNeedDisambiguator(false)
      setDisambiguator("")
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to assign person"
      toast.error(msg)
    } finally {
      setBusy(false)
    }
  }

  const handleApplyAuto = async (ev: React.MouseEvent) => {
    ev.preventDefault()
    ev.stopPropagation()
    const personId = match?.top?.[0]?.person_id
    if (!personId) {
      toast.error("No person to apply")
      return
    }
    await assign({ person_id: personId })
  }

  const handleAdd = async () => {
    const name = query.trim()
    if (!name) {
      setQuery("")
      return
    }
    if (!needDisambiguator) {
      setNeedDisambiguator(true)
      return
    }
    const note = disambiguator.trim()
    if (nameTaken && !note) {
      toast.error("Add a note to tell them apart")
      noteRef.current?.focus()
      return
    }
    await assign({
      new_person: { display_name: name, disambiguator: note || undefined },
    })
  }

  const handlePlay = async (personId: string, ev: React.MouseEvent) => {
    ev.preventDefault()
    ev.stopPropagation()
    document.querySelectorAll<HTMLAudioElement>("audio[data-meeting-audio]").forEach((el) => {
      if (!el.paused) el.pause()
    })
    audioRef.current?.pause()
    try {
      const prev = lastClip.current[personId]
      const clip = await getSpeakerPreview(personId, {
        exclude_meeting: prev?.meeting_id,
        exclude_start: prev?.start,
      })
      lastClip.current[personId] = { meeting_id: clip.meeting_id, start: clip.start }
      const el = new Audio(speakerPreviewAudioUrl(personId, clip))
      audioRef.current = el
      setPlaying(personId)
      el.addEventListener("ended", () => setPlaying((cur) => (cur === personId ? null : cur)))
      el.addEventListener("error", () => {
        setPlaying(null)
        toast.error("No playable clip for this person")
      })
      await el.play()
    } catch {
      setPlaying(null)
      toast.error("No playable clip for this person")
    }
  }

  const row = (person: SpeakerPerson, score?: number) => (
    <div key={person.id} className="pm-people-picker-row">
      <button
        type="button"
        role="menuitem"
        disabled={busy}
        className="pm-people-picker-row-main"
        onClick={() => void assign({ person_id: person.id })}
      >
        <span
          className="pm-people-avatar"
          style={{ background: personHue(person.id) }}
          aria-hidden
        >
          {initialOf(person.label || person.display_name)}
        </span>
        <span className="min-w-0 flex-1 text-left">
          <span className="pm-people-picker-name">{person.label}</span>
          {person.has_voiceprint && (
            <span className="pm-people-picker-sub">Voiceprint</span>
          )}
        </span>
        {score != null && (
          <span className="pm-people-picker-score">{Math.round(score * 100)}</span>
        )}
      </button>
      <button
        type="button"
        className={cn("pm-people-picker-play", playing === person.id && "is-on")}
        aria-label={`Play sample of ${person.label}`}
        onClick={(e) => void handlePlay(person.id, e)}
      >
        <Play className="size-3" />
      </button>
    </div>
  )

  return (
    <div className="relative min-w-0 flex-1">
      <div className="pm-people-picker-trigger-row">
        <button
          ref={anchorRef}
          type="button"
          className="pm-people-picker-trigger"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-haspopup="listbox"
        >
          <span className="pm-people-picker-trigger-name">{label}</span>
          <ChevronDown className="pm-people-picker-chev" />
        </button>
        {match?.auto && !match.enrolled && (
          <button
            type="button"
            className="pm-people-auto-pill"
            disabled={busy}
            aria-label="Apply auto match"
            onClick={(e) => void handleApplyAuto(e)}
          >
            <span className="pm-people-auto-pill-idle">Auto</span>
            <span className="pm-people-auto-pill-apply">Apply</span>
          </button>
        )}
      </div>
      <SoftMenu
        open={open}
        portal
        anchorRef={anchorRef}
        align="start"
        data-people-picker-menu=""
        className="pm-people-picker-menu"
      >
        <div className="pm-people-picker-search">
          <Search className="size-3.5 shrink-0 opacity-40" />
          <input
            placeholder="Search or add a name"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              if (!e.target.value.trim()) {
                setNeedDisambiguator(false)
                setDisambiguator("")
              }
            }}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === "Escape") setOpen(false)
              if (e.key === "Enter") void handleAdd()
            }}
            autoFocus
          />
        </div>
        <div className="pm-people-picker-list">
          {topRows.length > 0 && (
            <div className="pm-people-picker-sec">Most similar</div>
          )}
          {topRows.map(({ person, score }) => row(person, score))}
          {rest.length > 0 && (
            <div className="pm-people-picker-sec">Directory</div>
          )}
          {rest.map((person) => row(person))}
          {people.length === 0 && !query.trim() && (
            <p className="pm-people-picker-empty">
              Type a name to add the first person.
            </p>
          )}
        </div>
        <button
          type="button"
          className="pm-people-picker-add"
          disabled={busy || !query.trim()}
          onClick={() => void handleAdd()}
        >
          <Plus className="size-3.5 shrink-0" />
          {query.trim() ? `Add “${query.trim()}”` : "Type a name to add"}
        </button>
        {needDisambiguator && (
          <input
            ref={noteRef}
            className="pm-people-picker-note"
            placeholder={
              nameTaken
                ? "Note to tell them apart — Engineering"
                : "Note — Engineering, Client…"
            }
            value={disambiguator}
            onChange={(e) => setDisambiguator(e.target.value)}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === "Enter") void handleAdd()
              if (e.key === "Escape") setOpen(false)
            }}
          />
        )}
        {assigned && (
          <button
            type="button"
            className="pm-people-picker-clear"
            disabled={busy}
            onClick={() => void assign({ person_id: null })}
          >
            <UserMinus className="size-3.5" />
            Clear selection
          </button>
        )}
        <button
          type="button"
          className="pm-people-picker-manage"
          onClick={() => {
            setOpen(false)
            setManagerOpen(true)
          }}
        >
          <Users className="size-3.5" />
          Manage people
        </button>
      </SoftMenu>
      <PeopleManager
        open={managerOpen}
        onOpenChange={(next) => {
          setManagerOpen(next)
          if (!next) void load()
        }}
        nested
      />
    </div>
  )
}
