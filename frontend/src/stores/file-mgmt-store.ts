// Phase 6: Zustand store for file-mgmt folder view.
// Icon-grid layout: flat folder list + breadcrumb navigation.

import { create } from "zustand"
import type {
  FolderTreeNode,
  FileSummary,
  Message,
  Folder,
} from "@/types/file-mgmt"
import {
  getCollectionMessages,
  createCollectionMessage,
  getRootFiles,
  getFolderTree,
  getFolderFiles,
  getFolderMessages,
  getFileMessages,
  createFileMessage,
  createFolder,
  updateFolder,
  deleteFolder,
  uploadFileToFolder,
  uploadFolderToCollection,
  getArchivedFiles,
  removeFilePath,
  deleteFile,
  toggleFileArchive,
  addFilePath,
  createFolderMessage,
  updateMessage,
  deleteMessage,
  getFolder,
  getNameConflict,
  updateFile,
  type NameConflictDetail,
} from "@/api/file-mgmt"
import { enrichMessageSourceNames } from "@/components/file-mgmt/message-card"
import { toast } from "sonner"
import { getTasks } from "@/api/client"

/** Build folder_id → name map from tree for message source tags. */
function collectFolderNames(
  nodes: FolderTreeNode[],
  into: Map<string, string> = new Map()
): Map<string, string> {
  for (const n of nodes) {
    into.set(n.folder_id, n.name)
    if (n.children?.length) collectFolderNames(n.children, into)
  }
  return into
}

export type NameConflictState = {
  resource: "folder" | "file"
  name: string
  suggestedName: string
  message: string
  /** Called with the user-chosen name; should perform the original action. */
  retry: (newName: string) => Promise<void>
}

/** Wait for the Edit/Create silk dialog to finish closing before opening this one. */
const NAME_CONFLICT_AFTER_DIALOG_MS = 360

function queueNameConflict(payload: NameConflictState) {
  window.setTimeout(() => {
    useFileMgmtStore.setState({ nameConflict: payload })
  }, NAME_CONFLICT_AFTER_DIALOG_MS)
}

/**
 * Folder-view grid sort (persisted). Folders and files share the same order.
 * *_desc = newest first; *_asc = oldest first (re-click toggles direction).
 */
export type FolderFileSortMode =
  | "name"
  | "type"
  | "created_desc"
  | "created_asc"
  | "updated_desc"
  | "updated_asc"

export type FolderUploadConfirmState = {
  collectionId: string
  files: File[]
}

/** Per-collection sort cache in localStorage. Default mode is "type". */
const FOLDER_FILE_SORT_MAP_KEY = "sinkduce:folder-file-sort-by-collection"
/** @deprecated single global key — migrated once into the map */
const FOLDER_FILE_SORT_LEGACY_KEY = "sinkduce:folder-file-sort"

const SORT_MODES: FolderFileSortMode[] = [
  "name",
  "type",
  "created_desc",
  "created_asc",
  "updated_desc",
  "updated_asc",
]

const DEFAULT_FOLDER_FILE_SORT: FolderFileSortMode = "type"

function parseSortMode(raw: string | null | undefined): FolderFileSortMode | null {
  if (!raw) return null
  if ((SORT_MODES as string[]).includes(raw)) return raw as FolderFileSortMode
  // Legacy keys
  if (raw === "time" || raw === "time_desc") return "updated_desc"
  if (raw === "time_asc") return "updated_asc"
  return null
}

function loadSortMap(): Record<string, FolderFileSortMode> {
  try {
    const raw = localStorage.getItem(FOLDER_FILE_SORT_MAP_KEY)
    if (raw) {
      const obj = JSON.parse(raw) as Record<string, unknown>
      const out: Record<string, FolderFileSortMode> = {}
      for (const [k, v] of Object.entries(obj || {})) {
        const mode = parseSortMode(typeof v === "string" ? v : null)
        if (mode) out[k] = mode
      }
      return out
    }
  } catch {
    /* ignore */
  }
  return {}
}

function saveSortMap(map: Record<string, FolderFileSortMode>) {
  try {
    localStorage.setItem(FOLDER_FILE_SORT_MAP_KEY, JSON.stringify(map))
  } catch {
    /* ignore */
  }
}

/** Load sort for a collection; default = type. Migrates legacy global key once. */
export function loadFolderFileSort(collectionId: string): FolderFileSortMode {
  if (!collectionId) return DEFAULT_FOLDER_FILE_SORT
  const map = loadSortMap()
  if (map[collectionId]) return map[collectionId]
  // One-time migrate: old global preference becomes this collection's seed
  try {
    const legacy = parseSortMode(localStorage.getItem(FOLDER_FILE_SORT_LEGACY_KEY))
    if (legacy) {
      map[collectionId] = legacy
      saveSortMap(map)
      localStorage.removeItem(FOLDER_FILE_SORT_LEGACY_KEY)
      return legacy
    }
  } catch {
    /* ignore */
  }
  return DEFAULT_FOLDER_FILE_SORT
}

function persistFolderFileSort(
  collectionId: string,
  mode: FolderFileSortMode
) {
  if (!collectionId) return
  const map = loadSortMap()
  map[collectionId] = mode
  saveSortMap(map)
}

export function isCreatedSortMode(mode: FolderFileSortMode): boolean {
  return mode === "created_desc" || mode === "created_asc"
}

export function isUpdatedSortMode(mode: FolderFileSortMode): boolean {
  return mode === "updated_desc" || mode === "updated_asc"
}

/** Toggle create-time newest ↔ oldest; default newest. */
export function nextCreatedSortMode(
  current: FolderFileSortMode
): "created_desc" | "created_asc" {
  if (current === "created_desc") return "created_asc"
  if (current === "created_asc") return "created_desc"
  return "created_desc"
}

