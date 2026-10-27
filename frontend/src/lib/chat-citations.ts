export interface Evidence {
  id: string
  kind: string
  title: string
  quote: string
  source: Record<string, unknown>
  location: Record<string, unknown>
}

const markers = /```[\s\S]*?(?:```|$)|`[^`\n]*`|\[cite:([^\]\r\n]{0,200})\]/g

/** Backend offsets count Unicode code points; browser ranges count UTF-16 units. */
export function citationTextRange(text: string, quote: string, pythonOffset?: unknown): { offset: number; length: number } | undefined {
  if (!quote) return undefined
  if (typeof pythonOffset === "number" && Number.isInteger(pythonOffset) && pythonOffset >= 0 && pythonOffset <= text.length) {
    let offset = 0
    for (let count = 0; count < pythonOffset && offset < text.length; count++) offset += text.codePointAt(offset)! > 0xffff ? 2 : 1
    if (text.startsWith(quote, offset)) return { offset, length: quote.length }
  }
  const offset = text.indexOf(quote)
  return offset >= 0 ? { offset, length: quote.length } : undefined
}

export function citationOrder(source: string): string[] {
  return [...new Set(Array.from(source.matchAll(markers), (match) => match[1]).filter((id): id is string => !!id))]
}

export function citationMarkdown(source: string, evidence: Evidence[], streaming = false): string {
  const valid = new Set(evidence.map((e) => e.id))
  const numbers = new Map(citationOrder(source).filter((id) => valid.has(id)).map((id, index) => [id, index + 1]))
  let result = source.replace(markers, (text, id: string | undefined) => id !== undefined
    ? valid.has(id) ? `[${numbers.get(id)}](#evidence-${id})` : "〔?〕"
    : text)
  if (streaming) result = result.replace(/\[(?:c(?:i(?:t(?:e(?::[A-Za-z0-9_-]*)?)?)?)?)?$/, "")
  return result
}

export function normalizeEvidence(value: unknown): Evidence[] {
  if (!Array.isArray(value)) return []
  return value.filter((e): e is Evidence => !!e && typeof e === "object" && typeof e.id === "string" &&
    typeof e.title === "string" && typeof e.quote === "string" && !!e.source && !!e.location)
}
