/**
 * 设置页「连接相关配置」的显示判定。
 *
 * 设备 B 连到设备 A 的后端时（访客身份），设置页不该再出现连接配置：
 * 在别人的盒子上管理配对钥匙、或看它的 MCP 地址，都不该发生。
 * 切来源的入口在左上角切换器（始终可见），不受此影响。
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
  g.window = { localStorage: storage }
  g.localStorage = storage
  return { local }
}

let browser: ReturnType<typeof installFakeBrowser>
beforeEach(() => {
  browser = installFakeBrowser()
})

async function loadOrigin() {
  return import(`../src/api/origin.ts?gate=${Math.random()}`)
}

test("本机来源：显示连接设置", async () => {
  const o = await loadOrigin()
  assert.equal(o.getActiveSourceId(), o.LOCAL_SOURCE)
  assert.equal(o.isLocalSourceActive(), true)
})

test("连到别人的后端：隐藏连接设置", async () => {
  const o = await loadOrigin()
  o.saveConnections([
    { addr: "http://192.168.1.30:18901", token: "t", box_id: "b1", box_name: "书房盒子", last_checked: "" },
  ])
  o.setActiveSourceId("b1")
  assert.equal(o.isLocalSourceActive(), false)
})

test("连接记录已被清掉（回退场景）：按本机处理，显示连接设置", async () => {
  const o = await loadOrigin()
  o.setActiveSourceId("b1") // 指向一个不存在的连接
  assert.equal(o.isLocalSourceActive(), true)
})

test("从远端切回本机后又显示", async () => {
  const o = await loadOrigin()
  o.saveConnections([
    { addr: "http://192.168.1.30:18901", token: "t", box_id: "b1", box_name: "盒", last_checked: "" },
  ])
  o.setActiveSourceId("b1")
  assert.equal(o.isLocalSourceActive(), false)
  o.setActiveSourceId(o.LOCAL_SOURCE)
  assert.equal(o.isLocalSourceActive(), true)
})
