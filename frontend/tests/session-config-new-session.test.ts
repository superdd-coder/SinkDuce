/**
 * 新会话行为未变：沿用「最近用的那套」。
 *
 * 设计取舍（已与用户确认）：新会话**不**从服务端拷配置，而是沿用当前 store 里的值
 * —— 也就是模型选择器保持"粘手"，与改造前的体验一致。
 *
 * 这是与新功能（打开旧会话恢复其配置）配对的一条：两者不能互相干扰。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { beforeEach, test } from "node:test"

const APP_STORE = readFileSync(new URL("../src/stores/app-store.ts", import.meta.url), "utf8")
const CHAT_VIEW = readFileSync(
  new URL("../src/components/chat/chat-view.tsx", import.meta.url),
  "utf8",
)

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
  const o = await import(`../src/api/origin.ts?ns=${Math.random()}`)
  const sc = await import(`../src/api/source-scope.ts?ns=${Math.random()}`)
  const cfg = await import(`../src/api/session-config.ts?ns=${Math.random()}`)
  return { o, sc, cfg }
}

test("校验层：空配置下没有任何一项可复用（全 null）", async () => {
  const { cfg } = await load()
  const picked = cfg.pickRestorableConfig({}, { providers: [], collectionIds: new Set() })
  assert.equal(picked.provider_id, null)
  assert.equal(picked.model, null)
  assert.equal(picked.thinking, null)
  assert.equal(picked.web_search, null)
  assert.equal(picked.collections, null)
})

test("回退层（关键回归）：已有会话配置为空时落**默认**，而不是沿用上一个会话", () => {
  // 这是用户报的「所有会话都变成同一套」的第二层根因：
  // 只写有值的项、跳过没值的项 → store 留着上一个会话的值 → 一发送就写错。
  // resolveSessionConfig 把缺失项补成默认，得到确定的元组。
  return load().then(({ cfg }) => {
    const r = cfg.resolveSessionConfig({}, { providers: [], collectionIds: new Set() })
    assert.equal(r.provider_id, null, "不指定 provider（上层挑默认）")
    assert.equal(r.model, null, "不指定模型（用 provider 默认）")
    assert.equal(r.thinking, cfg.SESSION_CONFIG_DEFAULTS.thinking, "思考落默认")
    assert.equal(r.web_search, cfg.SESSION_CONFIG_DEFAULTS.web_search, "Web 落默认")
    assert.deepEqual(r.collections, [], "知识库落默认（空）")
  })
})

test("回退层：有值的项照常保留（不被默认覆盖）", async () => {
  const { cfg } = await load()
  const providers = [{ id: "p1", selected_models: ["m1"] }]
  const r = cfg.resolveSessionConfig(
    { provider_id: "p1", model: "m1", thinking: false, web_search: true, collections: ["c1"] },
    { providers, collectionIds: new Set(["c1"]) },
  )
  assert.equal(r.provider_id, "p1")
  assert.equal(r.model, "m1")
  assert.equal(r.thinking, false, "false 不能被默认（true）顶掉")
  assert.equal(r.web_search, true)
  assert.deepEqual(r.collections, ["c1"])
})

test("回退层：provider 有效但模型失效 → 保留 provider，模型留空（用其默认）", async () => {
  const { cfg } = await load()
  const providers = [{ id: "p1", selected_models: ["m1"] }]
  const r = cfg.resolveSessionConfig(
    { provider_id: "p1", model: "gone" },
    { providers, collectionIds: new Set() },
  )
  assert.equal(r.provider_id, "p1")
  assert.equal(r.model, null)
})

test("新建会话走 initSession，不套用任何会话配置", () => {
  // initSession 只创建会话；不应调用 applySessionConfig（那是「打开旧会话」的路径）
  const initBlock = APP_STORE.slice(
    APP_STORE.indexOf("initSession: async"),
    APP_STORE.indexOf("loadSessionMessages: async"),
  )
  assert.ok(initBlock.length > 0, "应能定位 initSession 实现")
  assert.ok(
    !initBlock.includes("applySessionConfig"),
    "新建会话不该套用配置（沿用 store 现值即可）",
  )
})

test("打开已有会话才恢复配置", () => {
  const openBlock = APP_STORE.slice(
    APP_STORE.indexOf("loadSessionMessages: async"),
    APP_STORE.indexOf("ensureSessionHydrated: async"),
  )
  assert.match(openBlock, /applySessionConfig\(detail\.config\)/, "打开会话应恢复其配置")
})

test("模型/语言等仍持久化在本地（与来源作用域配合，未回归）", () => {
  // 这几项是「新会话的初始值」，仍按来源存在本地（P1）
  for (const key of ["rag_activeProvider", "rag_activeModel", "rag_sessionId"]) {
    assert.match(APP_STORE, new RegExp(`readSourceScoped<[^>]*>\\("${key}"`))
  }
})

test("chat-view 不把 Provider 列表默认项自动变成显式模型覆盖", () => {
  assert.ok(CHAT_VIEW.length > 0)
  assert.ok(!CHAT_VIEW.includes("setActiveProvider(defaultP.id)"))
})
