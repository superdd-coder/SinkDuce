import { useCallback, useEffect, useRef, useState } from "react"
import { Bot, Clock } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import rehypeRaw from "rehype-raw"
import { cn } from "@/lib/utils"
import type { Message } from "@/types/file-mgmt"

/**
 * Tiptap (Color / Highlight) serializes styled text as HTML spans inside
 * markdown. Default ReactMarkdown escapes those tags as text — enable
 * rehype-raw so stream + hover previews match the editor.
 */
export function MessageBody({
  body,
  className,
}: {
  body: string
  className?: string
}) {
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw]}
        components={{
          // Preserve inline color styles from Tiptap
          span: ({ node: _n, ...props }) => <span {...props} />,
        }}
      >
        {body}
      </ReactMarkdown>
    </div>
  )
}

/** Compact markdown/HTML preview for list rows and hover cards. */
export function MessagePreview({
  body,
  lines = 3,
}: {
  body: string
  /** Tailwind line-clamp count */
  lines?: 2 | 3 | 4
}) {
  return (
    <MessageBody
      body={body}
      className={cn(
        "text-foreground/90 leading-relaxed break-words break-all text-[11px] prose prose-xs dark:prose-invert max-w-none overflow-x-hidden",
        "[&_p]:my-0 [&_ul]:my-0 [&_ol]:my-0 [&_li]:my-0",
        "[&_h1]:text-xs [&_h2]:text-xs [&_h3]:text-xs [&_h1]:my-0 [&_h2]:my-0 [&_h3]:my-0",
        "[&_code]:text-[10px] [&_pre]:hidden [&_blockquote]:my-0 [&_blockquote]:text-xs",
        lines === 2 && "line-clamp-2",
        lines === 3 && "line-clamp-3",
        lines === 4 && "line-clamp-4"
      )}
    />
  )
}

/** Resolve primary timeline source node for a message (owner node or source_node_id). */
export function resolveMessageSourceNodeId(msg: Message): string | null {
  if (msg.owner_type === "node") return msg.owner_id
  if (msg.source_node_id) return msg.source_node_id
  return null
}

const SOURCE_NAME_MAX = 18

/** Truncate display name for compact source tags. */
export function truncateSourceName(name: string, max = SOURCE_NAME_MAX): string {
  const t = name.trim()
  if (t.length <= max) return t
  return t.slice(0, Math.max(1, max - 1)) + "…"
}

/**
 * Build tag label: `Node: Kickoff`, `Docs`, `Root`, …
 * Folder-level messages owned by the active folder scope → "Current folder".
 */
export function formatMessageSourceTag(
  msg: Message,
  opts?: {
    /** Active folder id (navigated / selected). Matching folder msgs → Current folder. */
    highlightFolderId?: string | null
    /**
     * When true (Nested off), every folder-owned message in the list is for the
     * active folder scope — tag them all "Current folder" even if id is missing.
     */
    folderMsgsAreCurrentScope?: boolean
  }
): {
  kind: string
  label: string
  full: string
  /** Folder message owned by the active folder scope */
  isCurrentFolder?: boolean
} | null {
  const ot = (msg.owner_type || "").toLowerCase()
  if (ot === "system_version") {
    const rawName = (msg.source_name && msg.source_name.trim()) || ""
    return {
      kind: "Version",
      label: "version update",
      full: rawName ? `Version update · ${rawName}` : "Version update",
    }
  }

  // Folder-level message of the active folder → "Current folder"
  if (ot === "folder") {
    const isCurrent =
      (!!opts?.highlightFolderId && msg.owner_id === opts.highlightFolderId) ||
      !!opts?.folderMsgsAreCurrentScope
    if (isCurrent) {
      const rawName = (msg.source_name && msg.source_name.trim()) || ""
      return {
        kind: "Folder",
        label: "Current folder",
        full: rawName ? `Current folder: ${rawName}` : "Current folder",
        isCurrentFolder: true,
      }
    }
  }

  let kind = "Source"
  if (ot === "node") kind = "Node"
  else if (ot === "folder") kind = "Folder"
  else if (ot === "file") kind = "File"
  else if (ot === "collection") kind = "Root"
  else kind = ot ? ot.charAt(0).toUpperCase() + ot.slice(1) : "Source"

  if (ot === "collection") {
    return { kind: "Root", label: "Root", full: "Root" }
  }

  // Prefer backend source_name (resolved from id). Never fall back to owner_id.
  const rawName = (msg.source_name && msg.source_name.trim()) || ""
  if (!rawName) {
    // Name still loading / unresolved — kind + placeholder, not hex id
    const fallback =
      ot === "folder"
        ? "Unknown folder"
        : ot === "file"
          ? "Unknown file"
          : ot === "node"
            ? "Unknown node"
            : kind
    return {
      kind,
      label: fallback,
      full: `${fallback} (${msg.owner_id})`,
    }
  }
  // Nested folder messages: show the folder name (no "Folder:" prefix).
  // Keep kind prefix for node/file to disambiguate mixed streams.
  if (ot === "folder") {
    return {
      kind,
      label: truncateSourceName(rawName),
      full: rawName,
    }
  }
  const full = `${kind}: ${rawName}`
  const label = `${kind}: ${truncateSourceName(rawName)}`
  return { kind, label, full }
}

