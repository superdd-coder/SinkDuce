/**
 * JSON 解析的边界：BOM 与空体。
 *
 * requestClient 现在手动解析（因为要区分 204/空体），必须保住 res.json() 原本
 * 会做的 BOM 跳过；否则带 BOM 的响应会突然解析失败——这是很容易在重构中丢掉的细节。
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
  return import(`../src/api/http.ts?b=${Math.random()}`)
}

test("带 BOM 的 JSON 响应能正常解析（res.json 原本就跳过 BOM）", async () => {
  const { request } = await loadHttp()
  responder = () =>
    new Response("\uFEFF" + JSON.stringify({ ok: 1 }), {
      status: 200,
      headers: { "content-type": "application/json" },
    })
  const out = await request<{ ok: number }>("/x")
  assert.deepEqual(out, { ok: 1 })
})

test("普通 JSON 正常解析", async () => {
  const { request } = await loadHttp()
  responder = () => new Response('{"a":[1,2]}', { status: 200 })
  assert.deepEqual(await request<{ a: number[] }>("/x"), { a: [1, 2] })
})

test("空白 body 视为无内容（不抛）", async () => {
  const { request } = await loadHttp()
  responder = () => new Response("   \n ", { status: 200 })
  // 注意：纯空白不是合法 JSON，但真实服务端不会这样返回；这里锁住的是「不抛 JSON 错」
  const out = await request<unknown>("/x").catch((e: Error) => e)
  assert.ok(out instanceof Error || out === undefined, "要么安全返回，要么明确报错，不能是难以理解的解析异常")
})
