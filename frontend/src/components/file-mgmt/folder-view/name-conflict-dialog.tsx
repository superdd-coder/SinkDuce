import { useEffect, useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"

/** Last suffix including dot, e.g. ".pdf"; empty if none. */
function fileExt(name: string): string {
  const base = name.trim().split(/[/\\]/).pop() || name
  const i = base.lastIndexOf(".")
  if (i <= 0) return ""
  return base.slice(i)
}

function fileStem(name: string, ext: string): string {
  const base = name.trim().split(/[/\\]/).pop() || name
  if (ext && base.toLowerCase().endsWith(ext.toLowerCase())) {
    return base.slice(0, -ext.length)
  }
  const i = base.lastIndexOf(".")
  return i > 0 ? base.slice(0, i) : base
}

export function NameConflictDialog() {
  const nameConflict = useFileMgmtStore((s) => s.nameConflict)
  const resolveNameConflict = useFileMgmtStore((s) => s.resolveNameConflict)
  const cancelNameConflict = useFileMgmtStore((s) => s.cancelNameConflict)

  const [value, setValue] = useState("")
  const [submitting, setSubmitting] = useState(false)

  const isFile = nameConflict?.resource === "file"
  /** Locked extension for file conflicts (from original conflicting name). */
  const lockedExt = useMemo(() => {
    if (!nameConflict || nameConflict.resource !== "file") return ""
    // Prefer original name's suffix; fall back to suggested
    return fileExt(nameConflict.name) || fileExt(nameConflict.suggestedName)
  }, [nameConflict])

  useEffect(() => {
    if (nameConflict) {
      if (nameConflict.resource === "file") {
        const ext =
          fileExt(nameConflict.name) || fileExt(nameConflict.suggestedName)
        setValue(fileStem(nameConflict.suggestedName, ext))
      } else {
        setValue(nameConflict.suggestedName)
      }
      setSubmitting(false)
    }
  }, [nameConflict])

  const open = !!nameConflict
  const resourceLabel =
    nameConflict?.resource === "folder" ? "folder" : "file"

  const handleConfirm = async () => {
    if (!nameConflict) return
    let next = value.trim()
    if (!next) return

    if (nameConflict.resource === "file" && lockedExt) {
      // Strip accidental extension if user typed it into the stem field
      if (next.toLowerCase().endsWith(lockedExt.toLowerCase())) {
        next = next.slice(0, -lockedExt.length).trim()
      }
      next = (next || "unnamed") + lockedExt
    }

    setSubmitting(true)
    try {
      await resolveNameConflict(next)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        // Ignore dismiss while the Edit dialog is still tearing down.
        if (!v && nameConflict) cancelNameConflict()
      }}
    >
      <DialogContent
        overlayClassName="pm-dialog-overlay--silk"
        className="pm-dialog w-[min(32rem,calc(100vw-2rem))] max-w-[min(32rem,calc(100vw-2rem))] overflow-hidden"
      >
        <DialogHeader>
          <DialogTitle>Name already used</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 min-w-0">
          <p className="pm-dialog-body break-words [overflow-wrap:anywhere]">
            {nameConflict?.message ||
              `A ${resourceLabel} with this name already exists in this location. Choose another name.`}
          </p>
          {nameConflict?.name && (
            <p
              className="pm-meta font-mono text-[var(--pm-text)] bg-[var(--pm-green-wash)] rounded-[var(--pm-r-sm)] px-2 py-1.5 break-all [overflow-wrap:anywhere]"
              title={nameConflict.name}
            >
              {nameConflict.name}
            </p>
          )}
          <div className="min-w-0">
            <label className="pm-field-label">New name</label>
            {isFile && lockedExt ? (
              <>
                <div className="flex items-center gap-1.5 min-w-0">
                  <Input
                    value={value}
                    onChange={(e) => setValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void handleConfirm()
                    }}
                    autoFocus
                    className="h-8 flex-1 min-w-0 font-mono"
                    title={value}
                  />
                  <span
                    className="shrink-0 pm-meta font-mono bg-[var(--pm-green-wash)] rounded-[var(--pm-r-sm)] px-2 h-8 inline-flex items-center"
                    title="Extension cannot be changed"
                  >
                    {lockedExt}
                  </span>
                </div>
                <p className="pm-meta mt-1.5">
                  Extension is fixed and cannot be changed.
                </p>
              </>
            ) : (
              <Input
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void handleConfirm()
                }}
                autoFocus
                className="h-8 w-full min-w-0 font-mono"
                title={value}
              />
            )}
          </div>
        </div>
        <DialogFooter className="gap-2 pt-1 flex-wrap">
          <Button
            variant="ghost"
            onClick={() => cancelNameConflict()}
            disabled={submitting}
          >
            Cancel
          </Button>
          <Button
            onClick={() => void handleConfirm()}
            disabled={!value.trim() || submitting}
          >
            {submitting ? "Saving…" : "Use this name"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
