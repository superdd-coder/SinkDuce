import { apiFetch, request, parseApiErrorBody } from "./http"
import { withCriticalOp } from "./critical-op.ts"
import type { SpeakerMatch } from "./speakers"

// ── Meetings ──

export type MeetingStatus = "created" | "recording" | "transcribing" | "completed"
export type MeetingMode = "upload" | "record"

export interface TodoItem {
  text: string
  assignee?: string
  priority?: string
}

export interface TranscriptSegment {
  start: number
  end: number
  text: string
  speaker_id?: string
  sentence_id?: string
  section_tags?: string[]
  /** Live-translate target text (bilingual captions). */
  translation?: string | null
}

// ── Meeting v2 types ──

export type ProcessingState = "idle" | "summarizing" | "extracting"  // v3: "breaking_down" removed

export interface BlueprintItem {
  blueprint_id: string  // v3: "bp_01" format, code-assigned
  tab_name: string
  tab_description: string  // v3: ~200-char description (was section_description)
  associated_collection_id: string
  associated_collection_name: string
}

export interface MeetingTab {
  tab_id: string
  type: "general" | "section"
  blueprint_id: string  // v3: from blueprint item → "bp_01"; custom → ""; cleared on re-summarize
  name: string
  description: string
  processing_state: string  // v3: "idle" | "generating"
  associated_collection_id: string
  associated_collection_name: string
  allocated_file_id: string  // v3: UUID after ingest
  is_dirty: boolean  // v3: user edited name/description → true; regenerate resets to false
  /** Section MD edited after allocate; manual Update collection clears it */
  needs_reingest?: boolean
  /** Hash of MD at last allocate — used to restore needs_reingest after reload */
  ingested_content_hash?: string
  /** Timeline chain chosen at last allocate */
  allocated_chain_id?: string
  allocated_node_id?: string
  /** Parsed ## Todo candidates from last allocate */
  todo_candidates?: MeetingTodoCandidate[]
  md_file_path: string
  payload_ref: string[]
}

export interface MeetingBrief {
  state: "none" | "idle" | "generating" | "ready" | "error"
  markdown: string
  error?: string | null
  generated_at: string
  group_id?: string | null
  group_title?: string
  person_ids: string[]
  locale?: string
}

export interface Meeting {
  id: string
  title: string
  status: MeetingStatus
  mode?: MeetingMode
  audio_path?: string
  notes_path?: string
  transcript_path?: string
  detail?: string
  summary?: string
  notes_content?: string
  transcription_error?: string
  processing_state?: ProcessingState
  blueprint?: BlueprintItem[]
  blueprint_taxonomy?: { dimension: string; explanation: string }
  tabs?: MeetingTab[]
  allocated_collections: string[]
  allocated_file_ids: string[]
  speaker_names?: Record<string, string>
  speaker_people?: Record<string, string>
  speaker_matches?: Record<string, SpeakerMatch>
  speaker_slots_status?: "computing" | "ready" | "unavailable" | null
  speaker_slots_ms?: number | null
  hot_words_library_id?: string | null
  hot_words_library_ids?: string[]
  expected_people?: string[]
  brief?: MeetingBrief | null
  /** Kept on disk & browsable; hidden from LLM meeting query tools. */
  archived?: boolean
  /**
   * 创建者创建会议时的界面语言（"en" | "zh-CN"），实时纪要按它输出。
   * 老后端 / 存量会议没有这个字段 → 后端回落「跟随转写语言」。
   */
  ui_lang?: string | null
  /**
   * 当前身份对这个会议能做什么（后端算好；UI 靠它决定按钮画不画）。
   * `write` = 能否改名/归档/删除/丢弃/选热词/上传音频（都落 meeting 的写档，
   * 且**创建者对自建会议一律放行**）。`operate` 是旧名，保留兼容。
   * 缺省（老后端）按"无权限"处理：少画按钮，不多画。
   */
  permissions?: { read: boolean; write?: boolean; operate?: boolean }
  /**
   * **此刻**是否有设备正在录制这个会议（服务端按 PCM 文件写入新鲜度判定）。
   *
   * 与 `status === "recording"` 的区别：那个只表示"开始录过"（刷新后仍是它），
   * 这个表示"现在真的在录"。旁观者靠它显示「录制中」并禁止进入 —— 进去会抢走
   * 或干扰正在录的那个客户端，也会让自动回收逻辑误把别人的录制当成孤儿掐断。
   */
  recording_live?: boolean
  created_at: string
  updated_at: string
  transcript_index_status?: "" | "building" | "ready" | "failed"
  transcript_index_error?: string
}

