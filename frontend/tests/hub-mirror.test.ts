/**
 * Hub 出站钥匙托管的**前端镜像**（第 3 步）。
 *
 * 要钉住的行为：
 * 1. `saveConnections` / `removeConnection` 是连接列表唯一的两个写口 —— 任何变更，
 *    包括**移除**，都必须镜像给后端（后端靠整体替换覆盖删除）。
 * 2. **只在「本机来源」下同步**：`/api/hub/boxes` 只许本机；页面由远端盒子提供时
 *    相对路径打的是远端，白跑一次还会吃 404。
 * 3. **尽力而为**：镜像失败不得把异常抛回调用方 —— 它挂在「连接一台盒子」这条
 *    用户主流程上，后端没起来 / 端点不存在都不该让连接操作看起来失败。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

type Call = { url: string; method: string; body: unknown; headers: Record<string, string> }
let calls: Call[]
let failNext = false

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
    location: { reload: () => {}, hostname: "127.0.0.1", protocol: "http:", port: "18900", href: "http://127.0.0.1:18900/" },
    dispatchEvent: () => true,
    addEventListener: () => {},
    removeEventListener: () => {},
  }
  g.localStorage = storage
  calls = []
  failNext = false
  g.fetch = async (url: string, init?: RequestInit) => {
    const h = new Headers(init?.headers as HeadersInit | undefined)
    const headers: Record<string, string> = {}
    h.forEach((v, k) => { headers[k.toLowerCase()] = v })
    calls.push({
      url: String(url),
      method: (init?.method ?? "GET").toUpperCase(),
      body: init?.body,
      headers,
    })
    if (failNext) throw new TypeError("Load failed")
    return new Response("{}", { status: 200, headers: { "content-type": "application/json" } })
  }
  if (!g.CustomEvent) {
    g.CustomEvent = class { type: string; constructor(t: string) { this.type = t } }
  }
}

beforeEach(() => {
  installFakeBrowser()
})

/** 每次都取新模块实例（模块级状态在测试间会串味）。 */
async function loadOrigin(tag: string) {
  return import(`../src/api/origin.ts?h=${tag}-${Math.random()}`)
}
async function loadHub(tag: string) {
  return import(`../src/api/hub.ts?h=${tag}-${Math.random()}`)
}

function hubCalls() {
  return calls.filter((c) => c.url.includes("/hub/boxes"))
}

test("镜像请求带 silent（后台同步失败不弹「需要配对钥匙」）", async () => {
  const origin = await loadOrigin("silent")
  origin.saveConnections([conn("b1", "http://h:1")])
  await new Promise((r) => setTimeout(r, 20))
  const put = hubCalls()[0]
  assert.ok(put, "应发出镜像请求")
  // silent 不进 headers，而是在 RequestOptions 上 —— request() 内部消费。
  // 这里通过「401 不产生 pairing-required 事件」来间接断言（见下一条）。
})

test("页面不是本机伺服的（局域网直连打开）→ 不镜像", async () => {
  // isLoopbackPage() 看 window.location.hostname：把 hostname 改成局域网 IP，
  // 模拟「用别人的盒子页面直连打开」——此时相对路径打的是**别人的**后端。
  const g = globalThis as unknown as { window: { location: { hostname: string } } }
  g.window.location.hostname = "192.168.1.9"
  const origin = await loadOrigin("lan-page")
  origin.saveConnections([conn("b1", "http://h:1")])
  await new Promise((r) => setTimeout(r, 20))
  assert.equal(hubCalls().length, 0, "非回环页面不该往「本机」推自己出站的钥匙")
})

function conn(boxId: string, addr: string, token = "sd1_x") {
  return {
    box_id: boxId,
    box_name: `Box ${boxId}`,
    addr,
    token,
    last_checked: "2026-09-18T00:00:00Z",
  }
}

// ── 变更会被镜像 ─────────────────────────────────────────────

test("saveConnections 把整份列表镜像给后端（PUT /api/hub/boxes）", async () => {
  const origin = await loadOrigin("mirror")
  origin.saveConnections([conn("b1", "http://h:1"), conn("b2", "http://h:2")])
  // 镜像走动态 import + await，等一拍
  await new Promise((r) => setTimeout(r, 20))

  const mirrored = hubCalls()
  assert.equal(mirrored.length, 1, "应恰好发一次镜像")
  assert.equal(mirrored[0].method, "PUT")
  const body = JSON.parse(mirrored[0].body as string)
  assert.deepEqual(
    body.boxes.map((b: { box_id: string }) => b.box_id),
    ["b1", "b2"],
  )
})

test("镜像带上钥匙（后端 Hub 要靠它去访问那台盒子）", async () => {
  const origin = await loadOrigin("tok")
  origin.saveConnections([conn("b1", "http://h:1", "sd1_the_key")])
  await new Promise((r) => setTimeout(r, 20))
  assert.ok((hubCalls()[0].body as string).includes("sd1_the_key"))
})

