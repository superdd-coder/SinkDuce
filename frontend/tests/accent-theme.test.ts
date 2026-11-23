import assert from "node:assert/strict"
import { after, before, test } from "node:test"
import { readFile } from "node:fs/promises"
import { fileURLToPath } from "node:url"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { createServer } from "vite"
import { accentThemes, ACCENT_THEME_STORAGE_KEY, readAccentPreference } from "../src/lib/accent-theme.ts"

let server: Awaited<ReturnType<typeof createServer>>
let instance = 0
const original = Object.getOwnPropertyDescriptors(globalThis)
const requests: string[] = []

before(async () => {
  globalThis.fetch = async (input) => {
    requests.push(String(input))
    throw new Error("Display preferences must stay on the client")
  }
  const root = fileURLToPath(new URL("../", import.meta.url))
  server = await createServer({
    root, configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    resolve: { alias: { "@": root + "src", "#edition": root + "src/edition" } },
    optimizeDeps: { noDiscovery: true },
  })
})
after(async () => {
  await server?.close()
  await new Promise((resolve) => setTimeout(resolve, 550)) // existing chat preference debounce
  for (const key of ["fetch", "localStorage", "window", "document"]) {
    if (original[key]) Object.defineProperty(globalThis, key, original[key])
    else Reflect.deleteProperty(globalThis, key)
  }
})

function browser(values = new Map<string, string>()) {
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  }
  const page = Object.assign(new EventTarget(), { localStorage: storage })
  const properties = new Map<string, string>()
  const root = { dataset: {} as Record<string, string>, style: { setProperty: (key: string, value: string) => properties.set(key, value) } }
  for (const [key, value] of Object.entries({ localStorage: storage, window: page, document: { documentElement: root } })) {
    Object.defineProperty(globalThis, key, { value, configurable: true })
  }
  return { root, page, storage, properties }
}

async function loadStore() {
  return await server.ssrLoadModule(`/src/stores/app-store.ts?accent=${++instance}`) as typeof import("../src/stores/app-store.ts")
}

test("the host and a remote client can choose separate colors and restore them on reload", async () => {
  const hostValues = new Map<string, string>()
  const guestValues = new Map<string, string>()
  browser(hostValues)
  const host = (await loadStore()).useAppStore
  host.getState().setAccentTheme("mist")

  const { root } = browser(guestValues)
  const guest = await loadStore()
  guest.useAppStore.getState().setRemoteShellMode(true)
  const unbind = guest.bindAccentTheme()
  assert.equal(root.dataset.accentTheme, "pine")
  for (const theme of accentThemes) {
    guest.useAppStore.getState().setAccentTheme(theme)
    assert.equal(root.dataset.accentTheme, theme)
    assert.equal(readAccentPreference().theme, theme)
  }
  await new Promise((resolve) => setTimeout(resolve, 550))
  assert.deepEqual([...guestValues.keys()], [ACCENT_THEME_STORAGE_KEY], "changing appearance must not write chat preferences")
  unbind()
  assert.equal((await loadStore()).useAppStore.getState().accentPreference.theme, "custom")
  browser(hostValues)
  assert.equal((await loadStore()).useAppStore.getState().accentPreference.theme, "mist")
  assert.deepEqual(requests, [])
})

test("other tabs on the same client follow local storage changes, removal and clear", async () => {
  const values = new Map([[ACCENT_THEME_STORAGE_KEY, JSON.stringify({ theme: "black", customHue: 117 })]])
  const { root, page, storage, properties } = browser(values)
  const store = await loadStore()
  const unbind = store.bindAccentTheme()
  const notify = (key: string | null, storageArea: object) =>
    page.dispatchEvent(Object.assign(new Event("storage"), { key, storageArea }))

  assert.equal(root.dataset.accentTheme, "black")
  values.set(ACCENT_THEME_STORAGE_KEY, JSON.stringify({ theme: "custom", customHue: 84 }))
  notify("unrelated", storage)
  notify(ACCENT_THEME_STORAGE_KEY, {})
  assert.equal(root.dataset.accentTheme, "black")
  notify(ACCENT_THEME_STORAGE_KEY, storage)
  assert.equal(root.dataset.accentTheme, "custom")
  assert.equal(properties.get("--sd-custom-hue"), "84")
  assert.deepEqual(store.useAppStore.getState().accentPreference, { theme: "custom", customHue: 84 })
  for (const key of [ACCENT_THEME_STORAGE_KEY, null]) {
    store.useAppStore.getState().setAccentTheme("lilac")
    values.clear()
    notify(key, storage)
    assert.equal(root.dataset.accentTheme, "pine")
    assert.equal(values.has(ACCENT_THEME_STORAGE_KEY), false)
  }
  unbind()
})

