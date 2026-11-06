import { Check, ChevronRight, Pencil, Star } from "lucide-react"
import type { FileSummary, FolderTreeNode, NodeGroup } from "@/types/file-mgmt"
import { cn } from "@/lib/utils"
import { FileTypeIcon } from "@/components/file-mgmt/file-type-icon"
import { FolderIconView } from "@/components/file-mgmt/timeline-view/group-icons"
import { useT } from "@/i18n/use-t"
import { systemFolderDisplayName } from "@/i18n/system-folder"
import {
  fileExtLabel,
  formatItemDate,
  itemDateIso,
  type FolderGridItem,
} from "./sorted-items"
import type { FolderFileSortMode } from "@/stores/file-mgmt-store"

export function FolderListRow({
  folder,
  selected,
  multiSelectMode,
  sortMode,
  boundGroup,
  onOpen,
  onSelect,
  onEdit,
}: {
  folder: FolderTreeNode
  selected: boolean
  multiSelectMode: boolean
  sortMode: FolderFileSortMode
  boundGroup?: NodeGroup | null
  onOpen: () => void
  onSelect: () => void
  onEdit?: () => void
}) {
  const t = useT()
  const fullName =
    systemFolderDisplayName(folder.name || "", t) || t("common.untitled")
  const date = formatItemDate(
    itemDateIso({ kind: "folder", folder }, sortMode)
  )
  return (
    <div
      className={cn(
        "pm-files-row group",
        selected && "is-selected",
        folder.archived && "is-archived"
      )}
    >
      <button
        type="button"
        className="pm-files-row-main"
        title={fullName}
        aria-label={fullName}
        aria-pressed={selected}
        onClick={onSelect}
        onKeyDown={e => { if (e.key === "Enter" && !multiSelectMode) { e.preventDefault(); onOpen() } }}
        onDoubleClick={(e) => {
          e.preventDefault()
          onOpen()
        }}
      >
        {multiSelectMode && <span className={cn("sd-file-choice", selected && "is-checked")} aria-hidden="true">{selected && <Check size={11} />}</span>}
        <span className="pm-files-row-icon">
          <FolderIconView
            folder={folder}
            boundGroup={boundGroup}
            className="h-5 w-5"
          />
        </span>
        <span className="pm-files-row-name"><strong>{fullName}</strong><small>{t("fileMgmt.fileAndFolderCounts", { files: folder.file_count || 0, folders: folder.children.length })}</small></span>
        <span className="pm-files-row-type">{t("fileBrowser.folder")}</span>
        <span className="pm-files-row-meta">{date}</span>
      </button>
      {onEdit ? (
        <button
          type="button"
          className="pm-files-row-edit"
          title={t("common.edit")}
          onClick={(e) => {
            e.stopPropagation()
            onEdit()
          }}
        >
          <Pencil className="h-3 w-3" />
        </button>
      ) : <span className="pm-files-row-edit" aria-hidden />}
      {!multiSelectMode && <button className="sd-folder-open" title={t("fileBrowser.openFolder", { name: fullName })} aria-label={t("fileBrowser.openFolder", { name: fullName })} onClick={onOpen}><ChevronRight size={15} /></button>}
    </div>
  )
}

export function FileListRow({
  file,
  selected,
  multiSelectMode,
  ingesting,
  sortMode,
  onSelect,
  onOpen,
  onEdit,
}: {
  file: FileSummary
  selected: boolean
  multiSelectMode: boolean
  ingesting?: { taskId: string; progress: number; message: string } | null
  sortMode: FolderFileSortMode
  onSelect: () => void
  onOpen?: () => void
  onEdit?: () => void
}) {
  const t = useT()
  const isArchived = file.is_greyed || file.archived
  const isIngesting = !!ingesting
  const fullName = file.display_name || file.filename || t("common.untitled")
  const ext = fileExtLabel(file)
  const date = formatItemDate(itemDateIso({ kind: "file", file }, sortMode))
  return (
    <div
      className={cn(
        "pm-files-row group",
        selected && !isIngesting && "is-selected",
        isArchived && "is-archived",
        isIngesting && "is-busy"
      )}
    >
      <button
        type="button"
        className="pm-files-row-main"
        title={fullName}
        aria-label={fullName}
        aria-pressed={selected}
        aria-disabled={isIngesting}
        onKeyDown={e => { if (e.key === "Enter" && !multiSelectMode && !isIngesting && onOpen) { e.preventDefault(); onOpen() } }}
        onClick={() => {
          if (isIngesting) return
          onSelect()
        }}
        onDoubleClick={(e) => {
          e.preventDefault()
          e.stopPropagation()
          if (isIngesting || !onOpen) return
          onOpen()
        }}
      >
        {multiSelectMode && <span className={cn("sd-file-choice", selected && "is-checked")} aria-hidden="true">{selected && <Check size={11} />}</span>}
        <span className="pm-files-row-icon">
          <FileTypeIcon
            source={{
              filename: file.filename,
              original_ext: file.original_ext,
              unsupported: file.unsupported,
              source: file.source,
              display_name: file.display_name,
              kind: file.doc_kind as "meeting" | "note" | "file" | null,
            }}
            className="h-5 w-5"
          />
        </span>
        <span className="pm-files-row-name"><strong>{fullName}</strong>{(isIngesting || isArchived || file.is_definitive || (file.filename && file.filename !== fullName)) && <small>{isIngesting ? t("fileMgmt.ingestingEllipsis") : isArchived ? t("fileMgmt.archived") : file.is_definitive ? <><Star size={10} fill="currentColor" />{t("fileBrowser.definitive")}</> : file.filename}</small>}</span>
        <span className="pm-files-row-type">{ext}</span>
        <span className="pm-files-row-meta">
          {isIngesting ? t("fileMgmt.ingestingEllipsis") : date}
        </span>
      </button>
      {onEdit && !isIngesting ? (
        <button
          type="button"
          className="pm-files-row-edit"
          title={t("common.edit")}
          onClick={(e) => {
            e.stopPropagation()
            onEdit()
          }}
        >
          <Pencil className="h-3 w-3" />
        </button>
      ) : (
        <span className="pm-files-row-edit" aria-hidden />
      )}
      {!multiSelectMode && <span className="sd-folder-open" aria-hidden="true" />}
    </div>
  )
}

export function renderListItem(
  item: FolderGridItem,
  opts: {
    selected: boolean
    multiSelectMode: boolean
    sortMode: FolderFileSortMode
    boundGroup?: NodeGroup | null
    ingesting?: { taskId: string; progress: number; message: string } | null
    onOpen: () => void
    onSelect: () => void
    onEdit?: () => void
  }
) {
  if (item.kind === "folder") {
    return (
      <FolderListRow
        folder={item.folder}
        selected={opts.selected}
        multiSelectMode={opts.multiSelectMode}
        sortMode={opts.sortMode}
        boundGroup={opts.boundGroup}
        onOpen={opts.onOpen}
        onSelect={opts.onSelect}
        onEdit={opts.onEdit}
      />
    )
  }
  return (
    <FileListRow
      file={item.file}
      selected={opts.selected}
      multiSelectMode={opts.multiSelectMode}
      ingesting={opts.ingesting}
      sortMode={opts.sortMode}
      onSelect={opts.onSelect}
      onOpen={opts.onOpen}
      onEdit={opts.onEdit}
    />
  )
}
