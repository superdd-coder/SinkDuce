import { useState } from "react"
import { toast } from "sonner"

import { getActiveSourceId, saveConnections, savedConnections, type SavedConnection } from "@/api/origin"
import { testRemoteBox } from "@/api/pairing"
import {
  applyAddressEdit,
  isSameBox,
  normalizeRemoteAddr,
} from "@/lib/pairing-edit"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { useT } from "@/i18n/use-t"

interface EditBoxAddressDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 要改的那条已连接记录（受控弹窗由父级按 key 重挂载，字段即初始值）。 */
  connection: SavedConnection
  /** 保存成功后通知父级刷新列表。 */
  onSaved: () => void
}

type EditError = "unreachable" | "invalid_token" | "http_error" | "different_box" | null

/**
 * 改已连接设备的地址（2026-09-17 用户要求）。
 *
 * 为什么不是「移除 + 重新连接」：那要用户重新粘一遍 API Key，而地址变动（换网络、
 * DHCP 换 IP）时钥匙通常没变。这里就地改，**并用钥匙校验一次**再保存。
 *
 * 校验回来的 `box_id` 必须与这条记录一致——否则那个地址上是**另一台**盒子，
 * 覆盖记录会把两台盒子的来源作用域串味（见 lib/pairing-edit 的说明）。
 */
export function EditBoxAddressDialog({
  open,
  onOpenChange,
  connection,
  onSaved,
}: EditBoxAddressDialogProps) {
  const t = useT()
  const [addr, setAddr] = useState(connection.addr)
  const [token, setToken] = useState(connection.token)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<EditError>(null)
  const [otherBoxName, setOtherBoxName] = useState("")

  const handleSave = async () => {
    const a = normalizeRemoteAddr(addr)
    const tk = token.trim()
    if (!a || !tk) return
    setSaving(true)
    setError(null)
    try {
      const probed = await testRemoteBox(a, tk)
      if (!isSameBox(connection.box_id, probed.box_id)) {
        // 那个地址上是别的盒子 —— 拒绝覆盖，提示改用「连接」。
        setOtherBoxName(probed.box_name || "")
        setError("different_box")
        return
      }
      const next = applyAddressEdit(savedConnections(), connection.box_id, {
        addr: a,
        token: tk,
        boxName: probed.box_name,
        lastChecked: new Date().toISOString(),
      })
      saveConnections(next)
      onSaved()
      onOpenChange(false)
      // 改的正好是**当前正在用的**那条：页面自己的 SSE/WS 还连在旧地址上，
      // 只有整页重载才会干净地指过去（与来源切换器 pick() 同一个理由）。
      // 这种情况下不放 toast —— 重载会把它吞掉，重载本身即是反馈。
      if (getActiveSourceId() === connection.box_id) {
        window.location.reload()
        return
      }
      toast.success(t("settings.pairing.editAddrOk"))
    } catch (err) {
      setError((err as { code?: EditError }).code || "unreachable")
    } finally {
      setSaving(false)
    }
  }

  const ready = addr.trim().length > 0 && token.trim().length > 0

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="pm-connect-dialog">
        <DialogHeader>
          <DialogKicker>{t("settings.pairing.gateKicker")}</DialogKicker>
          <DialogTitle>{t("settings.pairing.editAddrTitle")}</DialogTitle>
          <DialogDescription>{t("settings.pairing.editAddrDesc")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1">
            <Input
              value={addr}
              onChange={(e) => {
                setAddr(e.target.value)
                setError(null)
              }}
              placeholder={t("settings.pairing.addressPlaceholder")}
            />
          </div>
          <div className="space-y-1">
            {/* 密码型：值得看一眼有没有粘错，但不该明文摊在屏幕上。
                校验必须要它，所以预填；要换钥匙就整段替换。 */}
            <Input
              type="password"
              value={token}
              onChange={(e) => {
                setToken(e.target.value)
                setError(null)
              }}
              placeholder={t("settings.pairing.tokenPlaceholder")}
            />
            <p className="pm-settings-provider-meta leading-5">
              {t("settings.pairing.editAddrTokenHint")}
            </p>
          </div>
          {error && (
            <div className="text-xs text-red-500">
              {error === "different_box"
                ? t("settings.pairing.errDifferentBox", {
                    name: otherBoxName || t("settings.pairing.genericBox"),
                  })
                : error === "unreachable"
                  ? t("settings.pairing.errUnreachable")
                  : error === "invalid_token"
                    ? t("settings.pairing.errInvalidToken")
                    : t("settings.pairing.errGeneric")}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="default"
            size="sm"
            disabled={!ready || saving}
            onClick={() => {
              void handleSave()
            }}
          >
            {saving ? t("settings.pairing.testing") : t("common.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