/** Toggle update-time newest ↔ oldest; default newest. */
export function nextUpdatedSortMode(
  current: FolderFileSortMode
): "updated_desc" | "updated_asc" {
  if (current === "updated_desc") return "updated_asc"
  if (current === "updated_asc") return "updated_desc"
  return "updated_desc"
}

/** Skip macOS junk and similar during folder upload. */
export function isSkippedUploadFile(file: File): boolean {
  const rel =
    (file as File & { webkitRelativePath?: string }).webkitRelativePath ||
    file.name ||
    ""
  const base = rel.split(/[/\\]/).pop() || file.name || ""
  if (base === ".DS_Store") return true
  if (base.startsWith("._")) return true // AppleDouble
  if (base === "Thumbs.db" || base === "desktop.ini") return true
  return false
}

// Check if a folder is the Archived virtual view (by folder_id or name)
function isArchivedFolder(folderId: string | null, folder?: Folder | null): boolean {
  if (folderId === "__archived__") return true
  if (folder?.name === "Archived" && folder?.kind === "system_group") return true
  return false
}

interface FileMgmtState {
  folderTree: FolderTreeNode[]
  folderTreeLoading: boolean

  currentFolderId: string | null
  currentFolder: Folder | null
  currentFolderFiles: FileSummary[]
  currentFolderMessages: Message[]
  filesLoading: boolean
  messagesLoading: boolean

  selectedFileIds: Set<string>
  selectedFolderIds: Set<string>
  /** When false: click file/folder = single-select; double-click folder = open. When true: click toggles multi-select. */
  multiSelectMode: boolean

  messageSidebarOpen: boolean
  /**
   * Folder message scope toggles (folder view sidebar).
   * Default: current folder messages only.
   * - messageIncludeFiles: files in current folder (or whole subtree if recursive)
   * - messageRecursive: every nested folder's own messages (+ files/nodes if on)
   * - messageIncludeNodes: nodes via bound group or branch chain folder
   */
  messageIncludeFiles: boolean
  messageRecursive: boolean
  messageIncludeNodes: boolean
  viewMode: "folder" | "timeline"
  uploadingTasks: Set<string>  // task IDs being polled
  /**
   * Files currently in async ingest (upload or new version).
   * Key = file_id → task progress for folder-view badges.
   */
  ingestingFiles: Record<
    string,
    { taskId: string; progress: number; message: string }
  >

  // Per-collection folder position cache
  perCollectionFolderCache: Record<string, string | null>

  // Folder navigation
  fetchFolderTree: (collectionId: string) => Promise<void>
  selectFolder: (collectionId: string, folderId: string) => Promise<void>
  navigateToRoot: (collectionId: string) => void
  createSubFolder: (
    collectionId: string,
    name: string,
    icon?: {
      icon_type?: string | null
      icon_value?: string | null
      icon_color?: string | null
    }
  ) => Promise<void>
  renameFolder: (collectionId: string, folderId: string, name: string, version: number) => Promise<void>
  /** Rename and/or update folder icon (plain / user_group / branch). */
  updateFolderDetails: (
    collectionId: string,
    folderId: string,
    version: number,
    patch: {
      name?: string
      icon_type?: string | null
      icon_value?: string | null
      icon_color?: string | null
    }
  ) => Promise<NameConflictDetail | null>
  /** Rename display filename of a file (current version). */
  renameFile: (
    collectionId: string,
    fileId: string,
    filename: string,
    version: number
  ) => Promise<NameConflictDetail | null>
  moveFolder: (collectionId: string, folderId: string, newParentId: string | null, version: number) => Promise<void>
  removeFolder: (collectionId: string, folderId: string) => Promise<void>
  toggleFolderSelection: (folderId: string) => void
  /** Replace selection with a single folder (normal / non-multi mode). */
  selectSingleFolder: (folderId: string) => void
  clearFolderSelection: () => void

  // Files
  /** Folder view sort for the active collection (default: type). */
  folderFileSort: FolderFileSortMode
  /** Load cached sort for a collection (or default type). Call on collection switch. */
  hydrateFolderFileSort: (collectionId: string) => void
  /** Persist sort for a collection and apply to the grid. */
  setFolderFileSort: (collectionId: string, mode: FolderFileSortMode) => void
  /** Pending folder-upload confirm (system Dialog, not window.confirm). */
  folderUploadConfirm: FolderUploadConfirmState | null
  cancelFolderUploadConfirm: () => void
  /** Always skips system junk (.DS_Store etc.) silently. */
  confirmFolderUpload: () => Promise<void>
  refreshFiles: (collectionId: string, opts?: { silent?: boolean }) => Promise<void>
  uploadFile: (collectionId: string, file: File) => Promise<void>
  /** Opens confirm dialog; actual upload runs after user confirms. */
  uploadFolder: (collectionId: string, files: File[]) => Promise<void>
  moveFilesToFolder: (collectionId: string, fileIds: string[], targetFolderId: string) => Promise<void>
  copyFilesToFolder: (collectionId: string, fileIds: string[], targetFolderId: string) => Promise<void>
  removeFilesFromCurrentFolder: (collectionId: string, fileIds: string[]) => Promise<void>
  /** Path-level: archive for current folder only */
  archiveFilesForFolder: (collectionId: string, fileIds: string[], files: FileSummary[]) => Promise<void>
  /** File-level: exclude from search */
  excludeFilesFromSearch: (collectionId: string, fileIds: string[], files: FileSummary[]) => Promise<void>
  permanentlyDeleteFiles: (collectionId: string, fileIds: string[]) => Promise<void>
  /** Clear file-level; with folder also clear that folder's path archives */
  unarchiveFiles: (collectionId: string, fileIds: string[], files: FileSummary[]) => Promise<void>
  toggleDefinitive: (collectionId: string, fileId: string, definitive: boolean, version: number) => Promise<void>
  toggleSelection: (fileId: string) => void
  /** Replace selection with a single file (normal / non-multi mode). */
  selectSingleFile: (fileId: string) => void
  selectAllFiles: () => void
  clearSelection: () => void
  setMultiSelectMode: (on: boolean) => void
  enterMultiSelectMode: () => void
  exitMultiSelectMode: () => void

