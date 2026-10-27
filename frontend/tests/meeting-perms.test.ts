/**
 * 会议权限判定（2026-09-17）。
 *
 * **背景（用户要求）**：「没有会议删除权限的话，会议的删除按钮也要看不到才对」。
 * 和库设置那边同一类问题：按钮不认权限，画出来了才发现按不动（或按了报错）。
 *
 * 承重性质：`permissions.operate` 严格 `=== true`；缺省（老后端 / 字段缺失）一律按
 * "没有权限"处理——少画一个按钮代价为零，多画一个的代价是用户点一下才发现。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { test } from "node:test"

import { canOperateMeeting, canOperateMeetingGroup, canCreateMeeting } from "../src/lib/meeting-perms.ts"

test("operate=true → 能操作（owner/admin 走这条路）", () => {
  assert.equal(canOperateMeeting({ permissions: { read: true, operate: true } }), true)
})

test("能读不能操作 → 不画按钮（member 默认就是这个档）", () => {
  assert.equal(canOperateMeeting({ permissions: { read: true, operate: false } }), false)
})

test("visitor（读和操作都没有）→ 不画按钮", () => {
  assert.equal(canOperateMeeting({ permissions: { read: false, operate: false } }), false)
})

test("没有 permissions 字段（老后端）→ 按没有权限处理，不乐观放行", () => {
  assert.equal(canOperateMeeting({}), false)
})

test("meeting 为 null/undefined → 不崩，且按没有权限", () => {
  assert.equal(canOperateMeeting(null), false)
  assert.equal(canOperateMeeting(undefined), false)
})

test("operate 显式为 false 即便 read 为 true 也不能操作", () => {
  assert.equal(canOperateMeeting({ permissions: { read: true, operate: false } }), false)
})

test("分组：can_operate=true → 能删/归档/改名", () => {
  assert.equal(canOperateMeetingGroup({ can_operate: true }), true)
})

test("分组：can_operate=false → 不画按钮", () => {
  assert.equal(canOperateMeetingGroup({ can_operate: false }), false)
})

test("分组：缺字段（老后端）→ 不画按钮", () => {
  assert.equal(canOperateMeetingGroup({}), false)
})

test("分组：null/undefined → 不崩，按没有权限", () => {
  assert.equal(canOperateMeetingGroup(null), false)
  assert.equal(canOperateMeetingGroup(undefined), false)
})

test("两个判定互不串味（会议的 permissions 不影响分组）", () => {
  const meetingLike = { permissions: { read: true, operate: true } }
  assert.equal(canOperateMeetingGroup(meetingLike), false, "分组看的是 can_operate，不是 permissions")
})

test("canCreateMeeting：can_create.meeting 严格 === true（无 create 档不画新建会议）", () => {
  assert.equal(canCreateMeeting({ can_create: { meeting: true, collection: false } }), true)
  assert.equal(canCreateMeeting({ can_create: { meeting: false, collection: true } }), false)
  // 老后端 / 信号未到：按没有处理
  assert.equal(canCreateMeeting(undefined), false)
  assert.equal(canCreateMeeting(null), false)
  assert.equal(canCreateMeeting({}), false)
})

test("canCreateMeeting：本机身份（is_local）直通——旧后端没有 can_create 也不丢盒主按钮", () => {
  assert.equal(canCreateMeeting({ is_local: true }), true)
  assert.equal(canCreateMeeting({ is_local: true, can_create: { meeting: false, collection: true } }), true)
  assert.equal(canCreateMeeting({ is_local: false, can_create: { meeting: false, collection: true } }), false)
})
