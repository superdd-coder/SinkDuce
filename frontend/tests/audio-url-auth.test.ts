/**
 * 播放类 URL（<audio src> / new Audio()）必须带钥匙。
 *
 * 浏览器自己发起这些请求，加不了请求头 → 钥匙只能走 ?token=（与文件预览同一机制）。
 * 连远端盒子时缺了钥匙，音频会静默播不出来（没有错误提示，只是没声音）。
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

beforeEach(() => {
  installFakeBrowser()
})

test("远端盒子：会议音频 URL 带该盒子的钥匙", async () => {
  const o = await import(`../src/api/origin.ts?au=${Math.random()}`)
  o.saveConnections([
    { addr: "http://192.168.1.30:18901", token: "sd1_B", box_id: "b1", box_name: "B", last_checked: "" },
  ])
  o.setActiveSourceId("b1")
  // 模拟 meeting-studio-stage 的构造方式：apiUrl + withTokenQuery
  const url = o.withTokenQuery(o.apiUrl(`/api/meetings/m1/audio?v=3`))
  assert.equal(url, "http://192.168.1.30:18901/api/meetings/m1/audio?v=3&token=sd1_B")
})

test("远端盒子：说话人试听 URL 带该盒子的钥匙", async () => {
  const o = await import(`../src/api/origin.ts?au2=${Math.random()}`)
  o.saveConnections([
    { addr: "http://192.168.1.30:18901", token: "sd1_B", box_id: "b1", box_name: "B", last_checked: "" },
  ])
  o.setActiveSourceId("b1")
  const url = o.withTokenQuery(o.apiUrl(`/api/speakers/p1/preview-audio?start=1&end=2`))
  assert.ok(url.includes("token=sd1_B"), "试听也必须带钥匙，否则静默无声")
})

test("本机来源：带上本机钥匙（无害且更稳）", async () => {
  const o = await import(`../src/api/origin.ts?au3=${Math.random()}`)
  o.setGateKey("sd1_LOCAL")
  const url = o.withTokenQuery(o.apiUrl(`/api/meetings/m1/audio`))
  assert.equal(url, "/api/meetings/m1/audio?token=sd1_LOCAL")
})
