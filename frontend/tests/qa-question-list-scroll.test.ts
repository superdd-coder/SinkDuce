import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { test } from "node:test"

const read = (file: string) => readFileSync(new URL(`../src/components/qa/${file}`, import.meta.url), "utf8")

test("both question list toolbars stay outside the scrolling rows", () => {
  const workspace = read("qa-workspace.tsx")
  const fixedHeaders = workspace.match(/<QaQuestionListHeader\b.*\/>\s*<div className="qa-question-list-scroll">/g)
  assert.equal(fixedHeaders?.length, 2)

  const css = read("qa.css")
  const scrollRules = [...css.matchAll(/([^{}]+)\{[^{}]*overflow-y:\s*auto[^{}]*\}/g)].map(([, selector]) => selector.trim())
  assert.ok(scrollRules.some(selector => selector.includes(".qa-question-list-scroll")))
  assert.ok(scrollRules.every(selector => !/(?:^|,\s*)\.qa-(?:overview|question-nav)(?:,|$)/.test(selector)))
})
