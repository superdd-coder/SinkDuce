import { unified } from "unified"
import remarkParse from "remark-parse"
import { trimEmphasisInteriorSpaces } from "./md-emphasis.ts"

interface SourcePosition {
  start: { line: number; column: number; offset?: number }
  end: { line: number; column: number; offset?: number }
}

interface MarkdownNode {
  type: string
  lang?: string
  value?: string
  position?: SourcePosition
  children?: MarkdownNode[]
}

interface Fence {
  marker: string
  prefix: string
  openingLine: string
}

export interface PreparedAnswerHead {
  raw: string
  length: number
  cleaned: string
  tailPrefix: string
}

const parser = unified().use(remarkParse)
const CITE_MARKER_RE =
  /\[(?:n|ref)\s*:\s*[^\]]*\]|\[\d+\s*:\s*(?:stt_)?\d+\s*[-–]\s*(?:stt_)?\d+\]/gi
const CITE_MARKER_PARTIAL_RE =
  /\[(?:(?:n|ref)\s*:\s*[^\]]*|\d+\s*:\s*(?:stt_)?\d+\s*[-–]\s*(?:stt_)?\d*)$/i

function protectedNodes(source: string, tree = parser.parse(source) as MarkdownNode): MarkdownNode[] {
  const nodes: MarkdownNode[] = []
  const visit = (node: MarkdownNode) => {
    if (node.type === "code" || node.type === "inlineCode") nodes.push(node)
    else node.children?.forEach(visit)
  }
  visit(tree)
  return nodes
}

/** CommonMark positions protect code in lists and quotes as well as root fences. */
function mapProse(source: string, transform: (value: string) => string, nodes = protectedNodes(source)): string {
  let cursor = 0
  let output = ""
  for (const node of nodes) {
    const start = node.position?.start.offset
    const end = node.position?.end.offset
    if (start === undefined || end === undefined) continue
    output += transform(source.slice(cursor, start)) + source.slice(start, end)
    cursor = end
  }
  return output + transform(source.slice(cursor))
}
export {mapProse as mapMarkdownProse}

function fenceAt(source: string, position?: SourcePosition): Fence | null {
  if (!position) return null
  const openingLine = source.split(/\r\n|\n|\r/)[position.start.line - 1]
  if (openingLine === undefined) return null
  const prefix = openingLine.slice(0, position.start.column - 1)
  const match = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(openingLine.slice(prefix.length))
  if (!match || (match[1][0] === "`" && match[2].includes("`"))) return null
  return { marker: match[1], prefix, openingLine }
}

/** A real closing line is excluded from the parser's code value. */
export function isCodeFenceClosed(source: string, position?: SourcePosition, codeValue?: string): boolean {
  if (!position || position.end.line <= position.start.line) return false
  const fence = fenceAt(source, position)
  const endLine = source.split(/\r\n|\n|\r/)[position.end.line - 1]
  if (!fence || endLine === undefined) return false
  const closing = /(`{3,}|~{3,})[ \t]*$/.exec(endLine)
  if (!closing || closing[1][0] !== fence.marker[0] || closing[1].length < fence.marker.length) return false
  const value = codeValue ?? protectedNodes(source).find((node) =>
    node.type === "code" && node.position?.start.line === position.start.line &&
    node.position.start.column === position.start.column,
  )?.value
  if (value === undefined) return false
  const bodyLines = value === "" ? 0 : value.split(/\r\n|\n|\r/).length
  return bodyLines < position.end.line - position.start.line
}

function openEndFence(source: string, nodes: MarkdownNode[]): Fence | null {
  const end = source.trimEnd().length
  const node = nodes.findLast((candidate) => candidate.type === "code" &&
    (candidate.position?.end.offset ?? -1) >= end,
  )
  return node && !isCodeFenceClosed(source, node.position, node.value) ? fenceAt(source, node.position) : null
}

export function cleanAnswerMarkdown(source: string): string {
  return mapProse(source, (value) => trimEmphasisInteriorSpaces(value.replace(CITE_MARKER_RE, "")))
}

/** Keep unfinished inline code/citations with the live tail across a newline. */
function pendingProseLine(source: string, tree: MarkdownNode, nodes: MarkdownNode[]): number {
  let paragraph: MarkdownNode | undefined
  const end = source.trimEnd().length
  const visit = (node: MarkdownNode) => {
    if (node.type === "paragraph" && (node.position?.end.offset ?? -1) >= end) paragraph = node
    node.children?.forEach(visit)
  }
  visit(tree)
  const start = paragraph?.position?.start.offset
  if (start === undefined) return source.length
  let pending = source.length
  let cursor = start
  const inspect = (from: number, to: number) => {
    const prose = source.slice(from, to)
    for (const match of prose.matchAll(/`+/g)) {
      const index = from + match.index
      const escapes = /\\*$/.exec(source.slice(from, index))![0].length
      if (escapes % 2 === 0) pending = Math.min(pending, index)
    }
    if (to === source.length) {
      const citation = CITE_MARKER_PARTIAL_RE.exec(prose)
      if (citation) pending = Math.min(pending, from + citation.index)
    }
  }
  for (const node of nodes) {
    const nodeStart = node.position?.start.offset ?? 0
    const nodeEnd = node.position?.end.offset ?? 0
    if (nodeEnd <= start) continue
    inspect(cursor, nodeStart)
    cursor = nodeEnd
  }
  inspect(cursor, source.length)
  return pending === source.length ? pending : source.lastIndexOf("\n", pending - 1) + 1
}