/**
 * Resolve display names for message owners by id (folder/file/node).
 * Used when API did not populate `source_name`.
 * Always returns names from id lookup — never leaves raw ids in tags.
 */
export async function enrichMessageSourceNames(
  collectionId: string,
  messages: Message[],
  opts?: {
    folderNameById?: Map<string, string>
    fileNameById?: Map<string, string>
  }
): Promise<Message[]> {
  const folderMap = new Map(opts?.folderNameById ?? [])
  const fileMap = new Map(opts?.fileNameById ?? [])
  const nodeMap = new Map<string, string>()

  const need = messages.filter((m) => !m.source_name?.trim())
  if (need.length === 0) return messages

  const { getFolder, getFileDetail, getNodeDetail } = await import(
    "@/api/file-mgmt"
  )

  // Dedupe ids per owner type so we only fetch each once
  const folderIds = new Set<string>()
  const fileIds = new Set<string>()
  const nodeIds = new Set<string>()
  for (const m of need) {
    const ot = (m.owner_type || "").toLowerCase()
    const id = m.owner_id
    if (!id) continue
    if (ot === "folder" && !folderMap.has(id)) folderIds.add(id)
    else if (
      (ot === "file" || ot === "system_version") &&
      !fileMap.has(id)
    )
      fileIds.add(id)
    else if (ot === "node") nodeIds.add(id)
  }

  await Promise.all([
    ...[...folderIds].map(async (id) => {
      try {
        const f = await getFolder(collectionId, id)
        if (f?.name?.trim()) folderMap.set(id, f.name.trim())
      } catch {
        /* ignore */
      }
    }),
    ...[...fileIds].map(async (id) => {
      try {
        const f = await getFileDetail(collectionId, id)
        // Prefer display_name (meeting/note title) over storage basename (tab_02.md)
        const name = (f?.display_name || f?.filename || "").trim()
        if (name) {
          const base = name.includes("/")
            ? name.slice(name.lastIndexOf("/") + 1)
            : name
          // For "Title / Section" display labels keep full string (not basename)
          fileMap.set(id, f?.display_name?.trim() || base)
        }
      } catch {
        /* ignore */
      }
    }),
    ...[...nodeIds].map(async (id) => {
      try {
        const n = await getNodeDetail(collectionId, id)
        nodeMap.set(id, (n?.title || "").trim() || "Untitled")
      } catch {
        /* ignore */
      }
    }),
  ])

  return messages.map((m) => {
    if (m.source_name?.trim()) return m
    const ot = (m.owner_type || "").toLowerCase()
    let name: string | undefined
    if (ot === "folder") name = folderMap.get(m.owner_id)
    else if (ot === "file" || ot === "system_version")
      name = fileMap.get(m.owner_id)
    else if (ot === "node") name = nodeMap.get(m.owner_id)
    else if (ot === "collection") name = "Root"
    return name ? { ...m, source_name: name } : m
  })
}

/**
 * Resolve all timeline nodes to highlight for a message.
 * - node message → that node
 * - file message → every node that mounts the file (file_nodes)
 * - otherwise → source_node_id if present
 */
