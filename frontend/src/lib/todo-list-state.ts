/** In-place list updates so checking a todo does not jump to Completed. */

export type TodoDoneRow = {
  todo_id: string
  done: boolean
}

export function mergeTodoUpdateInPlace<T extends TodoDoneRow>(
  todos: T[],
  updated: T,
): T[] {
  return todos.map((t) => (t.todo_id === updated.todo_id ? updated : t))
}

export function splitTodoSections<T extends TodoDoneRow>(
  todos: T[],
  justCompletedIds: Iterable<string>,
): { open: T[]; completed: T[] } {
  const hold = justCompletedIds instanceof Set
    ? justCompletedIds
    : new Set(justCompletedIds)
  const open: T[] = []
  const completed: T[] = []
  for (const t of todos) {
    if (t.done && !hold.has(t.todo_id)) completed.push(t)
    else open.push(t)
  }
  return { open, completed }
}
