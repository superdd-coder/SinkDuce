/**
 * 已连接设备「改地址」（2026-09-17 用户要求）。
 *
 * 场景：盒子换网络 / DHCP 换 IP → 连接记录指着旧地址。补一个就地改地址的入口，
 * 省掉「移除 + 重新粘贴 API Key」。
 *
 * 两条纪律在 lib/pairing-edit.ts 里集中，这里钉住：
 * 1. 地址归一规则与「连接」弹窗**完全一致**（否则两条路径存出两种形状）；
 * 2. **只允许改同一台盒子的地址**——探测回来的 box_id 必须与记录一致。若那个地址上
 *    是另一台盒子，覆盖记录会把两台盒子的来源作用域键 / 已存钥匙 / 界面偏好串味。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { test } from "node:test"

import {
  applyAddressEdit,
  isSameBox,
  normalizeRemoteAddr,
} from "../src/lib/pairing-edit.ts"

const CARD = readFileSync(
  new URL("../src/components/llm-provider/pairing-card.tsx", import.meta.url),
  "utf8",
)
const CONNECT = readFileSync(
  new URL("../src/components/llm-provider/connect-box-dialog.tsx", import.meta.url),
  "utf8",
)
const EDIT = readFileSync(
  new URL("../src/components/llm-provider/edit-box-address-dialog.tsx", import.meta.url),
  "utf8",
)

const conn = (over: Partial<Record<string, string>> = {}) => ({
  addr: "http://192.168.1.30:18901",
  token: "sd1_abc",
  box_id: "box_a",
  box_name: "Office Box",
  last_checked: "2026-09-17T00:00:00.000Z",
  ...over,
})

// ── 地址归一 ──────────────────────────────────────────────

test("缺 scheme 补 http://", () => {
  assert.equal(normalizeRemoteAddr("192.168.1.5:18901"), "http://192.168.1.5:18901")
})

test("去掉尾部斜杠", () => {
  assert.equal(normalizeRemoteAddr("http://a.b:18901///"), "http://a.b:18901")
})

test("https 保持不变", () => {
  assert.equal(normalizeRemoteAddr("https://box.example.com"), "https://box.example.com")
})

test("空串 / 纯空白 → 空串（调用方靠它判断禁用保存）", () => {
  assert.equal(normalizeRemoteAddr(""), "")
  assert.equal(normalizeRemoteAddr("   "), "")
})

test("归一规则与「连接」弹窗一致（两条路径不能存出两种形状）", () => {
  // 连接弹窗用的是同一套：先补 scheme 再 trim 尾部斜杠
  assert.match(CONNECT, /replace\(\/\\\/\+\$\/, ""\)/)
  assert.match(CONNECT, /\^https\?:\\\/\\\//i)
})

// ── 同一台盒子 ────────────────────────────────────────────

test("box_id 相同 → 同一台", () => {
  assert.equal(isSameBox("box_a", "box_a"), true)
})

test("box_id 不同 → 不是同一台（拒绝覆盖）", () => {
  assert.equal(isSameBox("box_a", "box_b"), false)
})

test("探测拿不到 box_id → 不当成同一台", () => {
  assert.equal(isSameBox("box_a", ""), false)
})

// ── 就地更新记录 ──────────────────────────────────────────

test("按 box_id 命中并更新地址/钥匙/名字/检测时间", () => {
  const list = applyAddressEdit([conn()], "box_a", {
    addr: "http://10.0.0.9:18901",
    token: "sd1_new",
    boxName: "Office Box",
    lastChecked: "2026-09-17T12:00:00.000Z",
  })
  assert.equal(list.length, 1)
  assert.equal(list[0].addr, "http://10.0.0.9:18901")
  assert.equal(list[0].token, "sd1_new")
  assert.equal(list[0].last_checked, "2026-09-17T12:00:00.000Z")
})

test("其他记录原样不动", () => {
  const other = conn({ box_id: "box_b", addr: "http://10.0.0.2:18901" })
  const list = applyAddressEdit([conn(), other], "box_a", {
    addr: "http://x:1",
    token: "t",
    boxName: "n",
    lastChecked: "now",
  })
  assert.equal(list[1], other, "未命中的那条必须是同一个对象（不被重写）")
})

test("按 box_id 而不是名字匹配（同名盒子不串）", () => {
  const a = conn({ box_id: "box_a", box_name: "Same" })
  const b = conn({ box_id: "box_b", box_name: "Same", addr: "http://b:1" })
  const list = applyAddressEdit([a, b], "box_b", {
    addr: "http://new:1",
    token: "t",
    boxName: "Same",
    lastChecked: "now",
  })
  assert.equal(list[0].addr, "http://192.168.1.30:18901", "box_a 不该被动")
  assert.equal(list[1].addr, "http://new:1")
})

test("探测回来的名字为空时保留原名（不写空）", () => {
  const list = applyAddressEdit([conn()], "box_a", {
    addr: "http://x:1",
    token: "t",
    boxName: "",
    lastChecked: "now",
  })
  assert.equal(list[0].box_name, "Office Box")
})

test("找不到 box_id → 原样返回（不新增记录）", () => {
  const list = applyAddressEdit([conn()], "nope", {
    addr: "http://x:1",
    token: "t",
    boxName: "n",
    lastChecked: "now",
  })
  assert.equal(list.length, 1)
  assert.equal(list[0].addr, "http://192.168.1.30:18901")
})

// ── 界面接线 ──────────────────────────────────────────────

test("已连接列表每行都有「改地址」按钮", () => {
  assert.match(CARD, /setEditTarget\(c\)/, "行内按钮应把该条记录设为编辑目标")
})

test("弹窗用重挂载（seq）而不是靠 onOpenChange 重置", () => {
  // 受控 Dialog 父置 open=true 时 onOpenChange 不触发，弹窗内 state 拿不到初始值。
  assert.match(CARD, /setEditSeq\(\(n\) => n \+ 1\)/)
  assert.match(EDIT, /useState\(connection\.addr\)/)
})

test("保存前必须校验，且校验不通过不写入", () => {
  assert.match(EDIT, /await testRemoteBox\(/)
  assert.match(EDIT, /isSameBox\(connection\.box_id, probed\.box_id\)/)
})

test("盒子身份不一致时拒绝覆盖并提示", () => {
  assert.match(EDIT, /setError\("different_box"\)/)
  assert.match(EDIT, /errDifferentBox/)
})

test("改的是当前正在用的来源 → 整页重载（SSE/WS 还挂在旧地址）", () => {
  assert.match(EDIT, /getActiveSourceId\(\) === connection\.box_id/)
  assert.match(EDIT, /window\.location\.reload\(\)/)
})

test("API Key 以密码型输入（不明文摊在屏幕上）", () => {
  assert.match(EDIT, /type="password"/)
})
