/**
 * 切换来源（本机 ⇄ 远端盒子）时，不能拿上一台盒子的 collection 去请求。
 *
 * 复现：把来源切到远端盒子 B 后选中 collection X（存进 localStorage 的
 * `rag_activeCollection`，**全局持久化、不区分来源**），再切回本机 A——A 上没有 X，
 * 于是文件树请求 404，界面弹「加载文件夹失败：Collection 'X' not found」。
 *
 * 现有自愈（fetchCollections 里发现 activeCollection 不存在就清空）来得太晚：
 * folder-view 一挂载就 fetchFolderTree，不等自愈。
 *
 * 约定：来源变了 → 该来源的 collection 选择必须重来。用「来源 id」作为
 * 缓存的域（key 里带上来源），切换后自然读不到旧值。
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
  return local
}

let store: Map<string, string>
beforeEach(() => {
  store = installFakeBrowser()
})

async function load() {
  return import(`../src/api/source-scope.ts?s=${Math.random()}`)
}

async function loadOrigin() {
  return import(`../src/api/origin.ts?s=${Math.random()}`)
}

test("本机来源：key 带 local 前缀", async () => {
  const { sourceScopedKey } = await load()
  assert.equal(sourceScopedKey("rag_activeCollection"), "rag_activeCollection@local")
})

test("远端来源：key 带该盒子 id", async () => {
  const o = await loadOrigin()
  o.saveConnections([
    { addr: "http://B:18901", token: "t", box_id: "boxB", box_name: "B", last_checked: "" },
  ])
  o.setActiveSourceId("boxB")
  const { sourceScopedKey } = await load()
  assert.equal(sourceScopedKey("rag_activeCollection"), "rag_activeCollection@boxB")
})

test("回归：两台盒子的 activeCollection 互不影响（切换不串味）", async () => {
  const o = await loadOrigin()
  const { sourceScopedKey } = await load()

  // 在远端 B 上选了 collection X
  o.saveConnections([
    { addr: "http://B:18901", token: "t", box_id: "boxB", box_name: "B", last_checked: "" },
  ])
  o.setActiveSourceId("boxB")
  localStorage.setItem(sourceScopedKey("rag_activeCollection"), JSON.stringify("col_X"))

  // 切回本机：读同一个逻辑键，必须读不到 B 的值
  o.setActiveSourceId(o.LOCAL_SOURCE)
  const onLocal = localStorage.getItem(sourceScopedKey("rag_activeCollection"))
  assert.equal(onLocal, null, "切回本机不该看到远端选中的 collection（那正是报错的来源）")
})

test("同一来源内仍能正常记住（未回归）", async () => {
  const { sourceScopedKey } = await load()
  localStorage.setItem(sourceScopedKey("rag_activeCollection"), JSON.stringify("col_A"))
  assert.equal(
    JSON.parse(localStorage.getItem(sourceScopedKey("rag_activeCollection")) as string),
    "col_A",
  )
})

test("旧版本的全局键值不会被读取（避免残留串味）", async () => {
  // 模拟升级前留下的全局值
  localStorage.setItem("rag_activeCollection", JSON.stringify("col_707b8ca8d617"))
  const { readSourceScoped } = await load()
  assert.equal(
    readSourceScoped("rag_activeCollection", ""),
    "",
    "旧全局键不应再被当作当前来源的值",
  )
})
