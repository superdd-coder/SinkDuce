import assert from "node:assert/strict"
import { after, before, test } from "node:test"
import { fileURLToPath } from "node:url"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { createServer } from "vite"
import type { UpdateInfo } from "../src/hooks/use-update-check"

let server: Awaited<ReturnType<typeof createServer>>
let Sidebar: typeof import("../src/components/layout/sidebar.tsx").Sidebar
type NavIdentity = { modules?: Record<string, boolean>; domains?: { meeting?: boolean } }
let state: { identity: NavIdentity | null; developerMode: boolean; sidebarView: string }
let updateState: { update: UpdateInfo | null; ignored: boolean }

before(async () => {
  const root = fileURLToPath(new URL("../", import.meta.url))
  const modules: Record<string, string> = {
    "virtual:shell-store": `
      export const state = { identity: null, developerMode: false, sidebarView: "database", logPanelOpen: false, toggleLogPanel() {}, setSidebarView() {} };
      export const useAppStore = selector => selector(state);
    `,
    "virtual:shell-update": `
      export const updateState = { update: null, ignored: false };
      export const useUpdateCheck = () => ({ ...updateState, currentVersion: "1.6.2", ignoreVersion() {} });
    `,
    "virtual:shell-dialog": `export const UpdateDialog = () => null;`,
    "virtual:shell-source": `export const SourcePill = () => null;`,
    "virtual:shell-client": `export const getLocalHealth = async () => ({ desktop: false });`,
    "virtual:shell-i18n": `export const useT = () => key => ({ "nav.main": "Main navigation", "search.title": "Search workspace", "nav.chat": "Chat", "nav.library": "Library", "nav.meeting": "Meeting", "nav.recall": "Recall", "nav.settings": "Settings", "logs.backendLogs": "Backend logs" }[key] || key);`,
  }
  server = await createServer({
    root, configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    resolve: { dedupe: ["react", "react-dom", "lucide-react"], alias: { "@": root + "src", "#edition": root + "src/edition" } },
    optimizeDeps: { noDiscovery: true },
    plugins: [{
      name: "shell-navigation-fixture",
      enforce: "pre",
      resolveId(id) {
        if (id in modules) return id
        if (/\/stores\/app-store(?:\.ts)?$/.test(id)) return "virtual:shell-store"
        if (/\/hooks\/use-update-check(?:\.ts)?$/.test(id)) return "virtual:shell-update"
        if (/\/i18n\/use-t(?:\.ts)?$/.test(id)) return "virtual:shell-i18n"
        if (id === "./update-dialog") return "virtual:shell-dialog"
        if (id === "./source-pill") return "virtual:shell-source"
        if (/\/api\/client(?:\.ts)?$/.test(id)) return "virtual:shell-client"
      },
      load(id) { return modules[id] },
    }],
  })
  ;({ Sidebar } = await server.ssrLoadModule("/src/components/layout/sidebar.tsx"))
  ;({ state } = await server.ssrLoadModule("virtual:shell-store"))
  ;({ updateState } = await server.ssrLoadModule("virtual:shell-update"))
})
after(async () => { await server?.close() })

function render(identity: NavIdentity | null, developerMode = false) {
  state.identity = identity
  state.developerMode = developerMode
  return renderToStaticMarkup(createElement(Sidebar))
}
function actions(html: string) {
  return [...html.matchAll(/<button\b[^>]*aria-label="([^"]+)"/g)].map(match => match[1])
}

test("icon navigation retains accessible names, current page and the settings entry", () => {
  const html = render(null)
  assert.deepEqual(actions(html), ["Search workspace", "Chat", "Library", "Meeting", "Settings"])
  assert.match(html, /aria-label="Library"[^>]*aria-current="page"/)
  assert.match(html, /<img[^>]*alt="SinkDuce"/)
})

test("module permissions still hide routes and the separate settings action", () => {
  const html = render({ modules: { chat: false, settings: false }, domains: { meeting: false } }, true)
  assert.deepEqual(actions(html), ["Search workspace", "Library", "Recall", "Backend logs"])
})

test("Recall requires both developer mode and module access; meeting follows its domain access", () => {
  const allowed = { modules: {}, domains: { meeting: true } }
  assert.deepEqual(actions(render(allowed, true)), ["Search workspace", "Chat", "Library", "Meeting", "Recall", "Settings", "Backend logs"])
  assert.deepEqual(actions(render(allowed, false)), ["Search workspace", "Chat", "Library", "Meeting", "Settings"])
  assert.deepEqual(actions(render({ modules: { recall: false, meeting: false }, domains: { meeting: true } }, true)), ["Search workspace", "Chat", "Library", "Meeting", "Settings", "Backend logs"])
})

test("available updates use the version button and ignored versions remain accessible there", () => {
  updateState.update = {
    currentVersion: "1.6.2",
    latestVersion: "v1.7.0",
    releaseUrl: "https://example.test/releases",
    releaseBody: "",
    downloadUrl: null,
    desktop: false,
  }
  try {
    const available = render(null)
    assert.match(available, /<button[^>]*class="sd-nav-item sd-nav-version is-update"[^>]*aria-haspopup="dialog"[^>]*aria-expanded="false"/)
    assert.doesNotMatch(available, /pm-shell-update-card/)

    updateState.ignored = true
    const ignored = render(null)
    assert.match(ignored, /<button[^>]*class="sd-nav-item sd-nav-version"[^>]*aria-haspopup="dialog"/)
  } finally {
    updateState.update = null
    updateState.ignored = false
  }
})
