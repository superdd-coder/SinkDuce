/**
 * Collection To-do card — Overview right rail + reusable list.
 * Spec: docs/superpowers/specs/2026-08-05-collection-todo-design.md
 */
import { useCallback, useEffect, useMemo, useState } from "react"
import {
  ChevronDown,
  ChevronRight,
  GitBranch,
  Layers,
  Link2,
  Loader2,
  Plus,
} from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { onTodoRefresh, triggerTodoRefresh } from "@/lib/todo-refresh"
import {
  deleteTodo,
  FileMgmtApiError,
  linkTodoNode,
  listGroups,
  listTodos,
  updateTodo,
} from "@/api/file-mgmt"
import type { Node, NodeGroup, TodoItem } from "@/types/file-mgmt"
import { AddNodeDialog } from "@/components/file-mgmt/timeline-view/add-node-dialog"
import { CreateTodoDialog } from "./create-todo-dialog"
import { TodoDetailDialog } from "./todo-detail-dialog"

interface TodoCardProps {
  collection: string
  /** Default chain for new todos (null/omit = main). Timeline passes current chain. */
  defaultChainId?: string | null
  /** Compact = sidebar; default = Overview card chrome */
  variant?: "card" | "sidebar"
  className?: string
  /** Controlled create dialog (timeline can open it). */
  createOpen?: boolean
  onCreateOpenChange?: (open: boolean) => void
}

