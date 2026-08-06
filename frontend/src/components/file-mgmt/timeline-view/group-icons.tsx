import type { LucideIcon } from "lucide-react"
import {
  Archive,
  Bookmark,
  Briefcase,
  Building2,
  Calendar,
  FileText,
  Flag,
  FolderIcon,
  GitBranch,
  Hash,
  Heart,
  Layers,
  Pencil,
  Star,
  Tag,
  Users,
  Video,
  Wrench,
} from "lucide-react"
import { cn } from "@/lib/utils"
import type { FolderTreeNode, NodeGroup } from "@/types/file-mgmt"

/**
 * System / brand forest — `--pm-green` / `--ze-green`.
 * Prefer this for Meeting · Notes · branch chrome so icons match Premium shell.
 */
export const ZE_GREEN = "#1A5E3D"

/** Archived system folder — `--pm-faint` family (warm gray, not cool slate). */
export const ARCHIVE_GRAY = "#969C96"

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

/**
 * Folder / node-group Lucide tints — clear hues, medium chroma.
 *
 * Need to read at ~16px: distinct hues + enough saturation (not gray sludge).
 * Still below neon; brand green stays one option among a full ring.
 */
export const ICON_COLORS = [
  "#1A5E3D", // brand forest (--pm-green)
  "#2A8A9A", // teal
  "#3B6FBF", // blue
  "#7B5CB8", // violet
  "#C45A6A", // rose
  "#D07A30", // orange
  "#C4A020", // gold
  "#3D8B5A", // leaf green (lighter than brand)
]

/** Default for new lucide icons — leaf green, clearer than muted teal-gray. */
export const DEFAULT_ICON_COLOR = ICON_COLORS[7]

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
      if (result.length > 0 && !/^[a-zA-Z]+$/.test(result)) break
      latin++
      result += g
      continue
    }
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
  if (source?.icon_type === "emoji" && source.icon_value) {
    return {
      kind: "emoji",
      emoji: source.icon_value,
      color: source.icon_color || ARCHIVE_GRAY,
    }
  }
  if (source?.icon_type === "lucide" && source.icon_value) {
    const Icon = LUCIDE_MAP[source.icon_value] ?? Users
    return {
      kind: "lucide",
      Icon,
      color: source.icon_color || DEFAULT_ICON_COLOR,
    }
  }

  if (!source || !source.name) {
    return { kind: "lucide", Icon: FolderIcon, color: DEFAULT_ICON_COLOR }
  }
  const name = source.name.trim().toLowerCase()

  // System groups / folders — ink green
  if (name === "meeting") return { kind: "lucide", Icon: Video, color: ZE_GREEN }
  if (name === "notes" || name === "note")
    return { kind: "lucide", Icon: Pencil, color: ZE_GREEN }
  if (name === "archived" || name === "archive")
    return { kind: "lucide", Icon: Archive, color: ARCHIVE_GRAY }

  if (
    name === "未分类" ||
    name === "uncategorized" ||
    name === "no group"
  ) {
    return { kind: "lucide", Icon: FolderIcon, color: DEFAULT_ICON_COLOR }
  }

  return { kind: "lucide", Icon: Users, color: DEFAULT_ICON_COLOR }
}

/**
 * Kind-based folder icon when no custom / bound group icon applies.
 * System + branch → ink green; Archived → gray; plain → Morandi default.
 */
export function resolveFolderKindIcon(folder: {
  kind: string
  name: string
}): { Icon: LucideIcon; color: string } {
  if (folder.kind === "system_group") {
    if (folder.name === "Archived" || folder.name === "Archive") {
      return { Icon: Archive, color: ARCHIVE_GRAY }
    }
    if (folder.name === "Meeting") return { Icon: Video, color: ZE_GREEN }
    if (folder.name === "Notes") return { Icon: Pencil, color: ZE_GREEN }
    return { Icon: FolderIcon, color: ZE_GREEN }
  }
  if (folder.kind === "user_group") return { Icon: Users, color: DEFAULT_ICON_COLOR }
  if (folder.kind === "branch") return { Icon: GitBranch, color: ZE_GREEN }
  return { Icon: FolderIcon, color: DEFAULT_ICON_COLOR }
}

