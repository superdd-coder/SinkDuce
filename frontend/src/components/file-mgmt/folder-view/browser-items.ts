import type { FileSummary, FolderTreeNode } from "@/types/file-mgmt"
import type { FolderFileSortMode } from "@/stores/file-mgmt-store"
import { getSubfolders, getVirtualViewFolders, groupRootFoldersForGrid, sortFolderGridItems, type FolderGridItem } from "./sorted-items"

export type BrowserFilter = "all" | "folder" | "file"

export function currentFolderItems(tree: FolderTreeNode[], folderId: string | null, files: FileSummary[], sort: FolderFileSortMode): FolderGridItem[] {
  const virtual = folderId ? getVirtualViewFolders(tree, folderId) : null
  if (virtual) return sortFolderGridItems(virtual, [], sort)
  const folders = getSubfolders(tree, folderId)
  return sortFolderGridItems(folderId ? folders : groupRootFoldersForGrid(folders), files, sort)
}

export function filterBrowserItems(items: FolderGridItem[], query: string, filter: BrowserFilter, folderLabel: (name: string) => string = name => name): FolderGridItem[] {
  const words = query.normalize("NFKC").toLocaleLowerCase().trim().split(/\s+/).filter(Boolean)
  return items.filter(item => {
    if (filter !== "all" && item.kind !== filter) return false
    const name = item.kind === "folder"
      ? `${item.folder.name} ${folderLabel(item.folder.name)}`
      : `${item.file.display_name || ""} ${item.file.filename || ""}`
    const normalized = name.normalize("NFKC").toLocaleLowerCase()
    return words.every(word => normalized.includes(word))
  })
}

export function browserPage(items: FolderGridItem[], requestedPage: number, pageSize: number) {
  const pages = Math.max(1, Math.ceil(items.length / pageSize))
  const page = Math.max(0, Math.min(requestedPage, pages - 1))
  const start = page * pageSize
  return { items: items.slice(start, start + pageSize), page, pages, start: items.length ? start + 1 : 0, end: Math.min(start + pageSize, items.length) }
}

export function folderAncestorIds(tree: FolderTreeNode[], id: string | null): string[] {
  if (!id) return []
  for (const folder of tree) {
    if (folder.folder_id === id) return [id]
    const children = folderAncestorIds(folder.children, id)
    if (children.length) return [folder.folder_id, ...children]
  }
  return []
}
