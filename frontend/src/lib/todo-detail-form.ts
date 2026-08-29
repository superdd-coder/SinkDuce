/**
 * Todo detail dialog form seed.
 * TipTap only applies `value` on mount. In a keepMounted dialog, mounting the
 * editor on the previous todo's body (first paint before seed sync) freezes
 * that content. Mount only after the seed matches the open todo.
 */

export type TodoDetailSeedInput = {
  todo_id: string
  title: string
  body?: string | null
  ddl?: string | null
  chain_id: string
}

export type TodoDetailSeed = {
  todoId: string
  title: string
  body: string
  ddl: string
  chainId: string
}

export function seedFromTodo(todo: TodoDetailSeedInput): TodoDetailSeed {
  return {
    todoId: todo.todo_id,
    title: todo.title,
    body: todo.body || "",
    ddl: todo.ddl?.slice(0, 10) || "",
    chainId: todo.chain_id,
  }
}

export function shouldMountTodoDetailEditor(
  open: boolean,
  openTodoId: string | null | undefined,
  seededTodoId: string | null | undefined,
): boolean {
  return Boolean(open && openTodoId && seededTodoId && openTodoId === seededTodoId)
}
