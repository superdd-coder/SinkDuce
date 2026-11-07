import { useMemo, useState } from "react"
import { ChevronRight, FolderTree, Home } from "lucide-react"
import { Popover } from "@/components/ui/popover"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { useShallow } from "zustand/react/shallow"
import { useT } from "@/i18n/use-t"
import { systemFolderDisplayName } from "@/i18n/system-folder"
import { FolderIconView } from "@/components/file-mgmt/timeline-view/group-icons"
import { groupRootFoldersForGrid } from "./sorted-items"
import { folderAncestorIds } from "./browser-items"
import type { FolderTreeNode } from "@/types/file-mgmt"

function FolderBranch({ folder, depth, currentId, expanded, toggle, choose }: {
  folder: FolderTreeNode; depth: number; currentId: string | null
  expanded: Set<string>; toggle: (id: string) => void; choose: (id: string) => void
}) {
  const t = useT()
  const name = systemFolderDisplayName(folder.name, t)
  const hasChildren = folder.children.length > 0
  const open = expanded.has(folder.folder_id)
  return <li>
    <div className="sd-folder-branch" data-active={currentId === folder.folder_id || undefined} style={{ paddingLeft: 6 + depth * 16 }}>
      {hasChildren ? <button className="sd-folder-disclosure" aria-label={t(open ? "fileBrowser.collapseFolder" : "fileBrowser.expandFolder", { name })} aria-expanded={open} onClick={() => toggle(folder.folder_id)}><ChevronRight size={13} style={{ transform: open ? "rotate(90deg)" : undefined }} /></button> : <span className="sd-folder-disclosure" />}
      <button className="sd-folder-link" title={name} aria-current={currentId === folder.folder_id ? "location" : undefined} onClick={() => choose(folder.folder_id)}>
        <FolderIconView folder={folder} className="size-4 shrink-0" />
        <span>{name}</span><small title={t("fileMgmt.fileAndFolderCounts", { files: folder.file_count, folders: folder.children.length })}>{folder.file_count + folder.children.length || ""}</small>
      </button>
    </div>
    {hasChildren && open && <ul>{folder.children.map(child => <FolderBranch key={child.folder_id} folder={child} depth={depth + 1} currentId={currentId} expanded={expanded} toggle={toggle} choose={choose} />)}</ul>}
  </li>
}

export function FolderNavigator({ collectionId }: { collectionId: string }) {
  const t = useT()
  const { folderTree, currentFolderId, selectFolder, navigateToRoot } = useFileMgmtStore(useShallow(s => ({ folderTree: s.folderTree, currentFolderId: s.currentFolderId, selectFolder: s.selectFolder, navigateToRoot: s.navigateToRoot })))
  const tree = useMemo(() => groupRootFoldersForGrid(folderTree), [folderTree])
  const [open, setOpen] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const choose = (id: string | null) => {
    if (id) void selectFolder(collectionId, id)
    else void navigateToRoot(collectionId)
    setOpen(false)
  }
  return <Popover.Root open={open} onOpenChange={next => {
    setOpen(next)
    if (next) setExpanded(new Set(folderAncestorIds(tree, currentFolderId)))
  }}>
    <Popover.Trigger className="sd-files-icon" title={t("fileBrowser.folderTree")} aria-label={t("fileBrowser.folderTree")}><FolderTree size={17} /></Popover.Trigger>
    <Popover.Portal><Popover.Positioner side="bottom" align="start" sideOffset={10} className="sd-folder-positioner">
      <Popover.Popup className="sd-folder-popover" aria-label={t("fileBrowser.folderTree")}>
        <Popover.Title className="sd-folder-popover-title">{t("fileBrowser.folderTree")}</Popover.Title>
        <button className="sd-folder-root" aria-current={currentFolderId === null ? "location" : undefined} onClick={() => choose(null)}><Home size={16} />{t("common.root")}</button>
        <ul>{tree.map(folder => <FolderBranch key={folder.folder_id} folder={folder} depth={0} currentId={currentFolderId} expanded={expanded} toggle={id => setExpanded(previous => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next })} choose={choose} />)}</ul>
      </Popover.Popup>
    </Popover.Positioner></Popover.Portal>
  </Popover.Root>
}
