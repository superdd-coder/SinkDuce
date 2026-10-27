import type { ExtraProps } from "react-markdown"

type Element = NonNullable<ExtraProps["node"]>
type Parent = Pick<Element, "children">

const DOCUMENT_TAGS = new Set([
  "a", "b", "blockquote", "br", "caption", "code", "col", "colgroup", "dd", "del",
  "div", "dl", "dt", "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5",
  "h6", "hr", "i", "img", "input", "kbd", "li", "mark", "ol", "p", "pre", "s",
  "small", "span", "strong", "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
])

const DOCUMENT_ATTRIBUTES: Record<string, readonly string[]> = {
  a: ["href"],
  img: ["src", "alt", "dataImageId", "dataOcrText", "dataVisualDesc"],
  input: ["type", "checked"],
  ol: ["start"],
  td: ["colSpan", "rowSpan", "align"],
  th: ["colSpan", "rowSpan", "align"],
  col: ["span"],
  colgroup: ["span"],
}

/**
 * Stored chunks are untrusted document content. Keep only passive document
 * attributes after HTML parsing; ReactMarkdown also applies its safe URL
 * transform before creating React elements.
 */
function keepChunkElement(element: Element): boolean {
  if (!DOCUMENT_TAGS.has(element.tagName)) return false
  const original = element.properties
  if (element.tagName === "input" && original.type !== "checkbox") return false

  const properties: typeof original = {}
  for (const key of ["title", ...(DOCUMENT_ATTRIBUTES[element.tagName] ?? [])]) {
    const value = original[key]
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      properties[key] = value
    }
  }
  if (element.tagName === "input") properties.disabled = true
  if (element.tagName === "code" && Array.isArray(original.className)) {
    properties.className = original.className.filter((name) => typeof name === "string" && /^language-[\w+-]+$/.test(name))
  }
  // The JSX adapter turns cell alignment into CSS. Accept only these values;
  // source-provided style, classes, IDs and event handlers are never retained.
  if (!["left", "right", "center"].includes(String(properties.align))) delete properties.align
  for (const key of ["colSpan", "rowSpan", "span", "start"]) {
    if (!(key in properties)) continue
    const value = Number(properties[key])
    if (Number.isSafeInteger(value) && (key === "start" || (value > 0 && value <= 1000))) properties[key] = value
    else delete properties[key]
  }
  element.properties = properties
  return true
}

/** Run immediately after rehypeRaw so parsed HTML cannot bypass the policy. */
export function rehypeChunkPolicy() {
  return (tree: unknown) => {
    const cleanChildren = (parent: Parent) => {
      for (let i = 0; i < parent.children.length;) {
        const node = parent.children[i]
        if (node.type === "element") {
          if (!keepChunkElement(node)) {
            parent.children.splice(i, 1)
            continue
          }
          cleanChildren(node)
        }
        i++
      }
    }
    cleanChildren(tree as Parent)
  }
}
