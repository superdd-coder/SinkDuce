/**
 * Todo detail preview / edit — same Premium silk shell as CreateTodoDialog.
 * Open todos: title, markdown body, ddl, chain editable.
 * Completed todos: read-only (can still open Add node from list).
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
import { listChains, updateTodo } from "@/api/file-mgmt"
import type { Chain, TodoItem } from "@/types/file-mgmt"
import { triggerTodoRefresh } from "@/lib/todo-refresh"

interface TodoDetailDialogProps {
  collectionId: string
  todo: TodoItem | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onUpdated?: (todo: TodoItem) => void
}

function chainOptionLabel(c: Chain): string {
  if (c.is_main) return c.title?.trim() ? `Main · ${c.title}` : "Main"
  return c.title?.trim() || "Branch"
}

export function TodoDetailDialog({
  collectionId,
  todo,
  open,
  onOpenChange,
  onUpdated,
}: TodoDetailDialogProps) {
  /** Hold last todo through exit animation (parent may clear after delay). */
  const [displayTodo, setDisplayTodo] = useState<TodoItem | null>(todo)
  const readonly = !!displayTodo?.done
  const [title, setTitle] = useState("")
  const [body, setBody] = useState("")
  const [ddl, setDdl] = useState("")
  const [chains, setChains] = useState<Chain[]>([])
  const [selectedChainId, setSelectedChainId] = useState("")
  const [loadingChains, setLoadingChains] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editorKey, setEditorKey] = useState(0)
  const ddlInputRef = useRef<HTMLInputElement>(null)

  const mainChain = useMemo(
    () => chains.find((c) => c.is_main) ?? null,
    [chains]
  )

  useEffect(() => {
    if (todo) setDisplayTodo(todo)
  }, [todo])

  useEffect(() => {
    if (!open || !todo) return
    setTitle(todo.title)
    setBody(todo.body || "")
    setDdl(todo.ddl?.slice(0, 10) || "")
    setSelectedChainId(todo.chain_id)
    setEditorKey((k) => k + 1)
    setLoadingChains(true)
    listChains(collectionId)
      .then((list) => {
        setChains(list)
        if (!list.some((c) => c.chain_id === todo.chain_id)) {
          const main = list.find((c) => c.is_main)
          setSelectedChainId(main?.chain_id || list[0]?.chain_id || "")
        }
      })
      .catch(() => setChains([]))
      .finally(() => setLoadingChains(false))
  }, [open, todo, collectionId])

  const openDdlPicker = () => {
    if (readonly) return
    const el = ddlInputRef.current
    if (!el) return
    try {
      el.showPicker?.()
    } catch {
      el.focus()
      el.click()
    }
  }

  const dirty = useMemo(() => {
    if (!displayTodo || readonly) return false
    const origDdl = displayTodo.ddl?.slice(0, 10) || ""
    const origBody = displayTodo.body || ""
    return (
      title.trim() !== displayTodo.title ||
      body !== origBody ||
      ddl !== origDdl ||
      selectedChainId !== displayTodo.chain_id
    )
  }, [displayTodo, readonly, title, body, ddl, selectedChainId])

  const handleSave = async () => {
    if (!displayTodo || readonly) return
    const t = title.trim()
    if (!t) {
      toast.error("Title is required")
      return
    }
    if (!selectedChainId) {
      toast.error("Please select a chain")
      return
    }
    setSaving(true)
    try {
      const isMain = mainChain?.chain_id === selectedChainId
      const nextBody = body.trim()
      const origBody = (displayTodo.body || "").trim()
      const payload: Parameters<typeof updateTodo>[2] = {
        title: t,
        target_chain_id: isMain ? null : selectedChainId,
      }
      if (ddl) {
        payload.ddl = ddl
      } else if (displayTodo.ddl) {
        payload.clear_ddl = true
      }
      if (nextBody !== origBody) {
        if (!nextBody) payload.clear_body = true
        else payload.body = nextBody
      }
      const updated = await updateTodo(
        collectionId,
        displayTodo.todo_id,
        payload
      )
      toast.success("Todo updated")
      onUpdated?.(updated)
      setDisplayTodo(updated)
      triggerTodoRefresh({ collectionId, reason: "update" })
      onOpenChange(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const sortedChains = useMemo(() => {
    return [...chains].sort((a, b) => {
      if (a.is_main !== b.is_main) return a.is_main ? -1 : 1
      return (a.title || "").localeCompare(b.title || "")
    })
  }, [chains])

  // Keep shell mounted for exit when displayTodo still held
  if (!displayTodo) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="pm-dialog pm-dialog--silk sm:max-w-lg"
        overlayClassName="pm-dialog-overlay--silk"
      >
        <DialogHeader>
          <DialogTitle>
            {readonly ? "Todo detail" : "Edit todo"}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3 py-1">
          {readonly && (
            <p className="pm-meta rounded-[var(--pm-r-sm)] bg-[var(--pm-green-wash)] px-2.5 py-1.5 text-[var(--pm-muted)]">
              Completed todos are read-only.
            </p>
          )}
          <div>
            <label className="pm-field-label">Todo</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              disabled={readonly}
              className="pm-field"
            />
          </div>
          <div>
            <label className="pm-field-label">Description</label>
            <div
              className={cn(
                "pm-todo-md-host",
                readonly && "pm-todo-md-host--readonly"
              )}
            >
              {open && (
                <MarkdownEditor
                  key={editorKey}
                  value={body}
                  onChange={setBody}
                  enableSlash={false}
                  showToolbar={!readonly}
                  readonly={readonly}
                  flush
                  placeholder={
                    readonly
                      ? "No description"
                      : "Details, context, acceptance criteria…"
                  }
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
                disabled={readonly || loadingChains}
                placeholder={
                  loadingChains ? "Loading chains…" : "Select chain"
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
            <label className="pm-field-label">Deadline</label>
            <div className="relative flex items-center gap-1">
              {!ddl && !readonly && (
                <span
                  className="pointer-events-none absolute left-3 pm-meta"
                  aria-hidden
                >
                  No deadline
                </span>
              )}
              {readonly && !ddl && (
                <span className="pm-field text-[var(--pm-faint)]">
                  No deadline
                </span>
              )}
              {!(readonly && !ddl) && (
                <input
                  ref={ddlInputRef}
                  type="date"
                  value={ddl}
                  onChange={(e) => setDdl(e.target.value)}
                  onClick={openDdlPicker}
                  onFocus={openDdlPicker}
                  disabled={readonly}
                  className={cn(
                    "pm-field cursor-pointer disabled:cursor-default",
                    ddl ? "text-[var(--pm-text)]" : "text-transparent",
                    !ddl &&
                      "[&::-webkit-datetime-edit]:text-transparent [&::-webkit-datetime-edit-fields-wrapper]:opacity-0"
                  )}
                />
              )}
              {ddl && !readonly && (
                <button
                  type="button"
                  className="shrink-0 p-1 text-[var(--pm-faint)] hover:text-[var(--pm-ink)] transition-colors"
                  title="Clear deadline"
                  onClick={() => setDdl("")}
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
          >
            {readonly ? "Close" : "Cancel"}
          </Button>
          {!readonly && (
            <Button
              type="button"
              onClick={() => void handleSave()}
              disabled={saving || !dirty || !title.trim()}
            >
              {saving ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                "Save"
              )}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
