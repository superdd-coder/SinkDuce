/**
 * 连接健康判定的测试。
 *
 * 跑法（Node 24 原生剥离 TS，无需新依赖）：
 *   node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { test } from "node:test"

import { classifyConnectionFailure } from "../src/api/connection-health.ts"

test("本机来源的 401 不归连接健康管（那是钥匙门的事）", () => {
  assert.equal(classifyConnectionFailure({ activeRemote: false, status: 401 }), null)
  assert.equal(
    classifyConnectionFailure({ activeRemote: false, status: 401, code: "pairing_required" }),
    null,
  )
})

test("远端盒子 401 = 钥匙被吊销", () => {
  assert.equal(classifyConnectionFailure({ activeRemote: true, status: 401 }), "revoked")
  assert.equal(
    classifyConnectionFailure({ activeRemote: true, status: 401, code: "pairing_required" }),
    "revoked",
  )
})

test("远端盒子连不上 = unreachable", () => {
  assert.equal(classifyConnectionFailure({ activeRemote: true, networkError: true }), "unreachable")
})

test("远端盒子的其他错误码不算连接断开", () => {
  assert.equal(classifyConnectionFailure({ activeRemote: true, status: 404 }), null)
  assert.equal(classifyConnectionFailure({ activeRemote: true, status: 500 }), null)
  assert.equal(
    classifyConnectionFailure({ activeRemote: true, status: 409, code: "name_conflict" }),
    null,
  )
})

test("网络失败优先于状态码判断", () => {
  assert.equal(
    classifyConnectionFailure({ activeRemote: true, networkError: true, status: 401 }),
    "unreachable",
  )
})
