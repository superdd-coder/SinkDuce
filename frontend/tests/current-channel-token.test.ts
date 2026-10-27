/**
 * 「当前通道该用哪把钥匙」必须只看一个地方。
 *
 * 取钥匙的逻辑散在多处，其中 WS 转写 / 会话 SSE / 日志流 / 聊天图片 / 热词下载
 * 只带了**本机**钥匙。连远端盒子时这些请求会把 A 的本机钥匙发给 B → 被拒，
 * 而 fetch 那条路（http.ts）是对的 —— 两边不一致，症状就变得零散难查。
 *
 * 约定：currentPairingToken() = 当前连接的钥匙优先，否则本机钥匙。
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

async function loadOrigin() {
  return import(`../src/api/origin.ts?cpt=${Math.random()}`)
}

/** 本机访问过自己（bootstrap 记下本机盒子钥匙），随后连到远端盒子 B。 */
async function browserOnRemoteBox() {
  const o = await loadOrigin()
  o.setLocalBoxId("boxA")
  o.storeOwnerKey("boxA", "sd1_LOCAL")
  o.setGateKey("sd1_LOCAL")
  o.saveConnections([
    { addr: "http://B:18901", token: "sd1_REMOTE", box_id: "boxB", box_name: "B", last_checked: "" },
  ])
  o.setActiveSourceId("boxB")
  return o
}

test("连远端盒子：currentPairingToken 用该盒子的钥匙", async () => {
  const o = await browserOnRemoteBox()
  assert.equal(o.currentPairingToken(), "sd1_REMOTE")
})

test("连远端盒子：withTokenQuery 附的是该盒子的钥匙（图片/下载通道）", async () => {
  const o = await browserOnRemoteBox()
  assert.equal(
    o.withTokenQuery("/api/documents/x/images/1"),
    "/api/documents/x/images/1?token=sd1_REMOTE",
  )
})

test("连远端盒子：pairingHeader 头里是该盒子的钥匙", async () => {
  const o = await browserOnRemoteBox()
  assert.equal(o.pairingHeader()["X-Pairing-Token"], "sd1_REMOTE")
})

test("本机来源：仍用本机钥匙（未回归）", async () => {
  const o = await loadOrigin()
  o.setLocalBoxId("boxA")
  o.storeOwnerKey("boxA", "sd1_LOCAL")
  o.setGateKey("sd1_LOCAL")
  assert.equal(o.currentPairingToken(), "sd1_LOCAL")
  assert.equal(o.withTokenQuery("/api/x"), "/api/x?token=sd1_LOCAL")
})

test("没有任何连接也没有钥匙：返回 null（本机首次启动）", async () => {
  const o = await loadOrigin()
  assert.equal(o.currentPairingToken(), null)
  assert.equal(o.withTokenQuery("/api/x"), "/api/x")
})
