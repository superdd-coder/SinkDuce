import { useCallback } from "react"
import { useAppStore } from "@/stores/app-store"
import { t } from "./index"

export function useT() {
  const locale = useAppStore((s) => s.locale)
  return useCallback(
    (key: string, vars?: Record<string, string | number>) => t(locale, key, vars),
    [locale],
  )
}
