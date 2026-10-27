/**
 * 预览 / 下载 URL 的鉴权（连远端盒子时必需）。
 *
 * 这类请求由浏览器或第三方 viewer 自己发起，加不了请求头，
 * 钥匙只能走 ?token= 查询参数。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { test } from "node:test"

import { authorizePreviewUrl } from "../src/api/connection-health.ts"

test("远端盒子：URL 附上钥匙（查询参数）", async () => {
  assert.equal(
    authorizePreviewUrl("http://192.168.1.30:18901/api/documents/preview/a.pdf", "sd1_k"),
    "http://192.168.1.30:18901/api/documents/preview/a.pdf?token=sd1_k",
  )
})

test("已有查询参数时用 & 拼接", () => {
  assert.equal(
    authorizePreviewUrl("http://box/api/documents/preview/a.pdf?collection=c1", "sd1_k"),
    "http://box/api/documents/preview/a.pdf?collection=c1&token=sd1_k",
  )
})

test("钥匙做过 URL 编码（避免特殊字符破坏查询串）", () => {
  const out = authorizePreviewUrl("http://box/api/x", "a+b/c=d")
  assert.equal(out, "http://box/api/x?token=a%2Bb%2Fc%3Dd")
})

test("已有 token 时不重复添加", () => {
  const u = "http://box/api/x?token=already"
  assert.equal(authorizePreviewUrl(u, "sd1_other"), u)
})

test("没有钥匙（本机）时原样返回", () => {
  assert.equal(authorizePreviewUrl("/api/documents/preview/a.pdf", null), "/api/documents/preview/a.pdf")
  assert.equal(authorizePreviewUrl("http://box/api/x", null), "http://box/api/x")
})

test("url 为空时安全返回", () => {
  assert.equal(authorizePreviewUrl(null, "sd1_k"), null)
  assert.equal(authorizePreviewUrl("", "sd1_k"), "")
})
