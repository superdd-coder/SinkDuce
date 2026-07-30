import type { LucideIcon } from "lucide-react"
import {
  Bookmark,
  Briefcase,
  Building2,
  Calendar,
  FileText,
  Flag,
  FolderIcon,
  Hash,
  Heart,
  Layers,
  Star,
  Tag,
  Users,
  Video,
  Wrench,
} from "lucide-react"
import { cn } from "@/lib/utils"
import type { NodeGroup } from "@/types/file-mgmt"

/** Virtual focus id for nodes with no group. */
export const UNCATEGORIZED_ID = "__uncategorized__"

/** Meeting / Notes (and is_system) cannot be edited or deleted. */
export function isSystemGroup(g: Pick<NodeGroup, "name" | "is_system">): boolean {
  if (g.is_system) return true
  const n = (g.name || "").trim().toLowerCase()
  return n === "meeting" || n === "notes" || n === "note"
}

export const LUCIDE_PRESETS: { key: string; Icon: LucideIcon; label: string }[] = [
  { key: "folder", Icon: FolderIcon, label: "Folder" },
  { key: "users", Icon: Users, label: "Users" },
  { key: "briefcase", Icon: Briefcase, label: "Briefcase" },
  { key: "file-text", Icon: FileText, label: "Document" },
  { key: "video", Icon: Video, label: "Video" },
  { key: "calendar", Icon: Calendar, label: "Calendar" },
  { key: "star", Icon: Star, label: "Star" },
  { key: "flag", Icon: Flag, label: "Flag" },
  { key: "tag", Icon: Tag, label: "Tag" },
  { key: "layers", Icon: Layers, label: "Layers" },
  { key: "hash", Icon: Hash, label: "Hash" },
  { key: "bookmark", Icon: Bookmark, label: "Bookmark" },
  { key: "building-2", Icon: Building2, label: "Building" },
  { key: "wrench", Icon: Wrench, label: "Tools" },
  { key: "heart", Icon: Heart, label: "Heart" },
]

export const ICON_COLORS = [
  "#3DAF73",
  "#3B82F6",
  "#A855F7",
  "#F59E0B",
  "#EF4444",
  "#06B6D4",
  "#EC4899",
  "#94A3B8",
]

const LUCIDE_MAP: Record<string, LucideIcon> = Object.fromEntries(
  LUCIDE_PRESETS.map((p) => [p.key, p.Icon])
)

export type GroupIconSource = {
  name?: string | null
  icon_type?: string | null
  icon_value?: string | null
  icon_color?: string | null
} | null

/**
 * Limit symbol field: at most 1 CJK char, or 2 Latin letters, or 1 emoji/other grapheme.
 */
export function limitSymbolInput(raw: string): string {
  if (!raw) return ""
  let segments: string[]
  try {
    segments = [
      ...new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(raw),
    ].map((s) => s.segment)
  } catch {
    segments = [...raw]
  }

  let result = ""
  let latin = 0
  let cjk = 0
  for (const g of segments) {
    if (!g || /\s/.test(g)) continue
    const isCjk = /\p{Script=Han}/u.test(g)
    const isLatin = /^[a-zA-Z]$/.test(g)
    if (isCjk) {
      if (cjk >= 1 || latin > 0 || result.length > 0) break
      cjk++
      result += g
      break
    }
    if (isLatin) {
      if (cjk > 0 || latin >= 2) break
      // can't mix with emoji already taken
      if (result.length > 0 && !/^[a-zA-Z]+$/.test(result)) break
      latin++
      result += g
      continue
    }
    // emoji / symbol — single grapheme only
    if (result.length > 0) break
    result += g
    break
  }
  return result
}

/** Resolve icon component + color for a group (or virtual uncategorized). */
export function resolveGroupIcon(source: GroupIconSource): {
  kind: "lucide" | "emoji"
  Icon?: LucideIcon
  emoji?: string
  color: string
} {
  // Explicit icon fields first — create/edit preview updates even when name is empty
  if (source?.icon_type === "emoji" && source.icon_value) {
    return {
      kind: "emoji",
      emoji: source.icon_value,
      color: source.icon_color || "#94A3B8",
    }
  }
  if (source?.icon_type === "lucide" && source.icon_value) {
    const Icon = LUCIDE_MAP[source.icon_value] ?? Users
    return {
      kind: "lucide",
      Icon,
      color: source.icon_color || "#A855F7",
    }
  }

  if (!source || !source.name) {
    return { kind: "lucide", Icon: FolderIcon, color: "#F59E0B" }
  }
  const name = source.name.trim().toLowerCase()

  if (name === "meeting") return { kind: "lucide", Icon: Video, color: "#3B82F6" }
  if (name === "notes" || name === "note") return { kind: "lucide", Icon: FileText, color: "#3B82F6" }
  if (
    name === "未分类" ||
    name === "uncategorized" ||
    name === "no group"
  ) {
    return { kind: "lucide", Icon: FolderIcon, color: "#F59E0B" }
  }

  return { kind: "lucide", Icon: Users, color: "#A855F7" }
}

export function GroupIconView({
  source,
  className = "h-3.5 w-3.5",
}: {
  source: GroupIconSource
  className?: string
}) {
  const r = resolveGroupIcon(source)
  if (r.kind === "emoji") {
    return (
      <span
        className={cn(
          "inline-flex items-center justify-center leading-none shrink-0 select-none",
          className
        )}
        style={{ fontSize: "1.1em" }}
        aria-hidden
      >
        {r.emoji}
      </span>
    )
  }
  const Icon = r.Icon!
  return <Icon className={`${className} shrink-0`} style={{ color: r.color }} />
}

export function groupFromList(
  groups: NodeGroup[],
  groupId: string | null
): GroupIconSource {
  if (!groupId) return { name: "Uncategorized" }
  const g = groups.find((x) => x.group_id === groupId)
  return g ?? { name: null }
}
