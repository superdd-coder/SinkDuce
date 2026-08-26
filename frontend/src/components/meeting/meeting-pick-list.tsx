import { useMemo, useState, type ReactNode } from "react"
import { Search, Trash2 } from "lucide-react"

export type MeetingPickItem = {
  id: string
  title: string
  status?: string
  sortAt?: string
  index?: string
}

export function catalogPickDate(iso?: string): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ""
  const diff = Date.now() - d.getTime()
  if (diff < 60_000) return "now"
  if (diff < 3600_000) return `${Math.max(1, Math.floor(diff / 60_000))}m`
  if (diff < 86_400_000) return `${Math.max(1, Math.floor(diff / 3600_000))}h`
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
}

function monthKey(iso?: string): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ""
  return `${d.getFullYear()}-${String(d.getMonth()).padStart(2, "0")}`
}

function monthLabel(iso: string): string {
  const d = new Date(iso)
  const now = new Date()
  return d.toLocaleDateString(undefined, {
    month: "long",
    year: d.getFullYear() === now.getFullYear() ? undefined : "numeric",
  })
}

export function MeetingPickList({
  items,
  emptyText,
  filterPlaceholder,
  onSelect,
  onRemove,
  removeLabel,
}: {
  items: MeetingPickItem[]
  emptyText: ReactNode
  filterPlaceholder?: string
  onSelect: (id: string) => void
  onRemove?: (id: string) => void
  removeLabel?: string
}) {
  const [query, setQuery] = useState("")
  const needle = query.trim().toLowerCase()
  const visible = useMemo(() => {
    if (!needle) return items
    return items.filter((row) => row.title.toLowerCase().includes(needle))
  }, [items, needle])

  const sections = useMemo(() => {
    const order: string[] = []
    const buckets = new Map<string, MeetingPickItem[]>()
    for (const row of visible) {
      const key = monthKey(row.sortAt) || "_"
      if (!buckets.has(key)) {
        buckets.set(key, [])
        order.push(key)
      }
      buckets.get(key)!.push(row)
    }
    order.sort((a, b) => b.localeCompare(a))
    return order.map((key) => {
      const rows = (buckets.get(key) || []).slice().sort((a, b) =>
        (b.sortAt || "").localeCompare(a.sortAt || ""),
      )
      const sample = rows.find((r) => r.sortAt)?.sortAt
      return {
        key,
        label: key === "_" || !sample ? "" : monthLabel(sample),
        rows,
      }
    })
  }, [visible])

  if (items.length === 0) {
    return (
      <div className="pm-meeting-pick-list">
        <p className="pm-meeting-pick-empty">{emptyText}</p>
      </div>
    )
  }

  return (
    <div className="pm-meeting-pick-list">
      {filterPlaceholder && (
        <label className="pm-meeting-pick-filter">
          <Search className="pm-meeting-pick-filter-icon" aria-hidden />
          <span className="sr-only">{filterPlaceholder}</span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={filterPlaceholder}
            autoComplete="off"
            spellCheck={false}
          />
        </label>
      )}
      <div className="pm-meeting-pick-scroll">
        {visible.length === 0 ? (
          <p className="pm-meeting-pick-empty">{emptyText}</p>
        ) : (
          sections.map((sec) => (
            <section key={sec.key} className="pm-meeting-pick-section">
              {sec.label ? <h3 className="pm-meeting-pick-kicker">{sec.label}</h3> : null}
              {sec.rows.map((row) => (
                <div
                  key={row.id}
                  role="button"
                  tabIndex={0}
                  className="pm-meeting-pick-row"
                  title={row.title}
                  onClick={() => onSelect(row.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault()
                      onSelect(row.id)
                    }
                  }}
                >
                  {row.index ? (
                    <span className="pm-meeting-pick-idx">{row.index}</span>
                  ) : null}
                  <span className="pm-meeting-pick-name">{row.title}</span>
                  <span className="pm-meeting-pick-aside">
                    <span className="pm-meeting-pick-time">{catalogPickDate(row.sortAt)}</span>
                    {row.status ? (
                      <span className="pm-meeting-pick-status">{row.status}</span>
                    ) : null}
                  </span>
                  {onRemove ? (
                    <button
                      type="button"
                      className="pm-meeting-pick-remove"
                      title={removeLabel}
                      aria-label={removeLabel}
                      onClick={(e) => {
                        e.stopPropagation()
                        onRemove(row.id)
                      }}
                    >
                      <Trash2 className="size-3" />
                    </button>
                  ) : null}
                </div>
              ))}
            </section>
          ))
        )}
      </div>
    </div>
  )
}
