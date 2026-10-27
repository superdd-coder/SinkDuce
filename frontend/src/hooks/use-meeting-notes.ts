import { useCallback, useEffect, useRef, useState } from "react"
import { updateMeeting, type Meeting } from "@/api/client"
import { ApiError } from "@/api/http"
import { unescapeMarkdownOverEscapes } from "@/components/meeting/summary-markdown-viewer"

const SAVE_DELAY = 800

export type MeetingNotesStatus = "saved" | "saving" | "dirty"

function sameNote(a: string, b: string): boolean {
  return unescapeMarkdownOverEscapes(a) === unescapeMarkdownOverEscapes(b)
}

export function useMeetingNotes(opts: {
  meetingId: string | null
  serverContent: string
  onSaved: (meeting: Meeting) => void
}) {
  const { meetingId, serverContent, onSaved } = opts
  const draftsRef = useRef(new Map<string, string>())
  const baselineRef = useRef("")
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const deadIdsRef = useRef(new Set<string>())
  const meetingIdRef = useRef(meetingId)
  const onSavedRef = useRef(onSaved)
  onSavedRef.current = onSaved
  const [draft, setDraft] = useState("")
  const [status, setStatus] = useState<MeetingNotesStatus>("saved")
  const draftRef = useRef(draft)
  draftRef.current = draft

  const clearTimer = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }

  const saveNow = useCallback(async (id: string, content: string) => {
    if (deadIdsRef.current.has(id)) return
    const cleaned = unescapeMarkdownOverEscapes(content)
    if (id === meetingIdRef.current && cleaned === unescapeMarkdownOverEscapes(baselineRef.current)) {
      setStatus("saved")
      return
    }
    if (id === meetingIdRef.current) setStatus("saving")
    try {
      const meeting = await updateMeeting(id, { notes: cleaned })
      if (id === meetingIdRef.current) {
        baselineRef.current = cleaned
        setStatus("saved")
      }
      onSavedRef.current(meeting)
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        deadIdsRef.current.add(id)
        return
      }
      if (id === meetingIdRef.current) setStatus("dirty")
    }
  }, [])

  const flush = useCallback((id?: string | null) => {
    clearTimer()
    const target = id ?? meetingIdRef.current
    if (!target) return
    const value = draftsRef.current.get(target) ?? (target === meetingIdRef.current ? draftRef.current : undefined)
    if (value === undefined) return
    void saveNow(target, value)
  }, [saveNow])

  useEffect(() => {
    const prev = meetingIdRef.current
    if (prev && prev !== meetingId) {
      clearTimer()
      const pending = draftsRef.current.get(prev)
      if (pending !== undefined) void saveNow(prev, pending)
    }
    meetingIdRef.current = meetingId
    if (!meetingId) {
      setDraft("")
      baselineRef.current = ""
      setStatus("saved")
      return
    }
    const incoming = serverContent ?? ""
    const stored = draftsRef.current.get(meetingId)
    const next = stored !== undefined ? stored : incoming
    setDraft(next)
    baselineRef.current = incoming
    setStatus(sameNote(next, incoming) ? "saved" : "dirty")
    // Seed from this meeting’s first paint; dirty drafts live in draftsRef.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meetingId])

  const prevServerRef = useRef(serverContent)
  const serverMeetingIdRef = useRef(meetingId)
  if (serverMeetingIdRef.current !== meetingId) {
    serverMeetingIdRef.current = meetingId
    prevServerRef.current = serverContent
  } else if (prevServerRef.current !== serverContent) {
    const incoming = serverContent ?? ""
    prevServerRef.current = incoming
    const draftEmpty = !draft.trim()
    const baselineEmpty = !baselineRef.current.trim()
    if (draft === baselineRef.current || (draftEmpty && baselineEmpty)) {
      setDraft(incoming)
      baselineRef.current = incoming
      if (meetingId) draftsRef.current.set(meetingId, incoming)
      setStatus("saved")
    }
  }

  const change = useCallback((value: string) => {
    const id = meetingIdRef.current
    setDraft(value)
    if (!id) return
    draftsRef.current.set(id, value)
    setStatus("dirty")
    clearTimer()
    timerRef.current = setTimeout(() => {
      timerRef.current = null
      void saveNow(id, value)
    }, SAVE_DELAY)
  }, [saveNow])

  useEffect(() => () => {
    clearTimer()
    const id = meetingIdRef.current
    if (!id) return
    const value = draftsRef.current.get(id) ?? draftRef.current
    void saveNow(id, value)
  }, [saveNow])

  return { draft, status, change, flush }
}
