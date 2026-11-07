import assert from "node:assert/strict"
import { after, before, beforeEach, test } from "node:test"
import { fileURLToPath } from "node:url"
import { createServer } from "vite"
import type { FileSummary, FolderTreeNode } from "../src/types/file-mgmt.ts"

let server: Awaited<ReturnType<typeof createServer>>
let helpers: typeof import("../src/components/file-mgmt/folder-view/browser-items.ts")
let store: typeof import("../src/stores/file-mgmt-store.ts").useFileMgmtStore
const savedStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage")
const memory = new Map<string, string>()

function file(id: string, name = `${id}.pdf`, extra: Partial<FileSummary> = {}): FileSummary {
  return { file_id: id, filename: name, display_name: "", original_ext: "pdf", current_version_id: null,
    is_definitive: false, archived: false, unsupported: false, created_by: "local", version: 1,
    created_at: "2026-01-01", is_greyed: false, task_id: null, ...extra }
}
function folder(id: string, name: string, extra: Partial<FolderTreeNode> = {}): FolderTreeNode {
  return { folder_id: id, parent_folder_id: null, name, kind: "plain", is_system: false,
    created_by: "local", created_at: "2026-01-01", updated_at: "2026-01-01", version: 1,
    children: [], file_count: 0, ...extra }
}

before(async () => {
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
    getItem: (key: string) => memory.get(key) ?? null,
    setItem: (key: string, value: string) => memory.set(key, value),
    removeItem: (key: string) => memory.delete(key),
  } })
  const root = fileURLToPath(new URL("../", import.meta.url))
  server = await createServer({ root, configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    resolve: { alias: { "@": `${root}src`, "#edition": `${root}src/edition` } },
    optimizeDeps: { noDiscovery: true },
  })
  helpers = await server.ssrLoadModule("/src/components/file-mgmt/folder-view/browser-items.ts")
  ;({ useFileMgmtStore: store } = await server.ssrLoadModule("/src/stores/file-mgmt-store.ts"))
})
beforeEach(() => {
  memory.clear()
  store.setState({ currentFolderFiles: [], selectedFileIds: new Set(), selectedFolderIds: new Set(), multiSelectMode: false, ingestingFiles: {} })
})
after(async () => {
  await server?.close()
  if (savedStorage) Object.defineProperty(globalThis, "localStorage", savedStorage)
  else Reflect.deleteProperty(globalThis, "localStorage")
})

test("the directory groups branches without changing real folder ancestry", () => {
  const child = folder("child", "Legal", { parent_folder_id: "branch" })
  const branch = folder("branch", "Review", { kind: "branch", children: [child] })
  const tree = [folder("hidden", "Root", { is_system: true }), branch, folder("group", "Board", { kind: "user_group" }), folder("notes", "Notes", { kind: "system_group", is_system: true })]
  const before = JSON.stringify(tree)
  const root = helpers.currentFolderItems(tree, null, [file("root-file")], "type")
  assert.deepEqual(root.filter(item => item.kind === "folder").map(item => item.folder.folder_id).sort(), ["__branches__", "__groups__", "notes"])
  const branches = helpers.currentFolderItems(tree, "__branches__", [file("stale-file")], "type")
  assert.deepEqual(branches.map(item => item.kind === "folder" && item.folder.folder_id), ["branch"])
  const groupedTree = root.flatMap(item => item.kind === "folder" ? [item.folder] : [])
  assert.deepEqual(helpers.folderAncestorIds(groupedTree, "child"), ["__branches__", "branch", "child"])
  assert.deepEqual(helpers.currentFolderItems(tree, "branch", [], "name"), [{ kind: "folder", folder: child }])
  assert.equal(JSON.stringify(tree), before)
})

test("search matches display and original names, Unicode and translated folder labels", () => {
  const items = helpers.currentFolderItems([folder("notes", "Notes")], null, [
    file("board", "Board Minutes.PDF", { display_name: "董事会纪要" }), file("budget", "Budget.xlsx"),
  ], "name")
  const ids = (query: string) => helpers.filterBrowserItems(items, query, "all").map(item => item.kind === "file" ? item.file.file_id : item.folder.folder_id)
  assert.deepEqual(ids("ＭＩＮＵＴＥＳ board"), ["board"])
  assert.deepEqual(ids("董事会"), ["board"])
  assert.deepEqual(ids("missing"), [])
  assert.equal(helpers.filterBrowserItems(items, "笔记", "folder", name => name === "Notes" ? "笔记" : name).length, 1)
  assert.equal(helpers.filterBrowserItems(items, "", "file").length, 2)
})

test("hundreds of files remain reachable exactly once in both view sizes", () => {
  const items = helpers.currentFolderItems([], null, Array.from({ length: 237 }, (_, i) => file(`${i}`)), "name")
  for (const size of [50, 24]) {
    const pages = helpers.browserPage(items, 0, size).pages
    const visited = Array.from({ length: pages }, (_, i) => helpers.browserPage(items, i, size).items).flat()
    assert.deepEqual(visited, items)
    const last = helpers.browserPage(items, 999, size)
    assert.equal(last.page, pages - 1)
    assert.equal(last.end, 237)
    const emptied = helpers.browserPage([], last.page, size)
    assert.deepEqual([emptied.page, emptied.pages, emptied.start, emptied.end], [0, 1, 0, 0])
  }
})

test("filtered select-all excludes hidden, removed and ingesting files across pages", () => {
  const files = Array.from({ length: 120 }, (_, i) => file(`${i}`, `${i % 2 ? "budget" : "report"}-${i}.pdf`))
  store.setState({ currentFolderFiles: files, multiSelectMode: true, selectedFolderIds: new Set(["old-folder"]),
    ingestingFiles: { "2": { collectionId: "col", taskId: "task", progress: 20, message: "Parsing" } } })
  const matching = helpers.filterBrowserItems(helpers.currentFolderItems([], null, files, "name"), "report", "all")
  const ids = matching.flatMap(item => item.kind === "file" ? [item.file.file_id] : [])
  store.getState().selectAllFiles([...ids, "removed-file"])
  assert.equal(store.getState().selectedFileIds.size, 59)
  assert.ok(store.getState().selectedFileIds.has("118"), "matching files beyond page one are included")
  assert.ok(!store.getState().selectedFileIds.has("2"))
  assert.ok(!store.getState().selectedFileIds.has("3"))
  assert.ok(!store.getState().selectedFileIds.has("removed-file"))
  assert.equal(store.getState().selectedFolderIds.size, 0)
  store.getState().selectAllFiles([])
  assert.equal(store.getState().selectedFileIds.size, 0, "an empty filter never falls back to selecting every file")
  store.getState().exitMultiSelectMode()
  store.getState().selectAllFiles(ids)
  assert.equal(store.getState().selectedFileIds.size, 0)
})

test("new collections default to list while an existing grid preference survives", () => {
  store.getState().hydrateFolderFileView("new")
  assert.equal(store.getState().folderFileView, "list")
  store.getState().setFolderFileView("existing", "grid")
  store.getState().hydrateFolderFileView("new")
  assert.equal(store.getState().folderFileView, "list")
  store.getState().hydrateFolderFileView("existing")
  assert.equal(store.getState().folderFileView, "grid")
})
