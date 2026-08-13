import assert from "node:assert/strict"
import { test } from "node:test"

import {
  clipLanguageHints,
  toggleLanguageHint,
} from "../frontend/src/lib/language-hints.ts"

test("auto stays exclusive", () => {
  assert.deepEqual(toggleLanguageHint(["zh"], "auto"), ["auto"])
  assert.deepEqual(toggleLanguageHint(["auto"], "auto"), ["auto"])
})

test("fun-asr max 1 replaces the previous language", () => {
  assert.deepEqual(toggleLanguageHint(["auto"], "zh", 1), ["zh"])
  assert.deepEqual(toggleLanguageHint(["zh"], "en", 1), ["en"])
  assert.deepEqual(toggleLanguageHint(["en"], "en", 1), ["auto"])
})

test("qwen max 4 allows multi-select then refuses a fifth", () => {
  let next = toggleLanguageHint(["auto"], "zh", 4)
  next = toggleLanguageHint(next, "en", 4)
  next = toggleLanguageHint(next, "ja", 4)
  next = toggleLanguageHint(next, "ko", 4)
  assert.deepEqual(next, ["zh", "en", "ja", "ko"])
  assert.deepEqual(toggleLanguageHint(next, "fr", 4), ["zh", "en", "ja", "ko"])
})

test("clipLanguageHints keeps auto or trims to max", () => {
  assert.deepEqual(clipLanguageHints(["zh", "en"], 1), ["zh"])
  assert.deepEqual(clipLanguageHints(["auto", "zh"], 1), ["zh"])
  assert.deepEqual(clipLanguageHints(["auto"], 4), ["auto"])
})
