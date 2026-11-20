import { useT } from "@/i18n/use-t"
import { useAppStore } from "@/stores/app-store"
import type { EmbeddingSearch } from "@/lib/embedding-searches"

export function RetrievalDetails({ searches = [], skipped = [] }: { searches?: EmbeddingSearch[]; skipped?: string[] }) {
  const collections = useAppStore(state => state.collections)
  const developerMode = useAppStore(state => state.developerMode)
  const t = useT()
  const names = new Map(collections.map(c => [c.id, c.name]))
  const visible = searches.filter(row => names.has(row.collection))
  const searched = new Set(visible.map(row => row.collection))
  const skippedNames = collections.filter(c => skipped.includes(c.id) && !searched.has(c.id)).map(c => c.name)
  const showDetails = developerMode && visible.length > 0
  if (!showDetails && !skippedNames.length) return null
  return (
    <div className="pm-meta grid min-w-0 gap-1.5">
      {showDetails && (
        <details>
          <summary className="w-fit cursor-pointer">{t("chat.retrievalDetails")}</summary>
          <ul className="mt-2 grid gap-1.5">
            {visible.map(row => (
              <li key={JSON.stringify([row.collection, row.provider_id, row.model, row.dimensions, row.mode])}
                className="grid min-w-0 gap-0.5 break-words">
                <strong>{names.get(row.collection)}</strong>
                <span title={row.provider_id || undefined}>
                  {row.provider_name && row.provider_name !== row.model ? row.provider_name + " · " : ""}{row.model}
                  {" · "}{t("chat.vectorDimensions", { n: row.dimensions })}
                  {" · "}{t(row.mode === "hybrid" ? "chat.hybridSearch" : "chat.denseSearch")}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
      {!!skippedNames.length && <p role="note">{t("chat.skippedEmbedding", { names: skippedNames.join(" · ") })}</p>}
    </div>
  )
}
