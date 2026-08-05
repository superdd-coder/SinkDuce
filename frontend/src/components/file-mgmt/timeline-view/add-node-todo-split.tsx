/**
 * Hover split: Node + stays put; Todo expands to the RIGHT.
 *
 * Both buttons live inside the hover hit-box so moving onto Todo does not
 * fire mouseleave (which would collapse the expand).
 * Anchor the wrapper's LEFT edge — growth only goes right.
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

const BTN =
  "w-10 h-10 shrink-0 rounded-md border border-dashed bg-background flex items-center justify-center transition-colors shadow-sm"

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
        "flex flex-row items-center relative z-20 h-10",
        // Keep a continuous hit area between the two buttons
        hover ? "gap-1" : "gap-0",
        className
      )}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      data-branch-add
    >
      <button
        type="button"
        className={cn(
          BTN,
          "border-muted-foreground/30 text-muted-foreground/50",
          "hover:text-muted-foreground/80 hover:border-muted-foreground/50"
        )}
        onClick={(e) => {
          e.stopPropagation()
          onAddNode()
        }}
        title={titleNode}
      >
        <Plus className="h-4 w-4" />
      </button>

      {/*
        Always mounted (opacity/width) so layout doesn't thrash; stays in the
        flex hit-box so pointer can travel + → Todo without mouseleave.
      */}
      <button
        type="button"
        className={cn(
          BTN,
          "border-emerald-500/35 text-emerald-600/80",
          "hover:text-emerald-700 hover:border-emerald-500/55",
          "transition-[opacity,max-width,margin,padding] duration-150 ease-out overflow-hidden",
          hover
            ? "opacity-100 max-w-10 pointer-events-auto"
            : "opacity-0 max-w-0 p-0 border-0 pointer-events-none"
        )}
        onClick={(e) => {
          e.stopPropagation()
          onAddTodo()
        }}
        title={titleTodo}
        tabIndex={hover ? 0 : -1}
        aria-hidden={!hover}
      >
        <ListTodo className="h-4 w-4 shrink-0" />
      </button>
    </div>
  )
}
