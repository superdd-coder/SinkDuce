/**
 * Add Node — Premium silk dialog (nested white cards).
 * Layout language matches Create Group / Create Todo:
 * float shell · FieldLabel · ui/* controls · soft attachments · silk overlay.
 */
import { useEffect, useMemo, useRef, useState, type DragEvent } from "react"
import { Button } from "@/components/ui/button"
import { DatePicker } from "@/components/ui/date-picker"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { FieldLabel } from "@/components/ui/field-label"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { MESSAGE_EDITOR_PLACEHOLDER } from "@/components/ui/tiptap-editor"
import { cn } from "@/lib/utils"
import { FolderOpen, Paperclip, Upload, X, XCircle } from "lucide-react"
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

function setsEqual(a: Set<string>, b: Set<string>): boolean {
  if (a.size !== b.size) return false
  for (const id of a) if (!b.has(id)) return false
  return true
}

/** Minimal FileSummary for draft selection (tree only needs file_id + label). */
function pendingToDraftFile(p: {
  file_id: string
  filename: string
}): FileSummary {
  return {
    file_id: p.file_id,
    current_version_id: null,
    is_definitive: false,
    archived: false,
    unsupported: false,
    created_by: "",
    version: 1,
    filename: p.filename,
    original_ext: "",
    created_at: "",
    is_greyed: false,
    task_id: null,
    display_name: p.filename,
  }
}

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
  /** From Collection picker open — same chrome as Node sidebar attach. */
  const [selectOpen, setSelectOpen] = useState(false)
  /** After preview rail finishes open tween — allow soft shadows past shell. */
  const [previewSettled, setPreviewSettled] = useState(false)
  /** Draft collection picks — applied on Confirm (mirrors NodeFileAttach). */
  const [draftFiles, setDraftFiles] = useState<Map<string, FileSummary>>(
    () => new Map()
  )
  const [selectPreviewFile, setSelectPreviewFile] =
    useState<FileSummary | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const titleInputRef = useRef<HTMLInputElement>(null)
  /** Remount message editor when dialog opens so TipTap starts clean */
  const [editorKey, setEditorKey] = useState(0)

  const pendingExistingIds = useMemo(
    () =>
      new Set(
        pending.filter((p) => p.kind === "existing").map((p) => p.file_id)
      ),
    [pending]
  )

  const draftIds = useMemo(() => new Set(draftFiles.keys()), [draftFiles])

  const draftDirty = useMemo(
    () => !setsEqual(draftIds, pendingExistingIds),
    [draftIds, pendingExistingIds]
  )

  const setSelect = (open: boolean) => {
    setSelectOpen(open)
    if (!open) {
      setSelectPreviewFile(null)
      setPreviewSettled(false)
      // Discard uncommitted draft when leaving picker
      setDraftFiles(new Map())
    } else {
      const m = new Map<string, FileSummary>()
      for (const p of pending) {
        if (p.kind === "existing") {
          m.set(p.file_id, pendingToDraftFile(p))
        }
      }
      setDraftFiles(m)
    }
  }

  /* Mark dual-window settled after open silk finishes (overflow → visible) */
  useEffect(() => {
    if (!selectOpen) {
      setPreviewSettled(false)
      return
    }
    setPreviewSettled(false)
    const t = window.setTimeout(() => setPreviewSettled(true), 400)
    return () => window.clearTimeout(t)
  }, [selectOpen])

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
      setSelectOpen(false)
      setPreviewSettled(false)
      setDraftFiles(new Map())
      setSelectPreviewFile(null)
      setDragOver(false)
      setGroupFormOpen(false)
    } else {
      setTitle(initialTitle?.trim() || "")
      setMessageBody(initialMessageBody?.trim() || "")
      setGroupSlug((prev) => prev || groups[0]?.group_id || "")
      setEditorKey((k) => k + 1)
    }
  }, [open, groups, initialTitle, initialMessageBody])

  /* Focus title after silk enter (280ms) so open fade isn’t interrupted */
  useEffect(() => {
    if (!open) return
    const t = window.setTimeout(() => {
      titleInputRef.current?.focus({ preventScroll: true })
    }, 300)
    return () => window.clearTimeout(t)
  }, [open])

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
    // Leave From Collection if open (upload path)
    if (selectOpen) setSelect(false)
  }

  /** Local tree toggle only — committed on Confirm. */
  const toggleDraft = (file: FileSummary) => {
    setDraftFiles((prev) => {
      const next = new Map(prev)
      if (next.has(file.file_id)) next.delete(file.file_id)
      else next.set(file.file_id, file)
      return next
    })
  }

  const applyCollectionConfirm = () => {
    if (!draftDirty) return
    setPending((prev) => {
      const uploads = prev.filter((p) => p.kind === "upload")
      const existing: PendingAttachment[] = [...draftFiles.values()].map(
        (f) => ({
          kind: "existing" as const,
          key: `existing-${f.file_id}`,
          file_id: f.file_id,
          filename: f.display_name || f.filename,
        })
      )
      return [...uploads, ...existing]
    })
    setSelect(false)
  }

  const removePending = (key: string) => {
    setPending((prev) => prev.filter((p) => p.key !== key))
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

  const showSelectPreview = selectOpen

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        overlayClassName="pm-dialog-overlay--silk"
        className={cn(
          "pm-dialog pm-dialog--silk pm-add-node-dialog",
          showSelectPreview && "is-preview-open",
          previewSettled && "is-preview-settled",
          "!animate-none data-open:!animate-none data-closed:!animate-none"
        )}
      >
        <div className="pm-add-node-shell">
          {/*
            Preview rail always mounted while dialog is open so open/close can
            silk-tween width + opacity (no remount hard-cut). Content is clipped
            by the rail when collapsed.
          */}
          <div
            className={cn(
              "pm-add-node-preview",
              showSelectPreview && "is-open"
            )}
            aria-hidden={!showSelectPreview}
          >
            <FileSelectPreviewPanel
              collectionId={collectionId}
              file={selectPreviewFile}
              className="pm-add-node-preview-panel"
            />
          </div>

          <div className="pm-add-node-main">
            {/*
              Title chrome = Group/Todo dialog language (Geist title · uppercase).
              Actions live in the same row so Cancel / Add Node / close share one baseline.
            */}
            <DialogHeader className="pm-add-node-head">
              <DialogTitle className="pm-add-node-title">Add Node</DialogTitle>
              <div className="pm-add-node-head-actions">
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={() => onOpenChange(false)}
                  disabled={submitting}
                >
                  Cancel
                </Button>
                <Button
                  type="button"
                  variant="default"
                  size="xs"
                  onClick={() => void handleSubmit()}
                  disabled={submitting || !groupSlug || !title.trim()}
                >
                  {submitting ? "Adding…" : "Add Node"}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  className="pm-add-node-head-close"
                  onClick={() => onOpenChange(false)}
                  disabled={submitting}
                  title="Close"
                  aria-label="Close"
                >
                  <X className="h-3.5 w-3.5" strokeWidth={1.75} />
                </Button>
              </div>
            </DialogHeader>

            <div className="pm-dialog-body pm-add-node-body">
              <div className="pm-add-node-cols">
                {/* ── Left: params + attachments ── */}
                <div className="pm-add-node-left">
                  <section className="pm-add-node-card pm-add-node-card--params">
                    <div className="pm-add-node-field">
                      <FieldLabel htmlFor="pm-add-node-title">
                        Title{" "}
                        <span className="text-[var(--pm-danger)]">*</span>
                      </FieldLabel>
                      <Input
                        ref={titleInputRef}
                        id="pm-add-node-title"
                        value={title}
                        onChange={(e) => setTitle(e.target.value)}
                        placeholder="Node title…"
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault()
                            void handleSubmit()
                          }
                        }}
                      />
                    </div>

                    <div className="pm-add-node-field">
                      <FieldLabel>
                        Group{" "}
                        <span className="text-[var(--pm-danger)]">*</span>
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

                    <div className="pm-add-node-field">
                      <FieldLabel htmlFor="pm-add-node-event-time">
                        Event Time
                      </FieldLabel>
                      <DatePicker
                        id="pm-add-node-event-time"
                        size="sm"
                        value={eventTime}
                        onChange={setEventTime}
                        placeholder="Optional"
                        allowClear
                      />
                    </div>
                  </section>

                  <section
                    className={cn(
                      "pm-add-node-card pm-add-node-card--attach",
                      selectOpen && "is-selecting"
                    )}
                  >
                    <div className="pm-add-node-attach-head">
                      <div className="pm-add-node-attach-labels">
                        <FieldLabel className="pm-add-node-attach-label">
                          Attachments
                        </FieldLabel>
                        <span className="pm-meta text-[var(--pm-faint)]">
                          {pending.length}
                        </span>
                      </div>
                    </div>

                    {/*
                      Same attach chrome as Node detail sidebar (node-file-attach):
                      drop zone · From Collection / Confirm · list ↔ tree silk swap.
                      File tree = FileTreePicker + pm-timeline-ftree-* (shared SoT).
                    */}
                    <div
                      className={cn(
                        "pm-timeline-attach-stack pm-add-node-attach-stack",
                        selectOpen && "is-picking"
                      )}
                    >
                      <input
                        ref={fileInputRef}
                        type="file"
                        multiple
                        className="hidden"
                        onChange={(e) => {
                          if (e.target.files?.length)
                            addUploadFiles(e.target.files)
                          e.target.value = ""
                        }}
                      />

                      <div
                        data-file-select-zone
                        className={cn(
                          "pm-timeline-attach-zone shrink-0",
                          selectOpen ? "is-compact" : "is-drop",
                          dragOver && "is-drag"
                        )}
                        onDragOver={handleDragOver}
                        onDragEnter={handleDragOver}
                        onDragLeave={handleDragLeave}
                        onDrop={handleDrop}
                      >
                        <button
                          type="button"
                          className={cn(
                            "w-full flex items-center justify-center gap-1.5 text-center cursor-pointer rounded-[var(--pm-r-sm)] transition-colors",
                            selectOpen
                              ? "h-full px-2 hover:bg-white/70"
                              : "flex-1 flex-col min-h-[88px] px-3 py-3 gap-1.5 hover:bg-white/70"
                          )}
                          onClick={() => {
                            if (selectOpen) {
                              // Compact row: leave From Collection → restore drop zone
                              setSelect(false)
                              return
                            }
                            fileInputRef.current?.click()
                          }}
                          title={selectOpen ? "Back to upload" : "Browse files"}
                        >
                          <Upload
                            className={cn(
                              "text-[var(--pm-faint)] shrink-0",
                              selectOpen ? "h-3.5 w-3.5" : "h-5 w-5"
                            )}
                            strokeWidth={1.5}
                          />
                          {selectOpen ? (
                            <span className="pm-meta text-[var(--pm-muted)]">
                              Upload File
                            </span>
                          ) : (
                            <>
                              <p className="pm-meta text-[var(--pm-muted)]">
                                Drag & drop files here
                              </p>
                              <p className="pm-meta text-[var(--pm-faint)]">
                                or click to browse
                              </p>
                            </>
                          )}
                        </button>
                      </div>

                      <div className="pm-timeline-attach-action-row shrink-0">
                        {selectOpen ? (
                          <button
                            type="button"
                            className={cn(
                              "pm-timeline-attach-confirm",
                              draftDirty && "is-ready"
                            )}
                            onClick={applyCollectionConfirm}
                            disabled={!draftDirty}
                          >
                            Confirm
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="pm-timeline-attach-from-collection"
                            onClick={() => setSelect(true)}
                          >
                            <FolderOpen
                              className="h-3.5 w-3.5 shrink-0"
                              strokeWidth={1.75}
                            />
                            From Collection
                          </button>
                        )}
                      </div>

                      <div className="pm-timeline-attach-body flex-1 min-h-0">
                        {/* Pending list — folds away when picker opens */}
                        <div
                          className={cn(
                            "pm-timeline-attach-list-pane",
                            !selectOpen && "is-open"
                          )}
                        >
                          <div className="pm-timeline-attach-list-inner">
                            {pending.length === 0 ? (
                              <p className="pm-meta text-[var(--pm-faint)] px-1 py-2">
                                No files attached yet
                              </p>
                            ) : (
                              <ul className="pm-ws-list pm-timeline-attach-list">
                                {pending.map((att) => (
                                  <li
                                    key={att.key}
                                    className="pm-timeline-attach-item"
                                  >
                                    <div className="pm-timeline-attach-item-inner">
                                      <div className="pm-ws-list-item group/file pm-timeline-attach-item-row">
                                        <div className="flex items-center gap-2 min-w-0 w-full">
                                          <Paperclip
                                            className="h-3 w-3 shrink-0 text-[var(--pm-faint)]"
                                            strokeWidth={1.75}
                                          />
                                          <span
                                            className="flex-1 min-w-0 text-left break-words"
                                            title={
                                              att.kind === "existing"
                                                ? att.filename
                                                : att.file.name
                                            }
                                          >
                                            {att.kind === "existing"
                                              ? att.filename
                                              : att.file.name}
                                          </span>
                                          {att.kind === "upload" && (
                                            <span className="pm-meta shrink-0 text-[var(--pm-green)]">
                                              new
                                            </span>
                                          )}
                                          <button
                                            type="button"
                                            className="opacity-0 group-hover/file:opacity-100 transition-opacity text-[var(--pm-faint)] hover:text-[var(--pm-danger)] shrink-0"
                                            onClick={() =>
                                              removePending(att.key)
                                            }
                                            title="Remove attachment"
                                            aria-label="Remove attachment"
                                          >
                                            <XCircle className="h-3 w-3" />
                                          </button>
                                        </div>
                                      </div>
                                    </div>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        </div>

                        {/* File tree — flat under Confirm (Node sidebar language) */}
                        <div
                          className={cn(
                            "pm-timeline-attach-tree-pane",
                            selectOpen && "is-open"
                          )}
                          data-file-select-zone={
                            selectOpen ? true : undefined
                          }
                        >
                          <div className="pm-timeline-attach-tree-inner">
                            {selectOpen ? (
                              <FileTreePicker
                                collectionId={collectionId}
                                selectedIds={draftIds}
                                onSelectFile={toggleDraft}
                                onPreviewFile={setSelectPreviewFile}
                                maxHeightClass="h-full max-h-full"
                                className="h-full flex flex-col"
                              />
                            ) : null}
                          </div>
                        </div>
                      </div>
                    </div>
                  </section>
                </div>

                {/* ── Right: message editor ── */}
                <section className="pm-add-node-card pm-add-node-card--message">
                  <div className="pm-add-node-msg-head">
                    <FieldLabel className="pm-add-node-msg-label">
                      Message
                    </FieldLabel>
                    <span className="pm-meta text-[var(--pm-faint)]">
                      Optional · markdown
                    </span>
                  </div>
                  <div className="pm-add-node-md-host">
                    {open && (
                      <MarkdownEditor
                        key={editorKey}
                        value={messageBody}
                        onChange={setMessageBody}
                        minHeight="100%"
                        placeholder={MESSAGE_EDITOR_PLACEHOLDER}
                        showToolbar
                        flush
                        className="pm-add-node-md-editor"
                      />
                    )}
                  </div>
                </section>
              </div>
            </div>
          </div>
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
            const newest = gs.find(
              (g) => !localGroups.some((o) => o.group_id === g.group_id)
            )
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
