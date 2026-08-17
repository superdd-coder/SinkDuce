export const DEFAULT_LANGUAGE_HINTS = ["auto"]

/**
 * Toggle language hint selection.
 * - "auto" is exclusive
 * - maxHints === 1 behaves as radio (Fun-ASR / Whisper / local ONNX)
 * - maxHints > 1 is multi-select up to the cap (Qwen-3 ASR)
 */
export function toggleLanguageHint(
  selected: string[],
  code: string,
  maxHints = 1,
): string[] {
  const max = Math.max(1, maxHints)
  if (code === "auto") return ["auto"]

  const isOn = selected.includes(code)
  if (isOn) {
    const next = selected.filter((c) => c !== code && c !== "auto")
    return next.length === 0 ? ["auto"] : next
  }

  const withoutAuto = selected.filter((c) => c !== "auto")
  if (withoutAuto.includes(code)) return withoutAuto
  if (max <= 1) return [code]
  if (withoutAuto.length >= max) return withoutAuto
  return [...withoutAuto, code]
}

/** Keep Auto, or trim concrete languages to the active model cap. */
export function clipLanguageHints(selected: string[], maxHints: number): string[] {
  const max = Math.max(1, maxHints)
  const langs = selected.filter((c) => c && c !== "auto")
  if (langs.length === 0) return ["auto"]
  return langs.slice(0, max)
}
