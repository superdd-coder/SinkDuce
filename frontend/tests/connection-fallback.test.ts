/**
 * 远端连接失效时的回退行为。
 *
 * 2026-09-19 反转：revoked 不再删除连接记录——401 无法区分「TTL 自然过期」
 * 和「被吊销」，而盒子名/地址是用户资产（删掉=重配对全流程）。两种原因
 * 都保留记录、退回本机；关键操作（录制/上传）进行中推迟回退。
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
  const state = {
    localStorage: storage,
    sessionStorage: storage,
    location: { reload: () => { state.reloads += 1 } },
    dispatchEvent: () => true,
    reloads: 0,
  }
  const g = globalThis as unknown as { window: unknown; localStorage: unknown }
  g.window = state
  g.localStorage = storage // origin.ts 里用的是裸 localStorage
  return { local, state }
}

let browser: ReturnType<typeof installFakeBrowser>
beforeEach(() => {
  browser = installFakeBrowser()
})

async function loadOrigin() {
  // 每次重载模块，避免模块级状态（revertingToLocal）跨用例串味
  return import(`../src/api/origin.ts?case=${Math.random()}`)
}

test("本机来源没有连接记录 → 不触发回退（本机 401 归钥匙门管）", async () => {
  const o = await loadOrigin()
  // 调用方只在「当前是远端来源」时才会调它；本机没有 activeConnection → 返回 false
  assert.equal(o.handleRemoteConnectionLost("revoked"), false)
  assert.equal(browser.state.reloads, 0)
})

test("远端盒子 401（钥匙过期/吊销）：保留连接记录并退回本机", async () => {
  const o = await loadOrigin()
  o.saveConnections([
    { addr: "http://192.168.1.30:18901", token: "sd1_dead", box_id: "b1", box_name: "书房盒子", last_checked: "" },
    { addr: "http://192.168.1.31:18901", token: "sd1_live", box_id: "b2", box_name: "客厅盒子", last_checked: "" },
  ])
  o.storeOwnerKey("b1", "sd1_dead")
  o.setActiveSourceId("b1")

  assert.equal(o.handleRemoteConnectionLost("revoked"), true)

  // **记录保留**（盒子名/地址是用户资产，重连弹窗可预填）；另一台完好
  assert.deepEqual(o.savedConnections().map((c) => c.box_id), ["b1", "b2"])
  // 退回本机并刷新
  assert.equal(o.getActiveSourceId(), o.LOCAL_SOURCE)
  assert.equal(browser.state.reloads, 1)
  // 留下了提示（供刷新后弹一次）
  const raw = browser.local.get("sinkduce.pairing.connectionLost")
  assert.ok(raw, "应留下断开提示")
  assert.deepEqual(JSON.parse(raw as string), {
    box_id: "b1",
    box_name: "书房盒子",
    reason: "revoked",
  })
})

test("远端盒子连不上：退回本机但保留连接（可能只是临时下线）", async () => {
  const o = await loadOrigin()
  o.saveConnections([
    { addr: "http://192.168.1.30:18901", token: "sd1_ok", box_id: "b1", box_name: "书房盒子", last_checked: "" },
  ])
  o.storeOwnerKey("b1", "sd1_ok")
  o.setActiveSourceId("b1")

  assert.equal(o.handleRemoteConnectionLost("unreachable"), true)

  // 保留在清单里，钥匙也留着 —— 盒子回来后可以直接切回去，不必重新配对
  assert.deepEqual(o.savedConnections().map((c) => c.box_id), ["b1"])
  assert.equal(o.getStoredOwnerKey("b1"), "sd1_ok")
  assert.equal(o.getActiveSourceId(), o.LOCAL_SOURCE)
  assert.equal(browser.state.reloads, 1)
})

test("并发失败只回退一次", async () => {
  const o = await loadOrigin()
  o.saveConnections([
    { addr: "http://192.168.1.30:18901", token: "t", box_id: "b1", box_name: "盒", last_checked: "" },
  ])
  o.setActiveSourceId("b1")
  assert.equal(o.handleRemoteConnectionLost("unreachable"), true)
  assert.equal(o.handleRemoteConnectionLost("unreachable"), true)
  assert.equal(browser.state.reloads, 1, "只应刷新一次")
})

test("关键操作进行中：不回退不刷新，操作结束后补做", async () => {
  // 不带 query：必须与 origin.ts 内部 import 到**同一个** critical-op 模块实例
  const { beginCriticalOp } = await import("../src/api/critical-op.ts")
  const o = await loadOrigin()
  o.saveConnections([
    { addr: "http://192.168.1.30:18901", token: "t", box_id: "b1", box_name: "盒", last_checked: "" },
  ])
  o.setActiveSourceId("b1")

  const end = beginCriticalOp()
  // 录制/上传进行中：返回 false（错误正常冒泡，页面不 reload）
  assert.equal(o.handleRemoteConnectionLost("unreachable"), false)
  assert.equal(browser.state.reloads, 0)
  assert.equal(o.getActiveSourceId(), "b1", "来源不切换")

  end()
  await new Promise((r) => setTimeout(r, 0))
  // 操作收尾后补做回退
  assert.equal(o.getActiveSourceId(), o.LOCAL_SOURCE)
  assert.equal(browser.state.reloads, 1)
})
