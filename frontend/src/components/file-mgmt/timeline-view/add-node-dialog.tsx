import { useEffect, useMemo, useRef, useState, type DragEvent } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { cn } from "@/lib/utils"
import { Paperclip, Search, Upload, X } from "lucide-react"
import { toast } from "sonner"
import type { FileSummary, NodeGroup } from "@/types/file-mgmt"
import {
  attachFileToNode,
  createNode,
  createNodeMessage,
  uploadFileToNode,
} from "@/api/file-mgmt"
import { FileTreePicker } from "./file-tree-picker"
import { GroupFormDialog } from "./group-form-dialog"
import { listGroups } from "@/api/file-mgmt"

type PendingAttachment =
  | { kind: "existing"; key: string; file_id: string; filename: string }
  | { kind: "upload"; key: string; file: File }

interface AddNodeDialogProps {
  collectionId: string
  chainId: string
  afterOrder: number
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: () => void
  groups: NodeGroup[]
  /** Refresh groups in parent after create-group from this dialog. */
  onGroupsChanged?: () => void
}

export function AddNodeDialog({
  collectionId,
  chainId,
  afterOrder,
  open,
  onOpenChange,
  onCreated,
  groups,
  onGroupsChanged,
}: AddNodeDialogProps) {
  const [title, setTitle] = useState("")
  const [groupSlug, setGroupSlug] = useState<string>(groups[0]?.group_id ?? "")
  const [localGroups, setLocalGroups] = useState<NodeGroup[]>(groups)
  const [groupFormOpen, setGroupFormOpen] = useState(false)
  const [eventTime, setEventTime] = useState("")
  const [messageBody, setMessageBody] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [pending, setPending] = useState<PendingAttachment[]>([])
  const [attachMode, setAttachMode] = useState<"select" | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const dateInputRef = useRef<HTMLInputElement>(null)

  const pendingExistingIds = useMemo(
    () =>
      new Set(
        pending.filter((p) => p.kind === "existing").map((p) => p.file_id)
      ),
    [pending]
  )

  useEffect(() => {
    setLocalGroups(groups)
  }, [groups])

  useEffect(() => {
    if (!open) {
      setTitle("")
      setGroupSlug(groups[0]?.group_id ?? "")
      setEventTime("")
      setMessageBody("")
      setPending([])
      setAttachMode(null)
      setDragOver(false)
      setGroupFormOpen(false)
    } else {
      setGroupSlug((prev) => prev || groups[0]?.group_id || "")
    }
  }, [open, groups])

  const addUploadFiles = (files: FileList | File[]) => {
    const list = Array.from(files).filter((f) => f.size >= 0)
    if (list.length === 0) return
    setPending((prev) => [
      ...prev,
      ...list.map((file, i) => ({
        kind: "upload" as const,
        key: `upload-${file.name}-${file.size}-${file.lastModified}-${prev.length + i}`,
        file,
      })),
    ])
    setAttachMode(null)
  }

  const addExistingFile = (file: FileSummary) => {
    setPending((prev) => {
      if (prev.some((p) => p.kind === "existing" && p.file_id === file.file_id)) {
        return prev
      }
      return [
        ...prev,
        {
          kind: "existing",
          key: `existing-${file.file_id}`,
          file_id: file.file_id,
          filename: file.filename,
        },
      ]
    })
  }

  const removePending = (key: string) => {
    setPending((prev) => prev.filter((p) => p.key !== key))
  }

  const openDatePicker = () => {
    const el = dateInputRef.current
    if (!el) return
    try {
      el.showPicker?.()
    } catch {
      el.focus()
      el.click()
    }
  }

  const handleDragOver = (e: DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (e.dataTransfer.types.includes("Files")) {
      setDragOver(true)
    }
  }

  const handleDragLeave = (e: DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    // Only clear when leaving the drop zone itself
    if (e.currentTarget === e.target) {
      setDragOver(false)
    }
  }

  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
    if (e.dataTransfer.files?.length) {
      addUploadFiles(e.dataTransfer.files)
    }
  }

  const handleSubmit = async () => {
    if (!chainId) return
    const trimmedTitle = title.trim()
    if (!trimmedTitle) {
      toast.error("Node name is required")
      return
    }
    if (!groupSlug) {
      toast.error("Group is required")
      return
    }
    setSubmitting(true)
    try {
      const node = await createNode(collectionId, chainId, {
        group_id: groupSlug,
        node_type: "event",
        title: trimmedTitle,
        order: afterOrder >= 0 ? afterOrder + 1 : 0,
        event_time: eventTime || null,
      })

      for (const att of pending) {
        if (att.kind === "existing") {
          await attachFileToNode(collectionId, node.node_id, att.file_id)
        } else {
          // Node upload: writes to group folder + mounts branch path automatically
          await uploadFileToNode(collectionId, node.node_id, att.file)
        }
      }

      const trimmedMessage = messageBody.trim()
      if (trimmedMessage) {
        await createNodeMessage(collectionId, node.node_id, {
          owner_type: "node",
          owner_id: node.node_id,
          body: trimmedMessage,
          author_type: "user",
        })
      }

      toast.success("Node added")
      onCreated()
      onOpenChange(false)
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[780px] max-w-[90vw] sm:max-w-[90vw] h-[85vh] flex flex-col gap-3">
        <DialogHeader>
          <DialogTitle className="text-sm">Add Node</DialogTitle>
        </DialogHeader>

        {/* Left params + Right message editor */}
        <div className="flex-1 min-h-0 flex gap-4">
          {/* ── Left: form params + attachments ── */}
          <div className="w-[300px] shrink-0 flex flex-col min-h-0 gap-3 overflow-y-auto pr-1">
            <div>
              <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60 block mb-1">
                Title <span className="text-destructive">*</span>
              </label>
              <input
                className="w-full text-xs border rounded px-2 py-1.5 bg-background"
                placeholder="Node title..."
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                autoFocus
                required
              />
            </div>

            <div>
              <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60 block mb-1">
                Group <span className="text-destructive">*</span>
              </label>
              <select
                className="w-full text-xs border rounded px-2 py-1.5 bg-background"
                value={groupSlug}
                onChange={(e) => {
                  if (e.target.value === "__add_group__") {
                    setGroupFormOpen(true)
                    return
                  }
                  setGroupSlug(e.target.value)
                }}
              >
                {localGroups.length === 0 && (
                  <option value="">Select a group</option>
                )}
                {localGroups.map((g) => (
                  <option key={g.group_id} value={g.group_id}>
                    {g.name}
                  </option>
                ))}
                <option value="__add_group__">+ Add group…</option>
              </select>
            </div>

            <div>
              <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60 block mb-1">
                Event Time (optional)
              </label>
              <div className="flex items-center gap-1">
                <input
                  ref={dateInputRef}
                  type="date"
                  value={eventTime}
                  onChange={(e) => setEventTime(e.target.value)}
                  onClick={openDatePicker}
                  className={cn(
                    "w-full text-xs border rounded px-2 py-1.5 bg-background cursor-pointer",
                    !eventTime &&
                      "[&::-webkit-datetime-edit]:text-transparent [&::-webkit-datetime-edit-fields-wrapper]:opacity-0"
                  )}
                />
                {eventTime && (
                  <button
                    type="button"
                    className="text-muted-foreground hover:text-foreground shrink-0 p-1"
                    title="Clear date"
                    onClick={() => setEventTime("")}
                  >
                    <X className="h-3 w-3" />
                  </button>
                )}
              </div>
            </div>

            {/* Attachments drop zone */}
            <div className="flex-1 min-h-0 flex flex-col">
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60">
                  Attachments ({pending.length})
                </label>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="text-[10px] font-medium text-primary hover:underline flex items-center gap-1"
                    onClick={() => setAttachMode(attachMode === "select" ? null : "select")}
                  >
                    <Search className="h-3 w-3" />
                    Select
                  </button>
                  <button
                    type="button"
                    className="text-[10px] font-medium text-primary hover:underline flex items-center gap-1"
                    onClick={() => fileInputRef.current?.click()}
                  >
                    <Upload className="h-3 w-3" />
                    Upload
                  </button>
                </div>
              </div>

              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(e) => {
                  if (e.target.files?.length) addUploadFiles(e.target.files)
                  e.target.value = ""
                }}
              />

              <div
                className={cn(
                  "h-[200px] shrink-0 rounded border border-dashed p-2 transition-colors overflow-hidden flex flex-col",
                  dragOver
                    ? "border-primary bg-primary/5"
                    : "border-border bg-muted/10"
                )}
                onDragOver={handleDragOver}
                onDragEnter={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
              >
                {attachMode === "select" ? (
                  <div className="flex-1 min-h-0 flex flex-col gap-1 overflow-hidden">
                    <div className="flex items-center justify-between gap-1 shrink-0">
                      <span className="text-[10px] text-muted-foreground">
                        Click a file to add
                      </span>
                      <button
                        type="button"
                        className="text-muted-foreground hover:text-foreground"
                        onClick={() => setAttachMode(null)}
                        title="Close"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                    <div className="flex-1 min-h-0 overflow-hidden">
                      <FileTreePicker
                        collectionId={collectionId}
                        selectedIds={pendingExistingIds}
                        onSelectFile={addExistingFile}
                        maxHeightClass="h-full max-h-full"
                        className="h-full flex flex-col"
                      />
                    </div>
                  </div>
                ) : (
                  <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
                    {pending.length > 0 && (
                      <div className="space-y-1 mb-2 overflow-y-auto max-h-[72px] shrink-0">
                        {pending.map((att) => (
                          <div
                            key={att.key}
                            className="flex items-center gap-1.5 px-2 py-1 rounded bg-background border border-border/60 text-xs"
                          >
                            <Paperclip className="h-3 w-3 text-muted-foreground shrink-0" />
                            <span className="flex-1 truncate">
                              {att.kind === "existing" ? att.filename : att.file.name}
                            </span>
                            {att.kind === "upload" && (
                              <span className="text-[9px] text-muted-foreground shrink-0">new</span>
                            )}
                            <button
                              type="button"
                              className="text-muted-foreground hover:text-red-500"
                              onClick={() => removePending(att.key)}
                              title="Remove"
                            >
                              <X className="h-3 w-3" />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="flex-1 flex flex-col items-center justify-center gap-1.5 text-center px-3 pointer-events-none">
                      <Upload className="h-5 w-5 text-muted-foreground/40" />
                      <p className="text-[11px] text-muted-foreground/60">
                        Drag & drop files here
                      </p>
                      <p className="text-[10px] text-muted-foreground/40">
                        or use Select / Upload above
                      </p>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* ── Right: message editor ── */}
          <div className="flex-1 min-w-0 min-h-0 flex flex-col">
            <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60 block mb-1.5 shrink-0">
              Message (optional)
            </label>
            <div className="flex-1 min-h-0 overflow-auto rounded border border-border">
              <MarkdownEditor
                value={messageBody}
                onChange={setMessageBody}
                minHeight="100%"
                placeholder="Write a message in Markdown..."
                showToolbar
              />
            </div>
          </div>
        </div>

        <DialogFooter className="gap-2 pt-1 shrink-0">
          <Button
            variant="outline"
            size="xs"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            Cancel
          </Button>
          <Button
            size="xs"
            onClick={handleSubmit}
            disabled={submitting || !groupSlug || !title.trim()}
          >
            {submitting ? "Adding..." : "Add Node"}
          </Button>
        </DialogFooter>
      </DialogContent>

      <GroupFormDialog
        collectionId={collectionId}
        open={groupFormOpen}
        onOpenChange={setGroupFormOpen}
        boundFolderIds={
          new Set(
            localGroups
              .map((g) => g.folder_id)
              .filter((id): id is string => !!id)
          )
        }
        onSaved={async () => {
          try {
            const gs = await listGroups(collectionId)
            setLocalGroups(gs)
            const newest = gs.find((g) => !localGroups.some((o) => o.group_id === g.group_id))
            if (newest) setGroupSlug(newest.group_id)
            else if (gs[0]) setGroupSlug(gs[0].group_id)
            onGroupsChanged?.()
          } catch {
            onGroupsChanged?.()
          }
        }}
      />
    </Dialog>
  )
}
