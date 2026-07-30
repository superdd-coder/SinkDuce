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
} from "@/api/file-mgmt"
import { toast } from "sonner"
import { getTasks } from "@/api/client"

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

  messageSidebarOpen: boolean
  viewMode: "folder" | "timeline"
  uploadingTasks: Set<string>  // task IDs being polled

  // Per-collection folder position cache
  perCollectionFolderCache: Record<string, string | null>

  // Folder navigation
  fetchFolderTree: (collectionId: string) => Promise<void>
  selectFolder: (collectionId: string, folderId: string) => Promise<void>
  navigateToRoot: (collectionId: string) => void
  createSubFolder: (collectionId: string, name: string) => Promise<void>
  renameFolder: (collectionId: string, folderId: string, name: string, version: number) => Promise<void>
  moveFolder: (collectionId: string, folderId: string, newParentId: string | null, version: number) => Promise<void>
  removeFolder: (collectionId: string, folderId: string) => Promise<void>
  toggleFolderSelection: (folderId: string) => void
  clearFolderSelection: () => void

  // Files
  refreshFiles: (collectionId: string) => Promise<void>
  uploadFile: (collectionId: string, file: File) => Promise<void>
  uploadFolder: (collectionId: string, files: File[]) => Promise<void>
  moveFilesToFolder: (collectionId: string, fileIds: string[], targetFolderId: string) => Promise<void>
  copyFilesToFolder: (collectionId: string, fileIds: string[], targetFolderId: string) => Promise<void>
  removeFilesFromCurrentFolder: (collectionId: string, fileIds: string[]) => Promise<void>
  archiveFiles: (collectionId: string, fileIds: string[], files: FileSummary[]) => Promise<void>
  permanentlyDeleteFiles: (collectionId: string, fileIds: string[]) => Promise<void>
  unarchiveFiles: (collectionId: string, fileIds: string[], files: FileSummary[]) => Promise<void>
  toggleDefinitive: (collectionId: string, fileId: string, definitive: boolean, version: number) => Promise<void>
  toggleSelection: (fileId: string) => void
  selectAllFiles: () => void
  clearSelection: () => void

  // Messages
  refreshMessages: (collectionId: string) => Promise<void>
  addMessage: (collectionId: string, body: string) => Promise<void>
  editMessage: (collectionId: string, messageId: string, body: string, version: number) => Promise<void>
  removeMessage: (collectionId: string, messageId: string) => Promise<void>

  // UI
  setViewMode: (mode: "folder" | "timeline") => void
  toggleMessageSidebar: () => void

  // Internal: task polling
  _startTaskPolling: (collectionId: string, taskId: string) => void
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
  messageSidebarOpen: true,
  viewMode: "folder",
  uploadingTasks: new Set<string>(),
  perCollectionFolderCache: {},

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
        const msgs = await getFolderMessages(collectionId, folderId)
        set({ currentFolderMessages: msgs })
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
      folderTree: [],
      perCollectionFolderCache: { ...get().perCollectionFolderCache, [collectionId]: null },
    })
    get().fetchFolderTree(collectionId)
    get().refreshFiles(collectionId)
  },

  createSubFolder: async (collectionId: string, name: string) => {
    const { currentFolderId } = get()
    try {
      await createFolder(collectionId, {
        name,
        parent_folder_id: currentFolderId,
        kind: "plain",
      })
      await get().fetchFolderTree(collectionId)
      toast.success(`Folder "${name}" created`)
    } catch (err) {
      toast.error(`Failed to create folder: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  renameFolder: async (collectionId: string, folderId: string, name: string, version: number) => {
    try {
      await updateFolder(collectionId, folderId, { name, version })
      await get().fetchFolderTree(collectionId)
      if (get().currentFolderId === folderId) {
        set({ currentFolder: get().currentFolder ? { ...get().currentFolder!, name } : null })
      }
      toast.success("Folder renamed")
    } catch (err) {
      toast.error(`Failed to rename: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  moveFolder: async (collectionId: string, folderId: string, newParentId: string | null, version: number) => {
    try {
      await updateFolder(collectionId, folderId, { parent_folder_id: newParentId, version })
      await get().fetchFolderTree(collectionId)
      toast.success("Folder moved")
    } catch (err) {
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

  refreshFiles: async (collectionId: string) => {
    const { currentFolderId, currentFolder } = get()
    if (currentFolderId && isArchivedFolder(currentFolderId, currentFolder)) return
    set({ filesLoading: true })
    try {
      if (currentFolderId) {
        if (isArchivedFolder(currentFolderId, currentFolder)) {
          const files = await getArchivedFiles(collectionId)
          set({ currentFolderFiles: files })
        } else {
          const files = await getFolderFiles(collectionId, currentFolderId)
          set({ currentFolderFiles: files })
        }
      } else {
        // Root level: orphan files
        const files = await getRootFiles(collectionId)
        set({ currentFolderFiles: files })
      }
    } catch {
      set({ currentFolderFiles: [] })
    } finally {
      set({ filesLoading: false })
    }
  },

  uploadFile: async (collectionId: string, file: File) => {
    const { currentFolderId, currentFolder } = get()
    if (currentFolderId && isArchivedFolder(currentFolderId, currentFolder)) {
      toast.error("Cannot upload to Archived")
      return
    }
    try {
      const result = currentFolderId
        ? await uploadFileToFolder(collectionId, currentFolderId, file)
        : await uploadFileToFolder(collectionId, "", file)  // root: upload without path
      await get().refreshFiles(collectionId)
      if (result.unsupported) {
        toast.warning(`"${file.name}" uploaded — file type not supported for ingest`)
      } else if (result.task_id) {
        toast.info(`"${file.name}" uploaded, ingesting...`)
        get()._startTaskPolling(collectionId, result.task_id)
      } else {
        toast.success(`"${file.name}" uploaded`)
      }
    } catch (err) {
      toast.error(`Upload failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  uploadFolder: async (collectionId: string, files: File[]) => {
    const { currentFolderId, currentFolder } = get()
    if (currentFolderId && isArchivedFolder(currentFolderId, currentFolder)) {
      toast.error("Cannot upload to Archived")
      return
    }
    try {
      const results = await uploadFolderToCollection(collectionId, currentFolderId || "", files)
      await get().fetchFolderTree(collectionId)
      await get().refreshFiles(collectionId)
      const taskIds = results.filter((r) => r.task_id).map((r) => r.task_id!)
      if (taskIds.length > 0) {
        toast.info(`${files.length} files uploaded, ${taskIds.length} ingesting...`)
        for (const tid of taskIds) {
          get()._startTaskPolling(collectionId, tid)
        }
      } else {
        toast.success(`${files.length} files uploaded`)
      }
    } catch (err) {
      toast.error(`Folder upload failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  moveFilesToFolder: async (collectionId: string, fileIds: string[], targetFolderId: string) => {
    const { currentFolderId } = get()
    try {
      await Promise.all(
        fileIds.map(async (fid) => {
          // Add to target folder
          await addFilePath(collectionId, fid, targetFolderId)
          // Remove from current folder (if different)
          if (currentFolderId && currentFolderId !== targetFolderId) {
            try {
              const { getFileDetail } = await import("@/api/file-mgmt")
              const detail = await getFileDetail(collectionId, fid)
              const pathInFolder = detail.paths?.find((p) => p.folder_id === currentFolderId)
              if (pathInFolder) {
                await removeFilePath(collectionId, fid, pathInFolder.path_id)
              }
            } catch {
              // skip if path removal fails
            }
          }
        })
      )
      await get().refreshFiles(collectionId)
      set({ selectedFileIds: new Set() })
      toast.success(`${fileIds.length} file(s) moved`)
    } catch (err) {
      toast.error(`Move failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  copyFilesToFolder: async (collectionId: string, fileIds: string[], targetFolderId: string) => {
    try {
      await Promise.all(
        fileIds.map((fid) => addFilePath(collectionId, fid, targetFolderId))
      )
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

  archiveFiles: async (collectionId: string, fileIds: string[], files: FileSummary[]) => {
    try {
      await Promise.all(
        fileIds.map((fid) => {
          const f = files.find((x) => x.file_id === fid)
          return toggleFileArchive(collectionId, fid, true, f?.version ?? 1)
        })
      )
      await get().refreshFiles(collectionId)
      set({ selectedFileIds: new Set() })
      toast.success(`${fileIds.length} file(s) archived`)
    } catch (err) {
      toast.error(`Archive failed: ${err instanceof Error ? err.message : String(err)}`)
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
      // Pass current folder so path-level greys (branch merge) can be cleared here
      const folderId = get().currentFolderId
      await Promise.all(
        fileIds.map((fid) => {
          const f = files.find((x) => x.file_id === fid)
          return toggleFileArchive(
            collectionId,
            fid,
            false,
            f?.version ?? 1,
            folderId
          )
        })
      )
      await get().refreshFiles(collectionId)
      set({ selectedFileIds: new Set() })
      toast.success(`${fileIds.length} file(s) unarchived`)
    } catch (err) {
      toast.error(`Unarchive failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  toggleDefinitive: async (collectionId: string, fileId: string, definitive: boolean, version: number) => {
    try {
      // 1. Update SQLite is_definitive
      const resp = await fetch(`/api/file-mgmt/${collectionId}/files/${fileId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_definitive: definitive, version }),
      })
      if (!resp.ok) {
        const body = await resp.text()
        toast.error(`Failed: API ${resp.status}: ${body}`)
        return
      }

      // 2. Sync to doc_summary system for consolidate pipeline
      const source = `__file__:${fileId}`
      try {
        const { setDocSummaryInclude, generateDocSummary, getDocSummary } = await import("@/api/client")
        if (definitive) {
          // Try to set include_in_summary — if no summary exists, generate one first
          try {
            await setDocSummaryInclude(collectionId, source, true)
          } catch {
            // No summary exists — generate one, poll, then set include
            toast.info("Generating summary for definitive file...")
            await generateDocSummary(collectionId, source)
            // Poll for completion (max 5 minutes)
            const start = Date.now()
            while (Date.now() - start < 300_000) {
              await new Promise((r) => setTimeout(r, 2000))
              try {
                const ds = await getDocSummary(collectionId, source)
                if (ds) {
                  await setDocSummaryInclude(collectionId, source, true)
                  break
                }
              } catch { /* still generating */ }
            }
          }
        } else {
          // Setting to false — just update include_in_summary if summary exists
          try {
            await setDocSummaryInclude(collectionId, source, false)
          } catch { /* no summary — nothing to do */ }
        }
      } catch {
        // Summary sync failed — SQLite flag is still set, not critical
      }

      await get().refreshFiles(collectionId)
      toast.success(definitive ? "Marked as definitive" : "Removed definitive")
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  toggleSelection: (fileId: string) => {
    set((s) => {
      const next = new Set(s.selectedFileIds)
      if (next.has(fileId)) next.delete(fileId)
      else next.add(fileId)
      return { selectedFileIds: next }
    })
  },

  selectAllFiles: () => {
    const { currentFolderFiles } = get()
    set({ selectedFileIds: new Set(currentFolderFiles.map((f) => f.file_id)) })
  },

  clearSelection: () => {
    set({ selectedFileIds: new Set() })
  },

  toggleFolderSelection: (folderId: string) => {
    set((s) => {
      const next = new Set(s.selectedFolderIds)
      if (next.has(folderId)) next.delete(folderId)
      else next.add(folderId)
      return { selectedFolderIds: next }
    })
  },

  clearFolderSelection: () => {
    set({ selectedFolderIds: new Set() })
  },

  // ── Messages ──

  refreshMessages: async (collectionId: string) => {
    const { currentFolderId, currentFolder } = get()
    if (currentFolderId && isArchivedFolder(currentFolderId, currentFolder)) return
    set({ messagesLoading: true })
    try {
      const msgs = currentFolderId
        ? await getFolderMessages(collectionId, currentFolderId)
        : await getCollectionMessages(collectionId)
      set({ currentFolderMessages: msgs })
    } catch {
      // ignore
    } finally {
      set({ messagesLoading: false })
    }
  },

  addMessage: async (collectionId: string, body: string) => {
    const { currentFolderId } = get()
    try {
      let msg: Message
      if (currentFolderId) {
        msg = await createFolderMessage(collectionId, currentFolderId, {
          owner_type: "folder",
          owner_id: currentFolderId,
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
      set((s) => ({ currentFolderMessages: [msg, ...s.currentFolderMessages] }))
      toast.success("Message added")
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  },

  editMessage: async (collectionId: string, messageId: string, body: string, version: number) => {
    try {
      const updated = await updateMessage(collectionId, messageId, { body, version })
      set((s) => ({
        currentFolderMessages: s.currentFolderMessages.map((m) =>
          m.message_id === messageId ? updated : m
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

  _startTaskPolling: (collectionId: string, taskId: string) => {
    set((s) => {
      const next = new Set(s.uploadingTasks)
      next.add(taskId)
      return { uploadingTasks: next }
    })

    const poll = async () => {
      try {
        const res = await getTasks(collectionId)
        const task = res.tasks.find((t) => t.id === taskId)
        if (!task) {
          // Task may have been cleared; stop polling
          set((s) => {
            const next = new Set(s.uploadingTasks)
            next.delete(taskId)
            return { uploadingTasks: next }
          })
          return
        }
        if (task.status === "completed") {
          set((s) => {
            const next = new Set(s.uploadingTasks)
            next.delete(taskId)
            return { uploadingTasks: next }
          })
          await get().refreshFiles(collectionId)
          await get().refreshMessages(collectionId)
          toast.success(`Ingest complete: ${task.filename}`)
        } else if (task.status === "failed") {
          set((s) => {
            const next = new Set(s.uploadingTasks)
            next.delete(taskId)
            return { uploadingTasks: next }
          })
          await get().refreshFiles(collectionId)
          await get().refreshMessages(collectionId)
          toast.error(`Ingest failed: ${task.filename} — ${task.error || "unknown error"}`)
        } else {
          // Still pending/processing — keep polling
          setTimeout(poll, 1500)
        }
      } catch {
        // Network error — retry once more after delay
        setTimeout(poll, 3000)
      }
    }
    setTimeout(poll, 1500)
  },
}))
