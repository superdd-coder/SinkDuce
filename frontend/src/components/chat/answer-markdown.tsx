import { createContext, createElement, memo, useContext } from "react"
import type { ComponentProps } from "react"
import ReactMarkdown from "react-markdown"
import type { Components, ExtraProps } from "react-markdown"
import remarkGfm from "remark-gfm"
import { useT } from "@/i18n/use-t"
import { isCodeFenceClosed, stabilizeAnswerMarkdown } from "@/lib/chat-answer-markdown"
import { SvgCodeBlock } from "./svg-code-block"
import { CitationLink } from "./citation-scope"
import { useCitations } from "./citation-context"
import { citationMarkdown } from "@/lib/chat-citations"
import type { ReportBlock } from "@/lib/chat-reports"
import {reviewMarkup} from '@/lib/report-review-markup'
import type {TextMark} from '@/lib/report-review'
import {inlineRevision} from '@/lib/report-inline-revision'
import type {PluggableList} from 'unified'

const MarkdownSource = createContext("")
const MarkdownHeadingAnchors = createContext(false)
const MarkdownImageRestriction = createContext(false)
const MarkdownReviewAnchors = createContext(false)
const MarkdownReport = createContext(false)
const MarkdownBlocks = createContext<ReportBlock[]>([])
const MarkdownLineOffset = createContext(0)
function useReviewAnchor(node: ExtraProps["node"]) {
  const enabled = useContext(MarkdownReviewAnchors)
  const blocks = useContext(MarkdownBlocks)
  const offset = useContext(MarkdownLineOffset)
  if (!enabled || !node?.position) return {}
  const first=blocks.find(block=>block.start_line<=node.position!.start.line+offset&&block.end_line>=node.position!.start.line+offset)
  return { "data-report-line": node.position.start.line+offset, "data-report-end": node.position.end.line+offset,
    ...(first ? {"data-report-block":first.id,"data-report-hash":first.hash} : {}) }
}

function MarkdownImage({ node, alt, ...props }: ComponentProps<"img"> & ExtraProps) {
  void node
  const restricted = useContext(MarkdownImageRestriction)
  return restricted ? <span className="pm-report-meta">{alt}</span> : <img {...props} alt={alt} />
}

function heading(tag: "h1" | "h2" | "h3" | "h4" | "h5" | "h6") {
  return function MarkdownHeading({ node, children, ...props }: ComponentProps<"h1"> & ExtraProps) {
    const anchored = useContext(MarkdownHeadingAnchors)
    const anchor = useReviewAnchor(node)
    const report = useContext(MarkdownReport)
    const source = useContext(MarkdownSource)
    const offset = useContext(MarkdownLineOffset)
    if (report && offset===0 && tag === "h1" && node?.position?.start.line === source.split("\n").findIndex((line) => line.trim()) + 1) {
      return <span {...anchor} className="chat-report-title-anchor" hidden>{children}</span>
    }
    return createElement(tag, { ...props, ...anchor, ...(anchored && node?.position ? { id: `heading-${node.position.start.line+offset}` } : {}) }, children)
  }
}

function MarkdownParagraph({ node, children, className, ...props }: ComponentProps<"p"> & ExtraProps) {
  const report = useContext(MarkdownReport)
  const [lead, following] = node?.children ?? []
  const standaloneLead = lead?.type === "element" && lead.tagName === "strong" &&
    lead.position && lead.position.start.line === lead.position.end.line &&
    following?.type === "text" && /^[\r\n]/.test(following.value) &&
    (following.value.trim().length > 0 || (node?.children.length ?? 0) > 2)
  const text = nodeText(node)
  const caption = report && /^(?:来源|数据来源|资料来源|Sources?|Data source)\s*[：:]/i.test(text)
  const allStrong = node?.children.length === 1 && lead?.type === "element" && lead.tagName === "strong"
  const deck = report && allStrong && (node?.position?.start.line ?? 100) <= 6
  const subheading = report && allStrong && !deck && text.length < 80 && !/[。！？.!?]$/.test(text)
  const paragraphClass = [className, standaloneLead && "chat-md-lead-paragraph", caption && "chat-report-caption", deck && "chat-report-deck", subheading && "chat-report-subheading"].filter(Boolean).join(" ") || undefined
  return <p {...props} {...useReviewAnchor(node)} className={paragraphClass}>{children}</p>
}

function nodeText(node: ExtraProps["node"]): string {
  if (!node) return ""
  return node.children.map((child) => child.type === "text" ? child.value : child.type === "element" ? nodeText(child) : "").join("")
}

