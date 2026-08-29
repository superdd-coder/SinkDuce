/**
 * Cross-view hot refresh for collection todos (INFO card ↔ Timeline sidebar).
 */

export const TODO_REFRESH_EVENT = "sinkduce:todo-refresh"

export type TodoRefreshDetail = {
  collectionId: string
  reason?:
    | "create"
    | "update"
    | "delete"
    | "link-node"
    | "complete"
    | "manual"
  /** Patch this row in place (complete) instead of re-fetching the sorted list. */
  todo?: import("@/types/file-mgmt").TodoItem
}

export function triggerTodoRefresh(detail: TodoRefreshDetail) {
  if (typeof window === "undefined") return
  window.dispatchEvent(new CustomEvent(TODO_REFRESH_EVENT, { detail }))
}

export function onTodoRefresh(
  handler: (detail: TodoRefreshDetail) => void
): () => void {
  if (typeof window === "undefined") return () => {}
  const listener = (e: Event) => {
    const ce = e as CustomEvent<TodoRefreshDetail>
    if (ce.detail) handler(ce.detail)
  }
  window.addEventListener(TODO_REFRESH_EVENT, listener)
  return () => window.removeEventListener(TODO_REFRESH_EVENT, listener)
}

const TODO_CHAT_TOOLS: Record<string, TodoRefreshDetail["reason"]> = {
  create_todo: "create",
  update_todo: "update",
  delete_todo: "delete",
}

/** After Chat / Quick Chat structure tools mutate todos, refresh visible lists. */
export function refreshTodosAfterChatTool(
  tool: string,
  opts?: { collectionId?: string | null; status?: string; content?: unknown },
) {
  const reason = TODO_CHAT_TOOLS[tool]
  if (!reason) return
  const status = opts?.status || "done"
  if (status === "declined" || status === "error") return
  let collectionId = (opts?.collectionId || "").trim()
  if (!collectionId && typeof opts?.content === "string") {
    try {
      const parsed = JSON.parse(opts.content) as Record<string, unknown>
      const raw = parsed.collection ?? parsed.collection_id
      if (typeof raw === "string") collectionId = raw.trim()
    } catch {
      /* tool preview may not be JSON */
    }
  }
  if (!collectionId) return
  triggerTodoRefresh({ collectionId, reason })
}