test("the previous camel preset opens as black and preserves the remembered custom hue", async () => {
  const { root } = browser(new Map([[ACCENT_THEME_STORAGE_KEY, JSON.stringify({ theme: "camel", customHue: 117 })]]))
  const store = await loadStore()
  const unbind = store.bindAccentTheme()
  assert.equal(root.dataset.accentTheme, "black")
  assert.deepEqual(store.useAppStore.getState().accentPreference, { theme: "black", customHue: 117 })
  store.useAppStore.getState().setAccentTheme("custom")
  assert.deepEqual(readAccentPreference(), { theme: "custom", customHue: 117 })
  unbind()
})

test("invalid preferences fall back to pine and blocked storage still permits live changes", async () => {
  const values = new Map<string, string>()
  const { root, storage } = browser(values)
  for (const invalid of ["{", '"unknown"', "42", "null", '["black"]']) {
    values.set(ACCENT_THEME_STORAGE_KEY, invalid)
    assert.deepEqual(readAccentPreference(), { theme: "pine", customHue: 160 })
  }
  storage.getItem = storage.setItem = () => { throw new Error("Storage is blocked") }
  const store = await loadStore()
  const unbind = store.bindAccentTheme()
  store.useAppStore.getState().setAccentTheme("clay")
  assert.equal(root.dataset.accentTheme, "clay")
  unbind()
})

test("custom hue stays within range and is remembered when switching between presets and custom", async () => {
  const { properties, storage } = browser()
  const store = await loadStore()
  const unbind = store.bindAccentTheme()
  for (const [input, expected] of [[-1, 0], [360, 359], [49.6, 50], [NaN, 160], [207, 207]]) {
    store.useAppStore.getState().setAccentTheme("custom", input)
    assert.equal(properties.get("--sd-custom-hue"), String(expected))
    assert.equal(readAccentPreference().customHue, expected)
  }
  store.useAppStore.getState().setAccentTheme("rose")
  const restored = (await loadStore()).useAppStore
  restored.getState().setAccentTheme("custom")
  assert.deepEqual(readAccentPreference(), { theme: "custom", customHue: 207 })
  storage.setItem(ACCENT_THEME_STORAGE_KEY, JSON.stringify({ theme: "custom", customHue: "invalid" }))
  assert.equal(readAccentPreference().customHue, 160)
  unbind()
})

test("every picker choice has a named radio, a palette and a label in both languages", async () => {
  browser()
  const { AccentThemeCard } = await server.ssrLoadModule("/src/components/llm-provider/accent-theme-card.tsx")
  const { t } = await server.ssrLoadModule("/src/i18n/index.ts")
  const html = renderToStaticMarkup(createElement(AccentThemeCard))
  const css = await readFile(new URL("../src/styles/foundation.css", import.meta.url), "utf8")
  assert.match(html, /role="radiogroup" aria-labelledby=/)
  assert.equal((html.match(/type="radio"/g) ?? []).length, accentThemes.length)
  for (const theme of accentThemes) {
    const key = `settings.colorThemes.${theme}`
    assert.ok(html.includes(`value="${theme}"`) && html.includes(t("en", key)))
    assert.ok(css.includes(`[data-accent-theme="${theme}"]`))
    assert.notEqual(t("zh-CN", key), t("en", key))
    assert.notEqual(t("en", key), key)
  }
})

test("the black palette gives the canvas, stage and reading cards distinct surfaces in both appearances", async () => {
  const css = await readFile(new URL("../src/styles/monochrome.css", import.meta.url), "utf8")
  for (const selector of [':root[data-accent-theme="black"]', ':root.dark[data-accent-theme="black"]']) {
    const block = css.split(selector)[1]?.match(/^[^{]*\{([^}]+)\}/)?.[1] ?? ""
    const tokens = new Map([...block.matchAll(/(--sd-[\w-]+):\s*([^;]+);/g)].map(([, key, value]) => [key, value.trim()]))
    const fills = ["--sd-canvas", "--sd-stage-base", "--sd-surface-raised"].map(key => {
      const color = tokens.get(key) ?? ""
      assert.match(color, /^#[a-f\d]{6}$/i, `${selector} needs an opaque ${key}`)
      return color.toLowerCase()
    })
    assert.equal(new Set(fills).size, 3, `${selector} needs three distinct layers`)
    for (const key of ["--sd-shadow-panel", "--sd-shadow-control"]) {
      assert.match(tokens.get(key) ?? "", /\b\d+px\b/, `${selector} needs visible elevation for ${key}`)
    }
  }
})
