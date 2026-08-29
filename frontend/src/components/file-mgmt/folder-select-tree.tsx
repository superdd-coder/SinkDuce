import { useEffect, useMemo, useState, type ReactNode } from "react"
import { Check, ChevronDown, ChevronRight, FolderIcon, Search } from "lucide-react"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"
import { systemFolderDisplayName } from "@/i18n/system-folder"
import type { FolderTreeNode, NodeGroup } from "@/types/file-mgmt"
import { FolderIconView } from "@/components/file-mgmt/timeline-view/group-icons"
import { listGroups } from "@/api/file-mgmt"

export function filterFolderTree(
  nodes: FolderTreeNode[],
  q: string
): FolderTreeNode[] {
  const needle = q.trim().toLowerCase()
  if (!needle) return nodes
  const walk = (list: FolderTreeNode[]): FolderTreeNode[] => {
    const out: FolderTreeNode[] = []
    for (const n of list) {
      const kids = walk(n.children ?? [])
      const label = (n.name || "").toLowerCase()
      if (label.includes(needle) || kids.length > 0) {
        out.push({ ...n, children: kids })
      }
    }
    return out
  }
  return walk(nodes)
}

export function findFolderName(
  nodes: FolderTreeNode[],
  folderId: string
): string | null {
  for (const n of nodes) {
    if (n.folder_id === folderId) return n.name
    if (n.children?.length) {
      const found = findFolderName(n.children, folderId)
      if (found) return found
    }
  }
  return null
}

export function collectExpandableIds(nodes: FolderTreeNode[]): string[] {
  const ids: string[] = []
  const walk = (list: FolderTreeNode[]) => {
    for (const n of list) {
      if (n.children?.length) {
        ids.push(n.folder_id)
        walk(n.children)
      }
    }
  }
  walk(nodes)
  return ids
}

function FolderRows({
  nodes,
  selectedId,
  onSelect,
  isSelectable,
  groupByFolderId,
  badge,
  expanded,
  onToggle,
  depth = 0,
}: {
  nodes: FolderTreeNode[]
  selectedId: string | null | undefined
  onSelect: (id: string | null) => void
  isSelectable: (n: FolderTreeNode) => boolean
  groupByFolderId?: Map<string, NodeGroup>
  badge?: (n: FolderTreeNode) => ReactNode
  expanded: Set<string>
  onToggle: (id: string) => void
  depth?: number
}) {
  const t = useT()
  return (
    <>
      {nodes.map((n) => {
        const hasChildren = (n.children?.length ?? 0) > 0
        const selectable = isSelectable(n)
        const isOpen = expanded.has(n.folder_id)
        const selected = selectedId === n.folder_id
        const boundGroup = groupByFolderId?.get(n.folder_id) ?? null
        const extra = badge?.(n)
        return (
          <div key={n.folder_id}>
            <div
              className={cn(
                "pm-timeline-ftree-row",
                !selectable && !hasChildren && "opacity-45"
              )}
              style={{ paddingLeft: depth * 14 }}
            >
              <button
                type="button"
                className={cn(
                  "pm-timeline-ftree-chev",
                  isOpen && hasChildren && "is-open"
                )}
                disabled={!hasChildren}
                aria-label={isOpen ? "Collapse" : "Expand"}
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  if (hasChildren) onToggle(n.folder_id)
                }}
              >
                {!hasChildren ? (
                  <span className="inline-block w-3 h-3" />
                ) : isOpen ? (
                  <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.75} />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5" strokeWidth={1.75} />
                )}
              </button>
              <button
                type="button"
                className={cn(
                  "pm-timeline-ftree-folder",
                  isOpen && hasChildren && "is-open",
                  selected && selectable && "is-selected",
                  !selectable && "is-muted"
                )}
                disabled={!selectable && !hasChildren}
                title={systemFolderDisplayName(n.name, t)}
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  if (selectable) onSelect(n.folder_id)
                  else if (hasChildren) onToggle(n.folder_id)
                }}
              >
                <span className="pm-timeline-ftree-icon">
                  <FolderIconView
                    folder={n}
                    boundGroup={boundGroup}
                    className="h-3.5 w-3.5 shrink-0"
                  />
                </span>
                <span className="pm-timeline-ftree-name">
                  {systemFolderDisplayName(n.name, t)}
                </span>
                {extra}
                {selectable && selected && (
                  <Check
                    className="pm-timeline-ftree-check"
                    strokeWidth={2.25}
                    aria-hidden
                  />
                )}
              </button>
            </div>
            <div
              className={cn(
                "pm-timeline-ftree-kids",
                isOpen && hasChildren && "is-open"
              )}
            >
              <div className="pm-timeline-ftree-kids-inner">
                {hasChildren && isOpen && (
                  <FolderRows
                    nodes={n.children}
                    selectedId={selectedId}
                    onSelect={onSelect}
                    isSelectable={isSelectable}
                    groupByFolderId={groupByFolderId}
                    badge={badge}
                    expanded={expanded}
                    onToggle={onToggle}
                    depth={depth + 1}
                  />
                )}
              </div>
            </div>
          </div>
        )
      })}
    </>
  )
}

/**
 * Shared folder picker — same chrome as Create Group → Existing:
 * sticky pill search, scrolling tree rows, optional selected caption.
 * `selectedId`: undefined = none, null = root, string = folder.
 */
