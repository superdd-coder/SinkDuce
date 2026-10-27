import assert from "node:assert/strict"
import { test } from "node:test"
import { existsSync } from "node:fs"

const moduleUrl = new URL("../src/lib/chat-model-label.ts", import.meta.url)
const modelLabel = existsSync(moduleUrl) ? await import(moduleUrl.href) : {}

test("未指定模型时显示默认模型，不猜另一个 Provider 的默认值", () => {
  assert.equal(typeof modelLabel.chatModelLabel, "function")
  assert.equal(modelLabel.chatModelLabel(null, null, "默认模型"), "默认模型")
})

test("只有 Provider 或只有模型都不是有效覆盖", () => {
  assert.equal(typeof modelLabel.chatModelLabel, "function")
  assert.equal(modelLabel.chatModelLabel("p1", null, "默认模型"), "默认模型")
  assert.equal(modelLabel.chatModelLabel(null, "m1", "默认模型"), "默认模型")
})

test("用户显式选中的 Provider 和模型成对显示", () => {
  assert.equal(typeof modelLabel.chatModelLabel, "function")
  assert.equal(modelLabel.chatModelLabel("p1", "m1", "默认模型"), "m1")
})
