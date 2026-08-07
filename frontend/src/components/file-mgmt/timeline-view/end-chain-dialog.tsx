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
import {
  CheckSquare,
  ChevronDown,
  ChevronRight,
  Paperclip,
  Search,
  Square,
  Upload,
  X,
} from "lucide-react"
import { toast } from "sonner"
import type { FileSummary, Node, NodeGroup } from "@/types/file-mgmt"
import {
  attachFileToNode,
  endChain,
  getNodeDetail,
  uploadFileToFolder,
} from "@/api/file-mgmt"
import { FileTreePicker } from "./file-tree-picker"
import { FileSelectPreviewPanel } from "@/components/file-mgmt/file-select-preview-panel"

type PendingAttachment =
  | { kind: "existing"; key: string; file_id: string; filename: string }
  | { kind: "upload"; key: string; file: File }

interface EndChainDialogProps {
  collectionId: string
  chainNodeId: string
  nodes: Node[]
  open: boolean
  onOpenChange: (open: boolean) => void
  onComplete: () => void
  groups: NodeGroup[]
}

interface NodeWithFiles {
  node: Node
  files: { file_id: string; filename: string; is_definitive: boolean }[]
}

export function EndChainDialog({
  collectionId,
  chainNodeId,
  nodes,
  open,
  onOpenChange,
  onComplete,
  groups,
}: EndChainDialogProps) {
  const [nodesWithFiles, setNodesWithFiles] = useState<NodeWithFiles[]>([])
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  /** Checked file ids = inherit (keep branch path active). */
  const [selectedFileIds, setSelectedFileIds] = useState<Set<string>>(new Set())
  const [title, setTitle] = useState("")
  const [groupId, setGroupId] = useState("")
  const [eventTime, setEventTime] = useState("")
  const [messageBody, setMessageBody] = useState("")
  const [pending, setPending] = useState<PendingAttachment[]>([])
  const [attachMode, setAttachMode] = useState<"select" | null>(null)
  const [selectPreviewFile, setSelectPreviewFile] =
    useState<FileSummary | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [loading, setLoading] = useState(false)
  const dateInputRef = useRef<HTMLInputElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const setSelectMode = (mode: "select" | null) => {
    setAttachMode(mode)
    if (mode !== "select") setSelectPreviewFile(null)
  }

  const nodesWithAttachments = useMemo(
    () => nodesWithFiles.filter((n) => n.files.length > 0),
    [nodesWithFiles]
  )

  useEffect(() => {
    if (!open) return
    setTitle("")
    setGroupId(groups[0]?.group_id ?? "")
    setEventTime("")
    setMessageBody("")
    setPending([])
    setAttachMode(null)
    setSelectPreviewFile(null)
    setDragOver(false)
    setExpanded(new Set())

    const fetchAll = async () => {
      const results: NodeWithFiles[] = []
      for (const n of nodes) {
        if (n.node_type === "end" || n.node_type === "start") continue
        try {
          const detail = await getNodeDetail(collectionId, n.node_id)
          const files = (detail.attachments ?? []).map((a) => ({
            file_id: a.file_id,
            filename: a.filename,
            is_definitive: a.is_definitive,
          }))
          results.push({ node: n, files })
        } catch {
          results.push({ node: n, files: [] })
        }
      }
      setNodesWithFiles(results)
      // Default: nothing inherited until user expands a node
      setSelectedFileIds(new Set())
      setExpanded(new Set())
    }
    void fetchAll()
  }, [open, collectionId, nodes, groups])

  /** Expand/collapse a node row; expanding auto-checks all of its files. */
  const toggleExpandNode = (nwf: NodeWithFiles) => {
    const nid = nwf.node.node_id
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(nid)) {
        next.delete(nid)
      } else {
        next.add(nid)
        // On expand: select every file under this node
        setSelectedFileIds((sel) => {
          const s = new Set(sel)
          for (const f of nwf.files) s.add(f.file_id)
          return s
        })
      }
      return next
    })
  }

  const nodeHasAnySelected = (nwf: NodeWithFiles) =>
    nwf.files.some((f) => selectedFileIds.has(f.file_id))

  const toggleFile = (fileId: string) => {
    setSelectedFileIds((prev) => {
      const next = new Set(prev)
      if (next.has(fileId)) next.delete(fileId)
      else next.add(fileId)
      return next
    })
  }

  const toggleNodeAll = (nwf: NodeWithFiles) => {
    const allOn = nwf.files.every((f) => selectedFileIds.has(f.file_id))
    setSelectedFileIds((prev) => {
      const next = new Set(prev)
      for (const f of nwf.files) {
        if (allOn) next.delete(f.file_id)
        else next.add(f.file_id)
      }
      return next
    })
  }

  const addUploadFiles = (files: FileList | File[]) => {
    const list = Array.from(files)
    if (!list.length) return
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
          kind: "existing" as const,
          key: `existing-${file.file_id}`,
          file_id: file.file_id,
          filename: file.display_name || file.filename,
        },
      ]
    })
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

  const handleSubmit = async () => {
    if (!chainNodeId) return
    const trimmedTitle = title.trim()
    if (!trimmedTitle) {
      toast.error("Merge node name is required")
      return
    }
    if (!groupId) {
      toast.error("Group is required")
      return
    }
    setLoading(true)
    try {
      // Upload pending files into the selected group's folder first, then end_chain attaches
      const groupFolderId = groups.find((g) => g.group_id === groupId)?.folder_id
      const attachmentIds: string[] = []
      for (const att of pending) {
        if (att.kind === "existing") {
          attachmentIds.push(att.file_id)
        } else {
          if (!groupFolderId) {
            throw new Error("Selected group has no folder for uploads")
          }
          const uploaded = await uploadFileToFolder(
            collectionId,
            groupFolderId,
            att.file,
          )
          attachmentIds.push(uploaded.file_id)
        }
      }

      const result = await endChain(collectionId, chainNodeId, {
        inherit_file_ids: Array.from(selectedFileIds),
        title: trimmedTitle,
        group_id: groupId,
        event_time: eventTime || null,
        message_body: messageBody.trim() || null,
        attachment_file_ids: attachmentIds,
      })

      // Attach uploads that end_chain already linked; double-attach is no-op if same
      if (result.merged_node_id) {
        for (const fid of attachmentIds) {
          try {
            await attachFileToNode(collectionId, result.merged_node_id, fid)
          } catch {
            /* already attached by end_chain */
          }
        }
      }

      const pathN = result.path_archived_files?.length ?? result.greyed_files?.length ?? 0
      const fileN = result.file_archived?.length ?? 0
      toast.success(
        `Chain ended. ${pathN} archived on branch` +
          (fileN ? `, ${fileN} excluded from search` : "") +
          (result.merged_node_id ? ". Merge node created." : ".")
      )
      onComplete()
      onOpenChange(false)
    } catch (err) {
      toast.error(`End chain failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setLoading(false)
    }
  }

  const showSelectPreview = attachMode === "select"

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        overlayClassName="pm-dialog-overlay--silk"
        className={cn(
          "pm-dialog pm-dialog--silk max-w-[98vw] sm:max-w-[98vw] h-[85vh] max-h-[85vh] !flex flex-row gap-0 p-0 overflow-hidden transition-[width] duration-300 ease-out",
          // Form body fixed (wider Message); preview is extra width on the left
          showSelectPreview
            ? "w-[min(calc(920px+min(calc(85vh*210/297),42vw)),98vw)]"
            : "w-[920px]"
        )}
      >
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
        <DialogHeader className="shrink-0">
          <DialogTitle className="text-sm">End Branch Chain</DialogTitle>
        </DialogHeader>

        <div className="flex-1 min-h-0 min-w-0 flex gap-4 overflow-hidden">
          {/* Left: form + inherit */}
          <div className="w-[320px] shrink-0 flex flex-col min-h-0 gap-3 overflow-y-auto pr-1">
            <div>
              <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60 block mb-1">
                Title <span className="text-destructive">*</span>
              </label>
              <input
                className="w-full text-xs border rounded px-2 py-1.5 bg-background"
                placeholder="Merge node title..."
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                required
              />
            </div>

            <div>
              <FieldLabel>
                Group <span className="text-[var(--pm-danger)]">*</span>
              </FieldLabel>
              <DropdownSelect
                size="sm"
                value={groupId}
                onChange={setGroupId}
                placeholder="No groups"
                options={
                  groups.length === 0
                    ? [{ value: "", label: "No groups" }]
                    : groups.map((g) => ({
                        value: g.group_id,
                        label: g.name,
                      }))
                }
              />
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
                    onClick={() => setEventTime("")}
                  >
                    <X className="h-3 w-3" />
                  </button>
                )}
              </div>
            </div>

            {/* Attachments for merge node — compact drop; expand tree on Select */}
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2 min-w-0">
                <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60 truncate">
                  Attachments ({pending.length})
                </label>
                <button
                  type="button"
                  className="text-[10px] font-medium text-primary hover:underline flex items-center gap-1 shrink-0"
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
                className="hidden"
                multiple
                onChange={(e) => {
                  if (e.target.files) addUploadFiles(e.target.files)
                  e.target.value = ""
                }}
              />
              <div
                className={cn(
                  "rounded-md border border-dashed p-2 transition-[height,colors] duration-200 overflow-hidden flex flex-col",
                  attachMode === "select"
                    ? "h-[min(380px,48vh)]"
                    : "h-[112px]",
                  dragOver
                    ? "border-primary bg-primary/5"
                    : "border-border bg-muted/10"
                )}
                onDragOver={(e: DragEvent) => {
                  e.preventDefault()
                  e.stopPropagation()
                  if (e.dataTransfer.types.includes("Files")) setDragOver(true)
                }}
                onDragEnter={(e: DragEvent) => {
                  e.preventDefault()
                  e.stopPropagation()
                  if (e.dataTransfer.types.includes("Files")) setDragOver(true)
                }}
                onDragLeave={(e: DragEvent) => {
                  e.preventDefault()
                  e.stopPropagation()
                  if (e.currentTarget === e.target) setDragOver(false)
                }}
                onDrop={(e: DragEvent) => {
                  e.preventDefault()
                  e.stopPropagation()
                  setDragOver(false)
                  if (e.dataTransfer.files?.length)
                    addUploadFiles(e.dataTransfer.files)
                }}
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
                        selectedIds={
                          new Set(
                            pending
                              .filter((p) => p.kind === "existing")
                              .map((p) => (p as { file_id: string }).file_id)
                          )
                        }
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
                      <div className="space-y-1 mb-1.5 overflow-y-auto max-h-[48%] shrink-0 min-h-0">
                        {pending.map((p) => (
                          <div
                            key={p.key}
                            className="flex items-center gap-1.5 px-2 py-1 rounded bg-background border border-border/60 text-xs"
                          >
                            <Paperclip className="h-3 w-3 text-muted-foreground shrink-0" />
                            <span className="flex-1 truncate min-w-0">
                              {p.kind === "upload" ? p.file.name : p.filename}
                            </span>
                            {p.kind === "upload" && (
                              <span className="text-[9px] text-muted-foreground shrink-0">
                                new
                              </span>
                            )}
                            <button
                              type="button"
                              className="text-muted-foreground hover:text-red-500 shrink-0"
                              title="Remove"
                              onClick={(e) => {
                                e.stopPropagation()
                                setPending((prev) =>
                                  prev.filter((x) => x.key !== p.key)
                                )
                              }}
                            >
                              <X className="h-3 w-3" />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                    <button
                      type="button"
                      className="flex-1 flex flex-col items-center justify-center gap-1 text-center px-2 cursor-pointer rounded hover:bg-muted/30 transition-colors min-h-0"
                      onClick={() => fileInputRef.current?.click()}
                    >
                      <Upload className="h-4 w-4 text-muted-foreground/40" />
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

            {/* Inherit module */}
            <div className="flex-1 min-h-0 flex flex-col border rounded-md overflow-hidden">
              <div className="px-2 py-1.5 border-b bg-muted/20">
                <p className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60">
                  Inherit files
                </p>
                <p className="text-[9px] text-muted-foreground mt-0.5">
                  Expand a node to select its files (auto-checked). Checked files keep their branch path; unchecked get path-archived on this branch.
                </p>
              </div>
              <div className="flex-1 overflow-y-auto p-1.5 space-y-1 max-h-48">
                {nodesWithAttachments.length === 0 ? (
                  <p className="text-[10px] text-muted-foreground/60 px-1 py-2">
                    No nodes with attachments on this branch.
                  </p>
                ) : (
                  nodesWithAttachments.map((nwf) => {
                    const open = expanded.has(nwf.node.node_id)
                    const selected = nodeHasAnySelected(nwf)
                    return (
                      <div
                        key={nwf.node.node_id}
                        className={cn(
                          "rounded-md border transition-shadow",
                          selected
                            ? "border-emerald-700/50 shadow-[0_0_10px_rgba(6,95,70,0.45)]"
                            : "border-border"
                        )}
                      >
                        <button
                          type="button"
                          className="w-full flex items-center gap-1.5 px-2 py-1.5 text-left"
                          onClick={() => toggleExpandNode(nwf)}
                        >
                          {open ? (
                            <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" />
                          ) : (
                            <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" />
                          )}
                          <span className="text-xs font-medium truncate flex-1">
                            {nwf.node.title || "Untitled"}
                          </span>
                          <button
                            type="button"
                            className="shrink-0 p-0.5"
                            onClick={(e) => {
                              e.stopPropagation()
                              toggleNodeAll(nwf)
                            }}
                            title="Toggle all files"
                          >
                            {nwf.files.every((f) => selectedFileIds.has(f.file_id)) ? (
                              <CheckSquare className="h-3.5 w-3.5 text-emerald-700" />
                            ) : (
                              <Square className="h-3.5 w-3.5 text-muted-foreground" />
                            )}
                          </button>
                        </button>
                        {open && (
                          <div className="px-2 pb-1.5 space-y-0.5">
                            {nwf.files.map((f) => {
                              const on = selectedFileIds.has(f.file_id)
                              return (
                                <button
                                  key={f.file_id}
                                  type="button"
                                  className="w-full flex items-center gap-1.5 px-1.5 py-1 rounded text-[10px] hover:bg-muted/40"
                                  onClick={() => toggleFile(f.file_id)}
                                >
                                  {on ? (
                                    <CheckSquare className="h-3 w-3 text-emerald-700 shrink-0" />
                                  ) : (
                                    <Square className="h-3 w-3 text-muted-foreground shrink-0" />
                                  )}
                                  <span className="truncate">{f.filename || f.file_id}</span>
                                </button>
                              )
                            })}
                          </div>
                        )}
                      </div>
                    )
                  })
                )}
              </div>
            </div>
          </div>

          {/* Right: message — min-w-0 + overflow so MarkdownEditor stays inside dialog */}
          <div className="flex-1 min-w-0 min-h-0 flex flex-col overflow-hidden">
            <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60 block mb-1.5 shrink-0">
              Message (optional)
            </label>
            <div className="flex-1 min-h-0 min-w-0 overflow-auto rounded border border-border">
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
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)} disabled={loading}>
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={() => void handleSubmit()}
            disabled={loading || !groupId || !title.trim()}
          >
            {loading ? "Processing..." : "End & Merge"}
          </Button>
        </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  )
}