/** Prefer bound group icon, then folder custom icon, then kind defaults. */
export function resolveFolderDisplayIcon(
  folder: Pick<
    FolderTreeNode,
    "kind" | "name" | "icon_type" | "icon_value" | "icon_color"
  >,
  boundGroup?: GroupIconSource
): {
  kind: "lucide" | "emoji"
  Icon?: LucideIcon
  emoji?: string
  color: string
} {
  if (
    boundGroup &&
    (folder.kind === "user_group" || folder.kind === "system_group")
  ) {
    return resolveGroupIcon(boundGroup)
  }
  if (folder.icon_type && folder.icon_value) {
    return resolveGroupIcon({
      name: folder.name,
      icon_type: folder.icon_type,
      icon_value: folder.icon_value,
      icon_color: folder.icon_color,
    })
  }
  const { Icon, color } = resolveFolderKindIcon(folder)
  return { kind: "lucide", Icon, color }
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

/** Render folder icon (custom / bound group / kind). */
export function FolderIconView({
  folder,
  boundGroup,
  className = "h-3.5 w-3.5",
}: {
  folder: Pick<
    FolderTreeNode,
    "kind" | "name" | "icon_type" | "icon_value" | "icon_color"
  >
  boundGroup?: GroupIconSource
  className?: string
}) {
  const r = resolveFolderDisplayIcon(folder, boundGroup)
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

export type IconPickerState = {
  iconMode: "lucide" | "emoji"
  iconKey: string
  iconColor: string
  symbol: string
}

export function buildIconPayload(state: IconPickerState): {
  icon_type: string
  icon_value: string
  icon_color: string | null
} {
  if (state.iconMode === "emoji" && state.symbol.trim()) {
    return {
      icon_type: "emoji",
      icon_value: state.symbol.trim(),
      icon_color: null,
    }
  }
  return {
    icon_type: "lucide",
    icon_value: state.iconKey,
    icon_color: state.iconColor,
  }
}

/** Shared icon picker UI (groups + plain folders). */
export function IconPickerPanel({
  iconMode,
  iconKey,
  iconColor,
  symbol,
  onIconMode,
  onIconKey,
  onIconColor,
  onSymbol,
}: {
  iconMode: "lucide" | "emoji"
  iconKey: string
  iconColor: string
  symbol: string
  onIconMode: (m: "lucide" | "emoji") => void
  onIconKey: (k: string) => void
  onIconColor: (c: string) => void
  onSymbol: (s: string) => void
}) {
  return (
    <div>
      <label className="pm-field-label block mb-1.5">Icon</label>
      <div className="space-y-2.5">
        <div className="grid grid-cols-8 gap-1">
          {LUCIDE_PRESETS.map((p) => (
            <button
              key={p.key}
              type="button"
              title={p.label}
              className={cn(
                "h-8 w-8 rounded-[var(--pm-r-sm)] flex items-center justify-center transition-colors",
                "border border-[color-mix(in_srgb,var(--pm-ink)_8%,transparent)]",
                "bg-white hover:bg-[var(--pm-green-wash)]",
                iconMode === "lucide" &&
                  iconKey === p.key &&
                  "border-[color-mix(in_srgb,var(--pm-green)_35%,transparent)] bg-[var(--pm-green-soft)] ring-1 ring-[color-mix(in_srgb,var(--pm-green)_20%,transparent)]"
              )}
              onClick={() => {
                onIconMode("lucide")
                onIconKey(p.key)
                onSymbol("")
              }}
            >
              <p.Icon
                className="h-3.5 w-3.5 transition-colors"
                style={{ color: iconColor }}
              />
            </button>
          ))}
        </div>
        <div className="flex gap-1.5 flex-wrap items-center">
          <span className="pm-meta mr-0.5 text-[var(--pm-faint)]">Color</span>
          {ICON_COLORS.map((c) => (
            <button
              key={c}
              type="button"
              className={cn(
                "h-5 w-5 rounded-full transition-transform",
                "border-2 border-transparent",
                "shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--pm-ink)_10%,transparent)]",
                iconColor.toLowerCase() === c.toLowerCase()
                  ? "scale-110 border-[var(--pm-ink)]"
                  : "hover:scale-105"
              )}
              style={{ background: c }}
              onClick={() => {
                onIconColor(c)
                onIconMode(iconMode === "emoji" && !symbol ? "lucide" : iconMode)
              }}
              title={c}
            />
          ))}
        </div>
        <div>
          <label className="pm-meta block mb-1 text-[var(--pm-faint)]">
            Custom
          </label>
          <input
            className="w-full pm-meta border border-[color-mix(in_srgb,var(--pm-ink)_10%,transparent)] rounded-[var(--pm-r-sm)] px-2 py-1.5 bg-white text-[var(--pm-text)]"
            value={symbol}
            onChange={(e) => {
              const next = limitSymbolInput(e.target.value)
              onSymbol(next)
              if (next) onIconMode("emoji")
              else onIconMode("lucide")
            }}
            placeholder="Custom icon"
          />
        </div>
      </div>
    </div>
  )
}
