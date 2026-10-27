/**
 * fetchFolderTree 的防御：collection 不存在时不要弹「加载文件夹失败」。
 *
 * 场景：来源刚切换 / collection 刚被别的设备删掉 / 首次挂载时 fetchCollections
 * 还没回来——此时 folder-view 已经拿着 activeCollection 去请求了，服务端 404，
 * 界面弹红字「加载文件夹失败：Collection 'X' not found」。
 *
 * 这类失败是**预期内的过期状态**，不是真正的故障：应该静默跳过（并让
 * fetchCollections 的自愈逻辑去清掉它），而不是打扰用户。
 *
 * 约定：isStaleCollectionError() 判定「这次失败只是因为 collection 过期」。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { test } from "node:test"

import { isStaleCollectionError } from "../src/api/collection-errors.ts"

test("后端的 Collection not found → 视为过期状态（不该弹错）", () => {
  // FastAPI 结构：{"detail": "Collection 'col_x' not found"}
  const err = Object.assign(new Error("Collection 'col_707b8ca8d617' not found"), {
    status: 404,
    detail: "Collection 'col_707b8ca8d617' not found",
  })
  assert.equal(isStaleCollectionError(err), true)
})

test("原始响应体里含 not found 也能识别（错误对象形态不一）", () => {
  const err = Object.assign(new Error("API 404"), {
    status: 404,
    rawBody: '{"detail":"Collection \'col_abc\' not found"}',
  })
  assert.equal(isStaleCollectionError(err), true)
})

test("400 Collection not found（后端另一处用 400）也识别", () => {
  const err = Object.assign(new Error("bad request"), {
    status: 400,
    detail: "Collection 'col_x' not found",
  })
  assert.equal(isStaleCollectionError(err), true)
})

test("真正的 500 不当作过期（该报错还是要报）", () => {
  const err = Object.assign(new Error("boom"), { status: 500, detail: "internal error" })
  assert.equal(isStaleCollectionError(err), false)
})

test("网络类错误不当作过期", () => {
  assert.equal(isStaleCollectionError(new TypeError("Failed to fetch")), false)
})

test("空值安全", () => {
  assert.equal(isStaleCollectionError(null), false)
  assert.equal(isStaleCollectionError(undefined), false)
  assert.equal(isStaleCollectionError("Collection not found"), false)
})
