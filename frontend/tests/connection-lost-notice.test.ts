/**
 * 连接断开提示的存取（跨整页刷新传递一次）。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

import { rememberConnectionLost, takeConnectionLostNotice } from "../src/api/connection-health.ts"

/** Node 里没有 sessionStorage，给个最小实现。 */
function installFakeSessionStorage() {
  const map = new Map<string, string>()
  ;(globalThis as unknown as { window: unknown }).window = {
    sessionStorage: {
      getItem: (k: string) => (map.has(k) ? (map.get(k) as string) : null),
      setItem: (k: string, v: string) => void map.set(k, v),
      removeItem: (k: string) => void map.delete(k),
    },
  }
  return map
}

let store: Map<string, string>
beforeEach(() => {
  store = installFakeSessionStorage()
})

test("记下后能读回，且只读一次（避免每次刷新都弹）", () => {
  rememberConnectionLost({ box_id: "b1", box_name: "书房盒子", reason: "revoked" })
  assert.deepEqual(takeConnectionLostNotice(), {
    box_id: "b1",
    box_name: "书房盒子",
    reason: "revoked",
  })
  assert.equal(takeConnectionLostNotice(), null)
})

test("没有记录时返回 null", () => {
  assert.equal(takeConnectionLostNotice(), null)
})

test("坏数据不会让页面崩", () => {
  store.set("sinkduce.pairing.connectionLost", "{not json")
  assert.equal(takeConnectionLostNotice(), null)
})

test("缺字段的记录被忽略", () => {
  store.set("sinkduce.pairing.connectionLost", JSON.stringify({ box_id: "b1" }))
  assert.equal(takeConnectionLostNotice(), null)
})
