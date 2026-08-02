import type { LucideIcon } from "lucide-react"
import {
  File,
  FileArchive,
  FileAudio,
  FileCode,
  FileImage,
  FileJson,
  FileSpreadsheet,
  FileText,
  FileVideo,
  FileWarning,
  ScrollText,
} from "lucide-react"
import { cn } from "@/lib/utils"

export type FileTypeIconSource = {
  filename?: string | null
  original_ext?: string | null
  unsupported?: boolean
  /** Document source key, e.g. __meeting__:{id}:{tab} */
  source?: string | null
  /** Human label (e.g. "Meeting: Title / Section" or "Note: …") */
  display_name?: string | null
  /** Explicit kind when source is unavailable */
  kind?: "meeting" | "note" | "file" | null
}

/** Resolve extension from original_ext or filename. */
export function resolveFileExt(source: FileTypeIconSource): string {
  const raw = (source.original_ext || "").trim().replace(/^\./, "").toLowerCase()
  if (raw) return raw
  const name = source.filename || ""
  const i = name.lastIndexOf(".")
  if (i <= 0 || i === name.length - 1) return ""
  return name.slice(i + 1).toLowerCase()
}

function isMeetingFile(source: FileTypeIconSource): boolean {
  if (source.kind === "meeting") return true
  // Only trust document source key — never match display_name/filename
  // (e.g. a PDF titled "Meeting notes" must not get MEET badge).
  return (source.source || "").trim().startsWith("__meeting__:")
}

function isNoteFile(source: FileTypeIconSource): boolean {
  if (source.kind === "note") return true
  return (source.source || "").trim().startsWith("__note__:")
}

/** Prefer explicit backend doc_kind, then source key / kind. */
export function resolveDocKind(
  file: {
    doc_kind?: string | null
    source?: string | null
  }
): "meeting" | "note" | "file" {
  const k = (file.doc_kind || "").toLowerCase()
  if (k === "meeting" || k === "note" || k === "file") return k
  const s = (file.source || "").trim()
  if (s.startsWith("__meeting__:")) return "meeting"
  if (s.startsWith("__note__:")) return "note"
  return "file"
}

/**
 * Map extension → lucide icon + color (+ optional letter badge for Office).
 * Colors are slightly muted so they sit next to ink-green UI without clashing.
 */
