import assert from "node:assert/strict"
import { test } from "node:test"

import {
  desktopDmgAssetName,
  pickDesktopDownloadUrl,
  shouldOfferUpdate,
} from "../frontend/src/lib/update-release.ts"

test("dmg asset name includes the release version", () => {
  assert.equal(desktopDmgAssetName("1.2.0"), "SinkDuce-macos-arm64-v1.2.0.dmg")
  assert.equal(desktopDmgAssetName("v1.2.0"), "SinkDuce-macos-arm64-v1.2.0.dmg")
})

test("picks only the versioned dmg for that release", () => {
  const url = pickDesktopDownloadUrl(
    [
      { name: "SinkDuce-macos-arm64.dmg", browser_download_url: "https://x/old.dmg" },
      {
        name: "SinkDuce-macos-arm64-v1.2.0.dmg",
        browser_download_url: "https://x/SinkDuce-macos-arm64-v1.2.0.dmg",
      },
      { name: "other.dmg", browser_download_url: "https://x/other.dmg" },
    ],
    "v1.2.0",
  )
  assert.equal(url, "https://x/SinkDuce-macos-arm64-v1.2.0.dmg")
})

test("returns null when the versioned dmg is missing", () => {
  assert.equal(
    pickDesktopDownloadUrl(
      [
        { name: "SinkDuce-macos-arm64.dmg", browser_download_url: "https://x/old.dmg" },
        { name: "SinkDuce.dmg", browser_download_url: "https://x/SinkDuce.dmg" },
      ],
      "v1.2.0",
    ),
    null,
  )
})

test("returns null for missing or empty assets", () => {
  assert.equal(pickDesktopDownloadUrl(undefined, "v1.2.0"), null)
  assert.equal(pickDesktopDownloadUrl(null, "v1.2.0"), null)
  assert.equal(pickDesktopDownloadUrl([], "v1.2.0"), null)
})

test("desktop update is hidden until the versioned dmg exists", () => {
  assert.equal(
    shouldOfferUpdate({ desktop: true, versionNewer: true, downloadUrl: null }),
    false,
  )
  assert.equal(
    shouldOfferUpdate({
      desktop: true,
      versionNewer: true,
      downloadUrl: "https://x/SinkDuce-macos-arm64-v1.2.0.dmg",
    }),
    true,
  )
})

test("docker update still shows when there is no dmg", () => {
  assert.equal(
    shouldOfferUpdate({ desktop: false, versionNewer: true, downloadUrl: null }),
    true,
  )
  assert.equal(
    shouldOfferUpdate({ desktop: false, versionNewer: false, downloadUrl: null }),
    false,
  )
})
