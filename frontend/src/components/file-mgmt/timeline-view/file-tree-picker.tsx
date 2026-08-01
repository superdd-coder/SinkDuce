import { useCallback, useEffect, useMemo, useState } from "react"
import {
  ChevronDown,
  ChevronRight,
  Loader2,
} from "lucide-react"
import { cn } from "@/lib/utils"
import type { FileSummary, FolderTreeNode, NodeGroup } from "@/types/file-mgmt"
import {
  getFolderFiles,
  getFolderTree,
  getRootFiles,
  listGroups,
} from "@/api/file-mgmt"
import { FileTypeIcon } from "@/components/file-mgmt/file-type-icon"
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
    <FolderIconView folder={node} boundGroup={boundGroup} className="h-3 w-3" />
  )
}

interface FileTreePickerProps {
  collectionId: string
  /** File ids already chosen (shown as selected). */
  selectedIds?: Set<string> | string[]
  /** Called when user clicks a file. */
  onSelectFile: (file: FileSummary) => void
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
    node.file_count > 0
  )
}

/** Keep folders/files that match query (folder name or file name). */
function filterTree(
  folders: PickerFolderNode[],
  rootFiles: FileSummary[],
  query: string
): { folders: PickerFolderNode[]; rootFiles: FileSummary[]; expandIds: string[] } {
  const q = query.trim().toLowerCase()
  if (!q) {
    return { folders, rootFiles, expandIds: [] }
  }

  const expandIds: string[] = []

  const markAll = (nodes: PickerFolderNode[]) => {
    for (const n of nodes) {
      expandIds.push(n.folder_id)
      markAll(n.children)
    }
  }

  const filterFolders = (list: PickerFolderNode[]): PickerFolderNode[] => {
    const out: PickerFolderNode[] = []
    for (const folder of list) {
      const nameMatch = folder.name.toLowerCase().includes(q)
      const matchedFiles = folder.files.filter((f) =>
        f.filename.toLowerCase().includes(q)
      )
      const matchedChildren = filterFolders(folder.children)

      if (nameMatch) {
        expandIds.push(folder.folder_id)
        markAll(folder.children)
        out.push({
          ...folder,
          children: folder.children,
          files: folder.files,
        })
      } else if (matchedFiles.length > 0 || matchedChildren.length > 0) {
        expandIds.push(folder.folder_id)
        out.push({
          folder_id: folder.folder_id,
          name: folder.name,
          kind: folder.kind,
          children: matchedChildren,
          files: matchedFiles,
          file_count: folder.file_count,
          icon_type: folder.icon_type,
          icon_value: folder.icon_value,
          icon_color: folder.icon_color,
        })
      }
    }
    return out
  }

  return {
    folders: filterFolders(folders),
    rootFiles: rootFiles.filter((f) => f.filename.toLowerCase().includes(q)),
    expandIds,
  }
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

  return (
    <div>
      <div
        className="w-full flex items-center gap-0.5 px-0.5 py-0.5 rounded text-[10px] hover:bg-muted/40 text-left"
        style={{ paddingLeft: depth * 12 + 2 }}
      >
        <button
          type="button"
          className="shrink-0 p-0.5 rounded hover:bg-muted/60 text-muted-foreground"
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
            <ChevronDown className="h-3 w-3" />
          ) : (
            <ChevronRight className="h-3 w-3" />
          )}
        </button>
        <button
          type="button"
          className="flex-1 min-w-0 flex items-center gap-1 py-0.5 text-left"
          onClick={(e) => {
            e.preventDefault()
            e.stopPropagation()
            if (expandable) onToggle(node.folder_id)
          }}
        >
          <FolderRowIcon node={node} boundGroup={boundGroup} />
          <span className="truncate font-medium">{node.name}</span>
          <span className="text-muted-foreground/50 shrink-0 ml-auto tabular-nums pr-1">
            {node.files.length || node.file_count || childFileCount || 0}
          </span>
        </button>
      </div>

      {isOpen && expandable && (
        <div>
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
              className="text-[10px] text-muted-foreground/40 py-0.5"
              style={{ paddingLeft: (depth + 1) * 12 + 20 }}
            >
              Empty
            </p>
          )}
        </div>
      )}
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
      className={cn(
        "w-full flex items-center gap-1.5 px-1 py-0.5 rounded text-[10px] text-left",
        selected
          ? "bg-primary/10 text-primary"
          : "hover:bg-muted/30 text-foreground"
      )}
      style={{ paddingLeft: depth * 12 + 20 }}
      onClick={(e) => {
        e.preventDefault()
        e.stopPropagation()
        onSelect(file)
      }}
    >
      <FileTypeIcon
        source={{
          filename: file.filename,
          original_ext: file.original_ext,
          unsupported: file.unsupported,
        }}
        className="h-3 w-3"
      />
      <span className="truncate">{file.filename}</span>
      {(file.archived || file.is_greyed) && (
        <span className="text-[9px] text-amber-500 shrink-0">archived</span>
      )}
    </button>
  )
}

export function FileTreePicker({
  collectionId,
  selectedIds,
  onSelectFile,
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

  return (
    <div className={cn("space-y-2 min-h-0", className)}>
      <input
        className="w-full text-[10px] border rounded px-2 py-1 bg-background shrink-0"
        placeholder="Search folders or files..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        autoFocus
      />

      <div
        className={cn(
          "overflow-auto space-y-0.5 rounded border border-border/60 bg-background p-1 min-h-0 flex-1",
          maxHeightClass
        )}
      >
        {loading && (
          <div className="flex items-center justify-center gap-1.5 py-6 text-[10px] text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Loading files...
          </div>
        )}

        {error && (
          <p className="text-[10px] text-red-500 px-1.5 py-2">{error}</p>
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
                onSelectFile={onSelectFile}
                groupByFolderId={groupByFolderId}
              />
            ))}
            {filtered.rootFiles.map((f) => (
              <FileRow
                key={f.file_id}
                file={f}
                depth={0}
                selected={selectedSet.has(f.file_id)}
                onSelect={onSelectFile}
              />
            ))}
            {filtered.folders.length === 0 && filtered.rootFiles.length === 0 && (
              <p className="text-[10px] text-muted-foreground/50 px-1.5 py-3 text-center">
                {search.trim() ? "No matching folders or files" : "No files found"}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  )
}
