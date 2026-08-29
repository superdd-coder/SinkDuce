/**
 * Todo detail / edit — same Premium silk shell as CreateTodoDialog.
 * Open todos: title, markdown body, ddl, chain editable.
 * Completed todos: read-only.
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
import { listChains, updateTodo } from "@/api/file-mgmt"
import type { Chain, TodoItem } from "@/types/file-mgmt"
import { triggerTodoRefresh } from "@/lib/todo-refresh"
import {
  seedFromTodo,
  shouldMountTodoDetailEditor,
} from "@/lib/todo-detail-form"
import type { Editor } from "@tiptap/core"
import { useT } from "@/i18n/use-t"
import { formatApiError } from "@/api/http"

interface TodoDetailDialogProps {
  collectionId: string
  todo: TodoItem | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onUpdated?: (todo: TodoItem) => void
}

function chainOptionLabel(
  c: Chain,
  t: (key: string, vars?: Record<string, string | number>) => string,
): string {
  if (c.is_main) {
    return c.title?.trim()
      ? t("library.mainDot", { title: c.title.trim() })
      : t("library.main")
  }
  return c.title?.trim() || t("library.branch")
}

export function TodoDetailDialog({
  collectionId,
  todo,
  open,
  onOpenChange,
  onUpdated,
}: TodoDetailDialogProps) {
  const t = useT()
  /** Hold last todo through exit animation (parent may clear after delay). */
  const [displayTodo, setDisplayTodo] = useState<TodoItem | null>(todo)
  const readonly = !!displayTodo?.done
  const [title, setTitle] = useState("")
  const [body, setBody] = useState("")
  const [ddl, setDdl] = useState("")
  const [chains, setChains] = useState<Chain[]>([])
  const [selectedChainId, setSelectedChainId] = useState("")
  const [loadingChains, setLoadingChains] = useState(false)
  const [saving, setSaving] = useState(false)
  /** Which todo the title/body/ddl state was seeded from. */
  const [seededTodoId, setSeededTodoId] = useState<string | null>(null)
  const titleInputRef = useRef<HTMLTextAreaElement>(null)
  const descEditorRef = useRef<Editor | null>(null)
  const openTodoId = todo?.todo_id ?? null

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
    if (todo) setDisplayTodo(todo)
  }, [todo])

  useEffect(() => {
    if (!open || !todo) return
    const expectedChainId = todo.chain_id
    setLoadingChains(true)
    listChains(collectionId)
      .then((list) => {
        setChains(list)
        if (!list.some((c) => c.chain_id === expectedChainId)) {
          const main = list.find((c) => c.is_main)
          setSelectedChainId(main?.chain_id || list[0]?.chain_id || "")
        }
      })
      .catch(() => setChains([]))
      .finally(() => setLoadingChains(false))
  }, [open, todo, collectionId])

  useEffect(() => {
    if (!open || readonly) return
    const t = window.setTimeout(() => {
      const el = titleInputRef.current
      if (!el) return
      el.focus({ preventScroll: true })
      syncTitleHeight()
    }, 300)
    return () => window.clearTimeout(t)
  }, [open, readonly, syncTitleHeight])

  useEffect(() => {
    if (!open) return
    requestAnimationFrame(syncTitleHeight)
  }, [open, title, syncTitleHeight])

  /** Click empty pad under last line → caret at end (editable only) */
  const focusDescEnd = useCallback(
    (e: ReactMouseEvent) => {
      if (readonly) return
      const ed = descEditorRef.current
      if (!ed || ed.isDestroyed) return
      const t = e.target as HTMLElement
      if (t.closest(".pm-fmt-toolbar")) return
      const pm = ed.view.dom as HTMLElement
      const last = pm.lastElementChild as HTMLElement | null
      const below =
        !last || e.clientY > last.getBoundingClientRect().bottom + 2
      const onHost =
        t === e.currentTarget || t.classList.contains("pm-todo-md-host")
      if (below || onHost || t === pm) {
        e.preventDefault()
        ed.chain().focus("end").run()
      }
    },
    [readonly]
  )

  const dirty = useMemo(() => {
    if (!displayTodo || readonly) return false
    const origDdl = displayTodo.ddl?.slice(0, 10) || ""
    const origBody = displayTodo.body || ""
    return (
      title.trim() !== displayTodo.title ||
      body !== origBody ||
      ddl !== origDdl ||
      selectedChainId !== displayTodo.chain_id
    )
  }, [displayTodo, readonly, title, body, ddl, selectedChainId])

  const handleSave = async () => {
    if (!displayTodo || readonly) return
    const titleText = title.trim()
    if (!titleText) {
      toast.error(t("common.titleRequired"))
      return
    }
    if (!selectedChainId) {
      toast.error(t("library.selectChain"))
      return
    }
    setSaving(true)
    try {
      const isMain = mainChain?.chain_id === selectedChainId
      const nextBody = body.trim()
      const origBody = (displayTodo.body || "").trim()
      const payload: Parameters<typeof updateTodo>[2] = {
        title: titleText,
        target_chain_id: isMain ? null : selectedChainId,
      }
      if (ddl) {
        payload.ddl = ddl
      } else if (displayTodo.ddl) {
        payload.clear_ddl = true
      }
      if (nextBody !== origBody) {
        if (!nextBody) payload.clear_body = true
        else payload.body = nextBody
      }
      const updated = await updateTodo(
        collectionId,
        displayTodo.todo_id,
        payload
      )
      toast.success(t("library.todoUpdated"))
      onUpdated?.(updated)
      setDisplayTodo(updated)
      triggerTodoRefresh({ collectionId, reason: "update" })
      onOpenChange(false)
    } catch (err) {
      toast.error(formatApiError(err, t))
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

  // Seed during render so TipTap never mounts on the previous todo's body.
  // (useEffect runs after first paint; keepMounted dialog + TipTap would stick.)
  if (open && todo && seededTodoId !== todo.todo_id) {
    const seed = seedFromTodo(todo)
    setSeededTodoId(seed.todoId)
    setDisplayTodo(todo)
    setTitle(seed.title)
    setBody(seed.body)
    setDdl(seed.ddl)
    setSelectedChainId(seed.chainId)
  }
  if (!open && seededTodoId !== null) {
    setSeededTodoId(null)
  }

  const descReady = shouldMountTodoDetailEditor(open, openTodoId, seededTodoId)

  // Keep shell mounted for exit when displayTodo still held
  if (!displayTodo) return null

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
          <DialogTitle className="pm-todo-dialog-title">
            {readonly ? t("library.todoDetail") : t("library.editTodo")}
          </DialogTitle>
        </DialogHeader>

        <div className="pm-dialog-body pm-todo-dialog-body">
          {readonly && (
            <p className="pm-todo-readonly-banner" role="status">
              {t("library.completedReadOnly")}
            </p>
          )}

          <div className="pm-todo-top-row">
            <section className="pm-todo-card pm-todo-card--title">
              {/* Label pinned top-left; input centered in remaining height */}
              <FieldLabel htmlFor="pm-todo-edit-title">{t("library.todo")}</FieldLabel>
              <div className="pm-todo-title-block">
                <textarea
                  ref={titleInputRef}
                  id="pm-todo-edit-title"
                  rows={1}
                  spellCheck={false}
                  className={cn(
                    "pm-node-id-title pm-node-id-title-input pm-todo-title-input",
                    readonly && "is-readonly"
                  )}
                  value={title}
                  onChange={(e) => {
                    setTitle(e.target.value)
                    requestAnimationFrame(syncTitleHeight)
                  }}
                  disabled={readonly}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") e.preventDefault()
                  }}
                  placeholder={t("library.todoTitle")}
                  aria-label={t("library.todoTitle")}
                />
              </div>
            </section>

            <section className="pm-todo-card pm-todo-card--meta">
              <div className="pm-todo-meta-stack">
                <div className="pm-todo-meta-field min-w-0">
                  <FieldLabel htmlFor="pm-todo-edit-chain">{t("library.chain")}</FieldLabel>
                  <div className="relative">
                    <DropdownSelect
                      size="sm"
                      value={selectedChainId}
                      onChange={setSelectedChainId}
                      disabled={readonly || loadingChains}
                      placeholder={
                        loadingChains ? t("library.loadingChains") : t("library.selectChainPh")
                      }
                      options={sortedChains.map((c) => ({
                        value: c.chain_id,
                        label: chainOptionLabel(c, t),
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
                  <FieldLabel htmlFor="pm-todo-edit-ddl">{t("library.deadline")}</FieldLabel>
                  {readonly && !ddl ? (
                    <span className="pm-todo-ddl-readonly-empty">
                      {t("library.noDeadline")}
                    </span>
                  ) : (
                    <DatePicker
                      id="pm-todo-edit-ddl"
                      size="sm"
                      value={ddl}
                      onChange={setDdl}
                      placeholder={t("common.optional")}
                      disabled={readonly}
                      allowClear={!readonly}
                    />
                  )}
                </div>
              </div>
            </section>
          </div>

          <section className="pm-todo-card pm-todo-card--desc">
            <div className="pm-todo-desc-head">
              <FieldLabel className="pm-todo-desc-label">{t("common.description")}</FieldLabel>
              {!readonly && (
                <span className="pm-todo-card-hint">{t("common.optionalMd")}</span>
              )}
            </div>
            <div
              className={cn(
                "pm-todo-md-host",
                readonly && "pm-todo-md-host--readonly"
              )}
              onMouseDown={focusDescEnd}
            >
              {descReady && (
                <MarkdownEditor
                  key={openTodoId || displayTodo.todo_id}
                  value={body}
                  onChange={setBody}
                  enableSlash={false}
                  showToolbar={!readonly}
                  readonly={readonly}
                  flush
                  placeholder={
                    readonly
                      ? t("library.noDescription")
                      : t("library.detailsCriteria")
                  }
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
            disabled={saving}
          >
            {readonly ? t("common.close") : t("common.cancel")}
          </Button>
          {!readonly && (
            <Button
              type="button"
              variant="default"
              size="sm"
              onClick={() => void handleSave()}
              disabled={saving || !dirty || !title.trim()}
            >
              {saving ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                t("common.save")
              )}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
