import { useEffect, useState } from "react"
import { ChevronRight, Home } from "lucide-react"
import { cn } from "@/lib/utils"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { getFolder } from "@/api/file-mgmt"
import type { Folder } from "@/types/file-mgmt"

export function Breadcrumb({ collectionId }: { collectionId: string }) {
  const { currentFolderId, folderTree, navigateToRoot, selectFolder } = useFileMgmtStore()
  const [path, setPath] = useState<Folder[]>([])

  useEffect(() => {
    if (!currentFolderId) {
      setPath([])
      return
    }

    if (currentFolderId === "__archived__") {
      setPath([{
        folder_id: "__archived__",
        parent_folder_id: null,
        name: "Archived",
        kind: "system_group",
        is_system: true,
        created_by: "local",
        created_at: "",
        updated_at: "",
        version: 1,
      }])
      return
    }

    let cancelled = false

    async function buildPath() {
      // Try to find path from tree first (no network)
      function findParents(nodes: typeof folderTree, fid: string, ancestors: Folder[]): Folder[] | null {
        for (const n of nodes) {
          if (n.folder_id === fid) return [...ancestors, n]
          const result = findParents(n.children, fid, [...ancestors, n])
          if (result) return result
        }
        return null
      }

      const treeHit = findParents(folderTree, currentFolderId!, [])
      if (treeHit) {
        if (!cancelled) setPath(treeHit)
        return
      }

      // Fallback: walk parent chain via API
      try {
        const segments: Folder[] = []
        let fid: string | null = currentFolderId
        while (fid && segments.length < 20) {
          const folder = await getFolder(collectionId, fid)
          segments.unshift(folder)
          fid = folder.parent_folder_id
        }
        if (!cancelled) setPath(segments)
      } catch {
        if (!cancelled) setPath([])
      }
    }

    buildPath()
    return () => { cancelled = true }
  }, [currentFolderId, collectionId, folderTree])

  return (
    <div className="flex items-center gap-0.5 text-xs py-1.5 px-2.5 min-h-[32px] overflow-x-auto overscroll-x-contain">
      <button
        type="button"
        className="flex items-center gap-1 text-muted-foreground hover:text-foreground transition-colors shrink-0 px-1 rounded hover:bg-muted/50"
        onClick={() => navigateToRoot(collectionId)}
        title="Root"
      >
        <Home className="h-3.5 w-3.5" />
      </button>
      {path.map((folder, i) => (
        <div key={folder.folder_id} className="flex items-center gap-0.5 shrink-0">
          <ChevronRight className="h-3 w-3 text-muted-foreground/40" />
          <button
            type="button"
            className={cn(
              "px-1.5 py-0.5 rounded transition-colors truncate max-w-[160px]",
              i === path.length - 1
                ? "text-foreground font-medium cursor-default"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
            )}
            onClick={() => {
              if (i < path.length - 1) selectFolder(collectionId, folder.folder_id)
            }}
          >
            {folder.name}
          </button>
        </div>
      ))}
    </div>
  )
}

// cn helper import
// (cn imported above)
