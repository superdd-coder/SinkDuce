import { useEffect, useState } from "react"

import { useT } from "@/i18n/use-t"

/**
 * 连接降级横幅（2026-09-19 前端加固 #7）。
 *
 * 远端盒子失联时，若录制/上传正在进行（critical-op），请求层**推迟**回退本机
 * （reload 会掐断它们），只 dispatch `sinkduce:connection-degraded`。这里
 * 监听并展示横幅：告诉用户「盒子失联，但你的操作在继续，结束后自动切回」，
 * 而不是让界面静默变空。
 */
export function ConnectionDegradedBanner() {
  const t = useT()
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const show = () => setVisible(true)
    window.addEventListener("sinkduce:connection-degraded", show)
    return () => window.removeEventListener("sinkduce:connection-degraded", show)
  }, [])

  if (!visible) return null
  return (
    <div
      role="alert"
      className="fixed top-3 left-1/2 z-[9999] -translate-x-1/2 rounded-lg border border-amber-300/60 bg-amber-50 px-4 py-2 text-sm text-amber-900 shadow-lg dark:border-amber-500/40 dark:bg-amber-950/80 dark:text-amber-200"
    >
      {t("shell.connectionDegraded")}
    </div>
  )
}
