import assert from "node:assert/strict"
import { test } from "node:test"
import { resolveLLMApiMode } from "../src/lib/llm-api-mode.ts"

test("the default API follows the Base URL until manually selected", () => {
  assert.equal(resolveLLMApiMode(""), "chat_completions")
  assert.equal(resolveLLMApiMode(" HTTPS://API.OPENAI.COM/v1/ "), "responses")
  assert.equal(resolveLLMApiMode("https://eu.api.openai.com/v1"), "responses")
  assert.equal(resolveLLMApiMode("https://api.openai.com.example.test/v1"), "chat_completions")
  assert.equal(resolveLLMApiMode("https://api.openai.com@proxy.example.test/v1"), "chat_completions")
  assert.equal(resolveLLMApiMode("https://["), "chat_completions")
  assert.equal(resolveLLMApiMode("https://api.openai.com/v1", "chat_completions"), "chat_completions")
  assert.equal(resolveLLMApiMode("https://gateway.example.test/v1", "responses"), "responses")
})
