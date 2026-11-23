export const accentThemes = ["pine", "black", "mist", "lilac", "rose", "clay", "custom"] as const
export type AccentTheme = (typeof accentThemes)[number]
export type AccentPreference = { theme: AccentTheme; customHue: number }

export const ACCENT_THEME_STORAGE_KEY = "rag_accentTheme"
const defaultPreference: AccentPreference = { theme: "pine", customHue: 160 }

export function normalizeAccentHue(hue: unknown): number {
  return typeof hue === "number" && Number.isFinite(hue)
    ? Math.min(359, Math.max(0, Math.round(hue)))
    : defaultPreference.customHue
}

export function readAccentPreference(): AccentPreference {
  try {
    const saved: { theme?: unknown; customHue?: unknown } | null =
      JSON.parse(localStorage.getItem(ACCENT_THEME_STORAGE_KEY) ?? "null")
    const selected = saved?.theme === "camel" ? "black" : saved?.theme
    return {
      theme: accentThemes.find((theme) => theme === selected) ?? "pine",
      customHue: normalizeAccentHue(saved?.customHue),
    }
  } catch {
    return defaultPreference
  }
}
