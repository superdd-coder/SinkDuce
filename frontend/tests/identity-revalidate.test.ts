/**
 * 权限变更的「焦点重校验」（2026-09-17 用户要求）。
 *
 * **用户报的**：管理员改了某把 API Key 的角色 / 某个库的 ACL 之后，**被调整的那一端
 * 界面不会变**，要手动刷新整页才生效。
 *
 * **为什么不用事件流推**（设计取舍，钉在这里防止以后有人"顺手改成事件"）：
 * 事件投递有安全过滤——「无作用域键的事件默认不投递给非管理员」，而权限变更的
 * 受影响者恰恰多半不是管理员；且鸡生蛋（被降权的钥匙可能正因这次降权失去接收资格）；
 * 事件还是尽力而为（断线/缺口走 resync 丢弃），而"我的权限变了"必须必然送达。
 *
 * 所以走重校验：切回本页时重拉身份 + 库列表。**好处是顺带自愈所有其它服务端漂移**
 * （别人改 ACL、钥匙被吊销、页面睡了一觉、bfcache 恢复）。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { test } from "node:test"

import {
  REVALIDATE_MIN_INTERVAL_MS,
  revalidate,
  shouldRevalidate,
  startIdentityRevalidation,
} from "../src/lib/identity-revalidate.ts"

const MAIN = readFileSync(new URL("../src/main.tsx", import.meta.url), "utf8")
const LIB = readFileSync(
  new URL("../src/lib/identity-revalidate.ts", import.meta.url),
  "utf8",
)

const deps = (over: Partial<Record<string, () => unknown>> = {}) => {
  const calls: string[] = []
  return {
    calls,
    refreshIdentity: () => {
      calls.push("identity")
    },
    refreshCollections: () => {
      calls.push("collections")
    },
    ...over,
  }
}

// ── 节流 ──────────────────────────────────────────────────

test("从没跑过 → 该跑（首次获得焦点立即校验）", () => {
  assert.equal(shouldRevalidate(0, 1_000_000), true)
})

test("距上次不足最小间隔 → 不跑（快速来回切标签页不打请求串）", () => {
  assert.equal(shouldRevalidate(1_000_000, 1_000_000 + 500), false)
})

test("超过最小间隔 → 该跑", () => {
  assert.equal(
    shouldRevalidate(1_000_000, 1_000_000 + REVALIDATE_MIN_INTERVAL_MS),
    true,
  )
})

test("最小间隔是个合理的正数（防止被改成 0 导致疯狂重拉）", () => {
  assert.ok(REVALIDATE_MIN_INTERVAL_MS >= 1000)
})

// ── 实际执行 ──────────────────────────────────────────────

test("重校验会同时刷新身份与库列表（两者都必须）", async () => {
  const d = deps()
  const state = { lastMs: 0 }
  const ran = await revalidate(d, state)
  assert.equal(ran, true)
  assert.deepEqual(d.calls.sort(), ["collections", "identity"])
})

test("节流命中时不发请求", async () => {
  const d = deps()
  const state = { lastMs: 5_000 }
  const ran = await revalidate({ ...d, now: () => 5_100 }, state)
  assert.equal(ran, false)
  assert.deepEqual(d.calls, [])
})

test("跑过一次后再次调用（间隔内）不再发请求", async () => {
  const d = deps()
  let t = 100_000
  const state = { lastMs: 0 }
  const withNow = { ...d, now: () => t }
  assert.equal(await revalidate(withNow, state), true)
  const first = d.calls.length
  t += 100
  assert.equal(await revalidate(withNow, state), false)
  assert.equal(d.calls.length, first, "间隔内不该再发")
})

test("某个请求失败不影响另一个，也不抛（网络抖动不该弹错）", async () => {
  const calls: string[] = []
  const d = {
    refreshIdentity: () => {
      calls.push("identity")
      throw new Error("boom")
    },
    refreshCollections: () => {
      calls.push("collections")
    },
  }
  const state = { lastMs: 0 }
  await assert.doesNotReject(() => revalidate(d, state))
  assert.ok(calls.includes("identity") && calls.includes("collections"))
})

test("失败后仍会更新时间戳（不会因失败变成无限重试风暴）", async () => {
  const boom = {
    refreshIdentity: () => Promise.reject(new Error("x")),
    refreshCollections: () => Promise.reject(new Error("x")),
  }
  const state = { lastMs: 0 }
  await revalidate(boom, state)
  assert.ok(state.lastMs > 0)
})

test("域刷新是可选的：没提供也不报错", async () => {
  const d = deps()
  await assert.doesNotReject(() => revalidate(d, { lastMs: 0 }))
})

test("session refresh is independent of identity and collection refresh failures", async () => {
  const calls: string[] = []
  await revalidate({
    refreshIdentity: () => Promise.reject(new Error("temporary identity failure")),
    refreshCollections: () => { calls.push("collections") },
    refreshSessions: () => { calls.push("sessions") },
  }, { lastMs: 0 })
  assert.deepEqual(calls.sort(), ["collections", "sessions"])
})

test("online, focus and restored-page events recover the session list and dispose their listeners", async () => {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window")
  const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document")
  const fakeWindow = new EventTarget()
  const fakeDocument = Object.assign(new EventTarget(), { visibilityState: "visible" })
  Object.defineProperty(globalThis, "window", { configurable: true, value: fakeWindow })
  Object.defineProperty(globalThis, "document", { configurable: true, value: fakeDocument })
  let requests = 0
  let now = 100_000
  const dispose = startIdentityRevalidation({
    refreshIdentity: () => undefined,
    refreshCollections: () => undefined,
    refreshSessions: () => { requests++ },
    now: () => now,
  })
  try {
    for (const event of ["online", "focus", "pageshow"]) {
      now += REVALIDATE_MIN_INTERVAL_MS
      fakeWindow.dispatchEvent(new Event(event))
      await new Promise((resolve) => setImmediate(resolve))
    }
    assert.equal(requests, 3)
    dispose()
    now += REVALIDATE_MIN_INTERVAL_MS
    fakeWindow.dispatchEvent(new Event("online"))
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(requests, 3)
  } finally {
    dispose()
    if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow)
    else Reflect.deleteProperty(globalThis, "window")
    if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument)
    else Reflect.deleteProperty(globalThis, "document")
  }
})

// ── 界面接线 ──────────────────────────────────────────────

test("三个入口全挂了（focus / visibilitychange / pageshow，缺一个就有漏网场景）", () => {
  assert.match(LIB, /addEventListener\("focus"/)
  assert.match(LIB, /document\.addEventListener\("visibilitychange"/)
  assert.match(LIB, /addEventListener\("pageshow"/)
})

test("页面不可见时不跑（切走那一刻拉一次没意义）", () => {
  assert.match(LIB, /visibilityState === "hidden"\) return/)
})

test("main.tsx 启动时接上了重校验，并把三域一并 bump", () => {
  assert.match(MAIN, /startIdentityRevalidation\(/)
  assert.match(MAIN, /refreshIdentity/)
  assert.match(MAIN, /fetchCollections/)
  for (const d of ["meetings", "hot_words", "people"]) {
    assert.match(MAIN, new RegExp(`bumpDomain\\('${d}'\\)`), `缺 ${d} 的域刷新`)
  }
})
