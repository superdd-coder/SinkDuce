import en from "./en.json" with { type: "json" }
import zhCN from "./zh-CN.json" with { type: "json" }

export type Locale = "en" | "zh-CN"

export type Catalog = { [key: string]: string | Catalog }

export type Catalogs = {
  en: Catalog
  "zh-CN": Catalog
}

const catalogs: Catalogs = {
  en: en as Catalog,
  "zh-CN": zhCN as Catalog,
}

export function normalizeLocale(raw: unknown): Locale {
  return raw === "zh-CN" ? "zh-CN" : "en"
}

function lookup(node: unknown, key: string): string | undefined {
  let cur: unknown = node
  for (const part of key.split(".")) {
    if (!cur || typeof cur !== "object" || Array.isArray(cur)) return undefined
    cur = (cur as Catalog)[part]
  }
  return typeof cur === "string" ? cur : undefined
}

function interpolate(
  template: string,
  vars?: Record<string, string | number>,
): string {
  if (!vars) return template
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match,
  )
}

export function translate(
  dicts: Catalogs | Record<string, Catalog>,
  locale: string,
  key: string,
  vars?: Record<string, string | number>,
): string {
  const loc = normalizeLocale(locale)
  const active = lookup(dicts[loc], key)
  const fallback = loc === "en" ? undefined : lookup(dicts.en, key)
  const raw = active ?? fallback ?? key
  return interpolate(raw, vars)
}

export function t(
  locale: string,
  key: string,
  vars?: Record<string, string | number>,
): string {
  return translate(catalogs, locale, key, vars)
}

export function applyDocumentLang(locale: Locale): void {
  if (typeof document === "undefined") return
  document.documentElement.lang = locale
}

export function notifyDesktopLocale(locale: Locale): void {
  if (typeof window === "undefined") return
  const w = window as Window & {
    __TAURI_INTERNALS__?: { invoke?: (cmd: string, args?: unknown) => Promise<unknown> }
    __TAURI__?: { core?: { invoke?: (cmd: string, args?: unknown) => Promise<unknown> } }
  }
  const invoke = w.__TAURI_INTERNALS__?.invoke ?? w.__TAURI__?.core?.invoke
  if (typeof invoke !== "function") return
  void invoke("set_ui_locale", { locale }).catch(() => {})
}
