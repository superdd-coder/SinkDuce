import { useEffect, useImperativeHandle, useMemo, useRef, useState, type PointerEvent, type Ref } from "react"
import { createPortal } from "react-dom"
import type { QaImportPlan } from "@/api/client"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { useT } from "@/i18n/use-t"
import { columnRole, excelColumn, qaParentCandidates } from "@/lib/qa-import-mapping"

const ROW_HEIGHT = 30
const WINDOW_ROWS = 28
const columnRoles = ["question", "number", "reply", "status", "group", "type", "extra", "ignore"]
const rowRoles = ["question", "child", "section", "note", "header", "continuation", "exclude", "review"]

type HoverCell = { body: string; left: number; top?: number; bottom?: number }
export interface QaImportTableHandle { focusRow(key: string): void }

export function QaImportTable({ plan, busy, columnsBusy = busy, columnMapping = plan.mapping, onRole, onRow, onParent, tableRef }: {
  plan: QaImportPlan; busy: boolean
  columnsBusy?: boolean
  columnMapping?: QaImportPlan["mapping"]
  onRole: (index: number, role: string) => void
  onRow: (key: string, role: string) => void
  onParent?: (key: string, parent: string) => void
  tableRef?: Ref<QaImportTableHandle>
}) {
  const t = useT()
  const scroller = useRef<HTMLDivElement>(null)
  const activeCell = useRef<HTMLElement | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const [hover, setHover] = useState<HoverCell>()
  const [scrollTop, setScrollTop] = useState(0)
  const [focusedRow, setFocusedRow] = useState("")
  const source = useMemo(() => new Map((plan.table_rows || plan.rows.map(row => ({ ...row, kind: "question" }))).map(row => [row.key, row])), [plan.table_rows, plan.rows])
  const firstColumn = (plan.region?.start_column || 1) - 1
  const columns = plan.headers.flatMap((_, index) => index >= firstColumn ? [index] : [])
  const headerEnd = plan.region?.header_end_row || plan.header_row || 1
  const lastRow = plan.region?.end_row || Math.max(headerEnd, ...Array.from(source.keys(), Number))
  const count = Math.max(0, lastRow - headerEnd)
  const start = count <= WINDOW_ROWS ? 0 : Math.max(0, Math.min(count - WINDOW_ROWS, Math.floor(scrollTop / ROW_HEIGHT) - 5))
  const end = Math.min(count, start + WINDOW_ROWS)

  function dismissHover() {
    clearTimeout(timer.current)
    activeCell.current = null
    setHover(undefined)
  }

  useEffect(() => () => clearTimeout(timer.current), [])

  useImperativeHandle(tableRef, () => ({
    focusRow(key) {
      const container = scroller.current
      if (!container) return
      const row = Number(key)
      container.scrollTop = Number.isFinite(row) ? Math.max(0, (row - headerEnd - 1) * ROW_HEIGHT) : 0
      setFocusedRow(key)
      setScrollTop(container.scrollTop)
      container.focus({ preventScroll: true })
      container.scrollIntoView({ block: "nearest" })
    },
  }))

  function onOver(event: PointerEvent<HTMLDivElement>) {
    const cell = (event.target as Element).closest<HTMLElement>("[data-qa-cell]")
    if (!cell || activeCell.current === cell) return
    dismissHover()
    const body = cell.textContent || ""
    if (cell.scrollWidth <= cell.clientWidth && !body.includes("\n")) return
    activeCell.current = cell
    timer.current = setTimeout(() => {
      if (activeCell.current !== cell || !cell.isConnected) return
      const bounds = cell.getBoundingClientRect()
      const below = window.innerHeight - bounds.bottom
      const left = Math.max(12, Math.min(bounds.left, window.innerWidth - 492))
      setHover(below >= 190 || bounds.top < below
        ? { body, left, top: bounds.bottom + 4 }
        : { body, left, bottom: window.innerHeight - bounds.top + 4 })
    }, 500)
  }

  function onOut(event: PointerEvent<HTMLDivElement>) {
    const next = event.relatedTarget
    if (!activeCell.current?.contains(event.target as Node) || next instanceof Node && activeCell.current.contains(next)) return
    clearTimeout(timer.current)
    activeCell.current = null
    timer.current = setTimeout(() => setHover(undefined), 150)
  }

  function row(number: number) {
    const key = String(number)
    const entry = source.get(key)
    const kind = entry?.kind || "blank"
    const isChild = entry?.kind === "child" || plan.row_actions?.[key] === "child"
    return <tr key={key} data-row-kind={kind} data-source-row={key} data-source-focused={focusedRow === key || undefined}>
      <th scope="row" className="qa-source-row">
        <div className="qa-sheet-row-controls">
          <span className="qa-sheet-index">{key}</span>
          {entry && <DropdownSelect size="sm" value={isChild ? "child" : kind} disabled={busy}
            menuClassName="qa-exchange-menu" aria-label={t("qa.rowRole", { number: key })}
            onChange={role => onRow(key, role)} options={rowRoles.map(value => ({ value, label: t(`qa.rowKind.${value}`) }))} />}
          {isChild && onParent && <DropdownSelect size="sm" aria-label={t("qa.rowParent")} menuClassName="qa-exchange-menu"
            value={plan.parents?.[key] || ("parent_key" in (entry || {}) ? (entry as { parent_key?: string }).parent_key || "" : "")}
            disabled={busy} onChange={value => onParent(key, value)}
            options={[{ value: "", label: t("qa.rowParent") }, ...qaParentCandidates(plan, key).map(item => ({ value: item.key, label: `${item.key} · ${item.text}` }))]} />}
        </div>
      </th>
      {columns.map(index => <td key={index}><span className="qa-sheet-cell" data-qa-cell>{entry?.values[index] || ""}</span></td>)}
    </tr>
  }

  return <div className="qa-table-review qa-sheet-preview">
    <div className="qa-import-grid-scroll" ref={scroller} tabIndex={0} aria-label={t("qa.tablePreview")}
      onPointerOver={onOver} onPointerOut={onOut}
      onScroll={event => { dismissHover(); setScrollTop(event.currentTarget.scrollTop) }}>
      <table className="qa-import-grid" aria-rowcount={count + 1} style={{ width: Math.max(1, columns.length) * 152 + 210 }}>
        <thead><tr><th scope="col" className="qa-source-row">{t("qa.sourceRow")}</th>
          {columns.map(index => <th scope="col" key={index}>
            <div className="qa-sheet-column-controls">
              <span className="qa-sheet-cell qa-sheet-column-title" data-qa-cell>{plan.headers[index] || "—"}</span>
              <DropdownSelect size="sm" value={columnRole(columnMapping, index)} disabled={columnsBusy} menuClassName="qa-exchange-menu"
                aria-label={t("qa.columnRole", { name: plan.headers[index] || excelColumn(index) })}
                onChange={role => onRole(index, role)}
                options={columnRoles.filter(role => index !== columnMapping.question || role === "question").map(value => ({ value, label: t(`qa.field.${value}`) }))} />
            </div>
          </th>)}
        </tr></thead>
        <tbody>
          {start > 0 && <tr aria-hidden="true" className="qa-sheet-spacer"><td colSpan={columns.length + 1} style={{ height: start * ROW_HEIGHT }} /></tr>}
          {Array.from({ length: end - start }, (_, index) => row(headerEnd + 1 + start + index))}
          {end < count && <tr aria-hidden="true" className="qa-sheet-spacer"><td colSpan={columns.length + 1} style={{ height: (count - end) * ROW_HEIGHT }} /></tr>}
        </tbody>
      </table>
    </div>
    {hover && createPortal(<div role="tooltip" className="qa-sheet-hover" style={{ left: hover.left, top: hover.top, bottom: hover.bottom }}
      onPointerEnter={() => clearTimeout(timer.current)} onPointerLeave={dismissHover}>{hover.body}</div>, document.body)}
  </div>
}