export const getMeetings = () =>
  request<Meeting[]>("/meetings")

export const getMeeting = (id: string) =>
  request<Meeting>(`/meetings/${id}`)

/** Fire-and-forget warm-up fired on meeting-row pointerdown: the click's real
 * fetch joins this in-flight request via the http-layer GET coalescing. */
export const prefetchMeeting = (id: string) =>
  getMeeting(id).catch(() => undefined)

export const startTranscriptIndex = (id: string) =>
  request<Meeting>(`/meetings/${id}/transcript-index`, { method: "POST" })

export interface MeetingGroupMember {
  meeting_id: string
  n: number
}

export interface MeetingGroup {
  id: string
  title: string
  members: MeetingGroupMember[]
  archived?: boolean
  /**
   * 能否删/归档/改名这个分组（后端按"角色整体有没有 operate 档"算——组是跨会议的
   * 容器，逐会议判定代表不了"删整组"这一下）。
   */
  can_operate?: boolean
  created_at: string
  updated_at: string
  last_chat_at: string
}

export const listMeetingGroups = () =>
  request<MeetingGroup[]>("/meeting-groups")

export const getMeetingGroup = (id: string) =>
  request<MeetingGroup>(`/meeting-groups/${id}`)

export const createMeetingGroup = (meetingId: string, title?: string) =>
  request<MeetingGroup>("/meeting-groups", {
    method: "POST",
    body: JSON.stringify({ meeting_id: meetingId, title: title || "" }),
  })

export const addMeetingGroupMember = (groupId: string, meetingId: string) =>
  request<MeetingGroup>(`/meeting-groups/${groupId}/members`, {
    method: "POST",
    body: JSON.stringify({ meeting_id: meetingId }),
  })

export const removeMeetingGroupMember = (groupId: string, meetingId: string) =>
  request<MeetingGroup>(`/meeting-groups/${groupId}/members/${meetingId}`, {
    method: "DELETE",
  })

export const deleteMeetingGroup = (groupId: string, deleteMeetings = true) =>
  request<{ message?: string }>(
    `/meeting-groups/${groupId}?delete_meetings=${deleteMeetings ? "true" : "false"}`,
    { method: "DELETE" },
  )

export const updateMeetingGroup = (groupId: string, title: string) =>
  request<MeetingGroup>(`/meeting-groups/${groupId}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  })

export const archiveMeetingGroup = (groupId: string) =>
  request<MeetingGroup>(`/meeting-groups/${groupId}/archive`, { method: "POST" })

export const unarchiveMeetingGroup = (groupId: string) =>
  request<MeetingGroup>(`/meeting-groups/${groupId}/unarchive`, { method: "POST" })

export const listGroupsForMeeting = (meetingId: string) =>
  request<MeetingGroup[]>(`/meetings/${meetingId}/groups`)

export const archiveMeeting = (id: string) =>
  request<Meeting>(`/meetings/${id}/archive`, { method: "POST" })

export const unarchiveMeeting = (id: string) =>
  request<Meeting>(`/meetings/${id}/unarchive`, { method: "POST" })

export const createMeeting = (title?: string, opts?: { uiLang?: string }) =>
  request<Meeting>("/meetings", {
    method: "POST",
    body: JSON.stringify({
      ...(title ? { title } : {}),
      // 创建者界面语言 → 实时纪要输出语言（后端白名单校验，其余值丢弃）
      ...(opts?.uiLang ? { ui_lang: opts.uiLang } : {}),
    }),
  })

export function meetingHotWordIds(meeting: Pick<Meeting, "hot_words_library_id" | "hot_words_library_ids">): string[] {
  if (meeting.hot_words_library_ids && meeting.hot_words_library_ids.length > 0) {
    return meeting.hot_words_library_ids
  }
  return meeting.hot_words_library_id ? [meeting.hot_words_library_id] : []
}

export const updateMeeting = (id: string, data: Partial<Pick<Meeting, "title" | "speaker_names" | "hot_words_library_id" | "hot_words_library_ids" | "expected_people"> & { notes?: string; blueprint?: BlueprintItem[]; tabs?: MeetingTab[] }>) =>
  request<Meeting>(`/meetings/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  })

