/**
 * 远程模式（瘦壳）的前端判定面。
 *
 * 要钉住的行为：
 * 1. `evaluateRemoteGate` 的四态：零连接 / 未选中 / 活动盒子离线 / 钥匙被拒；
 *    `unknown`（探活未回）绝不触发引导态——启动瞬间不能闪。
 * 2. hub 镜像在远程模式下放行：瘦壳的 `/api/hub/boxes` 就是 Hub 转发的数据源，
 *    新配对的盒子必须能镜像进瘦壳，否则本机 MCP 对新盒子失明。
 * 3. 镜像的旧纪律在远程模式下依然成立：页面不是本机伺服的（非回环）就不发。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

import { evaluateRemoteGate } from "../src/lib/remote-gate.ts"

// ── 引导态判定（纯函数）──────────────────────────────────────

const online = { local: "online", b1: "online" } as never

test("零个已配对盒子 → 引导态（no_connections）", () => {
  const out = evaluateRemoteGate({ connections: [], activeSourceId: "local", health: online })
  assert.equal(out.show, true)
  assert.equal(out.reason, "no_connections")
})

test("有盒子但没选中（active=local）→ 引导态（no_active）", () => {
  const out = evaluateRemoteGate({
    connections: [{ box_id: "b1" }],
    activeSourceId: "local",
    health: online,
  })
  assert.equal(out.show, true)
  assert.equal(out.reason, "no_active")
})

test("活动盒子离线 → 引导态（unreachable）", () => {
  const out = evaluateRemoteGate({
    connections: [{ box_id: "b1" }],
    activeSourceId: "b1",
    health: { b1: "offline" } as never,
  })
  assert.equal(out.show, true)
  assert.equal(out.reason, "unreachable")
})

test("活动盒子钥匙被拒 → 引导态（revoked）", () => {
  const out = evaluateRemoteGate({
    connections: [{ box_id: "b1" }],
    activeSourceId: "b1",
    health: { b1: "revoked" } as never,
  })
  assert.equal(out.show, true)
  assert.equal(out.reason, "revoked")
})

test("活动盒子在线 → 不出引导态", () => {
  const out = evaluateRemoteGate({
    connections: [{ box_id: "b1" }],
    activeSourceId: "b1",
    health: { b1: "online" } as never,
  })
  assert.equal(out.show, false)
  assert.equal(out.reason, null)
})

test("探活还没回来（unknown）→ 不出引导态（避免启动闪屏）", () => {
  const out = evaluateRemoteGate({
    connections: [{ box_id: "b1" }],
    activeSourceId: "b1",
    health: {},
  })
  assert.equal(out.show, false)
})

// ── hub 镜像在远程模式下放行 ────────────────────────────────

type Call = { url: string; method: string; body: unknown }
let calls: Call[]

function installFakeBrowser() {
  const local = new Map<string, string>()
  const storage = {
    getItem: (k: string) => (local.has(k) ? (local.get(k) as string) : null),
    setItem: (k: string, v: string) => void local.set(k, v),
    removeItem: (k: string) => void local.delete(k),
    clear: () => local.clear(),
    key: () => null,
    length: 0,
  } as unknown as Storage
  const g = globalThis as unknown as {
    window: unknown
    localStorage: unknown
    fetch: unknown
    CustomEvent?: unknown
  }
  g.window = {
    localStorage: storage,
    sessionStorage: storage,
    location: {
      reload: () => {},
      hostname: "127.0.0.1",
      protocol: "http:",
      port: "18910",
      href: "http://127.0.0.1:18910/",
    },
    dispatchEvent: () => true,
    addEventListener: () => {},
    removeEventListener: () => {},
  }
  g.localStorage = storage
  calls = []
  g.fetch = (async (url: string, init?: RequestInit) => {
    calls.push({
      url: String(url),
      method: (init?.method ?? "GET").toUpperCase(),
      body: init?.body,
    })
    return new Response("{}", { status: 200, headers: { "content-type": "application/json" } })
  }) as unknown as typeof fetch
  if (!g.CustomEvent) {
    g.CustomEvent = class {
      type: string
      constructor(t: string) {
        this.type = t
      }
    }
  }
}

beforeEach(() => {
  installFakeBrowser()
})

async function loadModules(tag: string) {
  const origin = await import(`../src/api/origin.ts?rs-${tag}-${Math.random()}`)
  const hub = await import(`../src/api/hub.ts?rs-${tag}-${Math.random()}`)
  return { origin, hub }
}

/**
 * 镜像测试用**无 query** 的共享实例：hub.ts 内部 `import "./origin.ts"` 拿到的
 * 就是无 query 的那份——带 query 的实例会让 `setRemoteShellActive` 打进另一份
 * 模块状态，旗标永远读不到。
 */
