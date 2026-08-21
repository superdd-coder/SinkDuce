import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { test } from "node:test"
import { fileURLToPath } from "node:url"

const dir = join(dirname(fileURLToPath(import.meta.url)), "../frontend/src/i18n")

type Catalog = Record<string, unknown>

function load(name: string): Catalog {
  return JSON.parse(readFileSync(join(dir, name), "utf8")) as Catalog
}

function dottedKeys(node: unknown, prefix = ""): string[] {
  if (typeof node === "string") return prefix ? [prefix] : []
  if (!node || typeof node !== "object" || Array.isArray(node)) {
    throw new Error(`catalog value at "${prefix}" must be a string or object`)
  }
  const out: string[] = []
  for (const [k, v] of Object.entries(node as Catalog)) {
    const path = prefix ? `${prefix}.${k}` : k
    out.push(...dottedKeys(v, path))
  }
  return out.sort()
}

function stringValues(node: unknown): string[] {
  if (typeof node === "string") return [node]
  if (!node || typeof node !== "object" || Array.isArray(node)) return []
  return Object.values(node as Catalog).flatMap(stringValues)
}

test("en.json and zh-CN.json share the same key tree", () => {
  const enKeys = dottedKeys(load("en.json"))
  const zhKeys = dottedKeys(load("zh-CN.json"))
  assert.deepEqual(zhKeys, enKeys)
  assert.ok(enKeys.includes("nav.chat"))
  assert.ok(enKeys.includes("settings.interfaceLanguage"))
  assert.ok(enKeys.includes("fileMgmt.rollbackBody"))
  assert.ok(enKeys.includes("settings.llmKicker"))
  assert.ok(enKeys.includes("fileMgmt.queuing"))
})

test("zh-CN catalog does not use 资料库", () => {
  const hits = stringValues(load("zh-CN.json")).filter((s) => s.includes("资料库"))
  assert.deepEqual(hits, [])
})
