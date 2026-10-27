/**
 * P1：provider / model 选择也按来源隔离。
 *
 * 为什么：`rag_activeProvider` / `rag_activeModel` 会**直接进请求体**
 * （`use-stream.ts:99` → `provider_id` / `model`），而发送时不校验有效性。
 * 切来源后残留的对方 provider id 会被发过去——后端对未知 id 是安全降级（用默认模型），
 * 不报错，所以用户只会疑惑「为什么用了别的模型」。属于静默降级，比 collection 的
 * 报错轻，但同样不该发生。
 *
 * 现有自愈（chat-input 加载完 providers 后清理）依赖「providers 先加载完」，
 * 与时序赛跑；按来源隔离则从根本上消除这种值。
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
  return local
}

let store: Map<string, string>
beforeEach(() => {
  store = installFakeBrowser()
})

async function load() {
  const o = await import(`../src/api/origin.ts?p1=${Math.random()}`)
  const sc = await import(`../src/api/source-scope.ts?p1=${Math.random()}`)
  return { o, sc }
}

function connectRemote(o: { saveConnections: (l: unknown[]) => void; setActiveSourceId: (id: string) => void }) {
  o.saveConnections([
    { addr: "http://B:18901", token: "t", box_id: "boxB", box_name: "B", last_checked: "" },
  ])
  o.setActiveSourceId("boxB")
}

// ── 工具层 ────────────────────────────────────────────────────

test("provider/model 键按来源隔离", async () => {
  const { sc } = await load()
  assert.match(sc.sourceScopedKey("rag_activeProvider"), /@local$/)
  assert.match(sc.sourceScopedKey("rag_activeModel"), /@local$/)
})

test("回归：在 B 上选的 provider 不会泄漏给本机", async () => {
  const { o, sc } = await load()
  connectRemote(o)
  sc.writeSourceScoped("rag_activeProvider", "provider-on-B")
  sc.writeSourceScoped("rag_activeModel", "model-on-B")

  o.setActiveSourceId(o.LOCAL_SOURCE)
  assert.equal(sc.readSourceScoped("rag_activeProvider", null), null, "本机不该继承 B 的 provider")
  assert.equal(sc.readSourceScoped("rag_activeModel", null), null, "本机不该继承 B 的 model")
})

test("同一来源内正常记住（未回归）", async () => {
  const { sc } = await load()
  sc.writeSourceScoped("rag_activeProvider", "p1")
  sc.writeSourceScoped("rag_activeModel", "m1")
  assert.equal(sc.readSourceScoped("rag_activeProvider", null), "p1")
  assert.equal(sc.readSourceScoped("rag_activeModel", null), "m1")
})

test("旧全局键不再被当作当前来源的值（升级即自愈）", async () => {
  const { sc } = await load()
  localStorage.setItem("rag_activeProvider", JSON.stringify("stale-provider-from-other-box"))
  localStorage.setItem("rag_activeModel", JSON.stringify("stale-model"))
  assert.equal(sc.readSourceScoped("rag_activeProvider", null), null)
  assert.equal(sc.readSourceScoped("rag_activeModel", null), null)
})

// ── store 接线（源码不变量：store 导不进来时必须靠这个防回退）────

const APP_STORE = readFileSync(
  new URL("../src/stores/app-store.ts", import.meta.url),
  "utf8",
)

test("store：activeProvider/activeModel 用来源作用域读写", () => {
  assert.match(
    APP_STORE,
    /activeProvider:\s*readSourceScoped<string \| null>\("rag_activeProvider"/,
    "activeProvider 初始化必须走 readSourceScoped",
  )
  assert.match(
    APP_STORE,
    /activeModel:\s*readSourceScoped<string \| null>\("rag_activeModel"/,
    "activeModel 初始化必须走 readSourceScoped",
  )
})

test("store：sessionId 也用来源作用域（每台盒子记住自己的上次会话）", () => {
  assert.match(
    APP_STORE,
    /sessionId:\s*readSourceScoped<string \| null>\("rag_sessionId"/,
    "sessionId 初始化必须走 readSourceScoped",
  )
  assert.match(APP_STORE, /writeSourceScoped\("rag_sessionId"/)
})

test("store：不再直接读写全局 provider/model/session 键", () => {
  for (const key of ["rag_activeProvider", "rag_activeModel", "rag_sessionId"]) {
    assert.ok(
      !APP_STORE.includes(`localStorage.setItem("${key}"`),
      `${key} 不应再有裸 localStorage.setItem（会绕过来源隔离）`,
    )
  }
})

test("store：setActiveProvider/setActiveModel 写来源作用域", () => {
  assert.match(APP_STORE, /setActiveProvider:\s*\(id\)\s*=>\s*\{?\s*writeSourceScoped\("rag_activeProvider"/)
  assert.match(APP_STORE, /setActiveModel:\s*\(model\)\s*=>\s*\{?\s*writeSourceScoped\("rag_activeModel"/)
})

test("store：防抖持久化订阅里也用来源作用域", () => {
  assert.match(APP_STORE, /writeSourceScoped\("rag_activeProvider"/)
  assert.match(APP_STORE, /writeSourceScoped\("rag_activeModel"/)
  assert.ok(
    !/localStorage\.setItem\("rag_activeProvider"/.test(APP_STORE),
    "订阅里的写入也必须走来源作用域",
  )
})

test("回归：三个后端实体键都已按来源隔离（含 sessionId）", () => {
  const scoped = ["rag_activeProvider", "rag_activeModel", "rag_sessionId"]
  for (const key of scoped) {
    assert.match(
      APP_STORE,
      new RegExp(`readSourceScoped<[^>]*>\\("${key}"`),
      `${key} 必须按来源读取`,
    )
  }
})
