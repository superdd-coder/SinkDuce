import { unified } from "unified"
import remarkParse from "remark-parse"
import remarkGfm from "remark-gfm"
import remarkRehype from "remark-rehype"
import rehypeRaw from "rehype-raw"
import rehypeStringify from "rehype-stringify"
import { rehypeChunkPolicy } from "./chunk-markdown-policy"

const documentProcessor = unified()
  .use(remarkParse)
  .use(remarkGfm)
  .use(remarkRehype, { allowDangerousHtml: true })
  .use(rehypeRaw)
  .use(rehypeChunkPolicy)
  .use(rehypeStringify)

export function renderUploadedMarkdown(source: string): string {
  return String(documentProcessor.processSync(source))
}
