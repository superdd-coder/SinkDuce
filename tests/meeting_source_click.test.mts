import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { test } from "node:test"

const chat = readFileSync(
  new URL("../frontend/src/components/chat/chat-view.tsx", import.meta.url),
  "utf8",
)
const qc = readFileSync(
  new URL("../frontend/src/components/database/quick-chat.tsx", import.meta.url),
  "utf8",
)
const db = readFileSync(
  new URL("../frontend/src/components/database/database-view.tsx", import.meta.url),
  "utf8",
)

test("Chat meeting source opens the meeting view, not chunk detail", () => {
  assert.match(chat, /source_type === "meeting"/)
  assert.match(chat, /setActiveMeeting\(mid\)/)
  assert.match(chat, /setSidebarView\("meeting"\)/)
})

test("Collection Quick Chat meeting source opens the meeting view", () => {
  assert.match(qc, /source_type === "meeting"/)
  assert.match(qc, /onMeetingClick/)
  assert.match(db, /onMeetingClick/)
  assert.match(db, /setSidebarView\("meeting"\)/)
  assert.doesNotMatch(qc, /sentence locate|scrollToSentence/)
})