  // Messages
  /** @param opts.silent Keep previous list visible (no spinner flash) while refetching. */
  refreshMessages: (
    collectionId: string,
    opts?: { silent?: boolean }
  ) => Promise<void>
  addMessage: (collectionId: string, body: string) => Promise<void>
  editMessage: (collectionId: string, messageId: string, body: string, version: number) => Promise<void>
  removeMessage: (collectionId: string, messageId: string) => Promise<void>

  // UI
  setViewMode: (mode: "folder" | "timeline") => void
  toggleMessageSidebar: () => void
  /** Explicit open/close — e.g. expand rail when Quick Chat opens */
  setMessageSidebarOpen: (open: boolean) => void
  setMessageIncludeFiles: (on: boolean) => void
  setMessageRecursive: (on: boolean) => void
  setMessageIncludeNodes: (on: boolean) => void
  /**
   * In-app jump: switch Database view to Timeline and focus a node.
   * Consumed by DatabaseView + TimelineView (never opens a new browser tab).
   */
  timelineNavRequest: { nodeId: string; chainId?: string } | null
  requestTimelineFocus: (nodeId: string, chainId?: string) => void
  clearTimelineNavRequest: () => void
  /**
   * Bumped after meeting/note ingest so a keep-mounted TimelineView
   * silent-refetches (DatabaseView stays mounted across sidebar switches).
   */
  timelineRefreshEpoch: number
  bumpTimelineRefresh: () => void
  /** Folder tree + current folder + keep-mounted Timeline after remote ingest/cancel. */
  refreshLibrarySurfaces: (collectionId: string) => Promise<void>

  /** Same-folder / sibling name conflict — rename dialog */
  nameConflict: NameConflictState | null
  resolveNameConflict: (newName: string) => Promise<void>
  cancelNameConflict: () => void

  // Internal: task polling (fileId links progress badge in folder grid)
  _startTaskPolling: (
    collectionId: string,
    taskId: string,
    fileId?: string | null,
    opts?: { silentToast?: boolean }
  ) => void
}

