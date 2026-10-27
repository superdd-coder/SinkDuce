/**
 * P2：UI 偏好也按来源隔离。
 *
 * 这些值**不含后端实体 ID**（不是 collection/session/provider id），所以切来源
 * 不会出错——拿去用永远有效。问题只是**两台盒子共用一个槽位**：
 * 在本机习惯用「会议」视图，切到远端盒子也是「会议」；在 B 上切成网格，切回本机
 * 本机也变网格。功能正确，但界面习惯互相覆盖。
 *
 * 与 P0/P1 的区别：P0/P1 的坏值是「别的盒子的 ID」（拿去用会错），
 * P2 的值本身没问题，只是「谁的习惯」被混在一起。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
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

async function load() {
  const o = await import(`../src/api/origin.ts?p2=${Math.random()}`)
  const sc = await import(`../src/api/source-scope.ts?p2=${Math.random()}`)
  return { o, sc }
}

function connectRemote(o: { saveConnections: (l: unknown[]) => void; setActiveSourceId: (id: string) => void }) {
  o.saveConnections([
    { addr: "http://B:18901", token: "t", box_id: "boxB", box_name: "B", last_checked: "" },
  ])
  o.setActiveSourceId("boxB")
}

// ── 工具层 ────────────────────────────────────────────────────

test("UI 偏好键按来源隔离", async () => {
  const { sc } = await load()
  for (const k of ["rag_sidebarView", "rag_dbActiveTab", "chat_thinking"]) {
    assert.match(sc.sourceScopedKey(k), /@local$/, `${k} 应带来源后缀`)
  }
})

test("回归：在 B 上的界面习惯不会覆盖本机", async () => {
  const { o, sc } = await load()
  // 本机习惯：会议视图
  sc.writeSourceScoped("rag_sidebarView", "meeting")

  // 切到 B，改成知识库视图
  connectRemote(o)
  sc.writeSourceScoped("rag_sidebarView", "database")

  // 切回本机：还是我自己的会议视图，没被 B 覆盖
  o.setActiveSourceId(o.LOCAL_SOURCE)
  assert.equal(
    sc.readSourceScoped("rag_sidebarView", "chat"),
    "meeting",
    "本机的界面习惯不该被远端盒子覆盖",
  )

  // B 那边也保留自己的
  o.setActiveSourceId("boxB")
  assert.equal(sc.readSourceScoped("rag_sidebarView", "chat"), "database")
})

test("两侧互不干扰（三个键一起验证）", async () => {
  const { o, sc } = await load()
  sc.writeSourceScoped("rag_sidebarView", "chat")
  sc.writeSourceScoped("rag_dbActiveTab", "info")
  sc.writeSourceScoped("chat_thinking", true)

  connectRemote(o)
  // B 上全新：应拿到各自的默认值，而不是本机的
  assert.equal(sc.readSourceScoped("rag_sidebarView", "chat"), "chat", "默认值")
  assert.equal(sc.readSourceScoped("rag_dbActiveTab", "info"), "info")
  assert.equal(sc.readSourceScoped("chat_thinking", true), true)
})

test("同一来源内正常记住（未回归）", async () => {
  const { sc } = await load()
  sc.writeSourceScoped("rag_dbActiveTab", "timeline")
  assert.equal(sc.readSourceScoped("rag_dbActiveTab", "info"), "timeline")
})

test("旧全局键不再被当作当前来源的值", async () => {
  const { sc } = await load()
  localStorage.setItem("rag_sidebarView", JSON.stringify("recall"))
  localStorage.setItem("chat_thinking", JSON.stringify(false))
  assert.equal(sc.readSourceScoped("rag_sidebarView", "chat"), "chat", "回落默认，不读旧全局值")
  assert.equal(sc.readSourceScoped("chat_thinking", true), true)
})

// ── 接线（源码不变量）─────────────────────────────────────────

const APP_STORE = readFileSync(new URL("../src/stores/app-store.ts", import.meta.url), "utf8")
const DATABASE_VIEW = readFileSync(
  new URL("../src/components/database/database-view.tsx", import.meta.url),
  "utf8",
)
const CHAT_INPUT = readFileSync(
  new URL("../src/components/chat/chat-input.tsx", import.meta.url),
  "utf8",
)

test("store：sidebarView 按来源读写", () => {
  assert.match(APP_STORE, /sidebarView:\s*readSourceScoped<SidebarView>\("rag_sidebarView"/)
  assert.match(APP_STORE, /writeSourceScoped\("rag_sidebarView"/)
  assert.ok(
    !APP_STORE.includes('localStorage.setItem("rag_sidebarView"'),
    "不应再有裸写",
  )
})

test("database-view：页签偏好按来源读写", () => {
  // 辅助函数是泛型的（键名动态），所以断言 readSourceScoped<T> 与 `${key}` 拼接
  assert.match(DATABASE_VIEW, /readSourceScoped<T>\(`rag_\$\{key\}`/)
  assert.match(DATABASE_VIEW, /writeSourceScoped\(`rag_\$\{key\}`/)
  assert.ok(
    !DATABASE_VIEW.includes('localStorage.setItem(`rag_${key}`'),
    "saveDbUi 不应再写全局 rag_ 前缀",
  )
})

test("chat-input：思考开关已改为会话级（不再是按来源的本地键）", () => {
  // 本文件早先断言 thinking 走 source-scoped 的 `chat_thinking` 键；
  // 该键已在「会话配置」改造中废弃 —— thinking 现在住 store，并随消息存进
  // 会话 config（见 session-config-store-prefs.test.ts）。
  assert.ok(
    !CHAT_INPUT.includes("chat_thinking"),
    "chat_thinking 本地键应已废弃，改为会话级配置",
  )
  assert.ok(
    !CHAT_INPUT.includes("readSourceScoped") && !CHAT_INPUT.includes("writeSourceScoped"),
    "chat-input 不再自己持久化偏好（store + 服务端会话配置负责）",
  )
})
