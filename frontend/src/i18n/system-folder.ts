type Translate = (key: string, vars?: Record<string, string | number>) => string

const SYSTEM_FOLDER_KEYS: Record<string, string> = {
  meeting: "fileMgmt.systemFolderMeeting",
  notes: "fileMgmt.systemFolderNotes",
  note: "fileMgmt.systemFolderNote",
  archived: "fileMgmt.systemFolderArchived",
  archive: "fileMgmt.systemFolderArchive",
  uncategorized: "common.uncategorized",
}

/** Display label for a stored system folder/group name. Identity names stay English. */
export function systemFolderDisplayName(name: string, translate: Translate): string {
  const raw = (name || "").trim()
  if (!raw) return name
  const key = SYSTEM_FOLDER_KEYS[raw.toLowerCase()]
  return key ? translate(key) : name
}

/** Translate system-folder segments in a stored breadcrumb path such as /Meeting/foo. */
export function systemFolderDisplayPath(path: string, translate: Translate): string {
  if (!path) return path
  return path
    .split("/")
    .map((seg) => (seg ? systemFolderDisplayName(seg, translate) : seg))
    .join("/")
}