export const useFileMgmtStore = create<FileMgmtState>((set, get) => ({
  folderTree: [],
  folderTreeLoading: false,

  currentFolderId: null,
  currentFolder: null,
  currentFolderFiles: [],
  currentFolderMessages: [],
  filesLoading: false,
  messagesLoading: false,

  selectedFileIds: new Set<string>(),
  selectedFolderIds: new Set<string>(),
  multiSelectMode: false,
  messageSidebarOpen: true,
  messageIncludeFiles: false,
  messageRecursive: false,
  messageIncludeNodes: false,
  viewMode: "folder",
  timelineNavRequest: null,
  timelineRefreshEpoch: 0,
  uploadingTasks: new Set<string>(),
  ingestingFiles: {},
  perCollectionFolderCache: {},
  nameConflict: null,
  folderFileSort: DEFAULT_FOLDER_FILE_SORT,
  folderUploadConfirm: null,

  hydrateFolderFileSort: (collectionId) => {
    set({ folderFileSort: loadFolderFileSort(collectionId) })
  },

  setFolderFileSort: (collectionId, mode) => {
    persistFolderFileSort(collectionId, mode)
    set({ folderFileSort: mode })
  },

  cancelFolderUploadConfirm: () => set({ folderUploadConfirm: null }),

  confirmFolderUpload: async () => {
    const pending = get().folderUploadConfirm
    if (!pending) return
    const { collectionId } = pending
    const { currentFolderId, currentFolder } = get()
    if (currentFolderId && isArchivedFolder(currentFolderId, currentFolder)) {
      toast.error("Cannot upload to Archived")
      set({ folderUploadConfirm: null })
      return
    }
    // Always strip .DS_Store / AppleDouble / Thumbs.db — no UI toggle
    const files = pending.files.filter((f) => !isSkippedUploadFile(f))
    set({ folderUploadConfirm: null })
    if (files.length === 0) {
      toast.info("No files to upload")
      return
    }
    try {
      const results = await uploadFolderToCollection(
        collectionId,
        currentFolderId || "",
        files
      )
      await get().fetchFolderTree(collectionId)
      await get().refreshFiles(collectionId)
      const withTasks = results.filter((r) => r.task_id)
      if (withTasks.length > 0) {
        toast.info(
          `${files.length} files uploaded, ${withTasks.length} ingesting...`
        )
        for (const r of withTasks) {
          get()._startTaskPolling(collectionId, r.task_id!, r.file_id)
        }
      } else {
        toast.success(`${files.length} files uploaded`)
      }
    } catch (err) {
      toast.error(
        `Folder upload failed: ${err instanceof Error ? err.message : String(err)}`
      )
    }
  },

  resolveNameConflict: async (newName: string) => {
    const pending = get().nameConflict
    if (!pending) return
    const name = newName.trim()
    if (!name) {
      toast.error("Name is required")
      return
    }
    try {
      await pending.retry(name)
      set({ nameConflict: null })
    } catch (err) {
      const again = getNameConflict(err)
      if (again) {
        set({
          nameConflict: {
            resource: again.resource,
            name: again.name,
            suggestedName: again.suggested_name,
            message: again.message,
            retry: pending.retry,
          },
        })
        return
      }
      set({ nameConflict: null })
      toast.error(err instanceof Error ? err.message : String(err))
    }
  },

  cancelNameConflict: () => set({ nameConflict: null }),

  // ── Folder navigation ──

  fetchFolderTree: async (collectionId: string) => {
    set({ folderTreeLoading: true })
    try {
      const tree = await getFolderTree(collectionId)
      set({ folderTree: tree })
    } catch (err) {
      toast.error(`Failed to load folders: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      set({ folderTreeLoading: false })
    }
  },

  selectFolder: async (collectionId: string, folderId: string) => {
    set({
      currentFolderId: folderId,
      currentFolder: null,
      currentFolderFiles: [],
      currentFolderMessages: [],
      filesLoading: true,
      messagesLoading: true,
      selectedFileIds: new Set<string>(),
      selectedFolderIds: new Set<string>(),
      multiSelectMode: false,
      perCollectionFolderCache: { ...get().perCollectionFolderCache, [collectionId]: folderId },
    })

    // Fetch folder detail first (to check if it's Archived)
    let folder: Folder | null = null
    try {
      folder = await getFolder(collectionId, folderId)
      set({ currentFolder: folder })
    } catch {
      // non-critical — might be __archived__ virtual ID
    }

    const isArchived = isArchivedFolder(folderId, folder)

    if (!isArchived) {
      // Normal folder: fetch files + messages
      try {
        const files = await getFolderFiles(collectionId, folderId)
        set({ currentFolderFiles: files })
      } catch (err) {
        toast.error(`Failed to load files: ${err instanceof Error ? err.message : String(err)}`)
        set({ currentFolderFiles: [] })
      } finally {
        set({ filesLoading: false })
      }
      try {
        const {
          messageIncludeFiles,
          messageRecursive,
          messageIncludeNodes,
        } = get()
        const msgs = await getFolderMessages(
          collectionId,
          folderId,
          messageIncludeNodes,
          messageIncludeFiles,
          messageRecursive
        )
        const folderNameById = collectFolderNames(get().folderTree)
        // Always include the folder we just opened (tree may still be loading)
        const cf = get().currentFolder
        if (cf?.folder_id && cf.name) {
          folderNameById.set(cf.folder_id, cf.name)
        }
        if (folder?.folder_id && folder.name) {
          folderNameById.set(folder.folder_id, folder.name)
        }
        const fileNameById = new Map(
          get().currentFolderFiles.map((f) => [f.file_id, f.filename])
        )
        const enriched = await enrichMessageSourceNames(collectionId, msgs, {
          folderNameById,
          fileNameById,
        })
        set({ currentFolderMessages: enriched })
      } catch {
        // non-critical
      } finally {
        set({ messagesLoading: false })
      }
    } else {
      // Archived virtual view: fetch archived files, no messages
      try {
        const files = await getArchivedFiles(collectionId)
        set({ currentFolderFiles: files })
      } catch {
        set({ currentFolderFiles: [] })
      } finally {
        set({ filesLoading: false })
      }
      set({ messagesLoading: false, currentFolderMessages: [] })
    }
  },

  navigateToRoot: (collectionId: string) => {
    set({
      currentFolderId: null,
      currentFolder: null,
      currentFolderFiles: [],
      currentFolderMessages: [],
      selectedFileIds: new Set(),
      selectedFolderIds: new Set(),
      multiSelectMode: false,
      folderTree: [],
      perCollectionFolderCache: { ...get().perCollectionFolderCache, [collectionId]: null },
    })
    get().fetchFolderTree(collectionId)
    get().refreshFiles(collectionId)
    // Load root / collection messages (was previously cleared and never re-fetched)
    void get().refreshMessages(collectionId)
  },

  createSubFolder: async (collectionId, name, icon) => {
    const { currentFolderId } = get()
    const doCreate = async (folderName: string) => {
      await createFolder(collectionId, {
        name: folderName,
        parent_folder_id: currentFolderId,
        kind: "plain",
        ...(icon ?? {}),
      })
      await get().fetchFolderTree(collectionId)
      toast.success(`Folder "${folderName}" created`)
    }
    try {
      await doCreate(name)
    } catch (err) {
      const conflict = getNameConflict(err)
      if (conflict) {
        queueNameConflict({
          resource: "folder",
          name: conflict.name,
          suggestedName: conflict.suggested_name,
          message: conflict.message,
          retry: doCreate,
        })
        return
      }
      toast.error(`Failed to create folder: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  renameFolder: async (collectionId: string, folderId: string, name: string, version: number) => {
    await get().updateFolderDetails(collectionId, folderId, version, { name })
  },

  updateFolderDetails: async (collectionId, folderId, version, patch) => {
    const doUpdate = async (nameOverride?: string) => {
      const name = nameOverride ?? patch.name
      await updateFolder(collectionId, folderId, {
        version,
        ...(name !== undefined ? { name } : {}),
        ...(patch.icon_type !== undefined ? { icon_type: patch.icon_type } : {}),
        ...(patch.icon_value !== undefined ? { icon_value: patch.icon_value } : {}),
        ...(patch.icon_color !== undefined ? { icon_color: patch.icon_color } : {}),
      })
      await get().fetchFolderTree(collectionId)
      if (get().currentFolderId === folderId && name) {
        set({
          currentFolder: get().currentFolder
            ? { ...get().currentFolder!, name }
            : null,
        })
      }
      toast.success("Folder updated")
    }
    try {
      await doUpdate()
      return null
    } catch (err) {
      const conflict = getNameConflict(err)
      if (conflict && patch.name !== undefined) {
        return conflict
      }
      toast.error(
        `Failed to update folder: ${err instanceof Error ? err.message : String(err)}`
      )
      return null
    }
  },

  renameFile: async (collectionId, fileId, filename, version) => {
    const doRename = async (newName: string) => {
      await updateFile(collectionId, fileId, { filename: newName, version })
      await get().refreshFiles(collectionId)
      toast.success("File renamed")
    }
    try {
      await doRename(filename)
      return null
    } catch (err) {
      const conflict = getNameConflict(err)
      if (conflict) return conflict
      toast.error(
        `Failed to rename file: ${err instanceof Error ? err.message : String(err)}`
      )
      return null
    }
  },

  moveFolder: async (collectionId: string, folderId: string, newParentId: string | null, version: number) => {
    const doMove = async (_ignoredName?: string) => {
      // Name conflict on move uses suggested folder name via rename+move;
      // if only parent changes, retry same version after user renames first.
      await updateFolder(collectionId, folderId, {
        parent_folder_id: newParentId,
        version,
      })
      await get().fetchFolderTree(collectionId)
      toast.success("Folder moved")
    }
    try {
      await doMove()
    } catch (err) {
      const conflict = getNameConflict(err)
      if (conflict) {
        set({
          nameConflict: {
            resource: "folder",
            name: conflict.name,
            suggestedName: conflict.suggested_name,
            message: conflict.message,
            retry: async (newName) => {
              await updateFolder(collectionId, folderId, {
                name: newName,
                parent_folder_id: newParentId,
                version,
              })
              await get().fetchFolderTree(collectionId)
              toast.success("Folder moved")
            },
          },
        })
        return
      }
      toast.error(`Failed to move: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  removeFolder: async (collectionId: string, folderId: string) => {
    try {
      await deleteFolder(collectionId, folderId)
      await get().fetchFolderTree(collectionId)
      if (get().currentFolderId === folderId) {
        set({ currentFolderId: null, currentFolder: null, currentFolderFiles: [], currentFolderMessages: [] })
      }
      toast.success("Folder deleted")
    } catch (err) {
      toast.error(`Failed to delete folder: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  // ── Files ──

  refreshFiles: async (collectionId: string, opts?: { silent?: boolean }) => {
    const { currentFolderId, currentFolder } = get()
    const silent = !!opts?.silent
    if (!silent) set({ filesLoading: true })
    try {
      if (currentFolderId && isArchivedFolder(currentFolderId, currentFolder)) {
        // Virtual /Archived: all file-level archives
        const files = await getArchivedFiles(collectionId)
        set({ currentFolderFiles: files })
      } else if (currentFolderId) {
        const files = await getFolderFiles(collectionId, currentFolderId)
        set({ currentFolderFiles: files })
      } else {
        // Root level: orphan files
        const files = await getRootFiles(collectionId)
        set({ currentFolderFiles: files })
      }
    } catch {
      set({ currentFolderFiles: [] })
    } finally {
      if (!silent) set({ filesLoading: false })
    }
  },

  uploadFile: async (collectionId: string, file: File) => {
    const { currentFolderId, currentFolder } = get()
    if (currentFolderId && isArchivedFolder(currentFolderId, currentFolder)) {
      toast.error("Cannot upload to Archived")
      return
    }
    const folderId = currentFolderId || ""
    const doUpload = async (displayName: string) => {
      const payload =
        displayName === file.name
          ? file
          : new File([file], displayName, { type: file.type })
      const result = await uploadFileToFolder(collectionId, folderId, payload)
      await get().refreshFiles(collectionId)
      if (result.unsupported) {
        toast.warning(
          `"${displayName}" uploaded — file type not supported for ingest`
        )
      } else if (result.task_id) {
        toast.info(`"${displayName}" uploaded, ingesting...`)
        get()._startTaskPolling(collectionId, result.task_id, result.file_id)
      } else {
        toast.success(`"${displayName}" uploaded`)
      }
    }
    try {
      await doUpload(file.name)
    } catch (err) {
      const conflict = getNameConflict(err)
      if (conflict) {
        set({
          nameConflict: {
            resource: "file",
            name: conflict.name,
            suggestedName: conflict.suggested_name,
            message: conflict.message,
            retry: doUpload,
          },
        })
        return
      }
      toast.error(`Upload failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  uploadFolder: async (collectionId: string, files: File[]) => {
    const { currentFolderId, currentFolder } = get()
    if (currentFolderId && isArchivedFolder(currentFolderId, currentFolder)) {
      toast.error("Cannot upload to Archived")
      return
    }
    const list = Array.from(files)
    if (list.length === 0) {
      toast.info("No files selected")
      return
    }
    // Open system confirm dialog (skip .DS_Store by default there)
    set({ folderUploadConfirm: { collectionId, files: list } })
  },

  moveFilesToFolder: async (collectionId: string, fileIds: string[], targetFolderId: string) => {
    const { currentFolderId, currentFolderFiles } = get()

    const moveOne = async (fid: string, renameTo?: string) => {
      if (renameTo) {
        const f = currentFolderFiles.find((x) => x.file_id === fid)
        await updateFile(collectionId, fid, {
          filename: renameTo,
          version: f?.version ?? 1,
        })
      }
      await addFilePath(collectionId, fid, targetFolderId)
      if (currentFolderId && currentFolderId !== targetFolderId) {
        try {
          const { getFileDetail } = await import("@/api/file-mgmt")
          const detail = await getFileDetail(collectionId, fid)
          const pathInFolder = detail.paths?.find(
            (p) => p.folder_id === currentFolderId
          )
          if (pathInFolder) {
            await removeFilePath(collectionId, fid, pathInFolder.path_id)
          }
        } catch {
          /* skip */
        }
      }
    }

    try {
      for (const fid of fileIds) {
        try {
          await moveOne(fid)
        } catch (err) {
          const conflict = getNameConflict(err)
          if (conflict) {
            // Pause remaining; user renames this file then we retry only this one
            await new Promise<void>((resolve, reject) => {
              set({
                nameConflict: {
                  resource: "file",
                  name: conflict.name,
                  suggestedName: conflict.suggested_name,
                  message: conflict.message,
                  retry: async (newName) => {
                    try {
                      await moveOne(fid, newName)
                      resolve()
                    } catch (e) {
                      reject(e)
                    }
                  },
                },
              })
            })
            continue
          }
          throw err
        }
      }
      await get().refreshFiles(collectionId)
      set({ selectedFileIds: new Set() })
      toast.success(`${fileIds.length} file(s) moved`)
    } catch (err) {
      toast.error(`Move failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  copyFilesToFolder: async (collectionId: string, fileIds: string[], targetFolderId: string) => {
    const { currentFolderFiles } = get()

    const linkOne = async (fid: string, renameTo?: string) => {
      if (renameTo) {
        const f = currentFolderFiles.find((x) => x.file_id === fid)
        await updateFile(collectionId, fid, {
          filename: renameTo,
          version: f?.version ?? 1,
        })
      }
      await addFilePath(collectionId, fid, targetFolderId)
    }

    try {
      for (const fid of fileIds) {
        try {
          await linkOne(fid)
        } catch (err) {
          const conflict = getNameConflict(err)
          if (conflict) {
            await new Promise<void>((resolve, reject) => {
              set({
                nameConflict: {
                  resource: "file",
                  name: conflict.name,
                  suggestedName: conflict.suggested_name,
                  message: conflict.message,
                  retry: async (newName) => {
                    try {
                      await linkOne(fid, newName)
                      resolve()
                    } catch (e) {
                      reject(e)
                    }
                  },
                },
              })
            })
            continue
          }
          throw err
        }
      }
      await get().refreshFiles(collectionId)
      set({ selectedFileIds: new Set() })
      toast.success(`${fileIds.length} file(s) linked to folder`)
    } catch (err) {
      toast.error(`Link failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  removeFilesFromCurrentFolder: async (collectionId: string, fileIds: string[]) => {
    try {
      const { currentFolderId } = get()
      if (!currentFolderId) return
      await Promise.all(
        fileIds.map(async (fid) => {
          try {
            const { getFileDetail } = await import("@/api/file-mgmt")
            const detail = await getFileDetail(collectionId, fid)
            const pathInFolder = detail.paths?.find((p) => p.folder_id === currentFolderId)
            if (pathInFolder) {
              await removeFilePath(collectionId, fid, pathInFolder.path_id)
            }
          } catch {
            // skip
          }
        })
      )
      await get().refreshFiles(collectionId)
      set({ selectedFileIds: new Set() })
      toast.success(`${fileIds.length} file(s) unlinked from this folder`)
    } catch (err) {
      toast.error(`Unlink failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  archiveFilesForFolder: async (collectionId: string, fileIds: string[], files: FileSummary[]) => {
    const folderId = get().currentFolderId
    if (!folderId || folderId === "__archived__") {
      toast.error("Open a folder to archive for this folder")
      return
    }
    try {
      await Promise.all(
        fileIds.map((fid) => {
          const f = files.find((x) => x.file_id === fid)
          return toggleFileArchive(collectionId, fid, true, f?.version ?? 1, {
            folderId,
            scope: "path",
          })
        })
      )
      await get().refreshFiles(collectionId)
      set({ selectedFileIds: new Set() })
      toast.success(`${fileIds.length} file(s) archived for this folder`)
    } catch (err) {
      toast.error(`Archive failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  excludeFilesFromSearch: async (collectionId: string, fileIds: string[], files: FileSummary[]) => {
    try {
      await Promise.all(
        fileIds.map((fid) => {
          const f = files.find((x) => x.file_id === fid)
          return toggleFileArchive(collectionId, fid, true, f?.version ?? 1, {
            scope: "file",
          })
        })
      )
      await get().refreshFiles(collectionId)
      set({ selectedFileIds: new Set() })
      toast.success(`${fileIds.length} file(s) excluded from search`)
    } catch (err) {
      toast.error(`Exclude failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  permanentlyDeleteFiles: async (collectionId: string, fileIds: string[]) => {
    try {
      await Promise.all(fileIds.map((fid) => deleteFile(collectionId, fid)))
      await get().refreshFiles(collectionId)
      set({ selectedFileIds: new Set() })
      toast.success(`${fileIds.length} file(s) permanently deleted`)
    } catch (err) {
      toast.error(`Delete failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  unarchiveFiles: async (collectionId: string, fileIds: string[], files: FileSummary[]) => {
    try {
      // Folder: file-level + this folder's paths
      // /Archived: file-level only (path archives left for per-folder Unarchive)
      const folderId = get().currentFolderId
      const inArchivedView =
        !folderId ||
        folderId === "__archived__" ||
        isArchivedFolder(folderId, get().currentFolder)
      await Promise.all(
        fileIds.map((fid) => {
          const f = files.find((x) => x.file_id === fid)
          return toggleFileArchive(collectionId, fid, false, f?.version ?? 1, {
            folderId: inArchivedView ? null : folderId,
          })
        })
      )
      await get().refreshFiles(collectionId)
      set({ selectedFileIds: new Set() })
      toast.success(
        inArchivedView
          ? `${fileIds.length} file(s) restored to search (folder archives unchanged)`
          : `${fileIds.length} file(s) restored`
      )
    } catch (err) {
      toast.error(`Unarchive failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  toggleDefinitive: async (collectionId: string, fileId: string, definitive: boolean, version: number) => {
    try {
      const ver = Number(version)
      if (!Number.isFinite(ver) || ver < 1) {
        toast.error("Cannot update definitive: missing file version")
        return
      }

      // Backend owns: is_definitive + optional doc_summary gen + consolidate debounce
      await updateFile(collectionId, fileId, {
        is_definitive: definitive,
        version: ver,
      })

      await get().refreshFiles(collectionId)
      // INFO panel: silent refresh definitive list + (after debounce) summary
      const { triggerInfoRefresh } = await import("@/lib/info-refresh")
      triggerInfoRefresh({ collectionId, reason: "definitive" })
      toast.success(
        definitive
          ? "Marked definitive — feeds Collection Summary"
          : "Cleared definitive"
      )
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  toggleSelection: (fileId: string) => {
    // Ingesting files cannot be selected (no file toolbar actions).
    if (get().ingestingFiles[fileId]) return
    if (!get().multiSelectMode) {
      get().selectSingleFile(fileId)
      return
    }
    set((s) => {
      const next = new Set(s.selectedFileIds)
      if (next.has(fileId)) next.delete(fileId)
      else next.add(fileId)
      return { selectedFileIds: next, selectedFolderIds: new Set() }
    })
  },

  selectSingleFile: (fileId: string) => {
    if (get().ingestingFiles[fileId]) return
    set({
      selectedFileIds: new Set([fileId]),
      selectedFolderIds: new Set(),
    })
  },

  selectAllFiles: () => {
    if (!get().multiSelectMode) return
    const { currentFolderFiles, ingestingFiles } = get()
    set({
      selectedFileIds: new Set(
        currentFolderFiles
          .map((f) => f.file_id)
          .filter((id) => !ingestingFiles[id])
      ),
      selectedFolderIds: new Set(),
    })
  },

  clearSelection: () => {
    set({ selectedFileIds: new Set() })
  },

  setMultiSelectMode: (on: boolean) => {
    if (on) {
      set({ multiSelectMode: true })
    } else {
      set({
        multiSelectMode: false,
        selectedFileIds: new Set(),
        selectedFolderIds: new Set(),
      })
    }
  },

  enterMultiSelectMode: () => {
    set({ multiSelectMode: true })
  },

  exitMultiSelectMode: () => {
    set({
      multiSelectMode: false,
      selectedFileIds: new Set(),
      selectedFolderIds: new Set(),
    })
  },

  toggleFolderSelection: (folderId: string) => {
    if (!get().multiSelectMode) {
      get().selectSingleFolder(folderId)
      return
    }
    set((s) => {
      const next = new Set(s.selectedFolderIds)
      if (next.has(folderId)) next.delete(folderId)
      else next.add(folderId)
      return { selectedFolderIds: next, selectedFileIds: new Set() }
    })
  },

  selectSingleFolder: (folderId: string) => {
    set({
      selectedFolderIds: new Set([folderId]),
      selectedFileIds: new Set(),
    })
  },

  clearFolderSelection: () => {
    set({ selectedFolderIds: new Set() })
  },

  // ── Messages ──

  refreshMessages: async (collectionId: string, opts?: { silent?: boolean }) => {
    const {
      currentFolderId,
      currentFolder,
      messageIncludeFiles,
      messageRecursive,
      messageIncludeNodes,
      selectedFileIds,
      selectedFolderIds,
    } = get()
    const selFiles = Array.from(selectedFileIds)
    const selFolders = Array.from(selectedFolderIds)

    // Selection-driven scope:
    // - exactly 1 file → that file's messages
    // - exactly 1 folder → that folder (with Nested/Files/Nodes flags)
    // - else → current navigated folder / collection root
    const focusFileId =
      selFiles.length === 1 && selFolders.length === 0 ? selFiles[0] : null
    const focusFolderId =
      selFolders.length === 1 && selFiles.length === 0 ? selFolders[0] : null

    const navArchived =
      currentFolderId && isArchivedFolder(currentFolderId, currentFolder)
    if (!focusFileId && !focusFolderId && navArchived) {
      if (!opts?.silent) set({ messagesLoading: false, currentFolderMessages: [] })
      return
    }
    if (focusFolderId === "__archived__") {
      if (!opts?.silent) set({ messagesLoading: false, currentFolderMessages: [] })
      else set({ currentFolderMessages: [] })
      return
    }

    if (!opts?.silent) set({ messagesLoading: true })
    try {
      let msgs: Message[]
      if (focusFileId) {
        msgs = await getFileMessages(collectionId, focusFileId)
      } else {
        const folderScope = focusFolderId ?? currentFolderId
        msgs = folderScope
          ? await getFolderMessages(
              collectionId,
              folderScope,
              messageIncludeNodes,
              messageIncludeFiles,
              messageRecursive
            )
          : await getCollectionMessages(collectionId, {
              includeNodeMsgs: messageIncludeNodes,
              includeFileMsgs: messageIncludeFiles,
              recursive: messageRecursive,
            })
      }
      const folderNameById = collectFolderNames(get().folderTree)
      const cf = get().currentFolder
      if (cf?.folder_id && cf.name) {
        folderNameById.set(cf.folder_id, cf.name)
      }
      const fileNameById = new Map(
        get().currentFolderFiles.map((f) => [f.file_id, f.filename])
      )
      const enriched = await enrichMessageSourceNames(collectionId, msgs, {
        folderNameById,
        fileNameById,
      })
      set({ currentFolderMessages: enriched })
    } catch {
      // ignore
    } finally {
      if (!opts?.silent) set({ messagesLoading: false })
    }
  },

  addMessage: async (collectionId: string, body: string) => {
    const { currentFolderId, selectedFileIds, selectedFolderIds } = get()
    const selFiles = Array.from(selectedFileIds)
    const selFolders = Array.from(selectedFolderIds)
    const focusFileId =
      selFiles.length === 1 && selFolders.length === 0 ? selFiles[0] : null
    const focusFolderId =
      selFolders.length === 1 && selFiles.length === 0 ? selFolders[0] : null
    try {
      let msg: Message
      if (focusFileId) {
        msg = await createFileMessage(collectionId, focusFileId, {
          owner_type: "file",
          owner_id: focusFileId,
          body,
          author_type: "user",
        })
      } else {
        const folderOwner = focusFolderId ?? currentFolderId
        if (folderOwner && folderOwner !== "__archived__") {
          msg = await createFolderMessage(collectionId, folderOwner, {
            owner_type: "folder",
            owner_id: folderOwner,
            body,
            author_type: "user",
          })
        } else {
          msg = await createCollectionMessage(collectionId, {
            owner_type: "collection",
            owner_id: collectionId,
            body,
            author_type: "user",
          })
        }
      }
      const folderNameById = collectFolderNames(get().folderTree)
      const fileNameById = new Map(
        get().currentFolderFiles.map((f) => [f.file_id, f.filename])
      )
      const [enriched] = await enrichMessageSourceNames(collectionId, [msg], {
        folderNameById,
        fileNameById,
      })
      set((s) => ({
        currentFolderMessages: [enriched, ...s.currentFolderMessages],
      }))
      toast.success("Message added")
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  editMessage: async (collectionId: string, messageId: string, body: string, version: number) => {
    try {
      const updated = await updateMessage(collectionId, messageId, { body, version })
      const folderNameById = collectFolderNames(get().folderTree)
      const [enriched] = await enrichMessageSourceNames(collectionId, [updated], {
        folderNameById,
      })
      set((s) => ({
        currentFolderMessages: s.currentFolderMessages.map((m) =>
          m.message_id === messageId ? enriched : m
        ),
      }))
      toast.success("Message updated")
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  removeMessage: async (collectionId: string, messageId: string) => {
    try {
      await deleteMessage(collectionId, messageId)
      set((s) => ({
        currentFolderMessages: s.currentFolderMessages.filter((m) => m.message_id !== messageId),
      }))
      toast.success("Message deleted")
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  setViewMode: (mode: "folder" | "timeline") => {
    set({ viewMode: mode })
  },

  toggleMessageSidebar: () => {
    set((s) => ({ messageSidebarOpen: !s.messageSidebarOpen }))
  },

  setMessageSidebarOpen: (open: boolean) => {
    set({ messageSidebarOpen: open })
  },

  setMessageIncludeFiles: (on: boolean) => {
    set({ messageIncludeFiles: on })
  },

  setMessageRecursive: (on: boolean) => {
    set({ messageRecursive: on })
  },

  setMessageIncludeNodes: (on: boolean) => {
    set({ messageIncludeNodes: on })
  },

  requestTimelineFocus: (nodeId, chainId) => {
    set({
      timelineNavRequest: { nodeId, chainId },
      viewMode: "timeline",
    })
  },

  clearTimelineNavRequest: () => {
    set({ timelineNavRequest: null })
  },

  bumpTimelineRefresh: () => {
    set((s) => ({ timelineRefreshEpoch: s.timelineRefreshEpoch + 1 }))
  },

  refreshLibrarySurfaces: async (collectionId) => {
    const colId = (collectionId || "").trim()
    if (!colId) return
    await get().fetchFolderTree(colId)
    await get().refreshFiles(colId, { silent: true })
    get().bumpTimelineRefresh()
  },

  _startTaskPolling: (collectionId, taskId, fileId = null, opts) => {
    const fid = fileId || null
    const silentToast = !!opts?.silentToast
    set((s) => {
      const next = new Set(s.uploadingTasks)
      next.add(taskId)
      const ingestingFiles = { ...s.ingestingFiles }
      // Drop selection so file action toolbar does not appear for ingesting files
      const selectedFileIds = new Set(s.selectedFileIds)
      if (fid) {
        ingestingFiles[fid] = {
          taskId,
          progress: 0,
          message: "Queued for processing",
        }
        selectedFileIds.delete(fid)
      }
      return { uploadingTasks: next, ingestingFiles, selectedFileIds }
    })

    const clearIngesting = () => {
      set((s) => {
        const next = new Set(s.uploadingTasks)
        next.delete(taskId)
        const ingestingFiles = { ...s.ingestingFiles }
        if (fid && ingestingFiles[fid]?.taskId === taskId) {
          delete ingestingFiles[fid]
        }
        // Also drop any entry still pointing at this taskId
        for (const [k, v] of Object.entries(ingestingFiles)) {
          if (v.taskId === taskId) delete ingestingFiles[k]
        }
        return { uploadingTasks: next, ingestingFiles }
      })
    }

    const poll = async () => {
      try {
        const res = await getTasks(collectionId)
        const task = res.tasks.find((t) => t.id === taskId)
        if (!task) {
          clearIngesting()
          return
        }
        if (task.status === "completed") {
          clearIngesting()
          await get().refreshFiles(collectionId, { silent: true })
          await get().refreshMessages(collectionId)
          if (!silentToast) {
            toast.success(`Ingest complete: ${task.filename}`)
          }
        } else if (task.status === "failed") {
          clearIngesting()
          await get().refreshFiles(collectionId, { silent: true })
          await get().refreshMessages(collectionId)
          if (!silentToast) {
            toast.error(
              `Ingest failed: ${task.filename} — ${task.error || "unknown error"}`
            )
          }
        } else {
          // Update progress badge while pending/processing
          if (fid) {
            set((s) => {
              if (!s.ingestingFiles[fid]) return s
              return {
                ingestingFiles: {
                  ...s.ingestingFiles,
                  [fid]: {
                    taskId,
                    progress: task.progress ?? 0,
                    message: task.message || "Processing…",
                  },
                },
              }
            })
          }
          setTimeout(poll, 1500)
        }
      } catch {
        setTimeout(poll, 3000)
      }
    }
    setTimeout(poll, 800)
  },
}))
