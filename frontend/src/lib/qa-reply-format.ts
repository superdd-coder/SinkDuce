import { unified } from "unified"
import remarkParse from "remark-parse"
import type { JSONContent } from "@tiptap/core"

export interface ReplyRun { text: string; bold: boolean; italic: boolean }
interface InlineNode {
  type: string; value?: string; children?: InlineNode[]
  position?: { start: { offset?: number }; end: { offset?: number } }
}
const parser = unified().use(remarkParse).data("micromarkExtensions", [{ disable: { null: [
  "headingAtx", "setextUnderline", "list", "blockQuote", "codeFenced", "codeIndented",
  "thematicBreak", "htmlFlow", "htmlText", "autolink", "definition", "labelStartLink", "labelStartImage", "codeText",
] } }])

/** Only emphasis becomes formatting. HTML, links and block syntax remain inert text. */
export function replyRuns(body: string): ReplyRun[] {
  const runs: ReplyRun[] = []
  function append(text: string, bold = false, italic = false) {
    if (!text) return
    const last = runs.at(-1)
    if (last && last.bold === bold && last.italic === italic) last.text += text
    else runs.push({ text, bold, italic })
  }
  function visit(node: InlineNode, bold = false, italic = false) {
    const start = node.position?.start.offset ?? 0
    const end = node.position?.end.offset ?? body.length
    if (node.type === "text") append(node.value ?? "", bold, italic)
    else if (node.type === "break") append("\n", bold, italic)
    else if (["root", "paragraph", "strong", "emphasis"].includes(node.type)) {
      let cursor = start
      for (const child of node.children ?? []) {
        const childStart = child.position?.start.offset ?? cursor
        if (node.type === "root" || node.type === "paragraph") append(body.slice(cursor, childStart), bold, italic)
        visit(child, bold || node.type === "strong", italic || node.type === "emphasis")
        cursor = child.position?.end.offset ?? cursor
      }
      if (node.type === "root" || node.type === "paragraph") append(body.slice(cursor, end), bold, italic)
    } else append(body.slice(start, end), bold, italic)
  }
  visit(parser.parse(body))
  return runs
}

export function replyDocument(body: string): JSONContent {
  const content: JSONContent[] = []
  for (const run of replyRuns(body)) {
    const marks = [...(run.bold ? [{ type: "bold" }] : []), ...(run.italic ? [{ type: "italic" }] : [])]
    run.text.split("\n").forEach((text, i) => {
      if (i) content.push({ type: "hardBreak" })
      if (text) content.push({ type: "text", text, marks })
    })
  }
  return { type: "doc", content: [{ type: "paragraph", content }] }
}
