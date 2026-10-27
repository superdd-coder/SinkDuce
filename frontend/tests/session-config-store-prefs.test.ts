/**
 * thinking / web_search 提升进 store —— 与 provider/model/collections 一致。
 *
 * 背景（架构一致性要求）：
 * - 今天 `thinking` 是 chat-input 的组件本地 state，持久化到全局键 `chat_thinking`；
 * - `web_search` 也是组件本地 state，持久化到 `chat_web_search_by_session`
 *   （而且**已经**按 session 记，但在浏览器本地）。
 * 两者归属与持久化策略都不一致。本次统一为：**住 store + 随消息存服务端 config**。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { test } from "node:test"

const APP_STORE = readFileSync(new URL("../src/stores/app-store.ts", import.meta.url), "utf8")
const CHAT_INPUT = readFileSync(
  new URL("../src/components/chat/chat-input.tsx", import.meta.url),
  "utf8",
)
const QUICK_CHAT = readFileSync(
  new URL("../src/components/database/quick-chat.tsx", import.meta.url),
  "utf8",
)
const USE_STREAM = readFileSync(new URL("../src/hooks/use-stream.ts", import.meta.url), "utf8")

test("store 持有 thinking / webSearch 字段与 setter", () => {
  assert.match(APP_STORE, /thinking:\s*boolean/, "store 应有 thinking 字段")
  assert.match(APP_STORE, /webSearch:\s*boolean/, "store 应有 webSearch 字段")
  assert.match(APP_STORE, /setThinking:\s*\(/, "应有 setThinking")
  assert.match(APP_STORE, /setWebSearch:\s*\(/, "应有 setWebSearch")
})

test("旧机制已废弃：session-web-search 不再存在、也无人引用", () => {
  assert.equal(
    existsSync(new URL("../src/lib/session-web-search.ts", import.meta.url)),
    false,
    "session-web-search.ts 应被删除（统一到服务端）",
  )
  for (const [name, src] of [["chat-input", CHAT_INPUT], ["quick-chat", QUICK_CHAT]] as const) {
    assert.ok(
      !src.includes("session-web-search"),
      `${name} 不应再引用 session-web-search`,
    )
    assert.ok(
      !src.includes("loadWebSearchForSession") && !src.includes("setSessionWebSearch"),
      `${name} 不应再调用旧的本地 map 接口`,
    )
  }
})

test("chat-input 不再把 thinking/webSearch 当组件本地持久化 state", () => {
  assert.ok(
    !CHAT_INPUT.includes('localStorage.setItem("chat_thinking"'),
    "chat_thinking 本地键应废弃",
  )
  assert.ok(
    !CHAT_INPUT.includes('persisted("thinking"'),
    "thinking 不再走组件本地 persisted()",
  )
  // 改为从 store 取
  assert.match(CHAT_INPUT, /useAppStore\(/, "chat-input 应从 store 读这两项")
})

test("发送时带上 store 里的 thinking / webSearch（而非组件 state）", () => {
  // use-stream 负责组请求体：应取自 store
  assert.match(USE_STREAM, /thinking/, "请求体应带 thinking")
  assert.match(USE_STREAM, /web_search_enabled/, "请求体应带 web_search_enabled")
})

test("applySessionConfig 恢复 thinking / webSearch（不再走中转）", () => {
  assert.match(
    APP_STORE,
    /applySessionConfig[\s\S]{0,900}setThinking\(/,
    "恢复时应写入 thinking",
  )
  assert.match(
    APP_STORE,
    /applySessionConfig[\s\S]{0,900}setWebSearch\(/,
    "恢复时应写入 webSearch",
  )
  assert.ok(
    !APP_STORE.includes("sessionPrefs"),
    "中转方案应已移除（字段直接住 store）",
  )
})
