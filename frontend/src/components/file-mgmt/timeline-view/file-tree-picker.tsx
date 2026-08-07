import { useCallback, useEffect, useMemo, useState } from "react"
import {
  Check,
  ChevronDown,
  ChevronRight,
  Loader2,
  Search,
} from "lucide-react"
import { cn } from "@/lib/utils"
import type { FileSummary, FolderTreeNode, NodeGroup } from "@/types/file-mgmt"
import {
  getFolderFiles,
  getFolderTree,
  getRootFiles,
  listGroups,
} from "@/api/file-mgmt"
import {
  FileTypeIcon,
  resolveDocKind,
} from "@/components/file-mgmt/file-type-icon"
import { FolderIconView } from "./group-icons"

export type PickerFolderNode = {
  folder_id: string
  name: string
  kind: FolderTreeNode["kind"]
  children: PickerFolderNode[]
  files: FileSummary[]
  /** Direct file count from API (fallback if files not yet loaded). */
  file_count: number
  icon_type?: string | null
  icon_value?: string | null
  icon_color?: string | null
}

function FolderRowIcon({
  node,
  boundGroup,
}: {
  node: PickerFolderNode
  boundGroup?: NodeGroup | null
}) {
  return (
    <FolderIconView
      folder={node}
      boundGroup={boundGroup}
      className="h-3.5 w-3.5 shrink-0"
    />
  )
}

interface FileTreePickerProps {
  collectionId: string
  /** File ids already chosen (shown as selected). */
  selectedIds?: Set<string> | string[]
  /** Called when user clicks a file (select / toggle). */
  onSelectFile: (file: FileSummary) => void
  /**
   * Focused file for external slide-in preview panel.
   * Called on click before onSelectFile so parents can open the left preview.
   */
  onPreviewFile?: (file: FileSummary) => void
  className?: string
  /** Max height of the scrollable tree. */
  maxHeightClass?: string
}

function collectFolderIds(nodes: FolderTreeNode[]): string[] {
  const ids: string[] = []
  const walk = (list: FolderTreeNode[]) => {
    for (const n of list) {
      ids.push(n.folder_id)
      if (n.children?.length) walk(n.children)
    }
  }
  walk(nodes)
  return ids
}

function collectPickerIds(nodes: PickerFolderNode[]): string[] {
  const ids: string[] = []
  const walk = (list: PickerFolderNode[]) => {
    for (const n of list) {
      ids.push(n.folder_id)
      if (n.children.length) walk(n.children)
    }
  }
  walk(nodes)
  return ids
}

function buildPickerTree(
  nodes: FolderTreeNode[],
  filesByFolder: Map<string, FileSummary[]>
): PickerFolderNode[] {
  return nodes.map((n) => ({
    folder_id: n.folder_id,
    name: n.name,
    kind: n.kind,
    children: buildPickerTree(n.children ?? [], filesByFolder),
    files: filesByFolder.get(n.folder_id) ?? [],
    file_count: n.file_count ?? 0,
    icon_type: n.icon_type,
    icon_value: n.icon_value,
    icon_color: n.icon_color,
  }))
}

function folderIsExpandable(node: PickerFolderNode): boolean {
  return (
    node.children.length > 0 ||
    node.files.length > 0 ||
    (node.file_count ?? 0) > 0
  )
}

function filterTree(
  folders: PickerFolderNode[],
  rootFiles: FileSummary[],
  q: string
): {
  folders: PickerFolderNode[]
  rootFiles: FileSummary[]
  expandIds: string[]
} {
  const term = q.trim().toLowerCase()
  if (!term) {
    return { folders, rootFiles, expandIds: [] }
  }

  const expandIds: string[] = []

  const filterNode = (node: PickerFolderNode): PickerFolderNode | null => {
    const nameHit = node.name.toLowerCase().includes(term)
    const kids = node.children
      .map(filterNode)
      .filter(Boolean) as PickerFolderNode[]
    const files = node.files.filter((f) =>
      (f.display_name || f.filename || "").toLowerCase().includes(term)
    )
    if (!nameHit && kids.length === 0 && files.length === 0) return null
    if (kids.length > 0 || files.length > 0 || nameHit) {
      expandIds.push(node.folder_id)
    }
    return {
      ...node,
      children: kids,
      files,
    }
  }

  const nextFolders = folders
    .map(filterNode)
    .filter(Boolean) as PickerFolderNode[]
  const nextRoot = rootFiles.filter((f) =>
    (f.display_name || f.filename || "").toLowerCase().includes(term)
  )
  return { folders: nextFolders, rootFiles: nextRoot, expandIds }
}

