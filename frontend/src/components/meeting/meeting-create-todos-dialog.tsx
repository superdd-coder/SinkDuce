/**
 * Confirm-create checklist todos from meeting section candidates.
 * Premium silk shell · nested soft-float stage · compact rows with DDL.
 * Default: all unchecked (design 2026-08-12).
 */
import { useEffect, useMemo, useState } from "react"
import { Loader2, ListTodo } from "lucide-react"
import { toast } from "sonner"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { DatePicker } from "@/components/ui/date-picker"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { createTodo } from "@/api/file-mgmt"
import {
  markTodoCandidatesCreated,
  type MeetingTodoCandidate,
  type Meeting,
} from "@/api/client"
import { triggerTodoRefresh } from "@/lib/todo-refresh"
import { useT } from "@/i18n/use-t"
import { formatApiError } from "@/api/http"

export interface MeetingCreateTodosDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  collectionId: string
  chainId?: string | null
  meetingId: string
  candidates: MeetingTodoCandidate[]
  defaultSectionTabId?: string | null
  title?: string
  loading?: boolean
  onCreated?: (count: number, meeting?: Meeting | null) => void
}

function priorityClass(p: string | null | undefined): string {
  const v = (p || "").toLowerCase()
  if (v === "high") return "is-high"
  if (v === "low") return "is-low"
  if (v === "medium") return "is-medium"
  return ""
}

/** Normalize free-text ddl → yyyy-mm-dd for DatePicker when possible. */
function normalizeDdlInput(raw: string | null | undefined): string {
  const s = (raw || "").trim()
  if (!s) return ""
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s
  const m = s.match(/^(\d{4})[./-](\d{1,2})[./-](\d{1,2})/)
  if (m) {
    const y = m[1]
    const mo = String(Number(m[2])).padStart(2, "0")
    const d = String(Number(m[3])).padStart(2, "0")
    return `${y}-${mo}-${d}`
  }
  const dt = new Date(s)
  if (!Number.isNaN(dt.getTime())) {
    const y = dt.getFullYear()
    const mo = String(dt.getMonth() + 1).padStart(2, "0")
    const d = String(dt.getDate()).padStart(2, "0")
    return `${y}-${mo}-${d}`
  }
  return ""
}

