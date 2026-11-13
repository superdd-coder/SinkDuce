import type { QaOption, QaOptions } from "@/api/client"

export const defaultQaOptions: QaOptions = {
  statuses: [{ id: "waiting", label: "Waiting for reply", color: "#b58c42" }, { id: "review", label: "To review", color: "#6486ad" }, { id: "closed", label: "Closed", color: "#397b60" }],
}
export function qaOptionLabel(option: QaOption | undefined, t: (key: string) => string) {
  if (!option) return t("qa.unset")
  return defaultQaOptions.statuses.some(o => o.id === option.id && o.label === option.label) ? t(`qa.status.${option.id}`) : option.label
}
