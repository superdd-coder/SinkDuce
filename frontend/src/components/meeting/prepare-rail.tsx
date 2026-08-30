import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import {
  Check,
  CircleHelp,
  ClipboardList,
  FileText,
  History,
  Lightbulb,
  ListTodo,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Users,
  X,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { SoftMenu } from "@/components/ui/menu"
import {
  createPerson,
  generateMeetingBrief,
  getMeeting,
  getMeetingBrief,
  getPersonProfile,
  listPeople,
  regeneratePersonProfile,
  type Meeting,
  type MeetingBrief,
  type PersonProfileState,
  type SpeakerPerson,
  updateMeeting,
} from "@/api/client"
import { useAppStore } from "@/stores/app-store"
import { useT } from "@/i18n/use-t"
import { MeetingNotesCard } from "./meeting-notes-card"
import {
  orderBriefSections,
  parseBriefSections,
  parseInlineBold,
  type BriefSection,
} from "./brief-sections"
import type { MeetingNotesStatus } from "@/hooks/use-meeting-notes"

type PrepareTab = "attendees" | "brief" | "notes"

export function PrepareRail({
  meetingId,
  meeting,
  open,
  onToggle,
  notes,
  onNotesChange,
  notesStatus,
  onMeetingUpdated,
}: {
  meetingId: string
  meeting: Meeting
  open: boolean
  onToggle: () => void
  notes: string
  onNotesChange: (value: string) => void
  notesStatus: MeetingNotesStatus
  onMeetingUpdated: (meeting: Meeting) => void
}) {
  const t = useT()
  const [tab, setTab] = useState<PrepareTab>(meeting.brief ? "brief" : "attendees")

  const tabs: { key: PrepareTab; label: string; icon: typeof Users }[] = [
    { key: "attendees", label: t("meeting.prepareTabAttendees"), icon: Users },
    { key: "notes", label: t("meeting.prepareTabNotes"), icon: ClipboardList },
    { key: "brief", label: t("meeting.prepareTabBrief"), icon: FileText },
  ]
  const briefReady = meeting.brief?.state === "ready"
  const handleDot = !!notes.trim() || briefReady

  return (
    <aside
      id={`meeting-notes-rail-${meetingId}`}
      className="pm-meeting-notes-rail"
      aria-hidden={!open}
    >
      <div className="pm-meeting-notes-dock">
        <button
          type="button"
          className={cn("pm-meeting-notes-handle", handleDot && "has-content")}
          aria-expanded={open}
          aria-controls={`meeting-notes-rail-${meetingId}`}
          aria-label={t("meeting.prepareHandle")}
          onClick={onToggle}
        >
          <span className="pm-meeting-notes-handle-label">{t("meeting.prepareHandle")}</span>
          {handleDot && <span className="pm-meeting-notes-handle-dot" aria-hidden />}
        </button>
        <div className="pm-prepare-card pm-meeting-f-card">
          <div className="pm-prepare-tabs" role="tablist" aria-label={t("meeting.prepareHandle")}>
            {tabs.map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={tab === key}
                className={cn("pm-prepare-tab", tab === key && "is-active")}
                onClick={() => setTab(key)}
              >
                <Icon className="size-3" />
                {label}
                {key === "brief" && briefReady && (
                  <span className="pm-prepare-tab-dot" aria-hidden />
                )}
              </button>
            ))}
          </div>
          <div className="pm-prepare-body" key={tab}>
            {tab === "attendees" && (
              <AttendeePicker meeting={meeting} onMeetingUpdated={onMeetingUpdated} />
            )}
            {tab === "notes" && (
              <MeetingNotesCard
                meetingId={meetingId}
                value={notes}
                onChange={onNotesChange}
                status={notesStatus}
                label={t("meeting.prepareTabNotes")}
                placeholder={t("meeting.prepareAgendaPlaceholder")}
              />
            )}
            {tab === "brief" && (
              <BriefPanel meeting={meeting} notes={notes} onMeetingUpdated={onMeetingUpdated} />
            )}
          </div>
        </div>
      </div>
    </aside>
  )
}

