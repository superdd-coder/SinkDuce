import assert from "node:assert/strict"
import { test } from "node:test"
import { createQaAutosave, createQaEditorAutosave, createQaMutationQueue } from "../src/lib/qa-autosave.ts"

function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>(done => { resolve = done })
  return { promise, resolve }
}

test("creating an editor does not read or invoke its deferred save callbacks", () => {
  const editor = createQaAutosave("", async () => { assert.fail("save during render") }, {
    onState() { assert.fail("state change during render") },
    onSaved() { assert.fail("saved during render") },
  })
  assert.equal(editor.dirty(), false)
})

test("autosave flushes on departure and coalesces typing during a pending save", async () => {
  const gate = deferred()
  const saved: string[] = []
  const editor = createQaAutosave("", async value => {
    saved.push(value)
    if (saved.length === 1) await gate.promise
  }, { delay: 60_000 })
  editor.edit("First response")
  const leaving = editor.flush()
  editor.edit("Updated response")
  editor.edit("Final response")
  const blur = editor.flush()
  gate.resolve()
  assert.equal(await leaving, true)
  assert.equal(await blur, true)
  assert.deepEqual(saved, ["First response", "Final response"])
  assert.equal(editor.dirty(), false)
  await editor.flush()
  assert.equal(saved.length, 2)
})

test("a failed autosave preserves the latest text for an explicit retry", async () => {
  let fail = true
  const saved: string[] = []
  const editor = createQaAutosave("Original", async value => {
    if (fail) throw new Error("offline")
    saved.push(value)
  }, { delay: 60_000 })
  editor.edit("")
  assert.equal(await editor.flush(), false)
  assert.equal(editor.dirty(), true)
  fail = false
  assert.equal(await editor.flush(), true)
  assert.deepEqual(saved, [""])
})

test("undoing an unsaved edit acknowledges the original text without a server write", async () => {
  const acknowledged: string[] = []
  const editor = createQaAutosave("Original", async () => { assert.fail("unnecessary write") }, {
    delay: 60_000, onSaved: value => acknowledged.push(value),
  })
  editor.edit("Temporary")
  editor.edit("Original")
  assert.equal(editor.dirty(), false)
  assert.deepEqual(acknowledged, ["Original"])
})

test("idle edits save automatically and external refresh cannot erase pending input", async () => {
  const saved: string[] = []
  const editor = createQaAutosave("Original", async value => { saved.push(value) }, { delay: 5 })
  editor.edit("New wording")
  assert.equal(editor.reset("External wording"), false)
  await new Promise(resolve => setTimeout(resolve, 30))
  assert.deepEqual(saved, ["New wording"])
  assert.equal(editor.reset("External wording"), true)
  assert.equal(editor.dirty(), false)
})

test("automatic acknowledgement keeps the reply editor active until the user leaves", async () => {
  const activity: boolean[] = []
  const editor = createQaEditorAutosave("Original", async () => {}, {
    delay: 60_000, onEditing: active => activity.push(active),
  })
  editor.begin()
  editor.edit("Revised wording")
  assert.equal(await editor.flush(), true)
  assert.equal(editor.editing(), true, "saving is independent of the focused editor")
  assert.deepEqual(activity, [true])
  assert.equal(await editor.finish(), true)
  assert.equal(editor.editing(), false)
  assert.deepEqual(activity, [true, false])
})

test("an earlier save finishing after refocus cannot close the editor again", async () => {
  const gate = deferred()
  const editor = createQaEditorAutosave("Original", async () => gate.promise, { delay: 60_000 })
  editor.begin(); editor.edit("New wording")
  const leaving = editor.finish()
  editor.begin()
  gate.resolve()
  assert.equal(await leaving, true)
  assert.equal(editor.editing(), true)
})

test("reply saves and column renames serialize against the latest list revision", async () => {
  const gate = deferred()
  const revisions: number[] = []
  const snapshot = { id: "list-a", version: { id: "v1" }, revision: 1 }
  const writer = createQaMutationQueue(async (list: typeof snapshot, path: string) => {
    revisions.push(list.revision)
    if (path === "/reply") await gate.promise
    return { ...list, revision: list.revision + 1 }
  })
  const reply = writer.write(snapshot, "/reply", {}, "PUT")
  const rename = writer.write(snapshot, "/column", {}, "PATCH")
  gate.resolve()
  assert.equal((await reply).revision, 2)
  assert.equal((await rename).revision, 3)
  assert.deepEqual(revisions, [1, 2])
})

test("failed writes do not block later edits or mix lists and versions", async () => {
  const calls: string[] = []
  const first = { id: "list-a", version: { id: "v1" }, revision: 1 }
  const writer = createQaMutationQueue(async (list: typeof first, path: string) => {
    if (path === "/fail") throw new Error("conflict")
    calls.push(`${list.id}/${list.version.id}/${list.revision}`)
    return { ...list, revision: list.revision + 1 }
  })
  await assert.rejects(writer.write(first, "/fail", {}))
  await writer.write({ ...first, id: "list-b", revision: 8 }, "/save", {})
  writer.remember({ ...first, revision: 4 })
  await writer.write(first, "/save", {})
  await writer.write({ ...first, version: { id: "v2" }, revision: 7 }, "/save", {})
  assert.deepEqual(calls, ["list-b/v1/8", "list-a/v1/4", "list-a/v2/7"])
})
