import assert from "node:assert/strict"
import { test } from "node:test"

import {
  buildMcpJson,
  mcpServerKey,
  mcpLoopbackUrl,
  MCP_LOOPBACK_HOST,
} from "../src/lib/mcp-config.ts"

// ── 服务端名字 ────────────────────────────────────────────────

test("mcpServerKey: 带 SinkDuce- 前缀 + 盒子名", () => {
  assert.equal(mcpServerKey("Office Box", "box_1"), "SinkDuce-Office-Box")
  assert.equal(mcpServerKey("Docker", "b"), "SinkDuce-Docker")
  assert.equal(mcpServerKey("JethroDesktop", "b"), "SinkDuce-JethroDesktop")
})

test("mcpServerKey: 空白折成连字符（server 名惯例）", () => {
  assert.equal(mcpServerKey("  My  Box  ", "b"), "SinkDuce-My-Box")
})

test("mcpServerKey: 中文盒子名保留（不强行 ASCII 化，用户能对上）", () => {
  assert.equal(mcpServerKey("办公室", "b"), "SinkDuce-办公室")
})

test("mcpServerKey: 空名回退 box_id，再回退裸前缀", () => {
  assert.equal(mcpServerKey("", "box_1"), "SinkDuce-box_1")
  assert.equal(mcpServerKey("   ", ""), "SinkDuce")
})

test("mcpServerKey: 两台设备的 Hub 得到不同 server 名（多 Hub 不混）", () => {
  const a = mcpServerKey("Docker", "box_a")
  const b = mcpServerKey("JethroDesktop", "box_b")
  assert.notEqual(a, b)
})

// ── ★ 地址恒为回环（不变式）────────────────────────────────────

test("mcpLoopbackUrl: 恒为 127.0.0.1（MCP 只经回环可达）", () => {
  const url = mcpLoopbackUrl(18900)
  assert.equal(url, "http://127.0.0.1:18900/mcp")
  assert.ok(url.includes(MCP_LOOPBACK_HOST), "必须是回环地址")
})

test("mcpLoopbackUrl: 端口用本进程真实端口（桌面壳常是 18910）", () => {
  assert.equal(mcpLoopbackUrl(18910), "http://127.0.0.1:18910/mcp")
  assert.equal(mcpLoopbackUrl("18901"), "http://127.0.0.1:18901/mcp")
})

test("mcpLoopbackUrl: 空端口回退 18900（不拼出 :undefined / :0）", () => {
  assert.equal(mcpLoopbackUrl(""), "http://127.0.0.1:18900/mcp")
  assert.equal(mcpLoopbackUrl(0), "http://127.0.0.1:18900/mcp")
})

// ── ★ 生成物：回环 + 无钥匙 ────────────────────────────────────

test("buildMcpJson: 地址是回环，且**任何情况下不带钥匙**", () => {
  const r = buildMcpJson({ boxName: "This Mac", boxId: "box_local", port: 18900 })
  const entry = JSON.parse(r.json).mcpServers["SinkDuce-This-Mac"]
  assert.equal(entry.type, "http")
  assert.equal(entry.url, "http://127.0.0.1:18900/mcp")
  assert.equal("headers" in entry, false, "回环免钥匙，且 owner 签不出钥匙")
})

test("buildMcpJson: 顶层是 mcpServers，可直接粘进 mcp.json", () => {
  const r = buildMcpJson({ boxName: "Box", boxId: "b", port: 18910 })
  const parsed = JSON.parse(r.json)
  assert.ok(parsed.mcpServers, "顶层必须有 mcpServers")
  assert.equal(parsed.mcpServers["SinkDuce-Box"].url, "http://127.0.0.1:18910/mcp")
})

test("buildMcpJson: 回传 url 与 JSON 内容一致（UI 显示用）", () => {
  const r = buildMcpJson({ boxName: "B", boxId: "b", port: 18900 })
  assert.equal(r.url, "http://127.0.0.1:18900/mcp")
  assert.ok(r.json.includes(r.url))
})

test("buildMcpJson: 绝不产生 LAN 地址或钥匙", () => {
  const r = buildMcpJson({ boxName: "Office Box", boxId: "box_1", port: 18900 })
  assert.ok(!/\d+\.\d+\.\d+\.\d+/.test(r.json.replace(/127\.0\.0\.1/g, "")), r.json)
  assert.ok(!r.json.includes("sd1_"), "不该出现钥匙")
  assert.ok(!r.json.toLowerCase().includes("headers"), "不该有 headers")
})
