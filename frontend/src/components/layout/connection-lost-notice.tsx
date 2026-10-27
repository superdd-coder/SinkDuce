import { useEffect } from "react"
import { toast } from "sonner"

import { takeConnectionLostNotice } from "@/api/connection-health"
import { useT } from "@/i18n/use-t"

/**
 * 远端盒子连接断开后的提示（整页刷新回来后弹一次）。
 *
 * 设备 B 连的设备 A 吊销了钥匙或下线时，请求层会退回本机并留下这条记录；
 * 这里读一次并告诉用户「为什么退回来了」，而不是让界面默默变空。
 */
export function ConnectionLostNotice() {
  const t = useT()

  useEffect(() => {
    const notice = takeConnectionLostNotice()
    if (!notice) return
    const key =
      notice.reason === "revoked"
        ? "shell.connectionLost.revoked"
        : "shell.connectionLost.unreachable"
    toast.warning(t(key, { name: notice.box_name }), { duration: 8000 })
  }, [t])

  return null
}
