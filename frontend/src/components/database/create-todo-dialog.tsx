/**
 * Modal to create a collection todo (title + optional markdown body + DDL + chain).
 * Description uses Premium MarkdownEditor without slash commands.
 */
import { useEffect, useMemo, useRef, useState } from "react"
import { Loader2, X } from "lucide-react"
import { toast } from "sonner"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { cn } from "@/lib/utils"
import { createTodo, listChains } from "@/api/file-mgmt"
import type { Chain, TodoItem } from "@/types/file-mgmt"

interface CreateTodoDialogProps {
  collectionId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Initial chain selection (null = main). User can change in the dialog. */
  defaultChainId?: string | null
  onCreated?: (todo: TodoItem) => void
}

function chainOptionLabel(c: Chain): string {
  if (c.is_main) return c.title?.trim() ? `Main · ${c.title}` : "Main"
  return c.title?.trim() || "Branch"
}

export function CreateTodoDialog({
  collectionId,
  open,
  onOpenChange,
  defaultChainId = null,
  onCreated,
}: CreateTodoDialogProps) {
  const [title, setTitle] = useState("")
  const [body, setBody] = useState("")
  const [ddl, setDdl] = useState("")
  const [chains, setChains] = useState<Chain[]>([])
  const [selectedChainId, setSelectedChainId] = useState<string>("")
  const [loadingChains, setLoadingChains] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const ddlInputRef = useRef<HTMLInputElement>(null)
  /** Remount editor when dialog opens so TipTap starts clean */
  const [editorKey, setEditorKey] = useState(0)

  const openDdlPicker = () => {
    const el = ddlInputRef.current
    if (!el) return
    try {
      el.showPicker?.()
    } catch {
      el.focus()
      el.click()
    }
  }

  const mainChain = useMemo(
    () => chains.find((c) => c.is_main) ?? null,
    [chains]
  )

  useEffect(() => {
    if (!open || !collectionId) return
    setTitle("")
    setBody("")
    setDdl("")
    setEditorKey((k) => k + 1)
    setLoadingChains(true)
    listChains(collectionId)
      .then((list) => {
        setChains(list)
        const main = list.find((c) => c.is_main)
        const preferred =
          (defaultChainId && list.some((c) => c.chain_id === defaultChainId)
            ? defaultChainId
            : null) ||
          main?.chain_id ||
          list[0]?.chain_id ||
          ""
        setSelectedChainId(preferred)
      })
      .catch((err) => {
        toast.error(
          err instanceof Error ? err.message : "Failed to load chains"
        )
        setChains([])
        setSelectedChainId("")
      })
      .finally(() => setLoadingChains(false))
  }, [open, collectionId, defaultChainId])

  const handleSubmit = async () => {
    const t = title.trim()
    if (!t) {
      toast.error("Title is required")
      return
    }
    if (!selectedChainId) {
      toast.error("Please select a chain")
      return
    }
    setSubmitting(true)
    try {
      // Store null for main chain (API convention)
      const isMain = mainChain?.chain_id === selectedChainId
      const todo = await createTodo(collectionId, {
        title: t,
        body: body.trim() || null,
        ddl: ddl.trim() || null,
        target_chain_id: isMain ? null : selectedChainId,
      })
      toast.success("Todo added")
      onCreated?.(todo)
      onOpenChange(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  // Main first, then branches by title
  const sortedChains = useMemo(() => {
    return [...chains].sort((a, b) => {
      if (a.is_main !== b.is_main) return a.is_main ? -1 : 1
      return (a.title || "").localeCompare(b.title || "")
    })
  }, [chains])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="pm-dialog pm-dialog--silk sm:max-w-lg"
        overlayClassName="pm-dialog-overlay--silk"
      >
        <DialogHeader>
          <DialogTitle>New to-do</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 py-1">
          <div>
            <label className="pm-field-label">Todo</label>
            <input
              autoFocus
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => {
                // Enter must not submit — only the Create button does
                if (e.key === "Enter") e.preventDefault()
              }}
              placeholder="What needs to be done?"
              className="pm-field"
            />
          </div>
          <div>
            <label className="pm-field-label">Description (optional)</label>
            <div className="pm-todo-md-host">
              {open && (
                <MarkdownEditor
                  key={editorKey}
                  value={body}
                  onChange={setBody}
                  enableSlash={false}
                  showToolbar
                  flush
                  placeholder="Details, context, acceptance criteria…"
                  className="pm-todo-md-editor"
                />
              )}
            </div>
          </div>
          <div>
            <label className="pm-field-label">Chain</label>
            <div className="relative">
              <DropdownSelect
                size="sm"
                value={selectedChainId}
                onChange={setSelectedChainId}
                disabled={loadingChains || sortedChains.length === 0}
                placeholder={
                  loadingChains
                    ? "Loading chains…"
                    : sortedChains.length === 0
                      ? "No chains"
                      : "Select chain"
                }
                options={sortedChains.map((c) => ({
                  value: c.chain_id,
                  label: chainOptionLabel(c),
                }))}
              />
              {loadingChains && (
                <Loader2 className="absolute right-8 top-1/2 -translate-y-1/2 h-3.5 w-3.5 animate-spin text-[var(--pm-faint)] pointer-events-none" />
              )}
            </div>
          </div>
          <div>
            <label className="pm-field-label">Deadline (optional)</label>
            <div className="relative flex items-center gap-1">
              {/* Empty: hide browser yyyy/mm/dd ghost text; click opens calendar */}
              {!ddl && (
                <span
                  className="pointer-events-none absolute left-3 pm-meta"
                  aria-hidden
                >
                  No deadline
                </span>
              )}
              <input
                ref={ddlInputRef}
                type="date"
                value={ddl}
                onChange={(e) => setDdl(e.target.value)}
                onClick={openDdlPicker}
                onFocus={openDdlPicker}
                className={cn(
                  "pm-field cursor-pointer",
                  ddl ? "text-[var(--pm-text)]" : "text-transparent",
                  !ddl &&
                    "[&::-webkit-datetime-edit]:text-transparent [&::-webkit-datetime-edit-fields-wrapper]:opacity-0 [&::-webkit-datetime-edit-text]:opacity-0"
                )}
              />
              {ddl && (
                <button
                  type="button"
                  className="shrink-0 p-1 text-[var(--pm-faint)] hover:text-[var(--pm-ink)] transition-colors"
                  title="Clear deadline"
                  onClick={(e) => {
                    e.preventDefault()
                    e.stopPropagation()
                    setDdl("")
                  }}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>
        </div>
        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            type="button"
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            Cancel
          </Button>
          <Button
            type="button"
            onClick={() => void handleSubmit()}
            disabled={
              submitting || !title.trim() || !selectedChainId || loadingChains
            }
          >
            {submitting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              "Create"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