/** Prepared only when completed lines change, rather than on every new token. */
export function prepareAnswerStreamHead(raw: string): PreparedAnswerHead {
  const tree = parser.parse(raw) as MarkdownNode
  const nodes = protectedNodes(raw, tree)
  const length = pendingProseLine(raw, tree, nodes)
  const safeHead = raw.slice(0, length)
  const safeNodes = length === raw.length ? nodes : protectedNodes(safeHead)
  const fence = openEndFence(safeHead, safeNodes)
  return {
    raw,
    length,
    cleaned: mapProse(safeHead, (value) => value.replace(CITE_MARKER_RE, ""), safeNodes),
    tailPrefix: fence ? `${fence.openingLine}\n` : "",
  }
}

/** SVG source belongs to the throttled Markdown card, never the live plain-text tail. */
function visibleStreamTail(source: string, prefixLength: number): string {
  // Parse the live suffix once, retaining CommonMark's list/quote and ordinary-code context.
  const nodes = protectedNodes(source)
  const clean = (value: string) => value.replace(CITE_MARKER_RE, "").replace(CITE_MARKER_PARTIAL_RE, "")
  let cursor = prefixLength
  let output = ""
  for (const node of nodes) {
    const nodeStart = node.position?.start.offset
    const nodeEnd = node.position?.end.offset
    if (nodeStart === undefined || nodeEnd === undefined || nodeEnd <= cursor) continue
    const svg = node.type === "code" && node.lang?.toLowerCase() === "svg"
    // Container markers and the closing line's newline are part of the hidden fence, too.
    const start = svg ? source.lastIndexOf("\n", nodeStart - 1) + 1 : nodeStart
    let end = nodeEnd
    if (svg && source[end] === "\r") end++
    if (svg && source[end] === "\n") end++
    output += clean(source.slice(cursor, Math.max(cursor, start)))
    if (!svg) output += source.slice(Math.max(cursor, start), end)
    cursor = end
  }
  return output + clean(source.slice(cursor))
}

export function splitAnswerStream(content: string, rawHead: string, prepared = prepareAnswerStreamHead(rawHead)): { head: string; tail: string } {
  const current = prepared.raw === rawHead ? prepared : prepareAnswerStreamHead(rawHead)
  const hasHead = !!rawHead && content.startsWith(rawHead)
  const head = hasHead ? current.cleaned : ""
  const prefix = hasHead ? current.tailPrefix : ""
  const rawTail = hasHead ? content.slice(current.length) : content
  const tail = visibleStreamTail(prefix + rawTail, prefix.length)
  return { head, tail }
}

/** Close an open fence for display only; real source still controls SVG readiness. */
export function stabilizeAnswerMarkdown(source: string): string {
  const display = mapProse(source, trimEmphasisInteriorSpaces)
  const fence = openEndFence(display, protectedNodes(display))
  // Continue the container indentation while retaining blockquote markers.
  const continuation = fence?.prefix.replace(/(?:[-+*]|\d+[.)])(?=[ \t])/g, (marker) => " ".repeat(marker.length))
  return fence ? `${display}\n${continuation}${fence.marker}` : display
}
