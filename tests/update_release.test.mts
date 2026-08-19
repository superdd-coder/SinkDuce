import assert from "node:assert/strict"
import { test } from "node:test"

import {
  DESKTOP_DMG_ASSET,
  pickDesktopDownloadUrl,
} from "../frontend/src/lib/update-release.ts"

test("prefers the stable macOS dmg asset name", () => {
  const url = pickDesktopDownloadUrl([
    { name: "notes.txt", browser_download_url: "https://x/notes.txt" },
    {
      name: DESKTOP_DMG_ASSET,
      browser_download_url: "https://x/SinkDuce-macos-arm64.dmg",
    },
    { name: "other.dmg", browser_download_url: "https://x/other.dmg" },
  ])
  assert.equal(url, "https://x/SinkDuce-macos-arm64.dmg")
})

test("falls back to any dmg when the stable name is missing", () => {
  const url = pickDesktopDownloadUrl([
    {
      name: "sinkduce-onnx-models-v1.0.0.zip",
      browser_download_url: "https://x/zip",
    },
    { name: "SinkDuce.dmg", browser_download_url: "https://x/SinkDuce.dmg" },
  ])
  assert.equal(url, "https://x/SinkDuce.dmg")
})

test("returns null when the release has no dmg", () => {
  assert.equal(
    pickDesktopDownloadUrl([
      {
        name: "sinkduce-onnx-models-v1.0.0.zip",
        browser_download_url: "https://x/zip",
      },
    ]),
    null,
  )
})

test("returns null for missing or empty assets", () => {
  assert.equal(pickDesktopDownloadUrl(undefined), null)
  assert.equal(pickDesktopDownloadUrl(null), null)
  assert.equal(pickDesktopDownloadUrl([]), null)
})