export function TodoCard({
  collection,
  defaultChainId = null,
  variant = "card",
  className,
  createOpen: createOpenProp,
  onCreateOpenChange,
}: TodoCardProps) {
  const [todos, setTodos] = useState<TodoItem[]>([])
  const [loading, setLoading] = useState(true)
  const [groupByChain, setGroupByChain] = useState(false)
  const [completedOpen, setCompletedOpen] = useState(false)
  const [justCompletedIds, setJustCompletedIds] = useState<Set<string>>(
    () => new Set()
  )
  const [busyId, setBusyId] = useState<string | null>(null)

  const [groups, setGroups] = useState<NodeGroup[]>([])
  const [addNodeOpen, setAddNodeOpen] = useState(false)
  const [linkTodo, setLinkTodo] = useState<TodoItem | null>(null)
  const [detailTodo, setDetailTodo] = useState<TodoItem | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)

  const [internalCreateOpen, setInternalCreateOpen] = useState(false)
  const createOpen = createOpenProp ?? internalCreateOpen
  const setCreateOpen = onCreateOpenChange ?? setInternalCreateOpen

  const refresh = useCallback(
    async (opts?: { silent?: boolean }) => {
      if (!collection) return
      if (!opts?.silent) setLoading(true)
      try {
        const list = await listTodos(collection)
        setTodos(list)
        if (!opts?.silent) {
          setJustCompletedIds(new Set())
        }
      } catch (err) {
        const msg =
          err instanceof FileMgmtApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : String(err)
        // HTML response usually means API process is old / not restarted
        if (msg.includes("<!doctype") || msg.includes("Unexpected token")) {
          toast.error(
            "Todos API unavailable — restart the backend (uvicorn) so /api/file-mgmt/.../todos is registered."
          )
        } else {
          toast.error(`Todos: ${msg}`)
        }
      } finally {
        if (!opts?.silent) setLoading(false)
      }
    },
    [collection]
  )

  useEffect(() => {
    setJustCompletedIds(new Set())
    void refresh()
  }, [collection, refresh])

  // INFO card ↔ Timeline sidebar: keep lists in sync without remount
  useEffect(() => {
    return onTodoRefresh((detail) => {
      if (detail.collectionId !== collection) return
      void refresh({ silent: true })
    })
  }, [collection, refresh])

  useEffect(() => {
    if (!collection) return
    listGroups(collection)
      .then(setGroups)
      .catch(() => setGroups([]))
  }, [collection])

  const { openRows, completedRows } = useMemo(() => {
    const open: TodoItem[] = []
    const completed: TodoItem[] = []
    for (const t of todos) {
      if (t.done && !justCompletedIds.has(t.todo_id)) {
        completed.push(t)
      } else {
        open.push(t)
      }
    }
    return { openRows: open, completedRows: completed }
  }, [todos, justCompletedIds])

  const toggleDone = async (t: TodoItem) => {
    setBusyId(t.todo_id)
    try {
      const next = !t.done
      const updated = await updateTodo(collection, t.todo_id, { done: next })
      setTodos((prev) =>
        prev.map((x) => (x.todo_id === t.todo_id ? updated : x))
      )
      if (next) {
        setJustCompletedIds((prev) => new Set(prev).add(t.todo_id))
      } else {
        setJustCompletedIds((prev) => {
          const n = new Set(prev)
          n.delete(t.todo_id)
          return n
        })
      }
      triggerTodoRefresh({
        collectionId: collection,
        reason: "complete",
      })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyId(null)
    }
  }

  const handleDelete = async (t: TodoItem) => {
    setBusyId(t.todo_id)
    try {
      await deleteTodo(collection, t.todo_id)
      setTodos((prev) => prev.filter((x) => x.todo_id !== t.todo_id))
      triggerTodoRefresh({
        collectionId: collection,
        reason: "delete",
      })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyId(null)
    }
  }

  const openAddNode = (t: TodoItem) => {
    setLinkTodo(t)
    setAddNodeOpen(true)
  }

  const openDetail = (t: TodoItem) => {
    setDetailTodo(t)
    setDetailOpen(true)
  }

  const onNodeCreated = async (node?: Node) => {
    if (node && linkTodo) {
      try {
        const updated = await linkTodoNode(collection, linkTodo.todo_id, {
          node_id: node.node_id,
        })
        setTodos((prev) =>
          prev.map((x) => (x.todo_id === linkTodo.todo_id ? updated : x))
        )
        toast.success("Linked node to todo")
        triggerTodoRefresh({
          collectionId: collection,
          reason: "link-node",
        })
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err))
      }
    }
    setLinkTodo(null)
    void refresh({ silent: true })
  }

  const renderRow = (t: TodoItem) => {
    const chainLabel = t.is_main_chain
      ? t.chain_title || "Main"
      : t.chain_title || "Branch"
    const busy = busyId === t.todo_id
    return (
      <li
        key={t.todo_id}
        role="button"
        tabIndex={0}
        aria-label={`Open todo: ${t.title}`}
        className={cn(
          "group/todo flex items-start gap-2 py-1.5 border-b border-dashed border-border last:border-0",
          "cursor-pointer rounded-sm transition-colors hover:bg-muted/40",
          busy && "opacity-70"
        )}
        onClick={() => openDetail(t)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault()
            openDetail(t)
          }
        }}
      >
        <button
          type="button"
          disabled={busy}
          title={t.done ? "Mark incomplete" : "Mark complete"}
          aria-label={t.done ? "Mark incomplete" : "Mark complete"}
          className={cn(
            "mt-0.5 h-4 w-4 shrink-0 rounded border flex items-center justify-center transition-colors",
            t.done
              ? "bg-[var(--ze-green,#1A5E3D)] border-[var(--ze-green,#1A5E3D)] text-white"
              : "border-muted-foreground/40 hover:border-[var(--ze-green,#1A5E3D)]"
          )}
          style={{ background: t.done ? undefined : "none" }}
          onClick={(e) => {
            e.stopPropagation()
            if (!busy) void toggleDone(t)
          }}
        >
          {busy ? (
            <Loader2 className="h-2.5 w-2.5 animate-spin" />
          ) : t.done ? (
            <span className="text-[10px] leading-none">✓</span>
          ) : null}
        </button>
        <div className="flex-1 min-w-0">
          <div
            className={cn(
              "text-xs leading-snug",
              t.done && "line-through text-muted-foreground"
            )}
          >
            {t.title}
          </div>
          {t.body?.trim() && (
            <div className="text-[10px] text-muted-foreground/80 mt-0.5 line-clamp-1">
              {t.body.trim()}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-1.5 mt-0.5">
            <span
              className={cn(
                "inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0 rounded-sm",
                t.is_main_chain
                  ? "bg-muted text-muted-foreground"
                  : "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
              )}
            >
              <GitBranch className="h-2.5 w-2.5" />
              {chainLabel}
            </span>
            {t.ddl && (
              <span className="text-[10px] text-muted-foreground">
                DDL {t.ddl.slice(0, 10)}
              </span>
            )}
          </div>
        </div>
        <div
          className="flex items-center gap-0.5 shrink-0 opacity-0 group-hover/todo:opacity-100 focus-within:opacity-100 transition-opacity"
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
        >
          {t.done && !t.completed_node_id && (
            <button
              type="button"
              title="Add node to timeline"
              className="p-1 text-muted-foreground hover:text-[var(--ze-green,#1A5E3D)]"
              style={{ background: "none", border: "none" }}
              onClick={(e) => {
                e.stopPropagation()
                openAddNode(t)
              }}
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
          )}
          {t.done && t.completed_node_id && (
            <span
              title="Linked to timeline node"
              className="p-1 text-[var(--ze-green,#1A5E3D)]"
            >
              <Link2 className="h-3.5 w-3.5" />
            </span>
          )}
          <button
            type="button"
            title="Delete"
            className="p-1 text-muted-foreground hover:text-red-500 text-[10px]"
            style={{ background: "none", border: "none" }}
            onClick={(e) => {
              e.stopPropagation()
              void handleDelete(t)
            }}
          >
            ×
          </button>
        </div>
      </li>
    )
  }

  const renderList = (items: TodoItem[]) => {
    if (!groupByChain) {
      return <ul className="space-y-0">{items.map(renderRow)}</ul>
    }
    const map = new Map<string, { title: string; items: TodoItem[] }>()
    for (const t of items) {
      const key = t.chain_id
      if (!map.has(key)) {
        map.set(key, {
          title: t.is_main_chain
            ? t.chain_title || "Main"
            : t.chain_title || "Branch",
          items: [],
        })
      }
      map.get(key)!.items.push(t)
    }
    const entries = [...map.entries()].sort((a, b) => {
      const aMain = a[1].items[0]?.is_main_chain ? 0 : 1
      const bMain = b[1].items[0]?.is_main_chain ? 0 : 1
      if (aMain !== bMain) return aMain - bMain
      return a[1].title.localeCompare(b[1].title)
    })
    return (
      <div className="space-y-3">
        {entries.map(([cid, g]) => (
          <div key={cid}>
            <div className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-1">
              {g.title}
            </div>
            <ul>{g.items.map(renderRow)}</ul>
          </div>
        ))}
      </div>
    )
  }

  const shell =
    variant === "card"
      ? "rounded-xl border border-border/60 bg-card/80 p-3 shadow-sm"
      : "h-full flex flex-col min-h-0"

  return (
    <div className={cn(shell, className)}>
      <div className="flex items-center justify-between gap-2 mb-2">
        <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
          To-do
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            title={groupByChain ? "Ungroup" : "Group by chain"}
            onClick={() => setGroupByChain((v) => !v)}
            className={cn(
              "p-1 rounded transition-colors",
              groupByChain
                ? "text-[var(--ze-green,#1A5E3D)] bg-emerald-500/10"
                : "text-muted-foreground hover:text-foreground"
            )}
            style={{
              background: groupByChain ? undefined : "none",
              border: "none",
            }}
          >
            <Layers className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => setCreateOpen(true)}
            className="text-[10px] font-medium uppercase tracking-[0.08em] px-2 py-0.5 rounded-md border border-border hover:bg-muted/40"
            style={{ background: "none" }}
            title="Add todo"
          >
            Add
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 py-4 text-muted-foreground justify-center">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          <span className="text-xs">Loading…</span>
        </div>
      ) : (
        <>
          {openRows.length === 0 && completedRows.length === 0 ? (
            <p className="text-xs text-muted-foreground py-2">No todos yet.</p>
          ) : (
            renderList(openRows)
          )}

          {completedRows.length > 0 && (
            <div className="mt-3 pt-2 border-t border-border/50">
              <button
                type="button"
                className="flex items-center gap-1 text-[10px] uppercase tracking-[0.1em] text-muted-foreground w-full text-left"
                style={{ background: "none", border: "none", padding: 0 }}
                onClick={() => setCompletedOpen((o) => !o)}
              >
                {completedOpen ? (
                  <ChevronDown className="h-3 w-3" />
                ) : (
                  <ChevronRight className="h-3 w-3" />
                )}
                Completed · {completedRows.length}
              </button>
              {completedOpen && (
                <div className="mt-1 opacity-80">
                  {renderList(completedRows)}
                </div>
              )}
            </div>
          )}
        </>
      )}

      <CreateTodoDialog
        collectionId={collection}
        open={createOpen}
        onOpenChange={setCreateOpen}
        defaultChainId={defaultChainId}
        onCreated={() => {
          void refresh({ silent: true })
          triggerTodoRefresh({
            collectionId: collection,
            reason: "create",
          })
        }}
      />

      <TodoDetailDialog
        collectionId={collection}
        todo={detailTodo}
        open={detailOpen}
        onOpenChange={(o) => {
          setDetailOpen(o)
          if (!o) setDetailTodo(null)
        }}
        onUpdated={(updated) => {
          setTodos((prev) =>
            prev.map((x) => (x.todo_id === updated.todo_id ? updated : x))
          )
          setDetailTodo(updated)
        }}
      />

      {linkTodo && (
        <AddNodeDialog
          collectionId={collection}
          chainId={linkTodo.chain_id}
          afterOrder={-1} // append at chain tail (not head)
          open={addNodeOpen}
          onOpenChange={(o) => {
            setAddNodeOpen(o)
            if (!o) setLinkTodo(null)
          }}
          onCreated={(node) => void onNodeCreated(node)}
          groups={groups}
          initialTitle={linkTodo.title}
          initialMessageBody={linkTodo.body || undefined}
        />
      )}
    </div>
  )
}
