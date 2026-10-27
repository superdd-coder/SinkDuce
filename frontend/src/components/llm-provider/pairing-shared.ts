/** Pairing 卡片与弹窗共享的非组件工具：相对时间 hook、分享链接拼装。 */
import { useCallback } from "react"

import { useT } from "@/i18n/use-t"
import type { PairingBoxInfo } from "@/api/pairing"

export function useRelTime() {
  const t = useT()
  return useCallback(
    (iso: string | null | undefined) => {
      if (!iso) return t("settings.pairing.never")
      const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
      if (s < 60) return t("settings.pairing.justNow")
      if (s < 3600) return t("settings.pairing.minAgo", { n: Math.floor(s / 60) })
      if (s < 86400) return t("settings.pairing.hourAgo", { n: Math.floor(s / 3600) })
      return t("settings.pairing.dayAgo", { n: Math.floor(s / 86400) })
    },
    [t],
  )
}

export function defaultShareAddr(): string {
  const { hostname, protocol, host } = window.location
  if (hostname && !/^(127\.|localhost$|::1$)/.test(hostname)) {
    return `${protocol}//${host}`
  }
  return ""
}

export function shareUrl(addr: string, box: PairingBoxInfo, token: string, port: string): string {
  const base = addr.startsWith("http") ? addr.replace(/\/+$/, "") : `http://${addr}:${port}`
  const params = new URLSearchParams({
    box: box.box_id,
    name: box.box_name,
    addr: base,
    token,
  })
  return `sinkduce://pair?${params.toString()}`
}
