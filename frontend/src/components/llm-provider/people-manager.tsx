import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { Loader2, Play, RefreshCw, Trash2, Users, UserRound } from "lucide-react"
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
import { useT } from "@/i18n/use-t"
import { useAppStore } from "@/stores/app-store"
import {
  deletePerson,
  getPerson,
  getPersonProfile,
  getSpeakerPreview,
  listPeople,
  regeneratePersonProfile,
  speakerPreviewAudioUrl,
  updatePerson,
  type PersonProfileState,
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
  label,
}: {
  onConfirm: () => void
  label?: string
}) {
  const t = useT()
  const actionLabel = label ?? t("common.delete")
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
      className={cn("pm-people-pill pm-people-delete", armed && "is-armed")}
      aria-expanded={armed}
      aria-label={armed ? t("common.confirm") : actionLabel}
      onClick={() => {
        if (!armed) {
          setArmed(true)
          return
        }
        disarm()
        onConfirm()
      }}
    >
      <Trash2 className="size-3.5 shrink-0" />
      <span className="pm-people-delete-track">
        <span className="pm-people-delete-idle" aria-hidden={armed}>
          {actionLabel}
        </span>
        <span className="pm-people-delete-ask" aria-hidden={!armed}>
          {t("common.confirm")}
        </span>
      </span>
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
  const t = useT()
  const [people, setPeople] = useState<SpeakerPerson[]>([])
  const [query, setQuery] = useState("")
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<SpeakerPersonDetail | null>(null)
  const [profile, setProfile] = useState<PersonProfileState | null>(null)
  const [profileBusy, setProfileBusy] = useState(false)
  const locale = useAppStore((s) => s.locale)
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

  useEffect(() => {
    if (!selectedId) {
      setProfile(null)
      return
    }
    let alive = true
    getPersonProfile(selectedId)
      .then((p) => {
        if (alive) setProfile(p)
      })
      .catch(() => {
        if (alive) setProfile(null)
      })
    return () => {
      alive = false
    }
  }, [selectedId, open])

  useEffect(() => {
    if (profile?.state !== "generating" || !selectedId) return
    const id = window.setInterval(() => {
      getPersonProfile(selectedId)
        .then(setProfile)
        .catch(() => {})
    }, 900)
    return () => window.clearInterval(id)
  }, [profile?.state, selectedId])

  const handleRegenerateProfile = useCallback(async () => {
    if (!selectedId) return
    setProfileBusy(true)
    setProfile((prev) =>
      prev
        ? { ...prev, state: "generating" }
        : { state: "generating", text: "", generated_at: "", source_count: 0, dirty: true },
    )
    try {
      setProfile(await regeneratePersonProfile(selectedId, locale))
    } catch {
      setProfile((prev) => (prev ? { ...prev, state: "ready" } : prev))
    } finally {
      setProfileBusy(false)
    }
  }, [locale, selectedId])

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
        .catch(() => toast.error(t("settings.failedSave")))
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
        toast.error(t("meeting.noPlayableClipShort"))
      })
      await el.play()
    } catch {
      setPlaying(false)
      toast.error(t("meeting.noPlayableClipShort"))
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
      toast.success(updated.is_me ? t("settings.markedAsYou") : t("settings.unmarked"))
    } catch {
      toast.error(t("settings.failedUpdateMe"))
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
      toast.error(t("settings.failedDeletePerson"))
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
          <DialogKicker>{t("nav.settings")}</DialogKicker>
          <DialogTitle>{t("settings.people")}</DialogTitle>
        </DialogHeader>

        <div className="pm-settings-hw pm-people-shell">
          <div className="pm-settings-hw-rail">
            <div className="pm-settings-hw-rail-head">
              <span className="pm-label text-[var(--pm-ink)]">{t("common.directory")}</span>
              <span className="pm-settings-hw-count">{people.length}</span>
            </div>
            <div className="px-3 pb-2">
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("common.search")}
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
                      <span className="pm-people-rail-name pm-rail-name">
                        {p.label}
                        {p.is_me && <span className="pm-people-me-tag">{t("common.me")}</span>}
                      </span>
                      <span className="pm-people-rail-meta">
                        {p.has_voiceprint ? formatSpeech(p.speech_sec) : t("settings.noPrint")}
                      </span>
                    </span>
                    {p.has_voiceprint && <span className="pm-people-dot" aria-hidden />}
                  </button>
                ))}
                {people.length === 0 && (
                  <p className="pm-meta p-2 text-center">{t("settings.noPeopleYet")}</p>
                )}
              </div>
            </ScrollArea>
          </div>

          <div className="pm-settings-hw-main">
            <div className={cn("pm-settings-hw-main-body", mainIn && "is-in")}>
              {detail ? (
                <>
                  <section className="pm-people-identity" aria-label={t("common.person")}>
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
                          {detail.has_voiceprint ? t("settings.enrolled") : t("settings.unenrolled")}
                          {detail.has_voiceprint
                            ? ` · ${formatSpeech(detail.speech_sec)}`
                            : ""}
                        </p>
                        <h3 className="pm-people-display">{detail.display_name || t("common.unnamed")}</h3>
                      </div>
                      <div className="pm-people-pills">
                        <button
                          type="button"
                          className={cn("pm-people-pill", detail.is_me && "is-on")}
                          title={detail.is_me ? t("settings.unmarkAsYou") : t("settings.markAsYou")}
                          onClick={() => void handleToggleMe()}
                        >
                          <UserRound className="size-3.5" />
                          {t("common.me")}
                        </button>
                        <button
                          type="button"
                          className={cn("pm-people-pill", playing && "is-on")}
                          onClick={() => void handlePlay()}
                        >
                          <Play className="size-3.5" />
                          {playing ? t("settings.nextClip") : t("common.listen")}
                        </button>
                        <SlideConfirmDelete onConfirm={() => void handleDelete()} />
                      </div>
                    </div>
                    <div className="pm-people-fields">
                      <div className="pm-settings-hw-field pm-settings-hw-field--name">
                        <FieldLabel className="pm-settings-hw-field-label">{t("common.name")}</FieldLabel>
                        <Input
                          className="pm-settings-hw-input"
                          value={detail.display_name}
                          onChange={(e) => scheduleSave({ display_name: e.target.value })}
                        />
                      </div>
                      <div className="pm-settings-hw-field">
                        <FieldLabel className="pm-settings-hw-field-label">{t("common.note")}</FieldLabel>
                        <Input
                          className="pm-settings-hw-input"
                          value={detail.disambiguator}
                          placeholder={t("settings.notePh")}
                          onChange={(e) => scheduleSave({ disambiguator: e.target.value })}
                        />
                      </div>
                    </div>
                  </section>

                  <section className="pm-people-profile" aria-label={t("settings.profileTitle")}>
                    <div className="pm-settings-hw-words-head">
                      <div className="pm-settings-hw-words-title">
                        <span className="pm-settings-hw-words-label">
                          {t("settings.profileTitle")}
                        </span>
                        {profile?.state === "ready" && profile.source_count > 0 && (
                          <span className="pm-settings-hw-count">{profile.source_count}</span>
                        )}
                        {profile?.state === "ready" && profile.dirty && (
                          <span
                            className="pm-people-profile-dirty"
                            title={t("settings.profileNeedsUpdate")}
                            aria-hidden
                          />
                        )}
                      </div>
                      {profile?.state === "generating" ? (
                        <span className="pm-people-profile-progress">
                          <Loader2 className="size-3.5 spin" />
                          {t("settings.profileGenerating")}
                        </span>
                      ) : (
                        <button
                          type="button"
                          className="pm-people-profile-refresh"
                          onClick={() => void handleRegenerateProfile()}
                          disabled={profileBusy}
                        >
                          <RefreshCw className="size-3.5" />
                          {profile?.text
                            ? t("settings.profileRegenerate")
                            : t("settings.profileGenerate")}
                        </button>
                      )}
                    </div>
                    {profile?.text ? (
                      <>
                        <p className="pm-people-profile-text">{profile.text}</p>
                        {profile.generated_at && (
                          <p className="pm-people-profile-meta">
                            {t("settings.profileBasedOn", {
                              n: profile.source_count,
                              date: new Date(profile.generated_at).toLocaleDateString(),
                            })}
                          </p>
                        )}
                      </>
                    ) : (
                      <p className="pm-settings-hw-words-empty-hint">
                        {t("settings.profileEmpty")}
                      </p>
                    )}
                  </section>

                  <section className="pm-people-history" aria-label={t("meeting.meetings")}>
                    <div className="pm-settings-hw-words-head">
                      <div className="pm-settings-hw-words-title">
                        <span className="pm-settings-hw-words-label">{t("meeting.meetings")}</span>
                        <span className="pm-settings-hw-count">{detail.meetings.length}</span>
                      </div>
                    </div>
                    {detail.meetings.length === 0 ? (
                      <div className="pm-settings-hw-words-empty">
                        <p className="pm-settings-hw-words-empty-title">{t("settings.noMeetingsYet")}</p>
                        <p className="pm-settings-hw-words-empty-hint">
                          {t("settings.assignPersonHint")}
                        </p>
                      </div>
                    ) : (
                      <ul className="pm-people-meetings">
                        {detail.meetings.map((row) => (
                          <li key={row.meeting_id} className="pm-people-meeting-card">
                            <span className="pm-people-meeting-title pm-rail-name">{row.title}</span>
                            <span className="pm-meta">
                              {row.speaker_id ? t("settings.slot", { id: row.speaker_id }) : t("settings.seen")}
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
                  <p className="pm-settings-hw-empty-title">{t("settings.peopleDirectory")}</p>
                  <p className="pm-settings-hw-empty-hint">
                    {t("settings.peopleEmptyHint")}
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