export const getMeetingBrief = (id: string) =>
  request<MeetingBrief>(`/meetings/${id}/brief`)

export const generateMeetingBrief = (id: string, locale = "zh-CN") =>
  request<MeetingBrief>(`/meetings/${id}/brief/generate`, {
    method: "POST",
    body: JSON.stringify({ locale }),
  })

export const deleteMeeting = (id: string) =>
  request<{ message?: string }>(`/meetings/${id}`, {
    method: "DELETE",
  })


export const discardMeetingRecording = (id: string) =>
  request<Meeting>(`/meetings/${id}/discard`, {
    method: "POST",
  })

export const appendRecordingPcm = async (id: string, pcm: ArrayBuffer) => {
  const res = await apiFetch(`/meetings/${id}/recording-pcm`, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: pcm,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`PCM persist failed (${res.status}): ${body}`)
  }
}

export const finalizeMeetingRecording = (id: string, recovery = false) =>
  request<Meeting>(`/meetings/${id}/finalize-recording${recovery ? "?recovery=true" : ""}`, {
    method: "POST",
  })

export const uploadMeetingAudio = async (id: string, file: File) => {
  const formData = new FormData()
  formData.append("file", file)
  return withCriticalOp(async () => {
    const res = await apiFetch(`/meetings/${id}/upload-audio`, {
      method: "POST",
      body: formData,
    })
    if (!res.ok) {
      const body = await res.text()
      throw new Error(`Upload failed (${res.status}): ${body}`)
    }
    return res.json() as Promise<Meeting>
  })
}

export const uploadMeetingImage = async (id: string, file: File) => {
  const formData = new FormData()
  formData.append("file", file)
  const res = await apiFetch(`/meetings/${id}/images`, {
    method: "POST",
    body: formData,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`Image upload failed (${res.status}): ${body}`)
  }
  return res.json() as Promise<{ url: string; filename: string }>
}

export const uploadMeetingNotes = async (id: string, file: File) => {
  const formData = new FormData()
  formData.append("file", file)
  const res = await apiFetch(`/meetings/${id}/upload-notes`, {
    method: "POST",
    body: formData,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`Upload failed (${res.status}): ${body}`)
  }
  return res.json() as Promise<{ notes_content: string }>
}

export const transcribeMeeting = (id: string, languageHints?: string[]) =>
  request<{ message: string; task_id?: string }>(`/meetings/${id}/transcribe`, {
    method: "POST",
    body: JSON.stringify({ language_hints: languageHints }),
  })

export const cancelTranscribeMeeting = (id: string) =>
  request<{ message: string }>(`/meetings/${id}/cancel-transcribe`, {
    method: "POST",
  })

export const generateMeetingSummary = (id: string) =>
  request<Meeting>(`/meetings/${id}/generate-summary`, {
    method: "POST",
  })

// ── Blueprint SSE streaming ──────────────────────────────────

export interface BlueprintStreamCallbacks {
  onState?: (data: { summary?: string; blueprint?: string }) => void
  onThinking?: (text: string) => void
  onToken?: (text: string) => void
  onSummaryDone?: (data: { general_md: string }) => void
  onBlueprintDone?: (data: { taxonomy: Record<string, unknown>; blueprint: Array<Record<string, unknown>> }) => void
  onError?: (message: string) => void
}

/** SSE parser state machine — handles partial chunks across fetch reads. */
class SSEDecoder {
  private buffer = ""
  private eventType = ""
  private dataBuffer = ""

  /** Push a raw text chunk. Returns complete SSE messages as [event, data] tuples. */
  push(chunk: string): Array<[string, string]> {
    const results: Array<[string, string]> = []
    this.buffer += chunk
    const lines = this.buffer.split("\n")
    // Keep last (potentially incomplete) line in buffer
    this.buffer = lines.pop() ?? ""

    for (const line of lines) {
      if (line.startsWith("event: ")) {
        this.eventType = line.slice(7).trim()
      } else if (line.startsWith("data: ")) {
        this.dataBuffer += line.slice(6)
      } else if (line === "" && this.eventType) {
        // Empty line = message boundary
        results.push([this.eventType, this.dataBuffer])
        this.eventType = ""
        this.dataBuffer = ""
      }
    }
    return results
  }
}

