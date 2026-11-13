import assert from "node:assert/strict"
import { test } from "node:test"

import {
  desktopDmgAssetName,
  detectDesktopClient,
  editionAllowsUpdate,
  pickDesktopDownloadUrl,
  releaseNotesForLocale,
  shouldOfferUpdate,
} from "../frontend/src/lib/update-release.ts"

test("enterprise edition does not offer community release updates", () => {
  assert.equal(editionAllowsUpdate("ENTERPRISE"), false)
  assert.equal(editionAllowsUpdate("COMMUNITY"), true)
})

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

test("desktop client is true when health says so", () => {
  assert.equal(detectDesktopClient(true), true)
  assert.equal(detectDesktopClient(false), false)
  assert.equal(detectDesktopClient(undefined), false)
})

test("release notes select the interface language before rendering", () => {
  const body = [
    "- 画布会记住缩放比例。",
    "- 新增 SVG 导出。",
    "",
    "- Remember the canvas zoom.",
    '- Improve the "中文" label in settings.',
  ].join("\n")
  assert.deepEqual(releaseNotesForLocale(body, "en"), {
    language: "en", items: ["Remember the canvas zoom.", 'Improve the "中文" label in settings.'],
  })
  assert.deepEqual(releaseNotesForLocale(body, "zh-CN"), {
    language: "zh-CN", items: ["画布会记住缩放比例。", "新增 SVG 导出。"],
  })
})

test("adjacent translated bullets and wrapped lines stay in the right language", () => {
  const body = "### Changes\r\n* **保存画布。**\r\n* Save the canvas\r\n  when the editor closes."
  assert.deepEqual(releaseNotesForLocale(body, "en"), {
    language: "en", items: ["Save the canvas when the editor closes."],
  })
  assert.deepEqual(releaseNotesForLocale(body, "zh-CN"), {
    language: "zh-CN", items: ["保存画布。"],
  })
})

test("legacy single-language notes keep their original language and text", () => {
  assert.deepEqual(releaseNotesForLocale("### Fixes\n- Restore canvas shortcuts.", "zh-CN"), {
    language: "en", items: ["Restore canvas shortcuts."],
  })
  assert.deepEqual(releaseNotesForLocale(" \n", "zh-CN"), {
    language: "zh-CN", items: [],
  })
})
