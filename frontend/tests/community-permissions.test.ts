import assert from "node:assert/strict"
import { after, before, test } from "node:test"
import { fileURLToPath } from "node:url"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { createServer } from "vite"

let server: Awaited<ReturnType<typeof createServer>>
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage")

before(async () => {
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: undefined })
  const root = fileURLToPath(new URL("../", import.meta.url))
  server = await createServer({
    root, configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    resolve: {
      dedupe: ["react", "react-dom", "sonner", "lucide-react"],
      alias: { "@": root + "src", "#edition": root + "src/edition" },
    },
    optimizeDeps: { noDiscovery: true },
  })
})

after(async () => {
  await server?.close()
  if (originalStorage) Object.defineProperty(globalThis, "localStorage", originalStorage)
  else Reflect.deleteProperty(globalThis, "localStorage")
})

test("community owners can open permission management", async () => {
  const { PermissionEntry } = await server.ssrLoadModule("/src/edition/permission-entry.tsx")
  const visible = renderToStaticMarkup(createElement(PermissionEntry, { allowed: true, onOpen() {} }))
  assert.match(visible, /<button\b/)
  assert.equal(renderToStaticMarkup(createElement(PermissionEntry, { allowed: false, onOpen() {} })), "")
})

test("full community keeps its public label and update channel", async () => {
  const { EDITION_LABEL } = await server.ssrLoadModule("/src/edition/edition-label.ts")
  const { editionAllowsUpdate } = await server.ssrLoadModule("/src/lib/update-release.ts")
  assert.equal(EDITION_LABEL, "COMMUNITY")
  assert.equal(editionAllowsUpdate(EDITION_LABEL), true)
})
