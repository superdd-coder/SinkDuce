import type { FileSummary, FolderTreeNode } from "@/types/file-mgmt"
import type { FolderFileSortMode } from "@/stores/file-mgmt-store"

export type FolderGridItem =
  | { kind: "folder"; folder: FolderTreeNode }
  | { kind: "file"; file: FileSummary }

export function getSubfolders(
  tree: FolderTreeNode[],
  parentId: string | null
): FolderTreeNode[] {
  if (parentId === "__archived__") return []
  if (!parentId) return tree
  const found = findFolderInTree(tree, parentId)
  return found?.children ?? []
}

export function findFolderInTree(
  tree: FolderTreeNode[],
  fid: string
): FolderTreeNode | null {
  for (const n of tree) {
    if (n.folder_id === fid) return n
    const found = findFolderInTree(n.children, fid)
    if (found) return found
  }
  return null
}

export function sortFolderGridItems(
  folders: FolderTreeNode[],
  files: FileSummary[],
  mode: FolderFileSortMode
): FolderGridItem[] {
  const items: FolderGridItem[] = [
    ...folders.map((folder) => ({ kind: "folder" as const, folder })),
    ...files.map((file) => ({ kind: "file" as const, file })),
  ]
  const latinCollator = new Intl.Collator("en", {
    numeric: true,
    sensitivity: "base",
  })
  /** V8/ICU pinyin order for Han; used only among CJK-leading names. */
  const pinyinCollator = new Intl.Collator("zh-Hans-u-co-pinyin", {
    numeric: true,
    sensitivity: "base",
  })
  const nameOf = (it: FolderGridItem) =>
    it.kind === "folder"
      ? it.folder.name || ""
      : it.file.display_name || it.file.filename || ""
  const startsWithHan = (s: string) => /^\p{Script=Han}/u.test(s.trim())
  const cmpName = (a: FolderGridItem, b: FolderGridItem) => {
    const na = nameOf(a)
    const nb = nameOf(b)
    const aHan = startsWithHan(na)
    const bHan = startsWithHan(nb)
    if (aHan !== bHan) return aHan ? 1 : -1
    if (aHan) return pinyinCollator.compare(na, nb)
    return latinCollator.compare(na, nb)
  }
  const typeRank = (it: FolderGridItem): string => {
    if (it.kind === "folder") {
      const k = it.folder.kind
      if (k === "system_group") return "0-system"
      if (k === "branch") return "1-branch"
      if (k === "user_group") return "2-group"
      return "3-plain"
    }
    const fromMeta = (it.file.original_ext || "").replace(/^\./, "").toLowerCase()
    if (fromMeta) return `4-${fromMeta}`
    const n = it.file.filename || ""
    const i = n.lastIndexOf(".")
    const ext = i > 0 ? n.slice(i + 1).toLowerCase() : ""
    return ext ? `4-${ext}` : "4-"
  }
  const createdOf = (it: FolderGridItem) =>
    it.kind === "folder" ? it.folder.created_at || "" : it.file.created_at || ""
  const updatedOf = (it: FolderGridItem) => {
    if (it.kind === "folder") {
      return (
        it.folder.content_updated_at ||
        it.folder.updated_at ||
        it.folder.created_at ||
        ""
      )
    }
    return it.file.updated_at || it.file.created_at || ""
  }

  if (mode === "type") {
    items.sort((a, b) => {
      const t = typeRank(a).localeCompare(typeRank(b))
      if (t !== 0) return t
      return cmpName(a, b)
    })
  } else if (mode === "created_desc" || mode === "created_asc") {
    const desc = mode === "created_desc"
    items.sort((a, b) => {
      const t = desc
        ? createdOf(b).localeCompare(createdOf(a))
        : createdOf(a).localeCompare(createdOf(b))
      if (t !== 0) return t
      return cmpName(a, b)
    })
  } else if (mode === "updated_desc" || mode === "updated_asc") {
    const desc = mode === "updated_desc"
    items.sort((a, b) => {
      const t = desc
        ? updatedOf(b).localeCompare(updatedOf(a))
        : updatedOf(a).localeCompare(updatedOf(b))
      if (t !== 0) return t
      return cmpName(a, b)
    })
  } else {
    items.sort(cmpName)
  }
  return items
}

export function formatItemDate(iso: string | undefined): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ""
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  })
}

export function itemDateIso(
  it: FolderGridItem,
  mode: FolderFileSortMode
): string {
  if (mode === "created_desc" || mode === "created_asc") {
    return it.kind === "folder"
      ? it.folder.created_at || ""
      : it.file.created_at || ""
  }
  if (it.kind === "folder") {
    return (
      it.folder.content_updated_at ||
      it.folder.updated_at ||
      it.folder.created_at ||
      ""
    )
  }
  return it.file.updated_at || it.file.created_at || ""
}

export function fileExtLabel(file: FileSummary): string {
  const fromMeta = (file.original_ext || "").replace(/^\./, "")
  if (fromMeta) return fromMeta.toUpperCase()
  const n = file.filename || ""
  const i = n.lastIndexOf(".")
  return i > 0 ? n.slice(i + 1).toUpperCase() : ""
}
