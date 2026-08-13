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