/** Connect to the blueprint streaming endpoint and invoke callbacks. */
export function streamBlueprint(
  meetingId: string,
  callbacks: BlueprintStreamCallbacks,
): AbortController {
  const controller = new AbortController()
  const decoder = new SSEDecoder()

  apiFetch(`/meetings/${meetingId}/blueprint/stream`, {
    signal: controller.signal,
  }).then(async (resp) => {
    if (!resp.ok) {
      callbacks.onError?.(`HTTP ${resp.status}: ${await resp.text()}`)
      return
    }
    const reader = resp.body?.getReader()
    if (!reader) return
    const utf8 = new TextDecoder("utf-8")

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      const text = utf8.decode(value, { stream: true })

      for (const [event, data] of decoder.push(text)) {
        switch (event) {
          case "state": {
            const parsed = JSON.parse(data) as { summary?: string; blueprint?: string }
            callbacks.onState?.(parsed)
            break
          }
          case "thinking":
            callbacks.onThinking?.(JSON.parse(data) as string)
            break
          case "token":
            callbacks.onToken?.(JSON.parse(data) as string)
            break
          case "summary_done": {
            const parsed = JSON.parse(data) as { general_md: string }
            callbacks.onSummaryDone?.(parsed)
            break
          }
          case "blueprint_done": {
            const parsed = JSON.parse(data) as { taxonomy: Record<string, unknown>; blueprint: Array<Record<string, unknown>> }
            callbacks.onBlueprintDone?.(parsed)
            break
          }
          case "error": {
            const parsed = JSON.parse(data) as { message: string }
            callbacks.onError?.(parsed.message)
            break
          }
        }
      }
    }
  }).catch((err: unknown) => {
    if (err instanceof DOMException && err.name === "AbortError") return
    callbacks.onError?.(err instanceof Error ? err.message : String(err))
  })

  return controller
}

// ── Section SSE streaming ────────────────────────────────────

export interface SectionStreamCallbacks {
  onState?: (data: { section_gen?: string }) => void
  onThinking?: (text: string) => void
  onToken?: (text: string) => void
  onSectionDone?: (data: { tab_id: string; md: string }) => void
  onError?: (message: string) => void
}

/** Connect to the section generation streaming endpoint and invoke callbacks. */
export function streamSectionGenerate(
  meetingId: string,
  tabId: string,
  callbacks: SectionStreamCallbacks,
): AbortController {
  const controller = new AbortController()
  const decoder = new SSEDecoder()

  apiFetch(`/meetings/${meetingId}/sections/${tabId}/generate-stream`, {
    signal: controller.signal,
  }).then(async (resp) => {
    if (!resp.ok) {
      callbacks.onError?.(`HTTP ${resp.status}: ${await resp.text()}`)
      return
    }
    const reader = resp.body?.getReader()
    if (!reader) return
    const utf8 = new TextDecoder("utf-8")

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      const text = utf8.decode(value, { stream: true })

      for (const [event, data] of decoder.push(text)) {
        switch (event) {
          case "state": {
            const parsed = JSON.parse(data) as { section_gen?: string }
            callbacks.onState?.(parsed)
            break
          }
          case "thinking":
            callbacks.onThinking?.(JSON.parse(data) as string)
            break
          case "token":
            callbacks.onToken?.(JSON.parse(data) as string)
            break
          case "section_done": {
            const parsed = JSON.parse(data) as { tab_id: string; md: string }
            callbacks.onSectionDone?.(parsed)
            break
          }
          case "error": {
            const parsed = JSON.parse(data) as { message: string }
            callbacks.onError?.(parsed.message)
            break
          }
        }
      }
    }
  }).catch((err: unknown) => {
    if (err instanceof DOMException && err.name === "AbortError") return
    callbacks.onError?.(err instanceof Error ? err.message : String(err))
  })

  return controller
}

export const getMeetingTranscript = (id: string) =>
  request<{ segments: TranscriptSegment[] }>(`/meetings/${id}/transcript`)

// ── Live summary (in-meeting incremental state) ──

export interface LiveSummaryEntry {
  id: string
  kind: string // point | decision | question | action
  text: string
  speaker?: string | null
  t: number
  status: string // active | resolved
}

export interface LiveSummaryTopic {
  text: string
  since: number
  closed: boolean
}

