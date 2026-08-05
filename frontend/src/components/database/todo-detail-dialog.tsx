/**
 * Todo detail preview / edit.
 * Open todos: title, body, ddl, chain editable.
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
  const readonly = !!todo?.done
  const [title, setTitle] = useState("")
  const [body, setBody] = useState("")
  const [ddl, setDdl] = useState("")
  const [chains, setChains] = useState<Chain[]>([])
  const [selectedChainId, setSelectedChainId] = useState("")
  const [loadingChains, setLoadingChains] = useState(false)
  const [saving, setSaving] = useState(false)
  const ddlInputRef = useRef<HTMLInputElement>(null)

  const mainChain = useMemo(
    () => chains.find((c) => c.is_main) ?? null,
    [chains]
  )

  useEffect(() => {
    if (!open || !todo) return
    setTitle(todo.title)
    setBody(todo.body || "")
    setDdl(todo.ddl?.slice(0, 10) || "")
    setSelectedChainId(todo.chain_id)
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
    if (!todo || readonly) return false
    const origDdl = todo.ddl?.slice(0, 10) || ""
    const origBody = todo.body || ""
    return (
      title.trim() !== todo.title ||
      body !== origBody ||
      ddl !== origDdl ||
      selectedChainId !== todo.chain_id
    )
  }, [todo, readonly, title, body, ddl, selectedChainId])

  const handleSave = async () => {
    if (!todo || readonly) return
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
      const origBody = (todo.body || "").trim()
      const payload: Parameters<typeof updateTodo>[2] = {
        title: t,
        target_chain_id: isMain ? null : selectedChainId,
      }
      if (ddl) {
        payload.ddl = ddl
      } else if (todo.ddl) {
        payload.clear_ddl = true
      }
      if (nextBody !== origBody) {
        if (!nextBody) payload.clear_body = true
        else payload.body = nextBody
      }
      const updated = await updateTodo(collectionId, todo.todo_id, payload)
      toast.success("Todo updated")
      onUpdated?.(updated)
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

  if (!todo) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {readonly ? "Todo detail" : "Edit todo"}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3 py-1">
          {readonly && (
            <p className="text-[11px] text-muted-foreground rounded-md bg-muted/50 px-2 py-1.5">
              Completed todos are read-only.
            </p>
          )}
          <div>
            <label className="text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
              TODO
            </label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              disabled={readonly}
              className="mt-1 w-full text-sm px-3 py-2 rounded-md border border-border bg-background disabled:opacity-70"
            />
          </div>
          <div>
            <label className="text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
              Description
            </label>
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              disabled={readonly}
              rows={5}
              placeholder={readonly ? "No description" : "Details…"}
              className="mt-1 w-full text-sm px-3 py-2 rounded-md border border-border bg-background resize-y min-h-[100px] disabled:opacity-70"
            />
          </div>
          <div>
            <label className="text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
              Chain
            </label>
            <select
              value={selectedChainId}
              onChange={(e) => setSelectedChainId(e.target.value)}
              disabled={readonly || loadingChains}
              className="mt-1 w-full text-sm px-3 py-2 rounded-md border border-border bg-background disabled:opacity-70"
            >
              {sortedChains.map((c) => (
                <option key={c.chain_id} value={c.chain_id}>
                  {chainOptionLabel(c)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
              Deadline
            </label>
            <div className="mt-1 relative flex items-center gap-1">
              {!ddl && !readonly && (
                <span
                  className="pointer-events-none absolute left-3 text-sm text-muted-foreground/50"
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
                disabled={readonly}
                className={cn(
                  "w-full text-sm px-3 py-2 rounded-md border border-border bg-background cursor-pointer disabled:cursor-default disabled:opacity-70",
                  ddl ? "text-foreground" : "text-transparent",
                  !ddl &&
                    "[&::-webkit-datetime-edit]:text-transparent [&::-webkit-datetime-edit-fields-wrapper]:opacity-0"
                )}
              />
              {ddl && !readonly && (
                <button
                  type="button"
                  className="shrink-0 p-1 text-muted-foreground hover:text-foreground"
                  title="Clear deadline"
                  onClick={() => setDdl("")}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
          >
            {readonly ? "Close" : "Cancel"}
          </Button>
          {!readonly && (
            <Button
              type="button"
              size="sm"
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