/* ── Attendees tab: pre-select who will join ─────────────────────────── */

function personHue(id: string): string {
  let h = 0
  for (let i = 0; i < id.length; i++) h = (h * 33 + id.charCodeAt(i)) >>> 0
  return `hsl(${h % 360} 38% 42%)`
}

function initialOf(label: string): string {
  const text = label.trim()
  return text ? text[0]!.toUpperCase() : "?"
}

function AttendeePicker({
  meeting,
  onMeetingUpdated,
}: {
  meeting: Meeting
  onMeetingUpdated: (meeting: Meeting) => void
}) {
  const t = useT()
  const locale = useAppStore((s) => s.locale)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [people, setPeople] = useState<SpeakerPerson[]>([])
  const [draft, setDraft] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const anchorRef = useRef<HTMLButtonElement>(null)
  const selectedIds = meeting.expected_people ?? []

  const load = useCallback(async () => {
    try {
      setPeople(await listPeople())
    } catch {
      setPeople([])
    }
  }, [])

  const selectedKey = selectedIds.join(",")
  // Load the directory on mount so selected chips show names (not ids)
  // before the dropdown is ever opened; refresh again on every open.
  useEffect(() => {
    void load()
  }, [load])
  useEffect(() => {
    if (open) {
      setDraft(selectedKey ? selectedKey.split(",") : [])
      void load()
    }
  }, [open, load, selectedKey])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node
      if (anchorRef.current?.contains(target)) return
      const menu = document.querySelector("[data-prepare-picker-menu]")
      if (menu?.contains(target)) return
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

  // "Me" never shows in the pre-selection list.
  const visible = useMemo(() => people.filter((p) => !p.is_me), [people])
  const q = query.trim().toLowerCase()
  const rows = useMemo(
    () =>
      visible
        .filter((p) => !q || `${p.display_name} ${p.disambiguator}`.toLowerCase().includes(q))
        .slice()
        .sort((a, b) => a.display_name.localeCompare(b.display_name, undefined, { sensitivity: "base" })),
    [visible, q],
  )
  const nameTaken = q
    ? visible.some((p) => p.display_name.trim().toLowerCase() === q)
    : false

  const apply = useCallback(
    async (next: string[], refreshIds: string[]) => {
      setBusy(true)
      try {
        const updated = await updateMeeting(meeting.id, { expected_people: next })
        onMeetingUpdated(updated)
        // Pull latest profiles for newly added attendees (dirty ones
        // regenerate in background threads, clean ones return instantly).
        if (refreshIds.length) {
          await Promise.allSettled(
            refreshIds.map((id) => regeneratePersonProfile(id, locale, false)),
          )
        }
      } finally {
        setBusy(false)
      }
    },
    [locale, meeting.id, onMeetingUpdated],
  )

  const confirm = () => {
    const added = draft.filter((id) => !selectedIds.includes(id))
    setOpen(false)
    setQuery("")
    void apply(draft, added)
  }

  const createAndPick = async () => {
    const name = query.trim()
    if (!name || busy) return
    setBusy(true)
    try {
      const person = await createPerson(name)
      setDraft((cur) => (cur.includes(person.id) ? cur : [...cur, person.id]))
      await load()
      setQuery("")
    } finally {
      setBusy(false)
    }
  }

  const byId = useMemo(() => new Map(people.map((p) => [p.id, p])), [people])

  return (
    <div className="pm-prepare-attendees">
      <p className="pm-prepare-hint">{t("meeting.prepareAttendeesHint")}</p>
      {selectedIds.length > 0 && (
        <div className="pm-prepare-chips">
          {selectedIds.map((id) => {
            const person = byId.get(id)
            return (
              <button
                key={id}
                type="button"
                className="pm-prepare-chip"
                onClick={() =>
                  void apply(
                    selectedIds.filter((x) => x !== id),
                    [],
                  )
                }
                disabled={busy}
              >
                {person?.label ?? id.slice(0, 6)}
                <X className="size-3" />
              </button>
            )
          })}
        </div>
      )}
      <div className="pm-prepare-picker-anchor">
        <button
          ref={anchorRef}
          type="button"
          className="pm-prepare-cta"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-haspopup="listbox"
          disabled={busy}
        >
          <Plus className="size-3.5" />
          {t("meeting.prepareAddAttendees")}
        </button>
        <SoftMenu
          open={open}
          portal
          anchorRef={anchorRef}
          align="start"
          data-prepare-picker-menu=""
          className="pm-people-picker-menu pm-prepare-picker-menu"
        >
          <div className="pm-people-picker-compose">
            <div className="pm-people-picker-search">
              <Search className="size-3.5 shrink-0 opacity-40" />
              <input
                placeholder={t("meeting.prepareAttendeeSearch")}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  e.stopPropagation()
                  if (e.key === "Escape") setOpen(false)
                }}
                autoFocus
              />
            </div>
          </div>
          <div className="pm-people-picker-list">
            {rows.length > 0 && (
              <div className="pm-people-picker-sec">{t("common.directory")}</div>
            )}
            {rows.map((person) => {
              const picked = draft.includes(person.id)
              return (
                <div key={person.id} className={cn("pm-people-picker-row", picked && "is-picked")}>
                  <button
                    type="button"
                    role="menuitem"
                    className="pm-people-picker-row-main"
                    onClick={() =>
                      setDraft((cur) =>
                        cur.includes(person.id)
                          ? cur.filter((x) => x !== person.id)
                          : [...cur, person.id],
                      )
                    }
                  >
                    <span
                      className="pm-people-avatar"
                      style={{ background: personHue(person.id) }}
                      aria-hidden
                    >
                      {initialOf(person.label || person.display_name)}
                    </span>
                    <span className="min-w-0 flex-1 text-left">
                      <span className="pm-people-picker-name">
                        {person.display_name || person.label}
                      </span>
                      {person.disambiguator.trim() ? (
                        <span className="pm-people-picker-sub">{person.disambiguator.trim()}</span>
                      ) : null}
                    </span>
                    {picked && (
                      <span className="pm-prepare-pick-badge" aria-hidden>
                        <Check className="size-3" />
                      </span>
                    )}
                  </button>
                </div>
              )
            })}
            {!rows.length && (
              <p className="pm-prepare-empty">{t("meeting.prepareNoPeople")}</p>
            )}
          </div>
          {q && !nameTaken && (
            <button
              type="button"
              className="pm-people-picker-add"
              disabled={busy}
              onClick={() => void createAndPick()}
            >
              <Plus className="size-3.5 shrink-0" />
              {t("meeting.prepareCreateAndAdd", { name: query.trim() })}
            </button>
          )}
          <div className="pm-prepare-picker-foot">
            <button type="button" className="pm-prepare-foot-cancel" onClick={() => setOpen(false)}>
              {t("meeting.prepareCancel")}
            </button>
            <button
              type="button"
              className="pm-prepare-foot-confirm"
              onClick={confirm}
              disabled={busy}
            >
              {busy && <Loader2 className="size-3.5 spin" />}
              {t("meeting.prepareConfirm")}
            </button>
          </div>
        </SoftMenu>
      </div>
      {selectedIds.length > 0 && <AttendeeProfileCards meeting={meeting} />}
    </div>
  )
}

