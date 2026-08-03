/**
 * Slide-in file preview for "select existing file" flows.
 * Renders outside the drop-zone / tree (left of node detail or dialog).
 */
import { X } from "lucide-react"
import { cn } from "@/lib/utils"
import type { FileSummary } from "@/types/file-mgmt"
import { getFilePreviewUrl } from "@/api/client"
import {
  RawFileViewer,
  resolveRawFilename,
} from "@/components/file-mgmt/raw-file-viewer"

export function FileSelectPreviewPanel({
  collectionId,
  file,
  onClose,
  className,
}: {
  collectionId: string
  file: FileSummary | null
  onClose?: () => void
  className?: string
}) {
  const source =
    file?.source || (file ? `__file__:${file.file_id}` : null)
  const url =
    source && collectionId
      ? getFilePreviewUrl(source, { collection: collectionId })
      : null
  const name = file
    ? resolveRawFilename(
        file.filename,
        file.display_name,
        file.original_ext ? `file.${file.original_ext}` : null
      )
    : "file.bin"

  return (
    <div
      className={cn(
        "flex flex-col min-h-0 min-w-0 overflow-hidden rounded-xl border border-border bg-background shadow-lg",
        className
      )}
    >
      <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-border">
        <span
          className="flex-1 min-w-0 text-[11px] font-medium truncate"
          title={file ? file.display_name || file.filename : "Preview"}
        >
          {file ? file.display_name || file.filename : "Preview"}
        </span>
        {onClose && (
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground shrink-0 p-0.5"
            onClick={onClose}
            title="Close preview"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      <div className="flex-1 min-h-0 overflow-hidden">
        {file && url ? (
          <RawFileViewer
            key={file.file_id}
            url={url}
            filename={name}
            downloadUrl={url}
            className="h-full border-0 rounded-none"
          />
        ) : (
          <div className="h-full flex items-center justify-center p-4 text-[11px] text-muted-foreground/60 text-center">
            Click a file to preview
          </div>
        )}
      </div>
    </div>
  )
}