export function resolveFileTypeIcon(source: FileTypeIconSource): {
  Icon: LucideIcon
  color: string
  label: string
  /** Single letter overlaid on the file glyph (Word/Excel/PPT). */
  badge?: string
} {
  if (source.unsupported) {
    return { Icon: FileWarning, color: "#D97706", label: "Unsupported" }
  }

  // Meeting / note ingest — must win over .md storage ext (often tab_xx.md)
  if (isMeetingFile(source)) {
    return { Icon: File, color: "#B45309", label: "Meeting", badge: "MEET" }
  }
  if (isNoteFile(source)) {
    return { Icon: File, color: "#2563EB", label: "Note", badge: "NOTE" }
  }

  const ext = resolveFileExt(source)

  // Microsoft Word / rich text — letter W
  if (["doc", "docx", "docm", "dot", "dotx", "rtf", "odt"].includes(ext)) {
    return { Icon: File, color: "#2B579A", label: "Word", badge: "W" }
  }

  // Excel / sheets — letter X (csv stays spreadsheet glyph, no badge)
  if (["xls", "xlsx", "xlsm", "xlsb", "ods", "numbers"].includes(ext)) {
    return { Icon: File, color: "#217346", label: "Excel", badge: "X" }
  }
  if (["csv", "tsv"].includes(ext)) {
    return { Icon: FileSpreadsheet, color: "#217346", label: "Spreadsheet" }
  }

  // PowerPoint — letter P
  if (["ppt", "pptx", "pptm", "pps", "ppsx", "odp", "key"].includes(ext)) {
    return { Icon: File, color: "#C43E1C", label: "PowerPoint", badge: "P" }
  }

  // PDF — document glyph + "PDF" corner badge (same position as W/X/P)
  if (ext === "pdf") {
    return { Icon: File, color: "#64748B", label: "PDF", badge: "PDF" }
  }

  // Markdown / plain text / notes
  if (["md", "markdown", "mdx", "txt", "text", "log"].includes(ext)) {
    return { Icon: ScrollText, color: "#64748B", label: "Text" }
  }

  // Images
  if (
    [
      "png",
      "jpg",
      "jpeg",
      "gif",
      "webp",
      "svg",
      "bmp",
      "ico",
      "tif",
      "tiff",
      "heic",
      "avif",
    ].includes(ext)
  ) {
    return { Icon: FileImage, color: "#7C3AED", label: "Image" }
  }

  // Video
  if (
    ["mp4", "mov", "avi", "mkv", "webm", "m4v", "wmv", "flv", "mpeg", "mpg"].includes(
      ext
    )
  ) {
    return { Icon: FileVideo, color: "#DB2777", label: "Video" }
  }

  // Audio
  if (["mp3", "wav", "flac", "aac", "m4a", "ogg", "wma", "aiff", "opus"].includes(ext)) {
    return { Icon: FileAudio, color: "#0891B2", label: "Audio" }
  }

  // Archives
  if (["zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "dmg", "iso"].includes(ext)) {
    return { Icon: FileArchive, color: "#B45309", label: "Archive" }
  }

  // Code / config
  if (
    [
      "js",
      "jsx",
      "ts",
      "tsx",
      "py",
      "rb",
      "go",
      "rs",
      "java",
      "c",
      "cpp",
      "h",
      "cs",
      "php",
      "swift",
      "kt",
      "scala",
      "sh",
      "bash",
      "zsh",
      "ps1",
      "sql",
      "r",
      "lua",
      "vue",
      "svelte",
      "html",
      "htm",
      "css",
      "scss",
      "less",
      "xml",
      "yaml",
      "yml",
      "toml",
      "ini",
      "cfg",
      "conf",
      "env",
    ].includes(ext)
  ) {
    return { Icon: FileCode, color: "#0D9488", label: "Code" }
  }

  // JSON / data
  if (["json", "jsonl", "ndjson", "geojson"].includes(ext)) {
    return { Icon: FileJson, color: "#CA8A04", label: "JSON" }
  }

  // Email
  if (["eml", "msg"].includes(ext)) {
    return { Icon: FileText, color: "#6366F1", label: "Email" }
  }

  // Default
  return { Icon: File, color: "#94A3B8", label: ext ? ext.toUpperCase() : "File" }
}

/** Parse Tailwind-ish h-* classes to a px size for letter scaling. */
function sizeFromClass(className?: string): number {
  if (!className) return 14
  const m = className.match(/\bh-(\d+(?:\.\d+)?)\b/)
  if (!m) return 14
  return parseFloat(m[1]) * 4 // tailwind spacing unit
}

export function FileTypeIcon({
  source,
  className = "h-3.5 w-3.5",
}: {
  source: FileTypeIconSource
  className?: string
}) {
  const { Icon, color, badge } = resolveFileTypeIcon(source)
  const px = sizeFromClass(className)

  if (!badge) {
    return (
      <Icon
        className={cn("shrink-0", className)}
        style={{ color }}
        aria-hidden
      />
    )
  }

  // File glyph + solid corner badge that covers the lower-left stroke.
  // Multi-char badges (PDF / MEET / NOTE) use a slightly smaller type scale.
  const multi = (badge?.length ?? 0) > 1
  const badgeFont = Math.max(6, px * (multi ? 0.20 : 0.24))
  const badgeH = Math.max(10, px * (multi ? 0.36 : 0.34))
  const padX = Math.max(2, px * (multi ? 0.06 : 0.08))

  return (
    <span
      className={cn(
        "relative inline-flex items-center justify-center shrink-0 select-none overflow-visible",
        className
      )}
      aria-hidden
    >
      <Icon className="h-full w-full" style={{ color }} />
      <span
        className="absolute font-bold leading-none pointer-events-none uppercase flex items-center justify-center"
        style={{
          // Opaque chip covers the page’s lower-left corner lines
          color: "#fff",
          background: color,
          fontSize: badgeFont,
          height: badgeH,
          minWidth: badgeH,
          paddingLeft: padX,
          paddingRight: padX,
          // Optical center (slight nudge for bold caps)
          lineHeight: 1,
          letterSpacing: badge.length > 1 ? "-0.04em" : "0",
          borderRadius: Math.max(1, px * 0.06),
          bottom: "2%",
          left: "4%",
          boxShadow: `0 0 0 ${Math.max(1, px * 0.04)}px var(--background, #fff)`,
        }}
      >
        {badge}
      </span>
    </span>
  )
}
