/**
 * 「这台电脑的硬件能力」必须问本机，不能问当前来源。
 *
 * 系统音频助手（抓 macOS 喇叭声音的原生程序）装在本机。之前它读的是
 * **来源感知**的 /health，于是桌面 App 连着远端盒子时会得到「没有助手」，
 * 整个系统音频采集被静默关掉——而助手其实正在运行。
 *
 * 约定：`apiUrl` 跟随来源（业务数据）；`localApiUrl` 永远本机（本机硬件能力）。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

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
  const g = globalThis as unknown as { window: unknown; localStorage: unknown }
  g.window = { localStorage: storage, sessionStorage: storage, location: { reload: () => {} } }
  g.localStorage = storage
}

let browser: ReturnType<typeof installFakeBrowser>
beforeEach(() => {
  browser = installFakeBrowser()
})

async function loadOrigin() {
  return import(`../src/api/origin.ts?lc=${Math.random()}`)
}

function connectRemote(o: { saveConnections: (l: unknown[]) => void; setActiveSourceId: (id: string) => void }) {
  o.saveConnections([
    { addr: "http://192.168.1.30:18901", token: "t", box_id: "b1", box_name: "书房盒子", last_checked: "" },
  ])
  o.setActiveSourceId("b1")
}

test("本机来源：两者都指向同源相对路径", async () => {
  const o = await loadOrigin()
  assert.equal(o.apiUrl("/health"), "/health")
  assert.equal(o.localApiUrl("/health"), "/health")
})

test("连远端时：apiUrl 跟随来源，localApiUrl 仍指本机", async () => {
  const o = await loadOrigin()
  connectRemote(o)
  assert.equal(o.apiUrl("/health"), "http://192.168.1.30:18901/health", "业务数据跟随来源")
  assert.equal(o.localApiUrl("/health"), "/health", "本机能力不跟随来源")
})

test("回归：连远端时两者必须不同（系统音频曾问错机器）", async () => {
  const o = await loadOrigin()
  connectRemote(o)
  assert.notEqual(
    o.localApiUrl("/health"),
    o.apiUrl("/health"),
    "若相等说明又把本机能力接到来源感知上了",
  )
})

test("回退到本机后两者重新一致", async () => {
  const o = await loadOrigin()
  connectRemote(o)
  o.setActiveSourceId(o.LOCAL_SOURCE)
  assert.equal(o.localApiUrl("/health"), o.apiUrl("/health"))
})