export interface LiveSummaryState {
  entries: LiveSummaryEntry[]
  topic: LiveSummaryTopic | null
  compacted_upto?: string
  tail_from_t: number
  round: number
  engine: string // idle | running
  updated_at: string
}

export const getLiveSummary = (id: string) =>
  request<{ state: LiveSummaryState | null }>(`/meetings/${id}/live-summary`)

export const saveMeetingTranscript = (
  id: string,
  payload: { segments: TranscriptSegment[]; text?: string; partial?: boolean },
) =>
  request<{ message: string; segments: number }>(`/meetings/${id}/save-transcript`, {
    method: "POST",
    body: JSON.stringify(payload),
  })

// ── Meeting v2: Extract (v3) ──

export interface ExtractReceipt {
  source: "blueprint" | "custom"
  tab_id?: string
  blueprint_id?: string  // v3: matches BlueprintItem.blueprint_id
  name: string
  description: string
}

export const extract = (id: string, receipts: ExtractReceipt[]) =>
  request<Meeting>(`/meetings/${id}/extract`, {
    method: "POST",
    body: JSON.stringify({ receipts }),
  })

export const deleteSection = (meetingId: string, tabId: string) =>
  request<Meeting>(`/meetings/${meetingId}/sections/${tabId}`, {
    method: "DELETE",
  })

export const regenerateSection = (meetingId: string, tabId: string) =>
  request<Meeting>(`/meetings/${meetingId}/sections/${tabId}/regenerate`, {
    method: "POST",
  })

/** Meeting after allocate, plus file-mgmt / timeline bridge fields. */
export type AllocateSectionResult = Meeting & {
  file_id?: string
  task_id?: string | null
  node_id?: string | null
  source?: string
  collection_id?: string
  chain_id?: string | null
  todo_candidate_count?: number
}

export const allocateSection = (
  meetingId: string,
  tabId: string,
  collectionId: string,
  chainId?: string | null,
) =>
  request<AllocateSectionResult>(`/meetings/${meetingId}/sections/${tabId}/allocate`, {
    method: "POST",
    body: JSON.stringify({
      collection_id: collectionId,
      ...(chainId ? { chain_id: chainId } : {}),
    }),
  })

export const deleteSectionAllocation = (meetingId: string, tabId: string) =>
  request<Meeting>(`/meetings/${meetingId}/sections/${tabId}/allocate`, {
    method: "DELETE",
  })

export interface MeetingTodoCandidate {
  candidate_id: string
  title: string
  body?: string | null
  assignee_label?: string | null
  priority?: string | null
  ddl?: string | null
  raw_line?: string
  created_todo_id?: string | null
  section_tab_id?: string
  section_name?: string
}

export interface SectionTodoCandidatesResponse {
  meeting_id: string
  tab_id: string
  section_name: string
  collection_id: string
  chain_id: string
  node_id: string
  candidates: MeetingTodoCandidate[]
  error?: string
}

export const getSectionTodoCandidates = (
  meetingId: string,
  tabId: string,
  opts?: { refresh?: boolean },
) => {
  const qs = opts?.refresh ? "?refresh=true" : ""
  return request<SectionTodoCandidatesResponse>(
    `/meetings/${meetingId}/sections/${tabId}/todo-candidates${qs}`,
  )
}

/** Bind candidate_id → todo_id after checklist create (anti-duplicate). */
export const markTodoCandidatesCreated = (
  meetingId: string,
  items: Array<{
    tab_id?: string | null
    candidate_id: string
    todo_id: string
  }>,
) =>
  request<Meeting>(`/meetings/${meetingId}/todo-candidates/mark-created`, {
    method: "POST",
    body: JSON.stringify({ items }),
  })

export const getSectionMd = (meetingId: string, tabId: string) =>
  apiFetch(`/meetings/${meetingId}/sections/${tabId}/md`)
    .then((r) => (r.ok ? r.text() : null))

export type MeetingSummaryFormat = 'md' | 'docx' | 'pdf'
export interface MeetingSummaryDownload { blob: Blob; filename: string }

