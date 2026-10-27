import { tr } from "@/i18n/tr"
import { systemFolderDisplayName } from "@/i18n/system-folder"

/** Technical source keys must never be shown as filenames. */
export function isOpaqueSourceKey(value: string | undefined | null): boolean {
  const s = (value || "").trim()
  if (!s) return true
  if (
    s.startsWith("__file__:") ||
    s.startsWith("__meeting__:") ||
    s.startsWith("__note__:") ||
    s.startsWith("file:")
  ) {
    return true
  }
  if (s.length === 32 && /^[0-9a-f]+$/i.test(s)) return true
  return false
}

export type SourceFileHint = {
  source: string
  display_name?: string
  file_id?: string
}

/** Prefer ingest `source_label`, then the files list, never a raw file id. */
export function humanSourceLabel(
  meta: {
    source?: unknown
    source_label?: unknown
    filename?: unknown
    display_name?: unknown
  },
  files?: SourceFileHint[],
): string {
  const source = String(meta.source || meta.filename || "").trim()
  const fromMeta = String(meta.source_label || meta.display_name || "").trim()
  if (fromMeta && !isOpaqueSourceKey(fromMeta)) {
    if (fromMeta.startsWith("Note: ")) return fromMeta.slice(6).trim() || fromMeta
    if (fromMeta.startsWith("Meeting: ")) return fromMeta.slice(9).trim() || fromMeta
    return fromMeta
  }
  if (files?.length && source) {
    const fid = source.startsWith("__file__:")
      ? source.slice("__file__:".length)
      : source.startsWith("file:")
        ? source.slice("file:".length)
        : source
    const hit = files.find(
      (f) =>
        f.source === source ||
        f.source === `__file__:${fid}` ||
        f.source === `file:${fid}` ||
        (f.file_id && f.file_id === fid),
    )
    const name = (hit?.display_name || "").trim()
    if (name && !isOpaqueSourceKey(name)) return name
  }
  if (source && !isOpaqueSourceKey(source)) {
    const last = source.split("/").pop() || source
    if (last && !isOpaqueSourceKey(last)) return last
  }
  if (source.startsWith("__meeting__:")) return systemFolderDisplayName("Meeting", tr)
  if (source.startsWith("__note__:")) return systemFolderDisplayName("Note", tr)
  return tr("common.file")
}