export function FolderSelectTree({
  nodes,
  selectedId,
  onSelect,
  isSelectable,
  groupByFolderId,
  includeRoot = false,
  rootLabel,
  badge,
  showSearch = true,
  showSelectedCaption = false,
  searchTabIndex = 0,
}: {
  nodes: FolderTreeNode[]
  selectedId: string | null | undefined
  onSelect: (id: string | null) => void
  isSelectable: (n: FolderTreeNode) => boolean
  groupByFolderId?: Map<string, NodeGroup>
  includeRoot?: boolean
  rootLabel?: string
  badge?: (n: FolderTreeNode) => ReactNode
  showSearch?: boolean
  showSelectedCaption?: boolean
  searchTabIndex?: number
}) {
  const t = useT()
  const [query, setQuery] = useState("")
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const visible = useMemo(
    () => filterFolderTree(nodes, query),
    [nodes, query]
  )

  useEffect(() => {
    setExpanded(new Set(collectExpandableIds(nodes)))
  }, [nodes])

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const empty = nodes.length === 0
  const noMatch =
    !empty && visible.length === 0 && !(includeRoot && !query.trim())
  const rootText = rootLabel || t("fileMgmt.moveToRoot")
  const selectedLabel =
    selectedId === null
      ? rootText
      : selectedId
        ? systemFolderDisplayName(
            findFolderName(nodes, selectedId) || selectedId.slice(0, 8),
            t
          )
        : null

  return (
    <div className="pm-files-folder-picker">
      {showSearch && (
        <div className="pm-timeline-ftree-search-wrap pm-group-folder-search">
          <Search
            className="pm-timeline-ftree-search-icon"
            strokeWidth={1.75}
          />
          <input
            className="pm-timeline-ftree-search pm-group-folder-search-input"
            placeholder={t("fileMgmt.searchFolders")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            tabIndex={searchTabIndex}
          />
        </div>
      )}
      <div className="pm-group-tree-list">
        {empty ? (
          <p className="pm-meta text-center py-6 text-[var(--pm-faint)]">
            {t("fileMgmt.noFoldersYet")}
          </p>
        ) : noMatch ? (
          <p className="pm-meta text-center py-6 text-[var(--pm-faint)]">
            {t("common.noMatches")}
          </p>
        ) : (
          <div className="pm-timeline-ftree pm-group-ftree">
            <div className="pm-timeline-ftree-scroll pm-group-ftree-scroll">
              {includeRoot && !query.trim() && (
                <div className="pm-timeline-ftree-row" style={{ paddingLeft: 0 }}>
                  <span className="inline-block w-[22px] h-[22px] shrink-0" />
                  <button
                    type="button"
                    className={cn(
                      "pm-timeline-ftree-folder",
                      selectedId === null && "is-selected"
                    )}
                    onClick={(e) => {
                      e.preventDefault()
                      onSelect(null)
                    }}
                  >
                    <span className="pm-timeline-ftree-icon">
                      <FolderIcon
                        className="h-3.5 w-3.5 shrink-0"
                        strokeWidth={1.75}
                      />
                    </span>
                    <span className="pm-timeline-ftree-name">{rootText}</span>
                    {selectedId === null && (
                      <Check
                        className="pm-timeline-ftree-check"
                        strokeWidth={2.25}
                        aria-hidden
                      />
                    )}
                  </button>
                </div>
              )}
              <FolderRows
                nodes={visible}
                selectedId={selectedId}
                onSelect={onSelect}
                isSelectable={isSelectable}
                groupByFolderId={groupByFolderId}
                badge={badge}
                expanded={expanded}
                onToggle={toggle}
              />
            </div>
          </div>
        )}
      </div>
      {showSelectedCaption && selectedLabel && (
        <p className="pm-group-folder-selected">
          {t("fileMgmt.selectedNamed", { name: selectedLabel })}
        </p>
      )}
    </div>
  )
}

/** White folder card used by Move / Mirror dialogs — matches Group → Existing. */
export function FolderDestCard({
  collectionId,
  nodes,
  selectedId,
  onSelect,
  isSelectable,
  includeRoot = false,
}: {
  collectionId: string
  nodes: FolderTreeNode[]
  selectedId: string | null | undefined
  onSelect: (id: string | null) => void
  isSelectable: (n: FolderTreeNode) => boolean
  includeRoot?: boolean
}) {
  const [groups, setGroups] = useState<NodeGroup[]>([])
  useEffect(() => {
    if (!collectionId) return
    listGroups(collectionId)
      .then(setGroups)
      .catch(() => setGroups([]))
  }, [collectionId])
  const groupByFolderId = useMemo(() => {
    const m = new Map<string, NodeGroup>()
    for (const g of groups) {
      if (g.folder_id) m.set(g.folder_id, g)
    }
    return m
  }, [groups])
  return (
    <section className="pm-group-card pm-group-card--folder pm-files-dest-card">
      <FolderSelectTree
        nodes={nodes}
        selectedId={selectedId}
        onSelect={onSelect}
        isSelectable={isSelectable}
        groupByFolderId={groupByFolderId}
        includeRoot={includeRoot}
        showSelectedCaption
      />
    </section>
  )
}
