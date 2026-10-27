/**
 * 库权限判定（2026-09-17）。
 *
 * **背景（用户报的 bug）**：collection 设置面板是自动保存、没有保存按钮。没有
 * config 档的人一打开那个界面，首屏就会 POST `/config` → 403 → 弹「保存失败」。
 * 用户要的不是"保存失败时温柔一点"，而是**压根进不去那个界面**。
 *
 * 承重性质：`permissions.config` 严格 `=== true` 才算有权限；缺省（老后端 / 列表
 * 还没回来）一律按"没有"处理。乐观放行的代价是发一个注定 403 的请求 + 一句报错，
 * 而少画一个入口的代价是零——所以宁可少画。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { test } from "node:test"

import {
  canCreateCollection,
  canOpenCollectionConfig,
  canWriteCollectionConfig,
  collectionPermsKnown,
} from "../src/lib/collection-perms.ts"

const full = { read: true, write: true, delete: true, config: true }
const readOnly = { read: true, write: false, delete: false, config: false }
const memberish = { read: true, write: true, delete: false, config: false }

test("config 档为 true → 能打开设置面板（owner/admin 走这条路）", () => {
  assert.equal(
    canOpenCollectionConfig([{ id: "col_a", permissions: full }], "col_a"),
    true,
  )
})

test("能读能写但没 config 档 → 不能打开（用户报的那个场景）", () => {
  assert.equal(
    canOpenCollectionConfig([{ id: "col_a", permissions: memberish }], "col_a"),
    false,
  )
})

test("visitor（只读）→ 不能打开", () => {
  assert.equal(
    canOpenCollectionConfig([{ id: "col_a", permissions: readOnly }], "col_a"),
    false,
  )
})

test("列表里没有这个库（不可见）→ 不能打开", () => {
  assert.equal(canOpenCollectionConfig([{ id: "col_b", permissions: full }], "col_a"), false)
})

test("没有 permissions 字段（老后端）→ 按没有权限处理，不乐观放行", () => {
  assert.equal(canOpenCollectionConfig([{ id: "col_a" }], "col_a"), false)
})

test("collections 还没加载（空数组）→ 不能打开", () => {
  assert.equal(canOpenCollectionConfig([], "col_a"), false)
})

test("collectionId 为空 → 不能打开（不崩）", () => {
  assert.equal(canOpenCollectionConfig([{ id: "col_a", permissions: full }], null), false)
  assert.equal(canOpenCollectionConfig([{ id: "col_a", permissions: full }], undefined), false)
  assert.equal(canOpenCollectionConfig([{ id: "col_a", permissions: full }], ""), false)
})

test("config 档显式 false 即便其他档全 true 也不能打开", () => {
  assert.equal(
    canOpenCollectionConfig(
      [{ id: "col_a", permissions: { read: true, write: true, delete: true, config: false } }],
      "col_a",
    ),
    false,
  )
})

test("自动保存与'能不能打开'是同一个答案（不设第二条乐观通道）", () => {
  const cases: Array<[string, Record<string, boolean> | undefined]> = [
    ["owner", full],
    ["member", memberish],
    ["visitor", readOnly],
    ["老后端", undefined],
  ]
  for (const [name, permissions] of cases) {
    const row = permissions ? { id: "col_a", permissions } : { id: "col_a" }
    assert.equal(
      canWriteCollectionConfig([row], "col_a"),
      canOpenCollectionConfig([row], "col_a"),
      `${name}: 两个判断必须一致`,
    )
  }
})

test("有 permissions 字段 = 信号已到（用来区分'没权限'与'还不知道'）", () => {
  assert.equal(collectionPermsKnown([{ id: "col_a", permissions: readOnly }], "col_a"), true)
})

test("没有 permissions 字段 = 信号未到（此时不该纠正用户的标签页）", () => {
  assert.equal(collectionPermsKnown([{ id: "col_a" }], "col_a"), false)
  assert.equal(collectionPermsKnown([], "col_a"), false)
  assert.equal(collectionPermsKnown([{ id: "col_a", permissions: full }], "col_b"), false)
})

test("canCreateCollection：can_create.collection 严格 === true（SIN 后续：无权限不画新建按钮）", () => {
  assert.equal(canCreateCollection({ can_create: { collection: true, meeting: false } }), true)
  assert.equal(canCreateCollection({ can_create: { collection: false, meeting: true } }), false)
  // 老后端 / 信号未到：按没有处理（少画一个按钮代价为零）
  assert.equal(canCreateCollection(undefined), false)
  assert.equal(canCreateCollection(null), false)
  assert.equal(canCreateCollection({}), false)
})

test("canCreateCollection：本机身份（is_local）直通——旧后端没有 can_create 也不丢盒主按钮", () => {
  assert.equal(canCreateCollection({ is_local: true }), true)
  assert.equal(canCreateCollection({ is_local: true, can_create: { collection: false, meeting: true } }), true)
  assert.equal(canCreateCollection({ is_local: false, can_create: { collection: false, meeting: true } }), false)
})
