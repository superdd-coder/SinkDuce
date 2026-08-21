import { useEffect, useState } from "react"
import { ChevronRight, Home } from "lucide-react"
import { cn } from "@/lib/utils"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { getFolder } from "@/api/file-mgmt"
import type { Folder } from "@/types/file-mgmt"
import { useT } from "@/i18n/use-t"
import { systemFolderDisplayName } from "@/i18n/system-folder"

export function Breadcrumb({ collectionId }: { collectionId: string }) {
  const t = useT()
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
    <div className="pm-files-breadcrumb">
      <button
        type="button"
        className="pm-files-crumb shrink-0"
        onClick={() => navigateToRoot(collectionId)}
        title={t("common.root")}
      >
        <Home className="h-3.5 w-3.5" />
      </button>
      {path.map((folder, i) => (
        <div key={folder.folder_id} className="flex items-center gap-0.5 shrink-0">
          <ChevronRight className="pm-files-crumb-sep" />
          <button
            type="button"
            className={cn(
              "pm-files-crumb truncate",
              i === path.length - 1 && "is-current"
            )}
            onClick={() => {
              if (i < path.length - 1) selectFolder(collectionId, folder.folder_id)
            }}
          >
            {systemFolderDisplayName(folder.name, t)}
          </button>
        </div>
      ))}
    </div>
  )
}