export async function resolveMessageHighlightNodeIds(
  collectionId: string,
  msg: Message
): Promise<string[]> {
  if (msg.owner_type === "node" && msg.owner_id) {
    return [msg.owner_id]
  }
  if (msg.owner_type === "file" && msg.owner_id) {
    try {
      const { getFileDetail } = await import("@/api/file-mgmt")
      const detail = await getFileDetail(collectionId, msg.owner_id)
      const ids: string[] = []
      for (const n of detail.nodes ?? []) {
        if (n && typeof n === "object" && "node_id" in n) {
          const id = (n as { node_id?: unknown }).node_id
          if (typeof id === "string" && id) ids.push(id)
        }
      }
      if (ids.length > 0) return [...new Set(ids)]
    } catch {
      /* fall through */
    }
    // Fallback: single source_node_id if file detail unavailable
    if (msg.source_node_id) return [msg.source_node_id]
    return []
  }
  if (msg.source_node_id) return [msg.source_node_id]
  return []
}

export function MessageCard({
  msg,
  onView,
  onDelete,
  /** When true, hover preview opens to the left (for right-side sidebars). */
  previewSide = "right",
  /**
   * Click source tag. For node messages typically opens the same preview dialog.
   * Receives full message so callers can open enriched UI.
   */
  onSourceTagClick,
  /** @deprecated edit is only in the message detail dialog */
  onEdit: _onEdit,
  /** @deprecated unused — source tag always shown when resolvable */
  showFromNode: _showFromNode,
  /** Selected / open in detail panel */
  isActive = false,
  /**
   * Folder id for the active list scope. Folder-owned messages with this
   * owner_id show a prominent "Current folder" tag.
   */
  highlightFolderId = null,
  /** Nested off: all folder-level messages in the list belong to current scope. */
  folderMsgsAreCurrentScope = false,
  /** Extra classes on the card root (e.g. `pm-node-msg-row` for sliding focus lists). */
  className,
  /**
   * Side flyout preview on hover. Disable in rails that open detail on click
   * (Message stream) — the 320px popover clips/misplaces in a narrow column.
   */
  showHoverPreview = true,
}: {
  msg: Message
  onView: (msg: Message) => void
  /** Optional — edit lives in the detail dialog now */
  onEdit?: (msg: Message) => void
  onDelete: () => void
  previewSide?: "left" | "right"
  onSourceTagClick?: (msg: Message) => void
  showFromNode?: boolean
  isActive?: boolean
  highlightFolderId?: string | null
  folderMsgsAreCurrentScope?: boolean
  className?: string
  showHoverPreview?: boolean
}) {
  const isSystem = msg.author_type === "system"
  const isVersionUpdate =
    (msg.owner_type || "").toLowerCase() === "system_version"
  // Version logs store a free-text note (often "version update"); show as-is
  const displayBody = (msg.body || "").trim() || (isVersionUpdate ? "version update" : "")
  const time = msg.created_at
    ? new Date(msg.created_at).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : ""
  const sourceTag = formatMessageSourceTag(msg, {
    highlightFolderId,
    folderMsgsAreCurrentScope,
  })
  const isNodeSource = (msg.owner_type || "").toLowerCase() === "node"
  const isCurrentFolder = !!sourceTag?.isCurrentFolder

  /** Two-step delete (× → DELETE) — same anti-mis-tap pattern as distill blocks */
  const [deleteArmed, setDeleteArmed] = useState(false)
  const deleteArmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const deleteBtnRef = useRef<HTMLButtonElement>(null)

  const disarmDelete = useCallback(() => {
    setDeleteArmed(false)
    if (deleteArmTimerRef.current) {
      clearTimeout(deleteArmTimerRef.current)
      deleteArmTimerRef.current = null
    }
  }, [])

  const armDelete = useCallback(() => {
    setDeleteArmed(true)
    if (deleteArmTimerRef.current) clearTimeout(deleteArmTimerRef.current)
    deleteArmTimerRef.current = setTimeout(() => disarmDelete(), 4000)
  }, [disarmDelete])

  useEffect(() => {
    if (!deleteArmed) return
    const onPointerDown = (ev: Event) => {
      const t = ev.target as Node | null
      if (t && deleteBtnRef.current?.contains(t)) return
      disarmDelete()
    }
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") disarmDelete()
    }
    const t = window.setTimeout(() => {
      document.addEventListener("pointerdown", onPointerDown, true)
      document.addEventListener("keydown", onKey, true)
    }, 0)
    return () => {
      window.clearTimeout(t)
      document.removeEventListener("pointerdown", onPointerDown, true)
      document.removeEventListener("keydown", onKey, true)
    }
  }, [deleteArmed, disarmDelete])

  useEffect(() => {
    return () => {
      if (deleteArmTimerRef.current) clearTimeout(deleteArmTimerRef.current)
    }
  }, [])

  // Reset confirm when card / selection changes
  useEffect(() => {
    disarmDelete()
  }, [msg.message_id, isActive, disarmDelete])

  return (
    <div
      className={cn(
        /* No fill — soft hairline separates cards (see .pm-msg-card) */
        "pm-msg-card group relative min-w-0 max-w-full overflow-x-hidden cursor-pointer",
        isVersionUpdate && "is-version",
        isSystem && !isVersionUpdate && "is-system",
        isActive && "is-active",
        className
      )}
      onClick={() => onView(msg)}
    >
      {/* Hover preview — opt-out in stream rails; hidden while card is active */}
      {showHoverPreview && !isActive && (
        <div
          className={cn(
            "absolute bottom-0 z-[9999] hidden group-hover:block pointer-events-none",
            previewSide === "right"
              ? "left-[calc(100%+6px)]"
              : "right-[calc(100%+6px)]"
          )}
        >
          <div className="w-[320px] max-h-[360px] overflow-y-auto rounded-lg border border-border bg-popover shadow-2xl p-3">
            <MessageBody
              body={displayBody}
              className="prose prose-xs dark:prose-invert max-w-none text-xs leading-relaxed break-words"
            />
          </div>
        </div>
      )}

      <div className="flex items-center gap-1 mb-0.5 min-w-0">
        {isSystem ? (
          <Bot className="h-3 w-3 text-muted-foreground/60 shrink-0" />
        ) : (
          <Clock className="h-3 w-3 text-muted-foreground/40 shrink-0" />
        )}
        <span className="text-[10px] text-muted-foreground/50 shrink-0">{time}</span>
        {msg.edited_at && (
          <span className="text-[9px] text-muted-foreground/40 italic shrink-0">
            edited
          </span>
        )}
        {isVersionUpdate ? (
          <span
            className="text-[9px] px-1.5 py-0.5 rounded shrink-0 font-medium border-transparent bg-[var(--ze-green,#1A5E3D)]/15 text-[var(--ze-green,#1A5E3D)]"
            title={sourceTag?.full || "Version update"}
          >
            version update
          </span>
        ) : sourceTag ? (
          <button
            type="button"
            className={cn(
              "text-[9px] px-1.5 py-0.5 rounded max-w-[10rem] truncate shrink-0 transition-colors font-medium",
              isCurrentFolder
                ? "text-primary bg-primary/15 ring-1 ring-primary/30 hover:bg-primary/20"
                : isNodeSource
                  ? "text-[var(--ze-green,#1A5E3D)] bg-[var(--ze-green,#1A5E3D)]/10 hover:bg-[var(--ze-green,#1A5E3D)]/15"
                  : "text-muted-foreground bg-muted/50 hover:bg-muted font-normal"
            )}
            title={sourceTag.full}
            onClick={(e) => {
              e.stopPropagation()
              if (onSourceTagClick) onSourceTagClick(msg)
              else onView(msg)
            }}
          >
            {sourceTag.label}
          </button>
        ) : null}
        {!isSystem && (
          <div
            className={cn(
              "ml-auto flex gap-0.5 shrink-0 transition-opacity",
              deleteArmed
                ? "opacity-100"
                : "opacity-0 group-hover:opacity-100"
            )}
          >
            <button
              ref={deleteBtnRef}
              type="button"
              className={cn("pm-msg-delete", deleteArmed && "is-confirm")}
              title={
                deleteArmed
                  ? "Click again to delete"
                  : "Delete message"
              }
              aria-label={
                deleteArmed
                  ? "Confirm delete message"
                  : "Delete message"
              }
              aria-expanded={deleteArmed}
              onClick={(e) => {
                e.preventDefault()
                e.stopPropagation()
                if (!deleteArmed) {
                  armDelete()
                  return
                }
                disarmDelete()
                onDelete()
              }}
            >
              <span className="pm-msg-delete-x" aria-hidden>
                ×
              </span>
              <span className="pm-msg-delete-label">Delete</span>
            </button>
          </div>
        )}
      </div>
      <MessagePreview body={displayBody} />
    </div>
  )
}