export function MeetingCreateTodosDialog({
  open,
  onOpenChange,
  collectionId,
  chainId = null,
  meetingId,
  candidates,
  defaultSectionTabId = null,
  title,
  loading = false,
  onCreated,
}: MeetingCreateTodosDialogProps) {
  const t = useT()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  /** Per-candidate deadline overrides (yyyy-mm-dd or ""). */
  const [ddlById, setDdlById] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)

  const items = useMemo(
    () => (candidates || []).filter((c) => (c.title || "").trim()),
    [candidates],
  )

  const openItems = useMemo(
    () => items.filter((c) => !(c.created_todo_id || "").trim()),
    [items],
  )

  useEffect(() => {
    if (!open) return
    setSelected(new Set())
    const next: Record<string, string> = {}
    for (const c of items) {
      next[c.candidate_id] = normalizeDdlInput(c.ddl)
    }
    setDdlById(next)
  }, [
    open,
    items.map((i) => `${i.candidate_id}:${i.created_todo_id || ""}:${i.ddl || ""}`).join("|"),
  ])

  const toggle = (id: string) => {
    const item = items.find((c) => c.candidate_id === id)
    if (item && (item.created_todo_id || "").trim()) return
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const setRowDdl = (id: string, value: string) => {
    setDdlById((prev) => ({ ...prev, [id]: value }))
  }

  const selectAll = () =>
    setSelected(new Set(openItems.map((i) => i.candidate_id)))
  const clearAll = () => setSelected(new Set())

  const handleCreate = async () => {
    const picked = openItems.filter((i) => selected.has(i.candidate_id))
    if (picked.length === 0) {
      toast.error(t("meeting.selectOneTodo"))
      return
    }
    setSubmitting(true)
    let ok = 0
    const marks: Array<{
      tab_id?: string | null
      candidate_id: string
      todo_id: string
    }> = []
    try {
      for (const c of picked) {
        const tabId =
          (c.section_tab_id || defaultSectionTabId || "").trim() || null
        const ddlRaw = (ddlById[c.candidate_id] ?? "").trim()
        const created = await createTodo(collectionId, {
          title: c.title.trim(),
          body: c.body || null,
          ddl: ddlRaw || null,
          target_chain_id: chainId || null,
          source_meeting_id: meetingId,
          source_section_tab_id: tabId,
          source_candidate_id: c.candidate_id,
        })
        ok += 1
        marks.push({
          tab_id: tabId,
          candidate_id: c.candidate_id,
          todo_id: created.todo_id,
        })
      }
      let updatedMeeting: Meeting | null = null
      if (marks.length > 0 && meetingId) {
        try {
          updatedMeeting = await markTodoCandidatesCreated(meetingId, marks)
        } catch (err) {
          console.warn("markTodoCandidatesCreated failed", err)
        }
      }
      toast.success(ok === 1 ? t("meeting.created1Todo") : t("meeting.createdNTodos", { n: ok }))
      triggerTodoRefresh({ collectionId })
      onCreated?.(ok, updatedMeeting)
      onOpenChange(false)
    } catch (err) {
      if (marks.length > 0 && meetingId) {
        try {
          const m = await markTodoCandidatesCreated(meetingId, marks)
          onCreated?.(ok, m)
        } catch {
          /* ignore */
        }
      }
      toast.error(
        ok
          ? t("meeting.createFailedAfter", { ok, error: formatApiError(err, t) })
          : t("common.failedWithError", { error: formatApiError(err, t) }),
      )
    } finally {
      setSubmitting(false)
    }
  }

  const groups = useMemo(() => {
    const map = new Map<string, MeetingTodoCandidate[]>()
    for (const c of items) {
      const key = c.section_name || c.section_tab_id || t("meeting.section")
      if (!map.has(key)) map.set(key, [])
      map.get(key)!.push(c)
    }
    return [...map.entries()]
  }, [items, t])

  const subtitle =
    title &&
    title !== t("fileMgmt.createTodosSummary") &&
    title !== t("meeting.fromSummary")
      ? title
      : t("meeting.todosSubtitle")

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "pm-dialog pm-dialog--silk pm-meeting-todos-dialog",
          "max-h-[min(740px,92vh)] flex flex-col gap-0",
          "!animate-none data-open:!animate-none data-closed:!animate-none",
        )}
        overlayClassName="pm-dialog-overlay--silk"
        showCloseButton
      >
        <DialogHeader className="pm-meeting-todos-dialog-head">
          <DialogKicker>{t("nav.meeting")}</DialogKicker>
          <DialogTitle>{t("meeting.createTodos")}</DialogTitle>
          <DialogDescription>{subtitle}</DialogDescription>
        </DialogHeader>

        <div className="pm-meeting-todos-dialog-body min-h-0 flex-1 flex flex-col">
          {loading ? (
            <div className="pm-meeting-todos-stage is-empty">
              <div className="pm-meeting-todos-empty">
                <Loader2 className="h-4 w-4 animate-spin text-[var(--pm-faint)]" />
                <span>{t("meeting.extractingTodos")}</span>
              </div>
            </div>
          ) : items.length === 0 ? (
            <div className="pm-meeting-todos-stage is-empty">
              <div className="pm-meeting-todos-empty">
                <span className="pm-meeting-todos-empty-icon" aria-hidden>
                  <ListTodo className="h-5 w-5" strokeWidth={1.5} />
                </span>
                <p>
                  {(() => {
                    const full = t("meeting.noTodoCandidates", { heading: "## Todo" })
                    const parts = full.split("## Todo")
                    if (parts.length < 2) return full
                    return (
                      <>
                        {parts[0]}
                        <code className="pm-meeting-todos-code">## Todo</code>
                        {parts[1]}
                      </>
                    )
                  })()}
                </p>
              </div>
            </div>
          ) : (
            <TooltipProvider delay={280} closeDelay={80}>
              <div className="pm-meeting-todos-stage">
                <div className="pm-meeting-todos-toolbar">
                  <div
                    className="pm-meeting-todos-seg"
                    role="group"
                    aria-label={t("meeting.selection")}
                  >
                    <button
                      type="button"
                      className="pm-meeting-todos-seg-btn"
                      onClick={selectAll}
                      disabled={openItems.length === 0}
                    >
                      {t("common.selectAll")}
                    </button>
                    <button
                      type="button"
                      className="pm-meeting-todos-seg-btn"
                      onClick={clearAll}
                      disabled={selected.size === 0}
                    >
                      {t("common.clear")}
                    </button>
                  </div>
                  <span
                    className={cn(
                      "pm-meeting-todos-count",
                      selected.size > 0 && "is-active",
                    )}
                  >
                    <span className="pm-meeting-todos-count-num">
                      {selected.size}
                    </span>
                    <span className="pm-meeting-todos-count-muted">
                      / {openItems.length}
                    </span>
                  </span>
                </div>

                <div className="pm-meeting-todos-list min-h-0 flex-1 overflow-y-auto">
                  {groups.map(([gName, list]) => (
                    <div key={gName} className="pm-meeting-todos-group">
                      {groups.length > 1 ? (
                        <div className="pm-meeting-todos-group-label">
                          {gName}
                        </div>
                      ) : null}
                      <ul className="pm-meeting-todos-ul">
                        {list.map((c) => {
                          const already = !!(c.created_todo_id || "").trim()
                          const on = selected.has(c.candidate_id)
                          const ddl = ddlById[c.candidate_id] ?? ""
                          const desc = (c.body || "").trim()
                          const selectButton = (
                            <button
                              type="button"
                              disabled={already}
                              aria-pressed={on}
                              className="pm-meeting-todos-select"
                              onClick={() => toggle(c.candidate_id)}
                            >
                              <span className="pm-meeting-todos-row-main">
                                <span className="pm-meeting-todos-chip-text">
                                  {c.title}
                                </span>
                                {already ? (
                                  <span className="pm-meeting-todos-done-tag">
                                    {t("common.created")}
                                  </span>
                                ) : null}
                              </span>
                            </button>
                          )
                          return (
                            <li
                              key={c.candidate_id}
                              className={cn(
                                "pm-meeting-todos-row",
                                already && "is-done",
                                on && !already && "is-on",
                              )}
                            >
                              {desc ? (
                                <Tooltip>
                                  <TooltipTrigger
                                    delay={280}
                                    closeDelay={80}
                                    render={selectButton}
                                  />
                                  <TooltipContent
                                    side="top"
                                    sideOffset={8}
                                    align="start"
                                    showArrow={false}
                                    className="pm-meeting-todos-tooltip"
                                  >
                                    {desc}
                                  </TooltipContent>
                                </Tooltip>
                              ) : (
                                selectButton
                              )}
                              <div
                                className="pm-meeting-todos-side"
                                onClick={(e) => e.stopPropagation()}
                                onPointerDown={(e) => e.stopPropagation()}
                              >
                                {c.priority ? (
                                  <span
                                    className={cn(
                                      "pm-meeting-todos-pri",
                                      priorityClass(c.priority),
                                    )}
                                  >
                                    {c.priority}
                                  </span>
                                ) : null}
                                <div className="pm-meeting-todos-ddl-field">
                                  <DatePicker
                                    size="sm"
                                    value={ddl}
                                    onChange={(v) =>
                                      setRowDdl(c.candidate_id, v)
                                    }
                                    placeholder={t("library.deadline")}
                                    allowClear
                                    disabled={already || submitting}
                                  />
                                </div>
                              </div>
                            </li>
                          )
                        })}
                      </ul>
                    </div>
                  ))}
                </div>
              </div>
            </TooltipProvider>
          )}
        </div>

        <DialogFooter className="pm-meeting-todos-dialog-foot">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="pm-meeting-todos-btn-ghost"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            {t("common.cancel")}
          </Button>
          <Button
            type="button"
            size="sm"
            className="pm-meeting-todos-btn-primary"
            onClick={() => void handleCreate()}
            disabled={
              submitting ||
              loading ||
              openItems.length === 0 ||
              selected.size === 0
            }
          >
            {submitting ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                {t("common.creating")}
              </>
            ) : selected.size > 0 ? (
              t("meeting.createN", { n: selected.size })
            ) : (
              t("common.create")
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
