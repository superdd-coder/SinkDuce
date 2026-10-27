/**
 * Hover split: Node + stays put; Todo silk-expands to the RIGHT.
 *
 * Both buttons live inside the hover hit-box so moving onto Todo does not
 * fire mouseleave (which would collapse the expand).
 * Anchor the wrapper's LEFT edge — growth only goes right.
 *
 * Expand uses explicit width (CSS), not grid 0fr→1fr — shrink-wrapped flex
 * parents give 1fr zero free space so Todo stayed invisible.
 */
import { useState } from "react"
import { ListTodo, Plus } from "lucide-react"
import { cn } from "@/lib/utils"

interface AddNodeTodoSplitProps {
  onAddNode: () => void
  onAddTodo: () => void
  className?: string
  titleNode?: string
  titleTodo?: string
}

export function AddNodeTodoSplit({
  onAddNode,
  onAddTodo,
  className,
  titleNode = "Add node",
  titleTodo = "Add todo",
}: AddNodeTodoSplitProps) {
  const [hover, setHover] = useState(false)

  return (
    <div
      className={cn(
        "pm-timeline-add-split",
        hover && "is-open",
        className
      )}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      data-branch-add
    >
      <button
        type="button"
        className="pm-timeline-add-slot w-10 h-10 shrink-0"
        onClick={(e) => {
          e.stopPropagation()
          onAddNode()
        }}
        title={titleNode}
      >
        <Plus className="h-4 w-4" strokeWidth={1.75} />
      </button>

      {/* Silk expand: width 0 → 40px (always mounted, stays in hit-box) */}
      <div
        className={cn(
          "pm-timeline-add-todo-expand",
          hover && "is-open"
        )}
        aria-hidden={!hover}
      >
        <div className="pm-timeline-add-todo-expand-inner">
          <button
            type="button"
            className="pm-timeline-add-slot is-todo w-10 h-10 shrink-0"
            onClick={(e) => {
              e.stopPropagation()
              onAddTodo()
            }}
            title={titleTodo}
            tabIndex={hover ? 0 : -1}
            aria-hidden={!hover}
          >
            <ListTodo className="h-4 w-4 shrink-0" strokeWidth={1.75} />
          </button>
        </div>
      </div>
    </div>
  )
}
