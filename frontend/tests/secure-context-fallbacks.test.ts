/**
 * secure-context 兜底：newId() / sha256Hex()。
 *
 * 背景（2026-09-16 远端设备实测 bug）：`crypto.randomUUID` 与 `crypto.subtle`
 * 是 secure-context-only API——本机 127.0.0.1 可用，但局域网设备用普通 HTTP
 * 打开盒子页面时它们**不存在**。Chat / QuickChat 发送时直接调用 randomUUID
 * 生成消息 id → 当场 TypeError → 消息发不出去；而创建类操作的 id 由后端
 * 生成，不受影响——正是「创建可以、发送不行」的根因。
 *
 * 承重性质：
 * - sha256Hex 兜底必须与 `hashlib.sha256` / `crypto.subtle.digest` **逐字节一致**
 *   （笔记 ingested_content_hash 两端比对，算法确定、无随机性）；
 * - newId 在无 randomUUID 的环境必须仍产出唯一、格式合法的 v4。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { after, test } from "node:test"
import { createHash, webcrypto } from "node:crypto"

import { newId } from "../src/lib/uuid.ts"
import { sha256Hex } from "../src/lib/sha256.ts"

// ── sha256Hex：与标准实现逐字节一致 ──────────────────────────────

const VECTORS: [string, string][] = [
  ["empty", ""],
  ["abc", "abc"],
  ["ascii", "hello world"],
  ["chinese", "笔记入库指纹：内容一致才算未修改。"],
  ["long-multi-block", "SinkDuce ".repeat(500) + "tail"],
  // padding 边界：55 字节 = 原块恰好装下 padding；56 起溢出新块
  ["len55", "a".repeat(55)],
  ["len56", "b".repeat(56)],
  ["len63", "c".repeat(63)],
  ["len64", "d".repeat(64)],
]

for (const [name, input] of VECTORS) {
  test(`sha256Hex(${name}) matches node:crypto`, () => {
    const expected = createHash("sha256").update(input, "utf8").digest("hex")
    assert.equal(sha256Hex(input), expected)
  })
}

test("sha256Hex matches crypto.subtle on the same input", async () => {
  const input = "ingest-hash-parity-check"
  const subtle = await webcrypto.subtle.digest("SHA-256", new TextEncoder().encode(input))
  const subtleHex = Array.from(new Uint8Array(subtle))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
  assert.equal(sha256Hex(input), subtleHex)
})

test("sha256Hex accepts raw bytes", () => {
  const raw = new Uint8Array(Array.from({ length: 100 }, (_, i) => (i * 37 + 11) % 256))
  const expected = createHash("sha256").update(raw).digest("hex")
  assert.equal(sha256Hex(raw), expected)
})

// ── newId：无 randomUUID 的环境（远端 HTTP 源）仍可用 ─────────────

const v4re = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

test("newId() emits valid v4 when crypto.randomUUID exists", () => {
  assert.match(newId(), v4re)
})

test("newId() falls back to getRandomValues when randomUUID is missing", () => {
  // 模拟非安全上下文：只有 getRandomValues，没有 randomUUID / subtle。
  const g = globalThis as { crypto?: Crypto }
  const original = g.crypto
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: { getRandomValues: (a: Uint8Array) => webcrypto.getRandomValues(a) },
  })
  after(() => {
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: original })
  })

  const ids = new Set<string>()
  for (let i = 0; i < 5000; i++) {
    const id = newId()
    assert.match(id, v4re, "fallback must still emit valid v4")
    ids.add(id)
  }
  assert.equal(ids.size, 5000, "fallback ids must be unique")
})

test("newId() still unique with no crypto at all", () => {
  const g = globalThis as { crypto?: Crypto }
  const original = g.crypto
  Object.defineProperty(globalThis, "crypto", { configurable: true, value: undefined })
  after(() => {
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: original })
  })

  const ids = new Set<string>()
  for (let i = 0; i < 100; i++) ids.add(newId())
  assert.equal(ids.size, 100)
})
