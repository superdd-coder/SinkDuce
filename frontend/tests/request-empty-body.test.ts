/**
 * request() 必须能处理「无响应体」的成功响应（204 No Content）。
 *
 * 曾经的 bug：requestClient 无条件 `return res.json()`。DELETE /api/sessions/{id}
 * 返回 204（空 body），对空字符串调 JSON.parse('') 会抛
 * "The string did not match the expected pattern."（WebKit/JavaScriptCore 的措辞，
 * 也就是桌面 App 的 WKWebView）——于是**服务端已删成功，界面却报删除失败**。
 *
 * 修前 deleteSession 用裸 fetch（从不解析 body）所以没暴露；
 * 改成走 request() 后引爆。防复发：request() 对 204/空 body 返回 undefined。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

let responder: () => Response

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
  responder = () => new Response("{}", { status: 200 })
  g.fetch = async () => responder()
  if (!g.CustomEvent) {
    g.CustomEvent = class { type: string; constructor(t: string) { this.type = t } }
  }
}

beforeEach(() => {
  installFakeBrowser()
})

async function loadHttp() {
  return import(`../src/api/http.ts?n=${Math.random()}`)
}

test("204 No Content（删会话）：不抛错，返回 undefined", async () => {
  const { request } = await loadHttp()
  // 204 不允许有 body，Response 构造器也只接受 null
  responder = () => new Response(null, { status: 204 })
  const out = await request<void>("/sessions/abc", { method: "DELETE" })
  assert.equal(out, undefined, "204 应安全返回 undefined，而不是抛 JSON 解析错")
})

test("回归：删除会话的真实调用形态（DELETE + 带钥匙）", async () => {
  const o = await import(`../src/api/origin.ts?n=${Math.random()}`)
  o.setGateKey("sd1_k")
  const { request } = await loadHttp()
  responder = () => new Response(null, { status: 204 })
  await assert.doesNotReject(
    () => request<void>("/sessions/abc", { method: "DELETE" }),
    "带钥匙的 204 也必须安全（头合并与空 body 是两条独立路径）",
  )
  void o
})

test("200 无 body（服务端只返回状态）也不崩", async () => {
  const { request } = await loadHttp()
  responder = () => new Response("", { status: 200 })
  const out = await request<void>("/something", { method: "POST" })
  assert.equal(out, undefined)
})

test("正常 JSON 响应照常解析（未回归）", async () => {
  const { request } = await loadHttp()
  responder = () =>
    new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    })
  const out = await request<{ ok: boolean }>("/x")
  assert.deepEqual(out, { ok: true })
})
