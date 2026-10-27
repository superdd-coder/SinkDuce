/**
 * 裸请求出口 apiFetch：给 request() 覆盖不了的载荷用（FormData / 二进制 / 流）。
 *
 * 它必须做到 request() 一样的三件事：来源感知的地址、自动附配对钥匙、
 * 远端失效时回退。之前有 19 处自己写裸 fetch，只继承了「地址」忘了「钥匙」，
 * 于是连远端盒子时会话列表 / 文件上传 / 录音落盘全部静默失败。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

interface Call {
  url: string
  method: string
  token: string | null
}

let calls: Call[]
let responder: (url: string) => { status: number; body?: string }

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
  const events: string[] = []
  const state = {
    localStorage: storage,
    sessionStorage: storage,
    location: { reload: () => { state.reloads += 1 } },
    reloads: 0,
    dispatchEvent: (e: { type: string }) => { events.push(e.type); return true },
    addEventListener: () => {},
    removeEventListener: () => {},
  }
  const g = globalThis as unknown as { window: unknown; localStorage: unknown; fetch: unknown; CustomEvent?: unknown }
  g.window = state
  g.localStorage = storage
  calls = []
  responder = () => ({ status: 200, body: "{}" })
  g.fetch = async (url: string, init?: RequestInit) => {
    const headers = new Headers(init?.headers as HeadersInit | undefined)
    calls.push({
      url: String(url),
      method: (init?.method ?? "GET").toUpperCase(),
      token: headers.get("X-Pairing-Token"),
    })
    const r = responder(String(url))
    return new Response(r.body ?? "", { status: r.status, headers: { "content-type": "application/json" } })
  }
  if (!g.CustomEvent) {
    g.CustomEvent = class { type: string; constructor(t: string) { this.type = t } }
  }
  return { local, state, events }
}

/** 每个用例都重新加载 http.ts：API_BASE 在模块加载时按当时的来源算一次。 */
async function loadHttp() {
  return import(`../src/api/http.ts?c=${Math.random()}`)
}

let browser: ReturnType<typeof installFakeBrowser>
beforeEach(() => {
  browser = installFakeBrowser()
})

function connectRemote(origin: { saveConnections: (l: unknown[]) => void; setActiveSourceId: (id: string) => void; storeOwnerKey: (b: string, t: string) => void }) {
  origin.saveConnections([
    { addr: "http://192.168.1.30:18901", token: "sd1_remote", box_id: "b1", box_name: "书房盒子", last_checked: "" },
  ])
  origin.setActiveSourceId("b1")
}

test("本机来源：相对路径 + 主人钥匙", async () => {
  const o = await import("../src/api/origin.ts")
  o.setGateKey("sd1_local")
  const { apiFetch } = await loadHttp()
  await apiFetch("/sessions")
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, "/api/sessions")
  assert.equal(calls[0].token, "sd1_local")
})

test("远端来源：连接地址 + 该连接的钥匙", async () => {
  const o = await import(`../src/api/origin.ts?r=${Math.random()}`)
  connectRemote(o)
  const { apiFetch } = await loadHttp()
  await apiFetch("/sessions")
  assert.equal(calls[0].url, "http://192.168.1.30:18901/api/sessions")
  assert.equal(calls[0].token, "sd1_remote")
})

test("file-mgmt 风味走 file-mgmt 基址", async () => {
  const o = await import(`../src/api/origin.ts?f=${Math.random()}`)
  connectRemote(o)
  const { apiFetch } = await loadHttp()
  await apiFetch("/c1/files/upload", { method: "POST" }, { flavor: "file-mgmt" })
  assert.equal(calls[0].url, "http://192.168.1.30:18901/api/file-mgmt/c1/files/upload")
  assert.equal(calls[0].method, "POST")
  assert.equal(calls[0].token, "sd1_remote")
})

test("FormData 上传也带上钥匙（浏览器自己设 boundary，别覆盖 Content-Type）", async () => {
  const o = await import(`../src/api/origin.ts?fd=${Math.random()}`)
  connectRemote(o)
  const { apiFetch } = await loadHttp()
  const fd = new FormData()
  fd.append("file", new Blob(["x"]), "a.txt")
  await apiFetch("/documents/upload", { method: "POST", body: fd })
  assert.equal(calls[0].token, "sd1_remote", "文件上传必须带钥匙，否则远端 401")
})

test("远端 401：触发回退（切回本机 + 刷新）", async () => {
  const o = await import(`../src/api/origin.ts?a=${Math.random()}`)
  connectRemote(o)
  const { apiFetch } = await loadHttp()
  responder = () => ({ status: 401 })
  await apiFetch("/sessions")
  assert.equal(browser.state.reloads, 1, "远端钥匙失效应退回本机")
  assert.equal(o.getActiveSourceId(), o.LOCAL_SOURCE)
  // 2026-09-19 反转：401 无法区分过期/吊销，连接记录保留（盒子名/地址是用户资产）
  assert.equal(o.savedConnections().length, 1, "401 不再删除连接记录")
})

test("本机 401：派发钥匙门事件（不触发回退）", async () => {
  const o = await import(`../src/api/origin.ts?e=${Math.random()}`)
  o.setGateKey("sd1_local")
  const { apiFetch } = await loadHttp()
  responder = () => ({ status: 401 })
  await apiFetch("/sessions")
  assert.equal(browser.state.reloads, 0, "本机不该回退")
  assert.ok(
    browser.events.includes("sinkduce:pairing-required"),
    "本机 401 应弹出钥匙门",
  )
})

test("响应原样返回（调用方自己处理 status/body）", async () => {
  const o = await import(`../src/api/origin.ts?p=${Math.random()}`)
  o.setGateKey("sd1_local")
  const { apiFetch } = await loadHttp()
  responder = () => ({ status: 418, body: '{"teapot":true}' })
  const res = await apiFetch("/x")
  assert.equal(res.status, 418)
  const body = (await res.json()) as { teapot: boolean }
  assert.equal(body.teapot, true)
})
