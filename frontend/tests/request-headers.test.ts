/**
 * request() 必须同时做到：带钥匙 + 保留 Content-Type。
 *
 * 曾经的 bug：requestClient 里
 *   fetch(url, { headers: { "Content-Type": "application/json", ...options?.headers }, ...options })
 * 当带钥匙时 options.headers 是个只含 X-Pairing-Token 的 Headers 实例，
 * 展开它不产生可枚举属性，随后 `...options` 又整体覆盖了 headers →
 * Content-Type 丢失。body 是 JSON 字符串却没有 JSON 头，FastAPI 不解析，
 * 报 "Input should be a valid dictionary or object"（422）。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

let calls: { url: string; headers: Record<string, string>; body: unknown }[]

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
    location: { reload: () => {} },
    dispatchEvent: () => true,
    addEventListener: () => {},
    removeEventListener: () => {},
  }
  g.localStorage = storage
  calls = []
  g.fetch = async (url: string, init?: RequestInit) => {
    const h = new Headers(init?.headers as HeadersInit | undefined)
    const headers: Record<string, string> = {}
    h.forEach((v, k) => { headers[k.toLowerCase()] = v })
    calls.push({ url: String(url), headers, body: init?.body })
    return new Response("{}", { status: 200, headers: { "content-type": "application/json" } })
  }
  if (!g.CustomEvent) {
    g.CustomEvent = class { type: string; constructor(t: string) { this.type = t } }
  }
}

beforeEach(() => {
  installFakeBrowser()
})

async function loadHttp() {
  return import(`../src/api/http.ts?ct=${Math.random()}`)
}

test("带钥匙发 JSON：Content-Type 与钥匙头必须同时存在", async () => {
  const o = await import(`../src/api/origin.ts?ct=${Math.random()}`)
  o.setGateKey("sd1_k") // 有钥匙 → 会走「附加头」分支
  const { request } = await loadHttp()
  await request("/sessions", { method: "POST", body: JSON.stringify({ title: "" }) })

  assert.equal(calls.length, 1)
  assert.equal(calls[0].headers["content-type"], "application/json", "Content-Type 不能被钥匙头挤掉")
  assert.equal(calls[0].headers["x-pairing-token"], "sd1_k", "钥匙头也要在")
})

test("带钥匙发 JSON：body 保持 JSON 字符串（不能被二次编码）", async () => {
  const o = await import(`../src/api/origin.ts?ct2=${Math.random()}`)
  o.setGateKey("sd1_k")
  const { request } = await loadHttp()
  await request("/sessions", { method: "POST", body: JSON.stringify({ title: "" }) })
  assert.equal(calls[0].body, '{"title":""}')
  assert.equal(JSON.parse(calls[0].body as string).title, "", "服务端解析后应是对象")
})

test("无钥匙时也带 Content-Type（未回归）", async () => {
  const o = await import(`../src/api/origin.ts?ct3=${Math.random()}`)
  // 不设置任何钥匙
  assert.equal(o.currentPairingToken(), null)
  const { request } = await loadHttp()
  await request("/sessions", { method: "POST", body: JSON.stringify({ a: 1 }) })
  assert.equal(calls[0].headers["content-type"], "application/json")
})

test("调用方显式指定的头不被覆盖", async () => {
  const o = await import(`../src/api/origin.ts?ct4=${Math.random()}`)
  o.setGateKey("sd1_k")
  const { request } = await loadHttp()
  await request("/x", {
    method: "POST",
    body: "raw",
    headers: { "Content-Type": "text/csv" },
  })
  assert.equal(calls[0].headers["content-type"], "text/csv")
  assert.equal(calls[0].headers["x-pairing-token"], "sd1_k")
})

test("每个请求都带稳定的 X-Client-Id（事件流回声抑制用）", async () => {
  const { request } = await loadHttp()
  await request("/sessions", { method: "POST", body: "{}" })
  await request("/collections")
  const first = calls[0].headers["x-client-id"]
  const second = calls[1].headers["x-client-id"]
  assert.ok(first, "X-Client-Id 必须存在")
  assert.equal(first, second, "同一设备两次请求必须是同一个 id")
})

test("X-Client-Id 与钥匙头共存", async () => {
  const o = await import(`../src/api/origin.ts?ct5=${Math.random()}`)
  o.setGateKey("sd1_k")
  const { request } = await loadHttp()
  await request("/sessions", { method: "POST", body: "{}" })
  assert.equal(calls[0].headers["x-pairing-token"], "sd1_k")
  assert.ok(calls[0].headers["x-client-id"], "客户端 id 不能被钥匙头挤掉")
})
