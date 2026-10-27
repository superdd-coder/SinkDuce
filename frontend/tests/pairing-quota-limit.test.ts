/**
 * 配额上限必须来自后端，不能前端硬编码。
 *
 * 背景（2026-09-16）：用户把免费档活跃钥匙上限从 2 提到 5。此前前端把 "2"
 * 写死在配对卡片里（`limit: "2"`），后端改了它就会显示「N / 2」——UI 说谎。
 * 现在 `/pairing/tokens` 一并返回 `limit`，前端照着显示。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { test } from "node:test"

const CARD = readFileSync(
  new URL("../src/components/llm-provider/pairing-card.tsx", import.meta.url),
  "utf8",
)
const PAIRING_API = readFileSync(
  new URL("../src/api/pairing.ts", import.meta.url),
  "utf8",
)

test("前端类型声明里带上 limit（与后端契约一致）", () => {
  assert.match(
    PAIRING_API,
    /tokens:\s*PairingTokenItem\[\][\s\S]{0,120}limit/,
    "listPairingTokens 的返回类型应含 limit",
  )
})

test("配对卡片不再硬编码配额为 2", () => {
  assert.ok(
    !/limit:\s*"2"/.test(CARD),
    '不应再出现硬编码的 limit: "2"（后端已提到 5，前端要跟着后端走）',
  )
})

test("配对卡片把后端返回的 limit 传进文案", () => {
  // 文案应使用从响应里取到的值（形如 limit: String(quotaLimit)）
  assert.match(
    CARD,
    /limit:\s*String\([A-Za-z_$][\w$]*\)/,
    "limit 应来自状态变量，而不是字面量",
  )
})