/* ── Per-person mini profile cards under the selection ──────────────── */

function AttendeeProfileCards({ meeting }: { meeting: Meeting }) {
  const t = useT()
  const ids = meeting.expected_people ?? []
  const idsKey = ids.join(",")
  const [rows, setRows] = useState<
    { id: string; label: string; profile: PersonProfileState | null }[]
  >([])

  useEffect(() => {
    if (!idsKey) {
      setRows([])
      return
    }
    const ids = idsKey.split(",")
    let alive = true
    const load = async () => {
      const [people, profiles] = await Promise.all([
        listPeople().catch(() => [] as SpeakerPerson[]),
        Promise.all(ids.map((id) => getPersonProfile(id).catch(() => null))),
      ])
      if (!alive) return
      const byId = new Map(people.map((p) => [p.id, p]))
      setRows(
        ids.map((id, i) => ({
          id,
          label: byId.get(id)?.label ?? id.slice(0, 6),
          profile: profiles[i],
        })),
      )
    }
    void load()
    return () => {
      alive = false
    }
  }, [idsKey])

  const anyGenerating = rows.some((r) => r.profile?.state === "generating")
  useEffect(() => {
    if (!anyGenerating || !idsKey) return
    const ids = idsKey.split(",")
    const id = window.setInterval(() => {
      void Promise.all(ids.map((pid) => getPersonProfile(pid).catch(() => null))).then(
        (profiles) => {
          setRows((cur) =>
            cur.map((row, i) => ({ ...row, profile: profiles[i] ?? row.profile })),
          )
        },
      )
    }, 1000)
    return () => window.clearInterval(id)
  }, [anyGenerating, idsKey])

  if (!ids.length) return null
  return (
    <div className="pm-attendee-profiles">
      {rows.map(({ id, label, profile }) => (
        <div key={id} className="pm-attendee-profile">
          <div className="pm-attendee-profile-head">
            <span className="pm-attendee-profile-name">{label}</span>
            {profile?.state === "generating" ? (
              <span className="pm-attendee-profile-state">
                <Loader2 className="size-3 spin" />
                {t("settings.profileGenerating")}
              </span>
            ) : profile?.state === "ready" && profile.dirty ? (
              <span className="pm-attendee-profile-state is-dirty">
                {t("settings.profileNeedsUpdate")}
              </span>
            ) : profile?.text ? (
              <span className="pm-attendee-profile-state">
                {t("settings.profileBasedOn", {
                  n: profile.source_count,
                  date: new Date(profile.generated_at).toLocaleDateString(),
                })}
              </span>
            ) : null}
          </div>
          <p className="pm-attendee-profile-text">
            {profile?.text || t("meeting.prepareNoProfileYet")}
          </p>
        </div>
      ))}
    </div>
  )
}

