import assert from "node:assert/strict"
import { test } from "node:test"

import {
  ApiError,
  parseApiErrorBody,
} from "../frontend/src/api/http.ts"

test("parses structured FastAPI detail with code", () => {
  const err = parseApiErrorBody(
    404,
    JSON.stringify({
      detail: {
        code: "file_not_found",
        params: { source: "notes.pdf" },
        message: "File not found for source 'notes.pdf'",
      },
    }),
  )
  assert.equal(err.status, 404)
  assert.equal(err.code, "file_not_found")
  assert.equal(err.params.source, "notes.pdf")
  assert.equal(err.message, "File not found for source 'notes.pdf'")
})

test("parses string detail as English fallback", () => {
  const err = parseApiErrorBody(400, JSON.stringify({ detail: "Invalid filename" }))
  assert.equal(err.status, 400)
  assert.equal(err.code, null)
  assert.equal(err.message, "Invalid filename")
})

test("malformed body keeps historical API status prefix", () => {
  const err = parseApiErrorBody(500, "not-json")
  assert.ok(err instanceof ApiError)
  assert.equal(err.code, null)
  assert.match(err.message, /API 500/)
})
