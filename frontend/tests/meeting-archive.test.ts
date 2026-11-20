import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { after, before, test } from "node:test"
import { fileURLToPath } from "node:url"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { createServer } from "vite"
import type { Meeting, MeetingGroup } from "../src/api/meeting.ts"

let server: Awaited<ReturnType<typeof createServer>>
let MeetingList: typeof import("../src/components/meeting/meeting-list.tsx").MeetingList
let meetingsApi: typeof import("../src/api/meeting.ts")

before(async () => {
  const root = fileURLToPath(new URL("../", import.meta.url))
  server = await createServer({
    root, configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    resolve: { alias: { "@": `${root}src`, "#edition": `${root}src/edition` } },
    optimizeDeps: { noDiscovery: true },
  })
  const listModule = await server.ssrLoadModule("/src/components/meeting/meeting-list.tsx")
  MeetingList = listModule.MeetingList
  meetingsApi = await server.ssrLoadModule("/src/api/meeting.ts")
})

after(async () => { await server?.close() })

const timestamp = "2025-02-14T12:00:00Z"
const meeting: Meeting = {
  id: "sample-meeting", title: "Sample review", status: "completed",
  allocated_collections: [], allocated_file_ids: [],
  permissions: { read: true, write: true },
  created_at: timestamp, updated_at: timestamp,
}
const group: MeetingGroup = {
  id: "sample-group", title: "Sample group", members: [{ meeting_id: meeting.id, n: 1 }],
  created_at: timestamp, updated_at: timestamp, last_chat_at: timestamp,
}
const otherMeeting = { ...meeting, id: "second-meeting", title: "Second review" }
const twoMemberGroup = {
  ...group, members: [...group.members, { meeting_id: otherMeeting.id, n: 2 }],
}

function renderList(meetings: Meeting[], groups: MeetingGroup[] = []) {
  const noop = () => {}
  return renderToStaticMarkup(createElement(MeetingList, {
    meetings, groups, activeMeeting: null,
    onSelect: noop, onCreated: noop, onDelete: noop,
    onSelectGroup: noop, onDeleteGroup: noop, onToggleArchiveMeeting: noop,
    onCreateGroupFromMeeting: async () => {}, onCreateMeetingInGroup: noop,
    onDropMeetingInGroup: noop, onRemoveMeetingFromGroup: noop,
  }))
}

test("the meeting page opts into archived rows while ordinary callers keep the default", async (t) => {
  const view = readFileSync(new URL("../src/components/meeting/meeting-view.tsx", import.meta.url), "utf8")
  assert.match(view, /const fetchMeetings = useCallback\([\s\S]*?await getMeetings\(\{ includeArchived: true/)

  const archived = { ...meeting, archived: true }
  const fetch = t.mock.method(globalThis, "fetch", async (url) =>
    Response.json(String(url).includes("include_archived=true") ? [archived] : []),
  )
  assert.deepEqual(await meetingsApi.getMeetings(), [])
  assert.deepEqual(await meetingsApi.getMeetings({ includeArchived: true }), [archived])
  assert.deepEqual(fetch.mock.calls.map(({ arguments: [url] }) => url), [
    "/api/meetings", "/api/meetings?include_archived=true",
  ])
})

test("an already archived meeting creates a collapsed section at the end of the list", () => {
  const html = renderList([{ ...meeting, archived: true }])
  assert.match(html, /aria-expanded="false" class="pm-rail-archive-head"/)
  assert.match(html, /class="pm-rail-archive-count">1</)
  assert.doesNotMatch(html, /Sample review/)
})

test("an archived member stays muted in its active group and also has an archive entry", () => {
  const archived = renderList([{ ...meeting, archived: true }, otherMeeting], [twoMemberGroup])
  const [active, archive] = archived.split('<div class="pm-rail-archive">')
  assert.match(active, /Sample group[\s\S]*pm-rail-group-branch-wrap[\s\S]*class="pm-chat-sess-row is-child is-archived"[^>]*>[\s\S]*?Sample review/)
  assert.match(active, /class="pm-chat-sess-row is-child"[^>]*>[\s\S]*?Second review/)
  assert.match(archive, /pm-rail-archive-head/)
  assert.match(archived, /class="pm-rail-archive-count">1</)

  const restored = renderList([meeting, otherMeeting], [twoMemberGroup])
  assert.match(restored, /pm-rail-group-branch-wrap[\s\S]*Sample review/)
  assert.doesNotMatch(restored, /is-archived|pm-rail-archive-head/)
})

test("an archived group and its archived members share one top-level entry", () => {
  const html = renderList(
    [{ ...meeting, archived: true }, { ...otherMeeting, archived: true }],
    [{ ...twoMemberGroup, archived: true }],
  )
  assert.match(html, /class="pm-rail-archive-count">1</)
  assert.doesNotMatch(html, /Sample (group|review)/)
})
