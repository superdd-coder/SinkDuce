import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { test } from "node:test"

const read = (path: string) => readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8")

test("the active list toolbar exposes its file space even when the list is read-only", () => {
  const workspace = read("components/qa/qa-workspace.tsx")
  const trigger = workspace.split("\n").find(line => line.includes("<FolderOpen />")) || ""
  assert.match(trigger, /\{data && <Button.*aria-haspopup="dialog".*\{t\("qa.materials"\)\}/)
  assert.match(workspace, /<QaFileSpaceDialog.*listId=\{data.id\}.*writable=\{data.can_edit\}/)
})

test("the toolbar and attachment picker use the same file-space name", () => {
  const chinese = JSON.parse(read("i18n/zh-CN.json")).qa
  const english = JSON.parse(read("i18n/en.json")).qa
  assert.equal(chinese.materials, "文件空间")
  assert.equal(english.materials, "File space")
  assert.ok(chinese.aiChooseMaterial.includes(chinese.materials))
  assert.ok(english.aiChooseMaterial.toLowerCase().includes(english.materials.toLowerCase()))
})
