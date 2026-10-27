import { t, type Locale } from "./index"

let getLocale: () => Locale = () => "en"

export function bindLocaleGetter(fn: () => Locale): void {
  getLocale = fn
}

export function tr(
  key: string,
  vars?: Record<string, string | number>,
): string {
  return t(getLocale(), key, vars)
}