export async function downloadMeetingSummary(
  meetingId: string, tabId: string, format: MeetingSummaryFormat,
  language?: string | null, signal?: AbortSignal,
): Promise<MeetingSummaryDownload> {
  const response = await apiFetch(`/meetings/${encodeURIComponent(meetingId)}/sections/${encodeURIComponent(tabId)}/export`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({format, language: language || null}), signal,
  })
  if (!response.ok) throw parseApiErrorBody(response.status, await response.text())
  const disposition = response.headers.get('Content-Disposition') || ''
  let filename = `Meeting summary.${format}`
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  const plain = /filename="([^"]+)"/i.exec(disposition)
  try {
    filename = encoded ? decodeURIComponent(encoded[1]) : plain?.[1] || filename
  } catch { /* A malformed filename must not discard an otherwise valid download. */ }
  return {blob: await response.blob(), filename}
}

export const saveSectionMd = (meetingId: string, tabId: string, content: string) =>
  request<{
    ok: boolean
    path?: string
    needs_reingest?: boolean
    meeting?: Meeting | null
  }>(`/meetings/${meetingId}/sections/${tabId}/md`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  })

export interface SectionDescResult {
  found: boolean
  description?: string
}

export const generateSectionDescription = (meetingId: string, sectionName: string) =>
  request<SectionDescResult>(`/meetings/${meetingId}/generate-section-description`, {
    method: "POST",
    body: JSON.stringify({ section_name: sectionName }),
  })

// ── Summary Translation ──

/** Target languages offered by the summary translation dropdown.
 *  `code` matches the backend file naming (`{tab_id}_{LANG}.md`). */
export const TRANSLATE_LANGUAGES: { code: string; label: string }[] = [
  { code: "CN", label: "中文" },
  { code: "EN", label: "English" },
  { code: "JA", label: "日本語" },
  { code: "KO", label: "한국어" },
  { code: "FR", label: "Français" },
  { code: "DE", label: "Deutsch" },
  { code: "ES", label: "Español" },
]

export const getSummaryTranslations = (meetingId: string, tabId: string) =>
  request<{ languages: string[] }>(`/meetings/${meetingId}/sections/${tabId}/translations`)

export const getActiveTranslations = (meetingId: string) =>
  request<{ active: { tab_id: string; language: string }[] }>(
    `/meetings/${meetingId}/translations/active`,
  )

export interface TranslationStreamCallbacks {
  onState?: (data: { translation_gen?: string }) => void
  onToken?: (text: string) => void
  onDone?: (data: { tab_id: string; language: string; md: string; cached: boolean }) => void
  /** kind "remote" = the backend reported a real failure (HTTP error or an
   *  `error` SSE event, e.g. content moderation); kind "network" = the
   *  connection dropped client-side (teardown, server unreachable). */
  onError?: (message: string, kind: "remote" | "network") => void
}

/** Connect to the summary translation streaming endpoint and invoke callbacks.
 *  The endpoint serves cached files instantly, re-attaches to in-progress
 *  tasks (replaying missed tokens), or starts a fresh generation. */
export function streamTranslation(
  meetingId: string,
  tabId: string,
  lang: string,
  callbacks: TranslationStreamCallbacks,
): AbortController {
  const controller = new AbortController()
  const decoder = new SSEDecoder()

  apiFetch(
    `/meetings/${meetingId}/sections/${tabId}/translate/stream?lang=${encodeURIComponent(lang)}`,
    { signal: controller.signal },
  ).then(async (resp) => {
    if (!resp.ok) {
      callbacks.onError?.(`HTTP ${resp.status}: ${await resp.text()}`, "remote")
      return
    }
    const reader = resp.body?.getReader()
    if (!reader) return
    const utf8 = new TextDecoder("utf-8")

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      const text = utf8.decode(value, { stream: true })

      for (const [event, data] of decoder.push(text)) {
        switch (event) {
          case "state": {
            callbacks.onState?.(JSON.parse(data) as { translation_gen?: string })
            break
          }
          case "token":
            callbacks.onToken?.(JSON.parse(data) as string)
            break
          case "translation_done": {
            callbacks.onDone?.(
              JSON.parse(data) as { tab_id: string; language: string; md: string; cached: boolean },
            )
            break
          }
          case "error": {
            callbacks.onError?.((JSON.parse(data) as { message: string }).message, "remote")
            break
          }
        }
      }
    }
  }).catch((err: unknown) => {
    // Aborts (intentional or page teardown via pagehide) are not errors.
    if (err instanceof DOMException && err.name === "AbortError") return
    // A genuine mid-stream connection drop — transient, resumable on reload.
    callbacks.onError?.(err instanceof Error ? err.message : String(err), "network")
  })

  return controller
}

