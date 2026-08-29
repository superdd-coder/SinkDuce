import type { FolderTreeNode } from "@/types/file-mgmt"

export type FolderMoveDest = {
  id: string | null
  name: string
  depth: number
  kind: string
}

function collectDescendantIds(node: FolderTreeNode, into: Set<string>) {
  into.add(node.folder_id)
  for (const c of node.children || []) collectDescendantIds(c, into)
}

function findInTree(
  tree: FolderTreeNode[],
  fid: string
): FolderTreeNode | null {
  for (const n of tree) {
    if (n.folder_id === fid) return n
    const found = findInTree(n.children || [], fid)
    if (found) return found
  }
  return null
}

export function blockedMoveFolderIds(
  tree: FolderTreeNode[],
  movingIds: string[]
): Set<string> {
  const blocked = new Set<string>()
  for (const id of movingIds) {
    const n = findInTree(tree, id)
    if (n) collectDescendantIds(n, blocked)
  }
  return blocked
}

export function isFolderMoveSelectable(
  n: FolderTreeNode,
  opts: { currentParentId: string | null; blocked: Set<string> }
): boolean {
  if (n.kind !== "plain" && n.kind !== "branch") return false
  if (n.name === "Archived") return false
  if (n.folder_id === opts.currentParentId) return false
  if (opts.blocked.has(n.folder_id)) return false
  return true
}

/** Files may land in any folder except the current one and Archived. */
export function isFileMoveSelectable(
  n: FolderTreeNode,
  currentFolderId: string | null
): boolean {
  if (n.name === "Archived") return false
  if (currentFolderId && n.folder_id === currentFolderId) return false
  return true
}

/** Destinations for moving plain folders: root, plain, branch. */
export function collectFolderMoveDestinations(
  tree: FolderTreeNode[],
  opts: {
    currentParentId: string | null
    movingIds: string[]
  }
): FolderMoveDest[] {
  const blocked = blockedMoveFolderIds(tree, opts.movingIds)
  const result: FolderMoveDest[] = []
  if (opts.currentParentId != null) {
    result.push({ id: null, name: "Root", depth: 0, kind: "root" })
  }

  const walk = (nodes: FolderTreeNode[], depth: number) => {
    for (const n of nodes) {
      if (isFolderMoveSelectable(n, { currentParentId: opts.currentParentId, blocked })) {
        result.push({
          id: n.folder_id,
          name: n.name,
          depth,
          kind: n.kind,
        })
      }
      if (n.children?.length) walk(n.children, depth + 1)
    }
  }
  walk(tree, opts.currentParentId != null ? 1 : 0)
  return result
}
