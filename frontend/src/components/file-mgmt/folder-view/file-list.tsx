import { Check, Pencil } from "lucide-react"
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
        onClick={onSelect}
        onDoubleClick={(e) => {
          e.preventDefault()
          onOpen()
        }}
      >
        <span className="pm-files-row-icon">
          <FolderIconView
            folder={folder}
            boundGroup={boundGroup}
            className="h-5 w-5"
          />
        </span>
        <span className="pm-files-row-name">{fullName}</span>
        <span className="pm-files-row-type" />
        <span className="pm-files-row-meta">{date}</span>
        {selected && multiSelectMode && (
          <span className="pm-files-row-check">
            <Check className="h-2.5 w-2.5" />
          </span>
        )}
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
      ) : (
        <span className="pm-files-row-edit" aria-hidden />
      )}
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
        <span className="pm-files-row-name">{fullName}</span>
        <span className="pm-files-row-type">{ext}</span>
        <span className="pm-files-row-meta">
          {isIngesting ? t("fileMgmt.ingestingEllipsis") : date}
        </span>
        {selected && multiSelectMode && !isIngesting && (
          <span className="pm-files-row-check">
            <Check className="h-2.5 w-2.5" />
          </span>
        )}
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
