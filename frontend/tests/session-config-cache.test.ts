/**
 * 会话配置的本地缓存（按会话隔离）。
 *
 * 这是 2026-09-16 那个回归的核心：`loadSessionMessages` 有**三条早返回路径**，
 * 而配置恢复只加在「从服务端新拉」那一条上。切回**已缓存**的会话时消息回来了、
 * 配置却还是上一个会话的 → 一旦发送，就把错的配置写进这个会话 → 级联污染，
 * 表现为「所有会话都变成同一套」。
 *
 * 修法：把「某会话的配置」缓存下来，**无论消息来自缓存还是服务端，都恢复配置**。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

import {
  forgetSessionConfig,
  recallSessionConfig,
  rememberSessionConfig,
} from "../src/api/session-config.ts"

beforeEach(() => {
  // 每个用例从干净缓存开始
  for (const id of ["A", "B", "C"]) forgetSessionConfig(id)
})

test("回归：两台会话的配置互不覆盖（正是级联污染的根因）", () => {
  rememberSessionConfig("A", { model: "m-A", thinking: false })
  rememberSessionConfig("B", { model: "m-B", thinking: true })

  assert.equal(recallSessionConfig("A")?.model, "m-A", "切回 A 必须拿到 A 自己的配置")
  assert.equal(recallSessionConfig("B")?.model, "m-B", "B 的不能被 A 覆盖")
  assert.notDeepEqual(
    recallSessionConfig("A"),
    recallSessionConfig("B"),
    "两个会话的配置必须不同（曾经会串成同一个）",
  )
})

test("未缓存过的会话 → undefined（调用方不恢复，保持现状）", () => {
  assert.equal(recallSessionConfig("C"), undefined)
})

test("remember 覆盖写：同一会话再次记录以最新为准", () => {
  rememberSessionConfig("A", { model: "old" })
  rememberSessionConfig("A", { model: "new" })
  assert.equal(recallSessionConfig("A")?.model, "new")
})

test("空配置也记住（老会话 config={}），以便区分「没记录」与「记录为空」", () => {
  rememberSessionConfig("A", {})
  assert.deepEqual(recallSessionConfig("A"), {}, "空对象 ≠ undefined")
})

test("坏值安全：null/undefined 记成空对象而非崩溃", () => {
  rememberSessionConfig("A", null)
  assert.deepEqual(recallSessionConfig("A"), {})
  rememberSessionConfig("B", undefined)
  assert.deepEqual(recallSessionConfig("B"), {})
})

test("forget 后不再命中（删除会话时清理）", () => {
  rememberSessionConfig("A", { model: "m" })
  forgetSessionConfig("A")
  assert.equal(recallSessionConfig("A"), undefined)
})

// ── 源码不变量：三条返回路径都要恢复配置（回归防线）──────────────

import { readFileSync } from "node:fs"

const APP_STORE = readFileSync(new URL("../src/stores/app-store.ts", import.meta.url), "utf8")

test("回归：loadSessionMessages 的**缓存快路径**也要恢复配置", () => {
  // 这是 2026-09-16 那次级联污染的直接根因：消息走内存缓存时不请求服务端，
  // 若不在那里恢复配置，store 会留着上一个会话的值。
  const cacheBranch = APP_STORE.slice(
    APP_STORE.indexOf("if (cached) {"),
    APP_STORE.indexOf("set({", APP_STORE.indexOf("sessionLoading: true")),
  )
  assert.ok(cacheBranch.length > 0, "应能定位缓存快路径")
  assert.match(
    cacheBranch,
    /applySessionConfig\(recallSessionConfig\(sessionId\)\)/,
    "缓存快路径必须从本地配置缓存恢复（否则串味）",
  )
})

test("回归：服务端路径要记住原始配置（供快路径复用）", () => {
  assert.match(
    APP_STORE,
    /rememberSessionConfig\(sessionId, detail\.config\)/,
    "拉到详情后应写入本地配置缓存",
  )
})

test("applySessionConfig 五项全写（不再「有值才写」——那是串味的第二层根因）", () => {
  const block = APP_STORE.slice(APP_STORE.indexOf("applySessionConfig: async"))
  for (const setter of [
    "setActiveProvider(resolved.",
    "setActiveModel(resolved.",
    "setSelectedCollections(resolved.",
    "setThinking(resolved.",
    "setWebSearch(resolved.",
  ]) {
    assert.ok(
      block.slice(0, 1200).includes(setter),
      `applySessionConfig 应无条件写 ${setter}（缺失项由 resolveSessionConfig 补默认）`,
    )
  }
})
