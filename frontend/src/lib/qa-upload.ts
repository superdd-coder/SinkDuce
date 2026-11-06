type WorkbookFile = { name: string; size: number }
export type QaWorkbookUploadError = "single" | "format" | "size"

/** Click selection and drag/drop share validation and one in-flight import. */
export function createQaWorkbookUpload() {
  let pending = false
  return {
    async receive<T extends WorkbookFile>(files: ArrayLike<T>, onFile: (file: T) => Promise<void>, disabled = false): Promise<QaWorkbookUploadError | null> {
      if (disabled || pending) return null
      if (files.length !== 1) return "single"
      const file = files[0]
      if (!file.name.toLowerCase().endsWith(".xlsx")) return "format"
      if (file.size > 20 * 1024 * 1024) return "size"
      pending = true
      try { await onFile(file) } finally { pending = false }
      return null
    },
  }
}