async function sharedModules() {
  return {
    origin: await import("../src/api/origin.ts"),
    hub: await import("../src/api/hub.ts"),
  }
}

function conn(boxId: string) {
  return {
    box_id: boxId,
    box_name: `Box ${boxId}`,
    addr: `http://10.0.0.${boxId.length}:18900`,
    token: "sd1_x",
    last_checked: "2026-09-21T00:00:00Z",
  }
}

test("远程模式：activeSource 是远端盒子时镜像仍放行（瘦壳要养 Hub 的 boxes.json）", async () => {
  const { origin, hub } = await sharedModules()
  origin.setRemoteShellActive(true)
  origin.saveConnections([conn("b1")])
  origin.setActiveSourceId("b1") // 非本机来源 —— 正常模式下这里会被跳过
  const sent = await hub.mirrorConnectionsToHub()
  assert.equal(sent, true, "远程模式必须允许把连接清单镜像进瘦壳")
  assert.ok(calls.some((c) => c.url.includes("/hub/boxes")))
})

test("远程模式：页面非回环伺服（别人盒子的页面）→ 依旧不镜像", async () => {
  const g = globalThis as unknown as { window: { location: { hostname: string } } }
  g.window.location.hostname = "192.168.1.9"
  const { origin, hub } = await sharedModules()
  origin.setRemoteShellActive(true)
  origin.saveConnections([conn("b1")])
  origin.setActiveSourceId("b1")
  const sent = await hub.mirrorConnectionsToHub()
  assert.equal(sent, false)
  assert.equal(calls.filter((c) => c.url.includes("/hub/boxes")).length, 0)
})

test("远程模式：断连不回退本机（handleRemoteConnectionLost 返回 false 且不 reload）", async () => {
  const { origin } = await loadModules("no-revert")
  origin.setRemoteShellActive(true)
  origin.saveConnections([conn("b1")])
  origin.setActiveSourceId("b1")
  let reloaded = false
  ;(globalThis as unknown as { window: { location: { reload: () => void } } }).window.location.reload =
    () => {
      reloaded = true
    }
  const handled = origin.handleRemoteConnectionLost("unreachable")
  assert.equal(handled, false, "远程模式失联不该被当成『已回退本机』处理")
  assert.equal(reloaded, false, "远程模式失联绝不整页 reload 回本机")
  assert.equal(origin.getActiveSourceId(), "b1", "activeSource 不应变回 local")
})

test("正常模式（无远程旗标）：断连仍走回退本机（行为零变化）", async () => {
  const { origin } = await loadModules("revert-still")
  origin.saveConnections([conn("b1")])
  origin.setActiveSourceId("b1")
  let reloaded = false
  ;(globalThis as unknown as { window: { location: { reload: () => void } } }).window.location.reload =
    () => {
      reloaded = true
    }
  const handled = origin.handleRemoteConnectionLost("unreachable")
  assert.equal(handled, true)
  assert.equal(reloaded, true)
  assert.equal(origin.getActiveSourceId(), "local")
})
