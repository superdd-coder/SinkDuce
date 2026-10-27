import { useEffect, useRef, useState } from "react"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"
import type {
  LiveSummaryEntry,
  LiveSummaryState,
  TranscriptSegment,
} from "@/api/client"
import {
  diffEntryStatus,
  groupEntriesByKind,
  relativeAge,
  SECTION_ORDER,
  tailBilingualRows,
  type RelativeAgeKey,
} from "./live-summary-sections"

const SECTION_LABEL_KEYS: Record<(typeof SECTION_ORDER)[number], string> = {
  question: "meeting.liveSummaryQuestions",
  decision: "meeting.liveSummaryDecisions",
  action: "meeting.liveSummaryActions",
  point: "meeting.liveSummaryKeyPoints",
}

const AGE_KEY_MAP: Record<RelativeAgeKey, string> = {
  justNow: "meeting.liveSummaryJustNow",
  minutesAgo: "meeting.liveSummaryMinutesAgo",
  hoursAgo: "meeting.liveSummaryHoursAgo",
}

function fmtClock(t: number): string {
  const total = Math.max(0, Math.floor(t))
  const m = Math.floor(total / 60)
  const s = String(total % 60).padStart(2, "0")
  return `${m}:${s}`
}

function EntryRow({
  entry,
  isNew,
  isAmended,
  speakerNames,
}: {
  entry: LiveSummaryEntry
  isNew: boolean
  isAmended: boolean
  speakerNames: Record<string, string>
}) {
  const speaker = entry.speaker
    ? (speakerNames[entry.speaker] ?? entry.speaker)
    : null
  return (
    <li className={cn("pm-ls-entry", isNew && "is-new", isAmended && "is-amended")}>
      <span className="pm-ls-entry-time">{fmtClock(entry.t)}</span>
      {speaker && <span className="pm-ls-entry-speaker">{speaker}</span>}
      <span className="pm-ls-entry-text">{entry.text}</span>
    </li>
  )
}

/**
 * In-meeting live summary content: current topic on top, then the four
 * entry sections. A diff against the previous snapshot flags new/amended
 * entries so their arrival (and corrections) animate visibly.
 */
export function LiveSummaryPanel({
  state,
  error,
  paused,
  speakerNames,
}: {
  state: LiveSummaryState | null
  error: string | null
  paused: boolean
  speakerNames: Record<string, string>
}) {
  const t = useT()
  const prevEntriesRef = useRef<LiveSummaryEntry[] | null>(null)
  const [diff, setDiff] = useState<{ added: Set<string>; amended: Set<string> }>({
    added: new Set(),
    amended: new Set(),
  })
  const [, forceTick] = useState(0)

  const entries = state?.entries ?? []
  useEffect(() => {
    const prev = prevEntriesRef.current
    if (prev !== null) {
      setDiff(diffEntryStatus(prev, entries))
    }
    prevEntriesRef.current = entries
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state])

  // Keep the "updated X ago" meta fresh without a snapshot
  useEffect(() => {
    const id = setInterval(() => forceTick((n) => n + 1), 30_000)
    return () => clearInterval(id)
  }, [])

  const grouped = groupEntriesByKind(entries)
  const age = relativeAge(state?.updated_at ?? "", Date.now())
  const hasContent = entries.length > 0 || !!state?.topic

  return (
    <div className="pm-ls-panel">
      {state?.topic ? (
        <div className={cn("pm-ls-topic", state.topic.closed && "is-closed")}>
          <span className="pm-ls-topic-label">{t("meeting.liveSummaryTopic")}</span>
          <span className="pm-ls-topic-text">{state.topic.text}</span>
        </div>
      ) : null}

      {paused ? (
        <p className="pm-ls-note">{t("meeting.liveSummaryPaused")}</p>
      ) : error ? (
        <p className="pm-ls-note is-error">{error}</p>
      ) : null}

      {hasContent ? (
        <div className="pm-ls-sections">
          {SECTION_ORDER.map((kind) => {
            const items = grouped[kind]
            if (items.length === 0) return null
            return (
              <section key={kind} className="pm-ls-section">
                <h4 className="pm-ls-section-label">
                  {t(SECTION_LABEL_KEYS[kind])}
                  <span className="pm-ls-section-count">{items.length}</span>
                </h4>
                <ul>
                  {items.map((e) => (
                    <EntryRow
                      key={e.id}
                      entry={e}
                      isNew={diff.added.has(e.id)}
                      isAmended={diff.amended.has(e.id)}
                      speakerNames={speakerNames}
                    />
                  ))}
                </ul>
              </section>
            )
          })}
        </div>
      ) : (
        <p className="pm-ls-note">{t("meeting.liveSummaryEmpty")}</p>
      )}

      {state && (state.round > 0 || state.updated_at) ? (
        <p className="pm-ls-meta">
          ✦ {t(AGE_KEY_MAP[age.key], { n: age.n })}
        </p>
      ) : null}
    </div>
  )
}

/** Bottom strip under the live summary: bilingual transcript lines — one
 * line of original text, one line of translation — capped to four lines,
 * with the in-flight partial as the newest pair. Keeps the "what is being
 * said right now" feel while reading the summary. */
export function LiveSummaryTail({
  segments,
  partial,
  partialTranslation,
  tailFromT,
}: {
  segments: TranscriptSegment[]
  partial: string
  partialTranslation?: string
  tailFromT: number
}) {
  const t = useT()
  const rows = tailBilingualRows(segments, tailFromT, partial, partialTranslation)
  if (rows.length === 0) return null
  const sourceRows = rows.filter((r) => r.kind === "source")
  const translationRows = rows.filter((r) => r.kind === "translation")
  // Two physically separate boxes so a wrapping source line can only clip
  // inside the source box — translations can never push sources out.
  const renderRows = (list: typeof rows) =>
    list.map((r) => (
      <p
        key={r.key}
        dir="auto"
        className={cn(
          r.kind === "translation" && "is-translation",
          r.partial && "pm-ls-tail-partial",
        )}
      >
        {r.text}
      </p>
    ))
  return (
    <div className="pm-ls-tail">
      <span className="pm-ls-tail-label">
        <span className="pm-ls-tail-dot" aria-hidden />
        {t("meeting.liveSummaryTail")}
      </span>
      {translationRows.length > 0 ? (
        <>
          <div className="pm-ls-tail-lines">{renderRows(sourceRows)}</div>
          <div className="pm-ls-tail-lines">{renderRows(translationRows)}</div>
        </>
      ) : (
        <div className="pm-ls-tail-lines is-source-only">{renderRows(sourceRows)}</div>
      )}
    </div>
  )
}