function MarkdownTable({ node, children, ...props }: ComponentProps<"table"> & ExtraProps) {
  const anchor = useReviewAnchor(node)
  const t = useT()
  return (
    <div {...anchor} className="chat-md-table-wrap" role="region" aria-label={t("chat.markdownTable")} tabIndex={0}>
      <table {...props}>{children}</table>
    </div>
  )
}

function MarkdownTableHeader({ node, children, ...props }: ComponentProps<"th"> & ExtraProps) {
  void node
  return <th {...props}><div className="chat-md-table-cell">{children}</div></th>
}

function MarkdownTableCell({ node, children, ...props }: ComponentProps<"td"> & ExtraProps) {
  void node
  return <td {...props}><div className="chat-md-table-cell">{children}</div></td>
}
function MarkdownTableRow({node,children,...props}:ComponentProps<'tr'>&ExtraProps) {
  return <tr {...props} {...useReviewAnchor(node)}>{children}</tr>
}

function MarkdownPre({ node, children, ...props }: ComponentProps<"pre"> & ExtraProps) {
  const anchor = useReviewAnchor(node)
  const source = useContext(MarkdownSource)
  const report = useContext(MarkdownReport)
  const code = node?.children.find((child) => child.type === "element" && child.tagName === "code")
  const classes = code?.type === "element" ? code.properties.className : undefined
  const language = (Array.isArray(classes) ? classes : []).map(String)
    .find((value) => value.startsWith("language-"))?.slice(9) ?? ""
  if (code?.type === "element" && language.toLowerCase() === "svg") {
    const text = code.children.map((child) => child.type === "text" ? child.value : "").join("")
    const value = text.replace(/\n$/, "")
    const visual = <SvgCodeBlock source={value} complete={isCodeFenceClosed(source, node?.position, value)} report={report} />
    return Object.keys(anchor).length ? <div {...anchor}>{visual}</div> : visual
  }
  return (
    <div {...anchor} className="chat-md-code-block">
      {language && <div className="chat-md-code-header"><span>{language}</span></div>}
      <pre {...props}>{children}</pre>
    </div>
  )
}

// Stable component identities keep completed diagram previews mounted while
// later answer tokens arrive. The source context supplies real fence state.
const components: Components = { p: MarkdownParagraph, table: MarkdownTable, tr: MarkdownTableRow, th: MarkdownTableHeader, td: MarkdownTableCell, pre: MarkdownPre }
components.img = MarkdownImage
components.h1 = heading("h1")
components.h2 = heading("h2")
components.h3 = heading("h3")
components.h4 = heading("h4")
components.h5 = heading("h5")
components.h6 = heading("h6")
components.li = function MarkdownListItem({node, children, ...props}) {
  return <li {...props} {...useReviewAnchor(node)}>{children}</li>
}

components.a = function CitationAnchor({ href, children, node, ...props }) {
  void node
  if (href?.startsWith("#evidence-")) return <CitationLink id={href.slice(10)}>{children}</CitationLink>
  return <a {...props} href={href}>{children}</a>
}

export const AnswerMarkdown = memo(function AnswerMarkdown({ source, previousSource, streaming = false, headingAnchors = false, restrictImages = false, reviewAnchors = false, report = false, blocks = [], lineOffset=0, reviewMarks }: { source: string; previousSource?:string; streaming?: boolean; headingAnchors?: boolean; restrictImages?: boolean; reviewAnchors?: boolean; report?: boolean; blocks?: ReportBlock[]; lineOffset?:number;reviewMarks?:{kind:'added'|'removed';passages:TextMark[];all?:boolean} }) {
  const { records } = useCitations()
  const markdown = citationMarkdown(source, records, streaming)
  const plugins:PluggableList=[]
  if(previousSource!==undefined)plugins.push([inlineRevision,{before:citationMarkdown(previousSource,records)}])
  if(reviewMarks)plugins.push([reviewMarkup,reviewMarks])
  return (
    <MarkdownSource value={source}>
      <MarkdownHeadingAnchors value={headingAnchors}>
      <MarkdownImageRestriction value={restrictImages}>
      <MarkdownReviewAnchors value={reviewAnchors}>
      <MarkdownReport value={report}>
      <MarkdownBlocks value={blocks}>
      <MarkdownLineOffset value={lineOffset}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={plugins} components={components}>
        {streaming ? stabilizeAnswerMarkdown(markdown) : markdown}
      </ReactMarkdown>
      </MarkdownLineOffset>
      </MarkdownBlocks>
      </MarkdownReport>
      </MarkdownReviewAnchors>
      </MarkdownImageRestriction>
      </MarkdownHeadingAnchors>
    </MarkdownSource>
  )
})
