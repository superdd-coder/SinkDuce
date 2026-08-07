import { useEffect, useMemo, useRef, useState, type DragEvent } from "react"
import { Button } from "@/components/ui/button"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { FieldLabel } from "@/components/ui/field-label"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { MESSAGE_EDITOR_PLACEHOLDER } from "@/components/ui/tiptap-editor"
import { cn } from "@/lib/utils"
import { Paperclip, Search, Upload, X } from "lucide-react"
import { toast } from "sonner"
import type { FileSummary, Node, NodeGroup } from "@/types/file-mgmt"
import {
  attachFileToNode,
  createNode,
  createNodeMessage,
  getNameConflict,
  uploadFileToNode,
} from "@/api/file-mgmt"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { FileTreePicker } from "./file-tree-picker"
import { GroupFormDialog } from "./group-form-dialog"
import { listGroups } from "@/api/file-mgmt"
import { FileSelectPreviewPanel } from "@/components/file-mgmt/file-select-preview-panel"

type PendingAttachment =
  | { kind: "existing"; key: string; file_id: string; filename: string }
  | { kind: "upload"; key: string; file: File }

interface AddNodeDialogProps {
  collectionId: string
  chainId: string
  /**
   * Insert after this order on the chain.
   * Use `-1` (or any negative) to **append at the end** (todo → node, etc.).
   */
  afterOrder: number
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Called after node (+ attachments) succeed; receives the created node. */
  onCreated: (node?: Node) => void
  groups: NodeGroup[]
  /** Refresh groups in parent after create-group from this dialog. */
  onGroupsChanged?: () => void
  /** Prefill title (e.g. from a completed todo). */
  initialTitle?: string
  /** Prefill node message (e.g. todo description). */
  initialMessageBody?: string
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
  initialTitle,
  initialMessageBody,
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
  const [selectPreviewFile, setSelectPreviewFile] =
    useState<FileSummary | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const dateInputRef = useRef<HTMLInputElement>(null)

