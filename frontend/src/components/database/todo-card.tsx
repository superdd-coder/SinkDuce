/**
 * Collection To-do card — Overview right rail + reusable list.
 * Spec: docs/superpowers/specs/2026-08-05-collection-todo-design.md
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  ChevronRight,
  GitBranch,
  Layers,
  Link2,
  Loader2,
  Plus,
} from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { onTodoRefresh, triggerTodoRefresh } from "@/lib/todo-refresh"
import {
  mergeTodoUpdateInPlace,
  splitTodoSections,
} from "@/lib/todo-list-state"
import {
  deleteTodo,
  FileMgmtApiError,
  linkTodoNode,
  listGroups,
  listTodos,
  updateTodo,
} from "@/api/file-mgmt"
import { getMePerson } from "@/api/client"
import { onMePersonRefresh } from "@/lib/me-person-refresh"
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

/**
 * Two-step delete (× → DELETE) — same anti-mis-tap pattern as message-card
 * and LogMsgDeleteButton (.pm-msg-delete).
 */
function TodoDeleteButton({
  disabled,
  onConfirm,
}: {
  disabled?: boolean
  onConfirm: () => void
}) {
  const [deleteArmed, setDeleteArmed] = useState(false)
  const deleteArmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const deleteBtnRef = useRef<HTMLButtonElement>(null)

  const disarmDelete = useCallback(() => {
    setDeleteArmed(false)
    if (deleteArmTimerRef.current) {
      clearTimeout(deleteArmTimerRef.current)
      deleteArmTimerRef.current = null
    }
  }, [])

  const armDelete = useCallback(() => {
    setDeleteArmed(true)
    if (deleteArmTimerRef.current) clearTimeout(deleteArmTimerRef.current)
    deleteArmTimerRef.current = setTimeout(() => disarmDelete(), 4000)
  }, [disarmDelete])

  useEffect(() => {
    if (!deleteArmed) return
    const onPointerDown = (ev: Event) => {
      const t = ev.target as globalThis.Node | null
      if (t && deleteBtnRef.current?.contains(t)) return
      disarmDelete()
    }
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") disarmDelete()
    }
    const t = window.setTimeout(() => {
      document.addEventListener("pointerdown", onPointerDown, true)
      document.addEventListener("keydown", onKey, true)
    }, 0)
    return () => {
      window.clearTimeout(t)
      document.removeEventListener("pointerdown", onPointerDown, true)
      document.removeEventListener("keydown", onKey, true)
    }
  }, [deleteArmed, disarmDelete])

  useEffect(() => {
    return () => {
      if (deleteArmTimerRef.current) clearTimeout(deleteArmTimerRef.current)
    }
  }, [])

  return (
    <button
      ref={deleteBtnRef}
      type="button"
      disabled={disabled}
      className={cn(
        "pm-msg-delete",
        deleteArmed ? "is-confirm opacity-100" : "opacity-100",
      )}
      title={deleteArmed ? "Click again to delete" : "Delete todo"}
      aria-label={deleteArmed ? "Confirm delete todo" : "Delete todo"}
      aria-expanded={deleteArmed}
      onClick={(e) => {
        e.preventDefault()
        e.stopPropagation()
        if (disabled) return
        if (!deleteArmed) {
          armDelete()
          return
        }
        disarmDelete()
        onConfirm()
      }}
    >
      {/* First click: × only. Confirm: text only (no icon). */}
      {!deleteArmed ? (
        <span className="pm-msg-delete-x" aria-hidden>
          ×
        </span>
      ) : (
        <span className="pm-msg-delete-label is-solo">Delete</span>
      )}
    </button>
  )
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
  const [mineOnly, setMineOnly] = useState(false)
  const [meId, setMeId] = useState<string | null>(null)
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
  /** Match pm-dialog--silk exit (~280ms + buffer) before clearing todo */
  const detailCloseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const DETAIL_CLOSE_MS = 320

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
      // Re-fetch would apply server sort and send a just-checked row to the bottom.
      if (detail.reason === "complete" && detail.todo) {
        const updated = detail.todo
        setTodos((prev) => mergeTodoUpdateInPlace(prev, updated))
        setJustCompletedIds((prev) => {
          const n = new Set(prev)
          if (updated.done) n.add(updated.todo_id)
          else n.delete(updated.todo_id)
          return n
        })
        return
      }
      void refresh({ silent: true })
    })
  }, [collection, refresh])

  useEffect(() => {
    if (!collection) return
    listGroups(collection)
      .then(setGroups)
      .catch(() => setGroups([]))
  }, [collection])

  useEffect(() => {
    const apply = (personId: string | null) => setMeId(personId)
    void getMePerson()
      .then((res) => apply(res.person_id))
      .catch(() => apply(null))
    return onMePersonRefresh((detail) => apply(detail.personId))
  }, [])

  const { openRows, completedRows } = useMemo(() => {
    const scoped = mineOnly && meId
      ? todos.filter((t) => t.assignee_person_id === meId)
      : todos
    const { open, completed } = splitTodoSections(scoped, justCompletedIds)
    return { openRows: open, completedRows: completed }
  }, [todos, justCompletedIds, mineOnly, meId])

  const toggleDone = async (t: TodoItem) => {
    setBusyId(t.todo_id)
    try {
      const next = !t.done
      const updated = await updateTodo(collection, t.todo_id, { done: next })
      setTodos((prev) => mergeTodoUpdateInPlace(prev, updated))
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
        todo: updated,
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
    if (detailCloseTimerRef.current) {
      clearTimeout(detailCloseTimerRef.current)
      detailCloseTimerRef.current = null
    }
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
    const mine = !!(meId && t.assignee_person_id === meId)
    return (
      <li
        key={t.todo_id}
        role="button"
        tabIndex={0}
        aria-label={`Open todo: ${t.title}`}
        className={cn(
          /* Horizontal inset: checkbox + hover wash leave a margin from card edge */
          "group/todo flex items-start gap-2 py-1.5 px-2 mx-0.5",
          "border-b border-dashed border-border/40 last:border-0",
          "cursor-pointer rounded-[var(--pm-r-sm,8px)] transition-colors hover:bg-black/[0.03]",
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
            "mt-0.5 size-3.5 shrink-0 rounded-[4px] flex items-center justify-center transition-colors",
            t.done
              ? "bg-[var(--pm-green)] border-none text-[var(--pm-on)]"
              : "border border-[rgba(26,94,61,0.35)] bg-transparent hover:border-[var(--pm-green)]"
          )}
          onClick={(e) => {
            e.stopPropagation()
            if (!busy) void toggleDone(t)
          }}
        >
          {busy ? (
            <Loader2 className="size-2.5 animate-spin" />
          ) : t.done ? (
            <span className="text-[10px] leading-none">✓</span>
          ) : null}
        </button>
        <div className="flex-1 min-w-0">
          <div
            className={cn(
              "text-xs leading-snug",
              t.done && "line-through text-muted-foreground",
              mine && !t.done && "text-[var(--pm-green,#1a5e3d)]",
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
              <GitBranch className="size-2.5" />
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
          className={cn(
            "flex items-center gap-0.5 shrink-0 self-center transition-opacity",
            // Stay visible while two-step delete is armed (same as message row)
            "opacity-0 group-hover/todo:opacity-100 focus-within:opacity-100",
            "has-[.pm-msg-delete.is-confirm]:opacity-100",
          )}
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
        >
          {t.done && !t.completed_node_id && (
            <button
              type="button"
              title="Add node to timeline"
              aria-label="Add node to timeline"
              className="inline-flex size-6 items-center justify-center rounded-md text-muted-foreground hover:text-[var(--pm-green)] hover:bg-black/[0.04] border-none bg-transparent p-0 cursor-pointer"
              onClick={(e) => {
                e.stopPropagation()
                openAddNode(t)
              }}
            >
              <Plus className="size-3.5" strokeWidth={2} />
            </button>
          )}
          {t.done && t.completed_node_id && (
            <span
              title="Linked to timeline node"
              className="inline-flex size-6 items-center justify-center text-[var(--pm-green)]"
            >
              <Link2 className="size-3.5" strokeWidth={2} />
            </span>
          )}
          <TodoDeleteButton
            disabled={busy}
            onConfirm={() => {
              void handleDelete(t)
            }}
          />
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
      ? "pm-rail-card todo-card h-full min-h-0 flex flex-col"
      : "todo-card h-full flex flex-col min-h-0"

  return (
    <div className={cn(shell, className)}>
      <div className="flex items-center justify-between gap-2 mb-2 shrink-0">
        <span
          className={
            variant === "sidebar" ? "pm-timeline-panel-title" : "pm-label"
          }
          style={
            variant === "sidebar"
              ? undefined
              : { textTransform: "none", letterSpacing: "0.02em" }
          }
        >
          To-do
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            title={
              meId
                ? mineOnly
                  ? "Show all todos"
                  : "Only todos assigned to you"
                : "Mark yourself in People first"
            }
            disabled={!meId}
            onClick={() => setMineOnly((v) => !v)}
            className={cn(
              "px-1.5 h-6 rounded-md text-[10px] tracking-[0.04em] uppercase border-none transition-colors",
              mineOnly
                ? "text-[var(--pm-green)] bg-[var(--pm-green-soft)]"
                : "text-[var(--pm-faint)] hover:text-[var(--pm-text)] bg-transparent",
              !meId && "opacity-40 cursor-default"
            )}
          >
            Mine
          </button>
          <button
            type="button"
            title={groupByChain ? "Ungroup" : "Group by chain"}
            onClick={() => setGroupByChain((v) => !v)}
            className={cn(
              "p-1 rounded-md transition-colors border-none",
              groupByChain
                ? "text-[var(--pm-green)] bg-[var(--pm-green-soft)]"
                : "text-[var(--pm-faint)] hover:text-[var(--pm-text)] bg-transparent"
            )}
          >
            <Layers className="h-3.5 w-3.5" />
          </button>
          <Button
            type="button"
            variant="ghost"
            size="xs"
            onClick={() => setCreateOpen(true)}
            title="Add todo"
          >
            Add
          </Button>
        </div>
      </div>

      {loading ? (
        <div className="todo-card-body flex flex-1 items-center gap-2 py-4 justify-center min-h-0">
          <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--pm-faint)]" />
          <span className="pm-meta">Loading…</span>
        </div>
      ) : (
        <div className="todo-card-body min-h-0 flex flex-col flex-1 overflow-auto">
          {openRows.length === 0 && completedRows.length === 0 ? (
            <p className="pm-meta py-2">
              {mineOnly ? "No todos assigned to you." : "No todos yet."}
            </p>
          ) : (
            renderList(openRows)
          )}

          {completedRows.length > 0 && (
            <div className="mt-3 pt-2 border-t border-border/40 shrink-0">
              <button
                type="button"
                className="pm-subcollapse-trigger"
                aria-expanded={completedOpen}
                onClick={() => setCompletedOpen((o) => !o)}
              >
                <span
                  className={cn(
                    "pm-rail-chev",
                    completedOpen && "is-open"
                  )}
                  aria-hidden
                >
                  <ChevronRight className="size-3.5" strokeWidth={2} />
                </span>
                Completed · {completedRows.length}
              </button>
              <div
                className={cn("pm-subcollapse", completedOpen && "is-open")}
              >
                <div className="pm-subcollapse-panel">
                  <div className="pm-subcollapse-inner mt-1 opacity-80">
                    {renderList(completedRows)}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
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
          if (detailCloseTimerRef.current) {
            clearTimeout(detailCloseTimerRef.current)
            detailCloseTimerRef.current = null
          }
          setDetailOpen(o)
          if (!o) {
            // Keep todo until silk exit finishes (same as Note / Create todo)
            detailCloseTimerRef.current = setTimeout(() => {
              setDetailTodo(null)
              detailCloseTimerRef.current = null
            }, DETAIL_CLOSE_MS)
          }
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
