import { request } from "./http"
import type { Meeting } from "./meeting"

export interface SpeakerPerson {
  id: string
  display_name: string
  disambiguator: string
  label: string
  has_voiceprint: boolean
  last_meeting_id: string | null
  speech_sec: number
  meeting_count?: number
  is_me?: boolean
}

export interface SpeakerPersonMeeting {
  meeting_id: string
  title: string
  speaker_id: string
  speech_sec: number
  enrolled_at: string
}

export interface SpeakerPersonDetail extends SpeakerPerson {
  meetings: SpeakerPersonMeeting[]
}

export interface SpeakerMatchTop {
  person_id: string
  score: number
}

export interface SpeakerMatch {
  auto: boolean
  score: number | null
  enrolled: boolean
  cleared?: boolean
  top: SpeakerMatchTop[]
}

export interface SpeakerPreview {
  meeting_id: string
  speaker_id: string
  start: number
  end: number
}

export const listPeople = (q?: string) =>
  request<SpeakerPerson[]>(`/speakers${q ? `?q=${encodeURIComponent(q)}` : ""}`)

export const createPerson = (display_name: string, disambiguator?: string) =>
  request<SpeakerPerson>("/speakers", {
    method: "POST",
    body: JSON.stringify({ display_name, disambiguator: disambiguator ?? "" }),
  })

export const getPerson = (id: string) =>
  request<SpeakerPersonDetail>(`/speakers/${id}`)

export const updatePerson = (
  id: string,
  data: { display_name?: string; disambiguator?: string; is_me?: boolean },
) =>
  request<SpeakerPerson>(`/speakers/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  })

export const getMePerson = () =>
  request<{ person_id: string | null; person: SpeakerPerson | null }>("/speakers/me")

export const setMePerson = (personId: string | null) =>
  request<{ person_id: string | null; person: SpeakerPerson | null }>("/speakers/me", {
    method: "PUT",
    body: JSON.stringify({ person_id: personId }),
  })

export const deletePerson = (id: string) =>
  request<{ ok: boolean }>(`/speakers/${id}`, { method: "DELETE" })

export const getSpeakerPreview = (
  personId: string,
  opts?: { exclude_meeting?: string; exclude_start?: number },
) => {
  const q = new URLSearchParams()
  if (opts?.exclude_meeting) q.set("exclude_meeting", opts.exclude_meeting)
  if (opts?.exclude_start != null) q.set("exclude_start", String(opts.exclude_start))
  const suffix = q.size ? `?${q}` : ""
  return request<SpeakerPreview>(`/speakers/${personId}/preview${suffix}`)
}

export function speakerPreviewAudioUrl(
  personId: string,
  clip: SpeakerPreview,
): string {
  const q = new URLSearchParams({
    meeting_id: clip.meeting_id,
    start: String(clip.start),
    end: String(clip.end),
    t: String(Date.now()),
  })
  return `/api/speakers/${personId}/preview-audio?${q}`
}

export const assignMeetingSpeaker = (
  meetingId: string,
  speakerId: string,
  body:
    | { person_id: string | null }
    | { new_person: { display_name: string; disambiguator?: string } }
    | { display_name: string },
) =>
  request<Meeting>(`/meetings/${meetingId}/speakers/${encodeURIComponent(speakerId)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  })

export const commitMeetingSpeakers = (meetingId: string) =>
  request<Meeting>(`/meetings/${meetingId}/speakers/commit`, { method: "POST" })
