/**
 * Create collection to-do — Premium silk dialog (nested white cards).
 * Layout language matches Create/Edit Group: float shell · FieldLabel · ui/* controls.
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react"
import { Loader2 } from "lucide-react"
import { toast } from "sonner"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { DatePicker } from "@/components/ui/date-picker"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { FieldLabel } from "@/components/ui/field-label"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { cn } from "@/lib/utils"
import { createTodo, listChains } from "@/api/file-mgmt"
import type { Chain, TodoItem } from "@/types/file-mgmt"
import type { Editor } from "@tiptap/core"

interface CreateTodoDialogProps {
  collectionId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Initial chain selection (null = main). User can change in the dialog. */
  defaultChainId?: string | null
  /** Prefill from smart suggestion or other callers. */
  initialTitle?: string
  initialBody?: string | null
  /** When set, sent on create so the server can consume that suggestion. */
  suggestionId?: string | null
  onCreated?: (todo: TodoItem) => void
}

function chainOptionLabel(c: Chain): string {
  if (c.is_main) return c.title?.trim() ? `Main · ${c.title}` : "Main"
  return c.title?.trim() || "Branch"
}

export function CreateTodoDialog({
  collectionId,
  open,
  onOpenChange,
  defaultChainId = null,
  initialTitle = "",
  initialBody = null,
  suggestionId = null,
  onCreated,
}: CreateTodoDialogProps) {
  const [title, setTitle] = useState("")
  const [body, setBody] = useState("")
  const [ddl, setDdl] = useState("")
  const [chains, setChains] = useState<Chain[]>([])
  const [selectedChainId, setSelectedChainId] = useState<string>("")
  const [loadingChains, setLoadingChains] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const titleInputRef = useRef<HTMLTextAreaElement>(null)
  const descEditorRef = useRef<Editor | null>(null)
  /** Remount editor when dialog opens so TipTap starts clean */
  const [editorKey, setEditorKey] = useState(0)
  /** After seed effect runs — avoid mounting TipTap with empty body before prefill applies */
  const [descReady, setDescReady] = useState(false)
  const activeSuggestionId = suggestionId?.trim() || null
  const seedTitle = (initialTitle || "").trim()
  const seedBody = (initialBody || "").trim()

  /** Click empty pad under last line → caret at end so any host area is typeable */
  const focusDescEnd = useCallback((e: ReactMouseEvent) => {
    const ed = descEditorRef.current
    if (!ed || ed.isDestroyed) return
    const t = e.target as HTMLElement
    if (t.closest(".pm-fmt-toolbar")) return
    const pm = ed.view.dom as HTMLElement
    const last = pm.lastElementChild as HTMLElement | null
    const below =
      !last || e.clientY > last.getBoundingClientRect().bottom + 2
    const onHost = t === e.currentTarget || t.classList.contains("pm-todo-md-host")
    if (below || onHost || t === pm) {
      e.preventDefault()
      ed.chain().focus("end").run()
    }
  }, [])

  /** Match Node detail title field — grow with content, no form box chrome */
  const syncTitleHeight = useCallback(() => {
    const el = titleInputRef.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = `${el.scrollHeight}px`
  }, [])

  const mainChain = useMemo(
    () => chains.find((c) => c.is_main) ?? null,
    [chains]
  )

  useEffect(() => {
    if (!open) {
      setDescReady(false)
      return
    }
    if (!collectionId) return
    // Seed form from props *before* mounting MarkdownEditor (TipTap only
    // reads initial content on mount; empty first paint would stick).
    setTitle(seedTitle)
    setBody(seedBody)
    setDdl("")
    setEditorKey((k) => k + 1)
    setDescReady(true)
    setLoadingChains(true)
    listChains(collectionId)
      .then((list) => {
        setChains(list)
        const main = list.find((c) => c.is_main)
        const preferred =
          (defaultChainId && list.some((c) => c.chain_id === defaultChainId)
            ? defaultChainId
            : null) ||
          main?.chain_id ||
          list[0]?.chain_id ||
          ""
        setSelectedChainId(preferred)
      })
      .catch((err) => {
        toast.error(
          err instanceof Error ? err.message : "Failed to load chains"
        )
        setChains([])
        setSelectedChainId("")
      })
      .finally(() => setLoadingChains(false))
  }, [open, collectionId, defaultChainId, seedTitle, seedBody])

  /* Focus title after silk enter so open fade isn’t interrupted */
  useEffect(() => {
    if (!open) return
    const t = window.setTimeout(() => {
      const el = titleInputRef.current
      if (!el) return
      el.focus({ preventScroll: true })
      syncTitleHeight()
    }, 300)
    return () => window.clearTimeout(t)
  }, [open, syncTitleHeight])

  useEffect(() => {
    if (!open) return
    requestAnimationFrame(syncTitleHeight)
  }, [open, title, syncTitleHeight])

  const handleSubmit = async () => {
    const t = title.trim()
    if (!t) {
      toast.error("Title is required")
      return
    }
    if (!selectedChainId) {
      toast.error("Please select a chain")
      return
    }
    setSubmitting(true)
    try {
      const isMain = mainChain?.chain_id === selectedChainId
      const todo = await createTodo(collectionId, {
        title: t,
        body: body.trim() || null,
        ddl: ddl.trim() || null,
        target_chain_id: isMain ? null : selectedChainId,
        suggestion_id: activeSuggestionId,
      })
      toast.success("Todo added")
      onCreated?.(todo)
      onOpenChange(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  const sortedChains = useMemo(() => {
    return [...chains].sort((a, b) => {
      if (a.is_main !== b.is_main) return a.is_main ? -1 : 1
      return (a.title || "").localeCompare(b.title || "")
    })
  }, [chains])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "pm-dialog pm-dialog--silk pm-todo-dialog",
          "sm:max-w-lg",
          "!animate-none data-open:!animate-none data-closed:!animate-none"
        )}
        overlayClassName="pm-dialog-overlay--silk"
      >
        <DialogHeader className="pm-todo-dialog-head">
          <DialogTitle className="pm-todo-dialog-title">New to-do</DialogTitle>
        </DialogHeader>

        <div className="pm-dialog-body pm-todo-dialog-body">
          {/* Top row: TODO (left) + Chain/DDL stack card (right) */}
          <div className="pm-todo-top-row">
            <section className="pm-todo-card pm-todo-card--title">
              {/* Label pinned top-left; input centered in remaining height */}
              <FieldLabel htmlFor="pm-todo-create-title">Todo</FieldLabel>
              <div className="pm-todo-title-block">
                <textarea
                  ref={titleInputRef}
                  id="pm-todo-create-title"
                  rows={1}
                  spellCheck={false}
                  className="pm-node-id-title pm-node-id-title-input pm-todo-title-input"
                  value={title}
                  onChange={(e) => {
                    setTitle(e.target.value)
                    requestAnimationFrame(syncTitleHeight)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") e.preventDefault()
                  }}
                  placeholder="What needs to be done?"
                  aria-label="Todo title"
                />
              </div>
            </section>

            <section className="pm-todo-card pm-todo-card--meta">
              <div className="pm-todo-meta-stack">
                <div className="pm-todo-meta-field min-w-0">
                  <FieldLabel htmlFor="pm-todo-create-chain">Chain</FieldLabel>
                  <div className="relative">
                    <DropdownSelect
                      size="sm"
                      value={selectedChainId}
                      onChange={setSelectedChainId}
                      disabled={loadingChains || sortedChains.length === 0}
                      placeholder={
                        loadingChains
                          ? "Loading chains…"
                          : sortedChains.length === 0
                            ? "No chains"
                            : "Select chain"
                      }
                      options={sortedChains.map((c) => ({
                        value: c.chain_id,
                        label: chainOptionLabel(c),
                      }))}
                    />
                    {loadingChains && (
                      <Loader2
                        className="pointer-events-none absolute right-8 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--pm-faint)]"
                        aria-hidden
                      />
                    )}
                  </div>
                </div>
                <div className="pm-todo-meta-field min-w-0">
                  <FieldLabel htmlFor="pm-todo-create-ddl">Deadline</FieldLabel>
                  <DatePicker
                    id="pm-todo-create-ddl"
                    size="sm"
                    value={ddl}
                    onChange={setDdl}
                    placeholder="Optional"
                    allowClear
                  />
                </div>
              </div>
            </section>
          </div>

          {/* Description card — full width */}
          <section className="pm-todo-card pm-todo-card--desc">
            <div className="pm-todo-desc-head">
              <FieldLabel className="pm-todo-desc-label">Description</FieldLabel>
              <span className="pm-todo-card-hint">Optional · markdown</span>
            </div>
            <div
              className="pm-todo-md-host"
              onMouseDown={focusDescEnd}
            >
              {open && descReady && (
                <MarkdownEditor
                  key={`${editorKey}:${seedBody.slice(0, 48)}`}
                  value={body || seedBody}
                  onChange={setBody}
                  enableSlash={false}
                  showToolbar
                  flush
                  placeholder="Details, context, acceptance criteria…"
                  className="pm-todo-md-editor"
                  onEditorReady={(ed) => {
                    descEditorRef.current = ed
                  }}
                />
              )}
            </div>
          </section>
        </div>

        <DialogFooter className="pm-todo-dialog-foot gap-2 sm:justify-end">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="default"
            size="sm"
            onClick={() => void handleSubmit()}
            disabled={
              submitting || !title.trim() || !selectedChainId || loadingChains
            }
          >
            {submitting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              "Create"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
