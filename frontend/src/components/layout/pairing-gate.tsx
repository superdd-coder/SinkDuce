import { useEffect, useState } from "react"
import { KeyRound } from "lucide-react"

import { parsePairLink } from "@/api/pairing"
import { setGateKey } from "@/api/origin"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { useT } from "@/i18n/use-t"

/** CONN-6 钥匙门：全局门卫返回 401 pairing_required 时，引导用户粘贴 API Key。 */
export function PairingGate() {
  const t = useT()
  const [show, setShow] = useState(false)
  const [key, setKey] = useState("")
  const [badLink, setBadLink] = useState(false)

  useEffect(() => {
    const handler = () => setShow(true)
    window.addEventListener("sinkduce:pairing-required", handler)
    return () => window.removeEventListener("sinkduce:pairing-required", handler)
  }, [])

  const submit = () => {
    const raw = key.trim()
    if (!raw) return
    // 支持整条配对链接：从中解出 token（页面本就由盒子伺服，addr 无需理会）。
    const parsed = parsePairLink(raw)
    if (parsed) {
      if (!parsed.token) {
        setBadLink(true)
        return
      }
      setGateKey(parsed.token)
    } else {
      setGateKey(raw)
    }
    window.location.reload() // 整页刷新：所有请求干净地携带钥匙
  }

  return (
    <Dialog open={show} onOpenChange={setShow}>
      <DialogContent className="pm-pairing-gate" showCloseButton={false}>
        <DialogHeader className="pm-dialog-header--premium">
          <DialogKicker>{t("settings.pairing.gateKicker")}</DialogKicker>
          <DialogTitle>{t("settings.pairing.gateTitle")}</DialogTitle>
          <DialogDescription>{t("settings.pairing.gateDesc")}</DialogDescription>
        </DialogHeader>

        <Input
          placeholder={t("settings.pairing.gatePlaceholder")}
          value={key}
          autoFocus
          onChange={(e) => {
            setKey(e.target.value)
            setBadLink(false)
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit()
          }}
        />
        {badLink && (
          <p className="text-xs text-red-500">{t("settings.pairing.gateBadLink")}</p>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => setShow(false)}>
            {t("common.cancel")}
          </Button>
          <Button disabled={!key.trim()} onClick={submit}>
            <KeyRound className="h-3.5 w-3.5" strokeWidth={2} />
            {t("settings.pairing.gateUnlock")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
