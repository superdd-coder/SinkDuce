/**
 * Meeting Summary export helpers.
 * Output is cleaned for humans: speaker names resolved, sentence refs stripped.
 * Never write this cleaned string back to on-disk section .md.
 */
import { unified } from "unified"
import remarkParse from "remark-parse"
import remarkGfm from "remark-gfm"
import remarkRehype from "remark-rehype"
import rehypeStringify from "rehype-stringify"
import { prepareMeetingSummaryForNote } from "@/lib/meeting-summary-display"

/** Resolve speakers + strip [stt_…] / priority (same cleaning as note distill). */
export function prepareMeetingSummaryForExport(
  md: string,
  speakerNames?: Record<string, string> | null,
): string {
  return prepareMeetingSummaryForNote(md || "", speakerNames)
}

export function safeExportBasename(parts: (string | null | undefined)[]): string {
  const raw = parts
    .map((p) => (p || "").trim())
    .filter(Boolean)
    .join(" - ")
  const cleaned = (raw || "meeting-summary")
    .replace(/[\\/:*?"<>|]+/g, "-")
    .replace(/\s+/g, " ")
    .trim()
  return cleaned.slice(0, 120) || "meeting-summary"
}

/** Trigger a browser download of a UTF-8 text/markdown file. */
export function downloadMarkdownFile(filename: string, markdown: string): void {
  const name = filename.endsWith(".md") ? filename : `${filename}.md`
  const blob = new Blob([markdown || ""], {
    type: "text/markdown;charset=utf-8",
  })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = name
  a.rel = "noopener"
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

function markdownToHtml(md: string): string {
  try {
    return String(
      unified()
        .use(remarkParse)
        .use(remarkGfm)
        .use(remarkRehype)
        .use(rehypeStringify)
        .processSync(md || ""),
    )
  } catch {
    return `<pre>${(md || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")}</pre>`
  }
}

/**
 * Open a print window with cleaned HTML so the user can “Save as PDF”.
 * No PDF library — browser print pipeline (zero extra deps).
 */
export function exportSummaryAsPdf(opts: {
  title: string
  markdown: string
  speakerNames?: Record<string, string> | null
}): void {
  const cleaned = prepareMeetingSummaryForExport(opts.markdown, opts.speakerNames)
  const bodyHtml = markdownToHtml(cleaned)
  const title = (opts.title || "Meeting Summary").replace(/</g, "&lt;")
  const doc = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>${title}</title>
  <style>
    @page { margin: 18mm 16mm; }
    body {
      font-family: "Newsreader", Georgia, "Times New Roman", serif;
      font-size: 12pt;
      line-height: 1.55;
      color: #1a1c1a;
      max-width: 42rem;
      margin: 0 auto;
      padding: 8px 4px 24px;
    }
    h1 { font-size: 1.45rem; font-weight: 600; margin: 0 0 1.1em; letter-spacing: -0.02em; }
    h2 { font-size: 1.15rem; font-weight: 600; margin: 1.35em 0 0.5em; }
    h3 { font-size: 1.02rem; font-weight: 600; margin: 1.15em 0 0.4em; }
    p { margin: 0 0 0.75em; }
    ul, ol { margin: 0.35em 0 0.9em; padding-left: 1.35em; }
    li { margin: 0.25em 0; }
    strong { font-weight: 650; }
    em { font-style: italic; }
    code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.9em; }
    blockquote {
      margin: 0.8em 0;
      padding-left: 0.9em;
      border-left: 2px solid #c5cac5;
      color: #4a504a;
    }
    hr { border: none; border-top: 1px solid #e4e6e2; margin: 1.4em 0; }
    .export-hint {
      font-family: system-ui, sans-serif;
      font-size: 11px;
      color: #6a706a;
      margin: 0 0 1.25em;
      padding: 8px 10px;
      border-radius: 8px;
      background: #f4f5f3;
    }
    @media print {
      body { padding: 0; max-width: none; }
      .export-hint { display: none; }
    }
  </style>
</head>
<body>
  <p class="export-hint">In the print dialog, set destination to <strong>Save as PDF</strong>, then save.</p>
  <h1>${title}</h1>
  ${bodyHtml}
  <script>
    window.onload = function () {
      setTimeout(function () {
        window.focus();
        window.print();
      }, 120);
    };
  <\/script>
</body>
</html>`

  const w = window.open("", "_blank")
  if (!w) {
    throw new Error("Pop-up blocked — allow pop-ups to export PDF")
  }
  w.document.open()
  w.document.write(doc)
  w.document.close()
}

export function exportSummaryMarkdown(opts: {
  filenameBase: string
  markdown: string
  speakerNames?: Record<string, string> | null
}): void {
  const cleaned = prepareMeetingSummaryForExport(opts.markdown, opts.speakerNames)
  downloadMarkdownFile(opts.filenameBase, cleaned)
}