/* ── Brief tab: generate / view the one-page prep brief ──────────────── */

const SECTION_ICON = {
  recap: History,
  chase: ListTodo,
  undecided: CircleHelp,
  attendees: Users,
  other: FileText,
} as const

const SECTION_TITLE_KEY = {
  recap: "meeting.briefSecRecap",
  chase: "meeting.briefSecChase",
  undecided: "meeting.briefSecUndecided",
  attendees: "meeting.briefSecAttendees",
} as const

function inline(text: string): ReactNode[] {
  return parseInlineBold(text).map((seg, i) => {
    if (seg.bold) return <strong key={i}>{seg.text}</strong>
    if (seg.italic) return <em key={i}>{seg.text}</em>
    return <span key={i}>{seg.text}</span>
  })
}

function BriefBody({ body }: { body: string }) {
  const t = useT()
  const nodes: ReactNode[] = []
  let bullets: string[] = []
  const flushBullets = (key: string) => {
    if (!bullets.length) return
    nodes.push(
      <ul key={key}>
        {bullets.map((item, i) => (
          <li key={i}>{inline(item)}</li>
        ))}
      </ul>,
    )
    bullets = []
  }
  body.split("\n").forEach((raw, idx) => {
    const text = raw.trim()
    if (/^[-*]\s+/.test(text)) {
      bullets.push(text.replace(/^[-*]\s+/, ""))
      return
    }
    flushBullets(`ul-${idx}`)
    if (!text) return
    const person = /^###\s+(.*)$/.exec(text)
    if (person) {
      nodes.push(
        <p key={idx} className="pm-brief-person">
          {inline(person[1]!)}
        </p>,
      )
      return
    }
    if (/^#{2,}\s/.test(text)) {
      nodes.push(
        <p key={idx} className="pm-brief-sub">
          {inline(text.replace(/^#+\s*/, ""))}
        </p>,
      )
      return
    }
    if (text.startsWith("->")) {
      nodes.push(
        <p key={idx} className="pm-brief-tip">
          <Lightbulb className="size-3 shrink-0" />
          <span className="pm-brief-tip-label">{t("meeting.briefTipLabel")}</span>
          {inline(text.replace(/^->\s*/, ""))}
        </p>,
      )
      return
    }
    nodes.push(<p key={idx}>{inline(text)}</p>)
  })
  flushBullets("ul-end")
  return <div className="pm-brief-body">{nodes}</div>
}

function BriefSectionCard({ section }: { section: BriefSection }) {
  const t = useT()
  const Icon = SECTION_ICON[section.kind] ?? FileText
  const titleKey =
    SECTION_TITLE_KEY[section.kind as keyof typeof SECTION_TITLE_KEY] ?? undefined
  const title = titleKey ? t(titleKey) : section.token || t("meeting.prepareTabBrief")
  return (
    <section className={cn("pm-brief-sec", `is-${section.kind}`)}>
      <div className="pm-brief-sec-h">
        <Icon className="size-3.5" />
        <span>{title}</span>
      </div>
      <BriefBody body={section.body} />
    </section>
  )
}

const EMPTY_BRIEF: MeetingBrief = {
  state: "none",
  markdown: "",
  error: null,
  generated_at: "",
  person_ids: [],
}

function BriefPanel({
  meeting,
  notes,
  onMeetingUpdated,
}: {
  meeting: Meeting
  notes: string
  onMeetingUpdated: (meeting: Meeting) => void
}) {
  const t = useT()
  const locale = useAppStore((s) => s.locale)
  const selectedCount = (meeting.expected_people ?? []).length
  const hasAgenda = !!notes.trim()
  const [brief, setBrief] = useState<MeetingBrief>(meeting.brief ?? EMPTY_BRIEF)
  const [busy, setBusy] = useState(false)
  const [dirtyCount, setDirtyCount] = useState<number | null>(null)
  const briefKey = `${meeting.id}:${meeting.brief?.generated_at ?? ""}`
  const keyRef = useRef(briefKey)

  // Sync when the parent meeting object brings a newer brief.
  useEffect(() => {
    if (keyRef.current !== briefKey) {
      keyRef.current = briefKey
      if (meeting.brief) setBrief(meeting.brief)
    }
  }, [briefKey, meeting.brief])

  useEffect(() => {
    let alive = true
    getMeetingBrief(meeting.id)
      .then((b) => {
        if (alive) setBrief(b)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [meeting.id])

  // Poll while the backend synthesizes.
  useEffect(() => {
    if (brief.state !== "generating") return
    const id = window.setInterval(() => {
      getMeetingBrief(meeting.id)
        .then((b) => setBrief(b))
        .catch(() => {})
    }, 900)
    return () => window.clearInterval(id)
  }, [brief.state, meeting.id])

  // How many selected attendees need a profile refresh (cost transparency).
  useEffect(() => {
    const ids = meeting.expected_people ?? []
    if (!ids.length) {
      setDirtyCount(null)
      return
    }
    let alive = true
    void Promise.all(ids.map((id) => getPersonProfile(id).catch(() => null))).then((rows) => {
      if (!alive) return
      setDirtyCount(rows.filter((r) => r?.dirty).length)
    })
    return () => {
      alive = false
    }
  }, [meeting.id, meeting.expected_people, brief.generated_at])

  const generate = useCallback(async () => {
    if (!selectedCount || busy) return
    setBusy(true)
    setBrief((prev) => ({ ...prev, state: "generating", error: null }))
    try {
      const next = await generateMeetingBrief(meeting.id, locale)
      setBrief(next)
      if (next.state === "ready") {
        try {
          onMeetingUpdated(await getMeeting(meeting.id))
        } catch {
          /* brief already in local state; meeting refresh is best-effort */
        }
      }
    } catch {
      setBrief((prev) => ({ ...prev, state: "error", error: "request failed" }))
    } finally {
      setBusy(false)
    }
  }, [busy, locale, meeting.id, onMeetingUpdated, selectedCount])

  if (brief.state === "generating") {
    return (
      <div className="pm-prepare-brief">
        <div className="pm-prepare-brief-skeleton" aria-hidden>
          <span />
          <span />
          <span className="w-3/5" />
          <span className="w-4/5" />
        </div>
        <p className="pm-prepare-brief-progress">
          <Loader2 className="size-3.5 spin" />
          {dirtyCount
            ? t("meeting.prepareRefreshProfiles", { n: dirtyCount })
            : t("meeting.prepareGenerating")}
        </p>
      </div>
    )
  }

  if (brief.state === "ready" || (brief.state === "error" && brief.markdown)) {
    const stamp = brief.generated_at ? new Date(brief.generated_at) : null
    return (
      <div className="pm-prepare-brief">
        <div className="pm-prepare-brief-head">
          <span className="pm-prepare-brief-meta">
            {brief.group_title
              ? t("meeting.prepareFromGroup", { title: brief.group_title })
              : t("meeting.prepareTabBrief")}
            {stamp ? ` · ${stamp.toLocaleString()}` : ""}
          </span>
          <button
            type="button"
            className="pm-prepare-brief-refresh"
            onClick={() => void generate()}
            disabled={busy || !selectedCount}
            title={t("meeting.prepareRegenerate")}
          >
            <RefreshCw className={cn("size-3.5", busy && "spin")} />
          </button>
        </div>
        {brief.state === "error" && (
          <p className="pm-prepare-brief-error">{t("meeting.prepareStaleWarning")}</p>
        )}
        <div className="pm-brief-list">
          {orderBriefSections(parseBriefSections(brief.markdown)).map((section) => (
            <BriefSectionCard key={`${section.kind}-${section.token}`} section={section} />
          ))}
        </div>
      </div>
    )
  }

  if (brief.state === "error") {
    return (
      <div className="pm-prepare-brief">
        <p className="pm-prepare-brief-error">{t("meeting.prepareFailed")}</p>
        <button
          type="button"
          className="pm-prepare-cta"
          onClick={() => void generate()}
          disabled={!selectedCount || busy}
        >
          {t("meeting.prepareRetry")}
        </button>
      </div>
    )
  }

  return (
    <div className="pm-prepare-brief">
      <p className="pm-prepare-hint">{t("meeting.prepareBriefHint")}</p>
      {selectedCount ? (
        <>
          <button
            type="button"
            className="pm-prepare-cta"
            onClick={() => void generate()}
            disabled={busy}
          >
            {brief.state === "none" && !brief.markdown
              ? t("meeting.prepareGenerate")
              : t("meeting.prepareRegenerate")}
          </button>
          {!hasAgenda && (
            <p className="pm-prepare-brief-dirty">
              {t("meeting.prepareAgendaNudge")}
            </p>
          )}
          {dirtyCount ? (
            <p className="pm-prepare-brief-dirty">
              {t("meeting.prepareNDirty", { n: dirtyCount })}
            </p>
          ) : null}
        </>
      ) : (
        <p className="pm-prepare-empty">{t("meeting.prepareNoSelection")}</p>
      )}
    </div>
  )
}