  const setSelectMode = (mode: "select" | null) => {
    setAttachMode(mode)
    if (mode !== "select") setSelectPreviewFile(null)
  }

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
      setSelectPreviewFile(null)
      setDragOver(false)
      setGroupFormOpen(false)
    } else {
      setTitle(initialTitle?.trim() || "")
      setMessageBody(initialMessageBody?.trim() || "")
      setGroupSlug((prev) => prev || groups[0]?.group_id || "")
    }
  }, [open, groups, initialTitle, initialMessageBody])

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

  /** Click once to select, again to deselect (toggle). */
  const toggleExistingFile = (file: FileSummary) => {
    setPending((prev) => {
      if (prev.some((p) => p.kind === "existing" && p.file_id === file.file_id)) {
        return prev.filter(
          (p) => !(p.kind === "existing" && p.file_id === file.file_id)
        )
      }
      return [
        ...prev,
        {
          kind: "existing",
          key: `existing-${file.file_id}`,
          file_id: file.file_id,
          filename: file.display_name || file.filename,
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
      // Backend clamps order to [1, max_order+1]. Large value → chain tail.
      // afterOrder < 0 means append (e.g. completed todo → node).
      const order =
        afterOrder >= 0 ? afterOrder + 1 : 1_000_000_000
      const node = await createNode(collectionId, chainId, {
        group_id: groupSlug,
        node_type: "event",
        title: trimmedTitle,
        order,
        event_time: eventTime || null,
      })

      for (const att of pending) {
        if (att.kind === "existing") {
          await attachFileToNode(collectionId, node.node_id, att.file_id)
        } else {
          // Node upload: writes to group folder + mounts branch path automatically
          const result = await uploadFileToNode(
            collectionId,
            node.node_id,
            att.file
          )
          if (result.task_id && result.file_id) {
            useFileMgmtStore
              .getState()
              ._startTaskPolling(
                collectionId,
                result.task_id,
                result.file_id
              )
          }
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
      onCreated(node)
      onOpenChange(false)
    } catch (err) {
      const conflict = getNameConflict(err)
      if (conflict) {
        toast.error(
          `A file named “${conflict.name}” already exists. Please rename and try again.`
        )
      } else {
        toast.error(
          `Failed: ${err instanceof Error ? err.message : String(err)}`
        )
      }
    } finally {
      setSubmitting(false)
    }
  }

  const showSelectPreview = attachMode === "select"

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        overlayClassName="pm-dialog-overlay--silk"
        className={cn(
          "pm-dialog pm-dialog--silk max-w-[98vw] sm:max-w-[98vw] h-[85vh] !flex flex-row gap-0 p-0 overflow-hidden transition-[width] duration-300 ease-out",
          // Form body fixed (wider Message); preview is extra width on the left
          showSelectPreview
            ? "w-[min(calc(920px+min(calc(85vh*210/297),42vw)),98vw)]"
            : "w-[920px]"
        )}
      >
        {/* Left preview ≈ A4 portrait — additive width only */}
        {showSelectPreview && (
          <div
            className={cn(
              "h-full shrink-0 border-r border-border min-h-0 flex flex-col",
              "w-[min(calc(85vh*210/297),42vw)] min-w-[280px]",
              "animate-in slide-in-from-left-2 fade-in-0 duration-250"
            )}
          >
            <FileSelectPreviewPanel
              collectionId={collectionId}
              file={selectPreviewFile}
              onClose={() => setSelectPreviewFile(null)}
              className="h-full rounded-none border-0 shadow-none"
            />
          </div>
        )}

        {/* Form body — fixed width so Message stays roomy */}
        <div className="w-[920px] max-w-full shrink-0 min-h-0 flex flex-col gap-3 p-4 overflow-hidden">
        <DialogHeader>
          <DialogTitle className="text-sm">Add Node</DialogTitle>
        </DialogHeader>

        {/* Left params + Right message editor */}
        <div className="flex-1 min-h-0 flex gap-4">
          {/* ── Left: form params + attachments ── */}
          <div className="w-[280px] shrink-0 flex flex-col min-h-0 gap-3 pr-1">
            <div className="shrink-0">
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

            <div className="shrink-0">
              <FieldLabel>
                Group <span className="text-[var(--pm-danger)]">*</span>
              </FieldLabel>
              <DropdownSelect
                size="sm"
                value={groupSlug}
                onChange={(v) => {
                  if (v === "__add_group__") {
                    setGroupFormOpen(true)
                    return
                  }
                  setGroupSlug(v)
                }}
                placeholder="Select a group"
                options={[
                  ...(localGroups.length === 0
                    ? [{ value: "", label: "Select a group" }]
                    : []),
                  ...localGroups.map((g) => ({
                    value: g.group_id,
                    label: g.name,
                  })),
                  { value: "__add_group__", label: "+ Add group…" },
                ]}
              />
            </div>

            <div className="shrink-0">
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

            {/* Attachments — fills remaining height under form fields */}
            <div className="flex-1 min-h-0 flex flex-col">
              <div className="flex items-center justify-between mb-1.5 shrink-0">
                <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60">
                  Attachments ({pending.length})
                </label>
                <button
                  type="button"
                  className="text-[10px] font-medium text-primary hover:underline flex items-center gap-1"
                  onClick={() =>
                    setSelectMode(attachMode === "select" ? null : "select")
                  }
                >
                  <Search className="h-3 w-3" />
                  {attachMode === "select" ? "Close select" : "Select existing"}
                </button>
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
                  "flex-1 min-h-[160px] rounded border border-dashed p-2 transition-colors overflow-hidden flex flex-col",
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
                        Multi-select · click to select · again to deselect
                      </span>
                      <button
                        type="button"
                        className="text-muted-foreground hover:text-foreground"
                        onClick={() => setSelectMode(null)}
                        title="Close"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                    <div className="flex-1 min-h-0 overflow-hidden">
                      <FileTreePicker
                        collectionId={collectionId}
                        selectedIds={pendingExistingIds}
                        onSelectFile={toggleExistingFile}
                        onPreviewFile={setSelectPreviewFile}
                        maxHeightClass="h-full max-h-full"
                        className="h-full flex flex-col"
                      />
                    </div>
                  </div>
                ) : (
                  <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
                    {pending.length > 0 && (
                      <div className="space-y-1 mb-2 overflow-y-auto max-h-[40%] shrink-0 min-h-0">
                        {pending.map((att) => (
                          <div
                            key={att.key}
                            className="flex items-center gap-1.5 px-2 py-1 rounded bg-background border border-border/60 text-xs"
                          >
                            <Paperclip className="h-3 w-3 text-muted-foreground shrink-0" />
                            <span className="flex-1 truncate">
                              {att.kind === "existing"
                                ? att.filename
                                : att.file.name}
                            </span>
                            {att.kind === "upload" && (
                              <span className="text-[9px] text-muted-foreground shrink-0">
                                new
                              </span>
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
                    <button
                      type="button"
                      className="flex-1 flex flex-col items-center justify-center gap-1.5 text-center px-3 cursor-pointer rounded hover:bg-muted/30 transition-colors min-h-[72px]"
                      onClick={() => fileInputRef.current?.click()}
                    >
                      <Upload className="h-5 w-5 text-muted-foreground/40" />
                      <p className="text-[11px] text-muted-foreground/60">
                        Drag & drop files here
                      </p>
                      <p className="text-[10px] text-muted-foreground/40">
                        or click to browse
                      </p>
                    </button>
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
                placeholder={MESSAGE_EDITOR_PLACEHOLDER}
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
        </div>
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
