import { useState, type ReactNode } from "react"
import type { ChunkDetail } from "@/api/client"
import { listImageFenceFields } from "@/lib/utils"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useT } from "@/i18n/use-t"
import { tr } from "@/i18n/tr"

const HIDDEN = new Set(["text", "images"])

const PRIORITY = [
  "chunk_index",
  "chunk_type",
  "sheet_name",
  "heading_path",
  "section_label",
  "label",
  "context",
  "summary",
  "page_number",
  "slide_number",
  "char_offset",
  "file_type",
  "source_label",
  "source",
  "file_id",
  "version_id",
  "parent_id",
  "chunk_id",
  "id",
  "total_chunks",
  "meeting_id",
  "meeting_date",
  "note_id",
  "ingested_at",
  "created_by",
  "archived",
  "is_current",
  "collection",
]

const LABELS: Record<string, string> = {
  chunk_index: "Index",
  chunk_type: "Type",
  sheet_name: "Sheet",
  heading_path: "Heading",
  section_label: "Section",
  label: "Label",
  context: "Context",
  summary: "Summary",
  page_number: "Page",
  slide_number: "Slide",
  char_offset: "Offset",
  file_type: "File type",
  source: "Source key",
  source_label: "Source",
  file_id: "File",
  version_id: "Version",
  parent_id: "Parent",
  chunk_id: "Chunk id",
  id: "Point",
  total_chunks: "Total",
  meeting_id: "Meeting",
  meeting_date: "Meeting date",
  note_id: "Note",
  ingested_at: "Ingested",
  created_by: "Created by",
  archived: "Archived",
  is_current: "Current",
  collection: "Collection",
}

const MULTILINE = new Set(["context", "summary", "heading_path"])

export type ChunkMetaRow = {
  key: string
  label: string
  value: string
  multiline?: boolean
}

function isEmpty(value: unknown): boolean {
  if (value == null) return true
  if (value === "") return true
  if (Array.isArray(value) && value.length === 0) return true
  return false
}

function formatMetaValue(key: string, value: unknown): string {
  if (typeof value === "boolean") return value ? tr("common.yes") : tr("common.no")
  if (key === "ingested_at" && typeof value === "number" && Number.isFinite(value)) {
    const ms = value > 1e12 ? value : value * 1000
    const date = new Date(ms)
    if (!Number.isNaN(date.getTime())) {
      return date.toLocaleString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    }
  }
  if (typeof value === "number" || typeof value === "string") return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function humanizeKey(key: string): string {
  if (LABELS[key]) return LABELS[key]
  return key.replace(/_/g, " ").replace(/\b\w/g, (ch) => ch.toUpperCase())
}

type ImageInspect = {
  imageId: string
  ocr: string
  description: string
  inVector: boolean
}

function collectImageInspect(chunk: ChunkDetail): ImageInspect[] {
  const byId = new Map<string, ImageInspect>()
  const metaImgs = chunk.images
  if (Array.isArray(metaImgs)) {
    for (const raw of metaImgs) {
      if (!raw || typeof raw !== "object") continue
      const rec = raw as Record<string, unknown>
      const imageId = String(rec.image_id || rec.imageId || "")
      if (!imageId) continue
      const ocr = String(rec.ocr_text || rec.ocrText || "").trim()
      const description = String(rec.description || "").trim()
      byId.set(imageId, {
        imageId,
        ocr,
        description,
        inVector: !!(ocr || description),
      })
    }
  }
  for (const f of listImageFenceFields(chunk.text)) {
    const prev = byId.get(f.imageId)
    const ocr = (f.ocrText || prev?.ocr || "").trim()
    const description = (f.description || prev?.description || "").trim()
    byId.set(f.imageId, {
      imageId: f.imageId,
      ocr,
      description,
      inVector: !!(ocr || description),
    })
  }
  return [...byId.values()]
}

export function collectChunkMeta(chunk: ChunkDetail): ChunkMetaRow[] {
  const keys = new Set([
    ...PRIORITY.filter((key) => key in chunk && !HIDDEN.has(key)),
    ...Object.keys(chunk)
      .filter((key) => !HIDDEN.has(key) && !PRIORITY.includes(key) && !key.startsWith("_"))
      .sort(),
  ])
  const rows: ChunkMetaRow[] = []
  for (const key of keys) {
    const raw = chunk[key]
    if (isEmpty(raw)) continue
    const value = formatMetaValue(key, raw)
    if (!value) continue
    rows.push({
      key,
      label: humanizeKey(key),
      value: value.length > 800 ? `${value.slice(0, 800)}…` : value,
      multiline: MULTILINE.has(key) || value.length > 72,
    })
  }
  return rows
}

export function ChunkInspect({
  chunk,
  children,
  className,
}: {
  chunk: ChunkDetail
  children: ReactNode
  className?: string
}) {
  const t = useT()
  const [armed, setArmed] = useState(false)
  if (!armed) {
    return (
      <div
        className={className}
        data-chunk-index={chunk.chunk_index}
        onPointerEnter={() => setArmed(true)}
        onFocusCapture={() => setArmed(true)}
      >
        {children}
      </div>
    )
  }

  const rows = collectChunkMeta(chunk)
  const images = collectImageInspect(chunk)
  const kind = String(chunk.chunk_type || "chunk")
  const index = chunk.chunk_index ?? "—"
  const empty = rows.length === 0 && images.length === 0

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <div
            className={className}
            data-chunk-index={chunk.chunk_index}
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent
        side="left"
        align="start"
        sideOffset={12}
        showArrow={false}
        positionerClassName="z-[80]"
        className="pm-chunk-inspect-pop"
      >
        <div className="pm-chunk-inspect">
          <header className="pm-chunk-inspect-head">
            <span className="pm-chunk-inspect-kicker">{t("fileMgmt.chunkMeta")}</span>
            <p className="pm-chunk-inspect-title">
              #{index}
              <span className="pm-chunk-inspect-kind">{kind}</span>
            </p>
          </header>
          {empty ? (
            <p className="pm-chunk-inspect-empty">{t("fileMgmt.noChunkMeta")}</p>
          ) : (
            <dl className="pm-chunk-inspect-list">
              {rows.map((row) => (
                <div
                  key={row.key}
                  className={
                    row.multiline
                      ? "pm-chunk-inspect-row is-block"
                      : "pm-chunk-inspect-row"
                  }
                >
                  <dt>{row.label}</dt>
                  <dd>{row.value}</dd>
                </div>
              ))}
              {images.map((img) => (
                <div
                  key={img.imageId}
                  className="pm-chunk-inspect-row is-block"
                >
                  <dt>{t("fileMgmt.image")}</dt>
                  <dd>
                    <div className="font-mono">{img.imageId.slice(0, 12)}</div>
                    <div>OCR: {img.ocr || "—"}</div>
                    <div>Description: {img.description || "—"}</div>
                    <div>
                      In vector:{" "}
                      {img.inVector
                        ? [img.ocr && "OCR", img.description && "description"]
                            .filter(Boolean)
                            .join(" + ")
                        : "no (empty fence)"}
                    </div>
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      </TooltipContent>
    </Tooltip>
  )
}