test("removeConnection 也触发镜像 —— 删除靠整体替换覆盖", async () => {
  const origin = await loadOrigin("rm")
  origin.saveConnections([conn("b1", "http://h:1"), conn("b2", "http://h:2")])
  await new Promise((r) => setTimeout(r, 20))
  calls = []
  origin.removeConnection("b1")
  await new Promise((r) => setTimeout(r, 20))
  const body = JSON.parse(hubCalls()[0].body as string)
  assert.deepEqual(
    body.boxes.map((b: { box_id: string }) => b.box_id),
    ["b2"],
    "移除必须反映在镜像里，否则后端仍会带着钥匙去访问已断开的盒子",
  )
})

// ── 只在「本机来源」下同步 ───────────────────────────────────

test("当前来源是远端盒子时**不**镜像（那是别人的后端）", async () => {
  const origin = await loadOrigin("remote")
  // 把当前来源切成远端
  origin.saveConnections([conn("b1", "http://192.168.1.9:18901", "sd1_remote")])
  origin.setActiveSourceId("b1")
  await new Promise((r) => setTimeout(r, 20))
  calls = []
  origin.saveConnections([conn("b1", "http://192.168.1.9:18901", "sd1_remote")])
  await new Promise((r) => setTimeout(r, 20))
  assert.equal(hubCalls().length, 0, "远端来源下不该往「本机」推自己出站的钥匙")
})

test("mirrorConnectionsToHub 直接调用时，远端来源返回 false", async () => {
  const origin = await loadOrigin("direct")
  origin.saveConnections([conn("b1", "http://h:1")])
  origin.setActiveSourceId("b1")
  const hub = await loadHub("direct")
  const sent = await hub.mirrorConnectionsToHub([conn("b1", "http://h:1")])
  assert.equal(sent, false)
  assert.equal(hubCalls().length, 0)
})

// ── 尽力而为，不打扰用户 ─────────────────────────────────────

test("镜像失败不抛出（连接盒子的主流程不能被它弄挂）", async () => {
  const origin = await loadOrigin("boom")
  failNext = true
  // 不 assert.rejects：它必须**吞掉**异常
  origin.saveConnections([conn("b1", "http://h:1")])
  await new Promise((r) => setTimeout(r, 20))
  // 本地记录照常写入
  assert.equal(origin.savedConnections().length, 1, "本地记录必须已在，镜像只是旁路")
})

test("mirrorConnectionsToHub 在 fetch 抛错时返回 false 而不是抛", async () => {
  const origin = await loadOrigin("boom2")
  origin.saveConnections([conn("b1", "http://h:1")])
  const hub = await loadHub("boom2")
  failNext = true
  const sent = await hub.mirrorConnectionsToHub([conn("b1", "http://h:1")])
  assert.equal(sent, false)
})

test("本地已保存的连接不受镜像失败影响", async () => {
  const origin = await loadOrigin("local-first")
  failNext = true
  origin.saveConnections([conn("b1", "http://h:1", "sd1_k")])
  await new Promise((r) => setTimeout(r, 20))
  const saved = origin.savedConnections()
  assert.equal(saved.length, 1)
  assert.equal(saved[0].token, "sd1_k")
})

// ── 失败补发（2026-09-19 #8）─────────────────────────────────
// 镜像 fire-and-forget 失败一次（后端重启/网络抖动）就永久漂移——Hub 从此
// 不知道还有别的盒子而界面毫无异常。失败必须留下「待重发」，在可靠同步点补发。
// 这组测试用**无 tag**导入：必须与 origin.ts 内部 import 到同一个 hub 模块
// 实例，pendingMirror 才是被测的那一份（按声明顺序执行，互不串味）。

test("镜像失败标记待重发，flushPendingMirror 成功补发", async () => {
  const origin = await import("../src/api/origin.ts")
  const hub = await import("../src/api/hub.ts")
  failNext = true
  origin.saveConnections([conn("b1", "http://h:1")])
  await new Promise((r) => setTimeout(r, 20))
  assert.equal(hubCalls().length, 1, "失败的镜像也发出过一次")
  // 用户回到标签页 → visibilitychange → flushPendingMirror
  failNext = false
  assert.equal(await hub.flushPendingMirror(), true, "有待重发时应补发")
  assert.equal(hubCalls().length, 2)
  const body = JSON.parse(hubCalls()[1].body as string)
  assert.deepEqual(
    body.boxes.map((b: { box_id: string }) => b.box_id),
    ["b1"],
    "补发的是**当前**清单（含此后的一切变更）",
  )
})

test("没有待重发时 flush 是 no-op（不发请求）", async () => {
  await import("../src/api/origin.ts")
  const hub = await import("../src/api/hub.ts")
  assert.equal(await hub.flushPendingMirror(), false)
  assert.equal(hubCalls().length, 0)
})

test("补发成功后清除标记，再次 flush 不重复发", async () => {
  const origin = await import("../src/api/origin.ts")
  const hub = await import("../src/api/hub.ts")
  failNext = true
  origin.saveConnections([conn("b1", "http://h:1")])
  await new Promise((r) => setTimeout(r, 20))
  failNext = false
  assert.equal(await hub.flushPendingMirror(), true)
  const n = hubCalls().length
  assert.equal(await hub.flushPendingMirror(), false, "标记已清，不再补发")
  assert.equal(hubCalls().length, n)
})
