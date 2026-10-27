/**
 * 从会话配置里挑出「可以安全恢复」的部分。
 *
 * 会话存的配置可能已经失效：provider 被删了、模型下线了、知识库被删了。
 * 直接灌回 store 会让人一发送就报错——所以要逐项校验，不通过就**落回默认**
 * （返回 null 表示「这一项别恢复，保持现状」）。
 *
 * 注意 provider 与 model 必须成对有效：只恢复 model 而不恢复 provider，
 * 会得到一个指向别家 provider 的模型名。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { test } from "node:test"

import { pickRestorableConfig, retainCollectionScope } from "../src/api/session-config.ts"

type P = { id: string; model?: string; selected_models?: string[]; function_call_model_ids?: string[] }

const providers: P[] = [
  { id: "p1", selected_models: ["m1", "m2"], default_model: "m1" } as P,
  { id: "p2", selected_models: ["m3"] },
]
const collectionIds = new Set(["col_a", "col_b"])

test("全部有效：整包恢复", () => {
  const out = pickRestorableConfig(
    { provider_id: "p1", model: "m2", web_search: true, thinking: false, collections: ["col_a"] },
    { providers, collectionIds },
  )
  assert.deepEqual(out, {
    provider_id: "p1",
    model: "m2",
    web_search: true,
    thinking: false,
    collections: ["col_a"],
  })
})

test("provider 已失效：provider 与 model 一起不恢复（不能只留 model）", () => {
  const out = pickRestorableConfig(
    { provider_id: "gone", model: "m1" },
    { providers, collectionIds },
  )
  assert.equal(out.provider_id, null, "provider 没了就别恢复")
  assert.equal(out.model, null, "model 必须跟着 provider 一起放弃")
})

test("模型已下线：保留 provider，模型落回该 provider 默认", () => {
  const out = pickRestorableConfig(
    { provider_id: "p1", model: "m-removed" },
    { providers, collectionIds },
  )
  assert.equal(out.provider_id, "p1", "provider 还有效，可以恢复")
  assert.equal(out.model, null, "模型失效 → 该 provider 的默认（null 让上层决定）")
})

test("只存了 model 没存 provider：不恢复 model（无从判断它属于谁）", () => {
  const out = pickRestorableConfig({ model: "m1" }, { providers, collectionIds })
  assert.equal(out.model, null)
})

test("知识库已删除：过滤掉失效 id，其余的照常恢复", () => {
  const out = pickRestorableConfig(
    { collections: ["col_a", "col_deleted"] },
    { providers, collectionIds },
  )
  assert.deepEqual(out.collections, ["col_a"])
})

test("知识库全失效：保留指定范围，避免空数组扩大到全部项目", () => {
  const out = pickRestorableConfig({ collections: ["gone"] }, { providers, collectionIds })
  assert.deepEqual(out.collections, ["gone"])
})

test("刷新时项目列表还没加载：保留会话指定项目", () => {
  const out = pickRestorableConfig(
    { collections: ["col_a"] },
    { providers, collectionIds: new Set() },
  )
  assert.deepEqual(out.collections, ["col_a"])
})

test("目录刷新和删除事件也不能把最后一个指定项目改成全部", () => {
  assert.deepEqual(retainCollectionScope(["col_a"], new Set()), ["col_a"])
  assert.deepEqual(retainCollectionScope(["col_a", "gone"], new Set(["col_a"])), ["col_a"])
  assert.deepEqual(retainCollectionScope([], new Set(["col_a"])), [])
})

test("布尔项：false 也要恢复（不能被当成「没存」）", () => {
  const out = pickRestorableConfig(
    { web_search: false, thinking: false },
    { providers, collectionIds },
  )
  assert.equal(out.web_search, false, "false 是有效值")
  assert.equal(out.thinking, false)
})

test("缺失项返回 null（调用方保持现状，不强制重置）", () => {
  const out = pickRestorableConfig({}, { providers, collectionIds })
  assert.equal(out.provider_id, null)
  assert.equal(out.model, null)
  assert.equal(out.web_search, null)
  assert.equal(out.thinking, null)
  assert.equal(out.collections, null)
})

test("空配置 / null 安全", () => {
  for (const bad of [null, undefined, {}]) {
    const out = pickRestorableConfig(bad, { providers, collectionIds })
    assert.equal(out.provider_id, null)
    assert.equal(out.collections, null)
  }
})

test("provider 有效但列表未加载：宁可不动也不误清（providers 为空）", () => {
  const out = pickRestorableConfig(
    { provider_id: "p1", model: "m1" },
    { providers: [], collectionIds },
  )
  assert.equal(out.provider_id, null, "没有 provider 列表时无法校验 → 不恢复，避免误伤")
})
