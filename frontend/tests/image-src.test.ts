/**
 * 笔记 / 会议图片的渲染 URL 解析。
 *
 * 存储里的图片 src 是**相对路径**（如 /api/notes/col_x/note_y/images/a.jpg），
 * 这对文档可移植性是对的——不能把某台盒子的地址写死进笔记内容。
 *
 * 但连远端盒子时，相对路径会被浏览器解析到**本机**后端 → 图在 B、本机没有 → 裂图。
 * 所以渲染时必须按当前来源解析成绝对地址，并附上配对钥匙（<img> 带不了请求头）。
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

async function load() {
  const o = await import(`../src/api/origin.ts?img=${Math.random()}`)
  const m = await import(`../src/api/image-src.ts?img=${Math.random()}`)
  return { o, m }
}

function connectRemote(o: { saveConnections: (l: unknown[]) => void; setActiveSourceId: (id: string) => void }) {
  o.saveConnections([
    { addr: "http://192.168.1.30:18901", token: "sd1_B", box_id: "b1", box_name: "B", last_checked: "" },
  ])
  o.setActiveSourceId("b1")
}

test("本机来源：相对路径原样 + 本机钥匙", async () => {
  const { o, m } = await load()
  o.setGateKey("sd1_LOCAL")
  const out = m.resolveImageSrc("/api/notes/c/n/images/a.jpg")
  assert.equal(out, "/api/notes/c/n/images/a.jpg?token=sd1_LOCAL")
})

test("远端盒子：解析成该盒子的绝对地址 + 该盒子的钥匙", async () => {
  const { o, m } = await load()
  connectRemote(o)
  const out = m.resolveImageSrc("/api/notes/c/n/images/a.jpg")
  assert.equal(out, "http://192.168.1.30:18901/api/notes/c/n/images/a.jpg?token=sd1_B")
})

test("回归：连远端时不能仍是相对路径（那会打到本机）", async () => {
  const { o, m } = await load()
  connectRemote(o)
  const out = m.resolveImageSrc("/api/meetings/m/images/b.png")
  assert.ok(out.startsWith("http://192.168.1.30:18901/"), "必须是远端绝对地址，否则图裂")
})

test("已是绝对地址（外链）不动它", async () => {
  const { m } = await load()
  for (const u of ["https://example.com/a.png", "http://x/y.png", "data:image/png;base64,AAA", "blob:http://x/1"]) {
    assert.equal(m.resolveImageSrc(u), u, `不应改写 ${u}`)
  }
})

test("不重复附加 token", async () => {
  const { o, m } = await load()
  o.setGateKey("sd1_LOCAL")
  const once = m.resolveImageSrc("/api/x/a.png")
  assert.equal(m.resolveImageSrc(once), once)
})

test("空值安全", async () => {
  const { m } = await load()
  assert.equal(m.resolveImageSrc(""), "")
  assert.equal(m.resolveImageSrc(null), null)
  assert.equal(m.resolveImageSrc(undefined), undefined)
})
