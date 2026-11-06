import assert from "node:assert/strict"
import { test } from "node:test"

// The controller is shared by picker and drag/drop events.
const controller = () => import("../src/lib/qa-upload.ts").then(m => m.createQaWorkbookUpload())

test("workbook selection accepts uppercase extensions and rejects invalid input before uploading", async () => {
  const upload = await controller()
  const accepted: string[] = []
  const save = async (file: { name: string; size: number }) => { accepted.push(file.name) }
  const file = { name: "Museum questions.XLSX", size: 1024 }
  assert.equal(await upload.receive([file], save), null)
  assert.equal(await upload.receive([file, file], save), "single")
  assert.equal(await upload.receive([{ ...file, name: "example.csv" }], save), "format")
  assert.equal(await upload.receive([{ ...file, size: 20 * 1024 * 1024 + 1 }], save), "size")
  assert.deepEqual(accepted, [file.name])
})

test("dropping again while a workbook is processing does not create a second import", async () => {
  const upload = await controller()
  let finish!: () => void
  let count = 0
  const pending = new Promise<void>(resolve => { finish = resolve })
  const save = async () => { count++; await pending }
  const file = { name: "example.xlsx", size: 1024 }
  const first = upload.receive([file], save)
  const second = upload.receive([file], save)
  try { assert.equal(count, 1) } finally { finish() }
  await Promise.all([first, second])
  await upload.receive([file], save, true)
  assert.equal(count, 1)
  await upload.receive([file], save)
  assert.equal(count, 2)
})

test("failed imports allow the same workbook to be selected again", async () => {
  const upload = await controller()
  const file = { name: "example.xlsx", size: 1024 }
  await assert.rejects(upload.receive([file], async () => { throw new Error("unavailable") }))
  let count = 0
  await upload.receive([file], async () => { count++ })
  assert.equal(count, 1)
})
