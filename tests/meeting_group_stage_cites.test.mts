import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { test } from "node:test"

const stage = readFileSync(
  new URL("../frontend/src/components/meeting/meeting-group-stage.tsx", import.meta.url),
  "utf8",
)

function overlaySource(): string {
  const start = stage.indexOf("const openOverlay")
  const end = stage.indexOf("const handleStartEditTitle")
  assert.ok(start >= 0 && end > start, "openOverlay body not found")
  return stage.slice(start, end)
}

test("cite click resolves by snapshot meeting_id; roster n is only a fallback", () => {
  // Roster n gets reused after remove + re-add, so an n-first lookup opens
  // the wrong meeting for answers written before the swap.
  const src = overlaySource()
  assert.match(
    src,
    /const mem = meetingId\s*\?\s*group\?\.members\.find\(\(x\) => x\.meeting_id === meetingId\)\s*:\s*group\?\.members\.find\(\(x\) => Number\(x\.n\) === Number\(n\)\)/,
  )
})

test("clicking a cite whose meeting left the group tells the user", () => {
  const src = overlaySource()
  assert.match(src, /toast\.error\(t\("meeting\.citeMeetingRemoved"\)\)/)
})