function FolderRow({
  node,
  depth,
  expanded,
  onToggle,
  selectedSet,
  onSelectFile,
  groupByFolderId,
}: {
  node: PickerFolderNode
  depth: number
  expanded: Set<string>
  onToggle: (id: string) => void
  selectedSet: Set<string>
  onSelectFile: (file: FileSummary) => void
  groupByFolderId: Map<string, NodeGroup>
}) {
  const isOpen = expanded.has(node.folder_id)
  const expandable = folderIsExpandable(node)
  const childFileCount =
    node.files.length +
    node.children.reduce((sum, c) => sum + c.files.length + c.file_count, 0)
  const boundGroup = groupByFolderId.get(node.folder_id) ?? null
  const count =
    node.files.length || node.file_count || childFileCount || 0

  return (
    <div>
      <div
        className="pm-timeline-ftree-row"
        style={{ paddingLeft: depth * 14 + 4 }}
      >
        <button
          type="button"
          className={cn("pm-timeline-ftree-chev", isOpen && "is-open")}
          aria-label={isOpen ? "Collapse folder" : "Expand folder"}
          aria-expanded={isOpen}
          disabled={!expandable}
          onClick={(e) => {
            e.preventDefault()
            e.stopPropagation()
            if (expandable) onToggle(node.folder_id)
          }}
        >
          {!expandable ? (
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
            isOpen && "is-open"
          )}
          onClick={(e) => {
            e.preventDefault()
            e.stopPropagation()
            if (expandable) onToggle(node.folder_id)
          }}
        >
          <span className="pm-timeline-ftree-icon">
            <FolderRowIcon node={node} boundGroup={boundGroup} />
          </span>
          <span className="pm-timeline-ftree-name">{node.name}</span>
          <span className="pm-timeline-ftree-count">{count}</span>
        </button>
      </div>

      <div
        className={cn(
          "pm-timeline-ftree-kids",
          isOpen && expandable && "is-open"
        )}
      >
        <div className="pm-timeline-ftree-kids-inner">
          {expandable && (
            <>
              {node.children.map((child) => (
                <FolderRow
                  key={child.folder_id}
                  node={child}
                  depth={depth + 1}
                  expanded={expanded}
                  onToggle={onToggle}
                  selectedSet={selectedSet}
                  onSelectFile={onSelectFile}
                  groupByFolderId={groupByFolderId}
                />
              ))}
              {node.files.map((f) => (
                <FileRow
                  key={f.file_id}
                  file={f}
                  depth={depth + 1}
                  selected={selectedSet.has(f.file_id)}
                  onSelect={onSelectFile}
                />
              ))}
              {node.children.length === 0 && node.files.length === 0 && (
                <p
                  className="pm-timeline-ftree-empty"
                  style={{ paddingLeft: (depth + 1) * 14 + 30 }}
                >
                  Empty
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function FileRow({
  file,
  depth,
  selected,
  onSelect,
}: {
  file: FileSummary
  depth: number
  selected: boolean
  onSelect: (file: FileSummary) => void
}) {
  return (
    <button
      type="button"
      className={cn("pm-timeline-ftree-file", selected && "is-on")}
      style={{ paddingLeft: depth * 14 + 28 }}
      onClick={(e) => {
        e.preventDefault()
        e.stopPropagation()
        onSelect(file)
      }}
    >
      <span className="pm-timeline-ftree-icon">
        <FileTypeIcon
          source={{
            filename: file.filename,
            original_ext: file.original_ext,
            unsupported: file.unsupported,
            source: file.source,
            kind: resolveDocKind(file),
          }}
          className="h-3.5 w-3.5"
        />
      </span>
      <span className="pm-timeline-ftree-name">
        {file.display_name || file.filename}
      </span>
      {(file.archived || file.is_greyed) && (
        <span className="pm-meta text-[var(--pm-faint)] shrink-0">
          archived
        </span>
      )}
      <Check
        className="pm-timeline-ftree-check"
        strokeWidth={2.25}
        aria-hidden
      />
    </button>
  )
}

export function FileTreePicker({
  collectionId,
  selectedIds,
  onSelectFile,
  onPreviewFile,
  className,
  maxHeightClass = "max-h-48",
}: FileTreePickerProps) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [folders, setFolders] = useState<PickerFolderNode[]>([])
  const [rootFiles, setRootFiles] = useState<FileSummary[]>([])
  const [groups, setGroups] = useState<NodeGroup[]>([])
  const [search, setSearch] = useState("")
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())

  const selectedSet = useMemo(() => {
    if (!selectedIds) return new Set<string>()
    return selectedIds instanceof Set ? selectedIds : new Set(selectedIds)
  }, [selectedIds])

  const groupByFolderId = useMemo(() => {
    const m = new Map<string, NodeGroup>()
    for (const g of groups) {
      if (g.folder_id) m.set(g.folder_id, g)
    }
    return m
  }, [groups])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    ;(async () => {
      try {
        const [tree, gs] = await Promise.all([
          getFolderTree(collectionId),
          listGroups(collectionId).catch(() => [] as NodeGroup[]),
        ])
        const folderIds = collectFolderIds(tree)
        const [root, ...folderFileLists] = await Promise.all([
          getRootFiles(collectionId),
          ...folderIds.map((id) =>
            getFolderFiles(collectionId, id).catch(() => [] as FileSummary[])
          ),
        ])
        if (cancelled) return

        const filesByFolder = new Map<string, FileSummary[]>()
        folderIds.forEach((id, i) => {
          filesByFolder.set(id, folderFileLists[i] ?? [])
        })

        const built = buildPickerTree(tree, filesByFolder)
        setFolders(built)
        setRootFiles(root)
        setGroups(gs)
        // Expand every folder by default so nested content is reachable immediately
        setExpanded(new Set(collectPickerIds(built)))
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err))
          setFolders([])
          setRootFiles([])
          setGroups([])
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [collectionId])

  const filtered = useMemo(
    () => filterTree(folders, rootFiles, search),
    [folders, rootFiles, search]
  )

  // When search text changes, open matching paths (does not lock them open)
  useEffect(() => {
    if (!search.trim()) return
    if (filtered.expandIds.length === 0) return
    setExpanded((prev) => {
      const next = new Set(prev)
      let changed = false
      for (const id of filtered.expandIds) {
        if (!next.has(id)) {
          next.add(id)
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [search, filtered.expandIds])

  const toggle = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const handleSelectFile = useCallback(
    (file: FileSummary) => {
      onPreviewFile?.(file)
      onSelectFile(file)
    },
    [onSelectFile, onPreviewFile]
  )

  return (
    <div
      data-file-select-tree
      className={cn("pm-timeline-ftree", className)}
    >
      <div className="pm-timeline-ftree-search-wrap">
        <Search className="pm-timeline-ftree-search-icon" strokeWidth={1.75} />
        <input
          className="pm-timeline-ftree-search"
          placeholder="Search folders or files…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          autoFocus
        />
      </div>

      <div
        className={cn(
          "pm-timeline-ftree-scroll",
          maxHeightClass
        )}
      >
        {loading && (
          <div className="flex items-center justify-center gap-2 py-10">
            <Loader2 className="h-4 w-4 animate-spin text-[var(--pm-faint)]" />
            <span className="pm-meta text-[var(--pm-muted)]">Loading files…</span>
          </div>
        )}

        {error && (
          <p className="pm-meta text-[var(--pm-danger)] px-3 py-3">{error}</p>
        )}

        {!loading && !error && (
          <>
            {filtered.folders.map((node) => (
              <FolderRow
                key={node.folder_id}
                node={node}
                depth={0}
                expanded={expanded}
                onToggle={toggle}
                selectedSet={selectedSet}
                onSelectFile={handleSelectFile}
                groupByFolderId={groupByFolderId}
              />
            ))}
            {filtered.rootFiles.map((f) => (
              <FileRow
                key={f.file_id}
                file={f}
                depth={0}
                selected={selectedSet.has(f.file_id)}
                onSelect={handleSelectFile}
              />
            ))}
            {filtered.folders.length === 0 &&
              filtered.rootFiles.length === 0 && (
                <p className="pm-meta text-[var(--pm-faint)] px-3 py-8 text-center">
                  {search.trim()
                    ? "No matching folders or files"
                    : "No files found"}
                </p>
              )}
          </>
        )}
      </div>
    </div>
  )
}
