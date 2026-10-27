import { useState } from "react"
import { toast } from "sonner"

import { saveConnections, savedConnections } from "@/api/origin"
import { parsePairLink, testRemoteBox } from "@/api/pairing"
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

interface ConnectBoxDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 连接成功（已写入连接清单）后通知父级刷新。 */
  onConnected: () => void
}

type TestError = "unreachable" | "invalid_token" | "http_error" | null

/**
 * 连接其他盒子：顶部可选粘贴 sinkduce:// 配对链接自动回填；目标地址 + API Key
 * 均必填（未填齐禁用连接按钮）。连接 = testRemoteBox 探活 + 写入连接清单。
 */
export function ConnectBoxDialog({ open, onOpenChange, onConnected }: ConnectBoxDialogProps) {
  const t = useT()
  const [link, setLink] = useState("")
  const [addr, setAddr] = useState("")
  const [token, setToken] = useState("")
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState<TestError>(null)

  // 重置策略：父级每次点「连接」递增 key 强制重挂载（受控 Dialog 父置 open=true
  // 不回调 onOpenChange，「打开时重置」挂这里永远不跑；重挂载=useState 天然全新）。

  const handleLinkChange = (v: string) => {
    setLink(v)
    const parsed = parsePairLink(v.trim())
    if (parsed?.addr || parsed?.token) {
      if (parsed.addr) setAddr(parsed.addr)
      if (parsed.token) setToken(parsed.token)
      setError(null)
    }
  }

  const handleConnect = async () => {
    let a = addr.trim()
    let tk = token.trim()
    // 兜底：用户把整条链接粘进了地址栏也照常解析
    const fromAddr = parsePairLink(a)
    if (fromAddr?.addr) {
      a = fromAddr.addr
      if (!tk && fromAddr.token) tk = fromAddr.token
      setAddr(a)
      setToken(tk)
    }
    if (!a || !tk) return
    if (!/^https?:\/\//i.test(a)) a = `http://${a}`
    setTesting(true)
    setError(null)
    try {
      const remote = await testRemoteBox(a, tk)
      const next = [
        ...savedConnections().filter((c) => c.box_id !== remote.box_id),
        {
          addr: a.trim().replace(/\/+$/, ""),
          token: tk.trim(),
          box_id: remote.box_id,
          box_name: remote.box_name,
          last_checked: new Date().toISOString(),
        },
      ]
      saveConnections(next)
      onConnected()
      onOpenChange(false)
      toast.success(t("settings.pairing.testOk", { name: remote.box_name }))
    } catch (err) {
      setError((err as { code?: TestError }).code || "unreachable")
    } finally {
      setTesting(false)
    }
  }

  const ready = addr.trim().length > 0 && token.trim().length > 0

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="pm-connect-dialog">
        <DialogHeader>
          <DialogKicker>{t("settings.pairing.gateKicker")}</DialogKicker>
          <DialogTitle>{t("settings.pairing.connectTitle")}</DialogTitle>
          <DialogDescription>{t("settings.pairing.connectDesc")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1">
            <Input
              value={link}
              onChange={(e) => handleLinkChange(e.target.value)}
              placeholder={t("settings.pairing.pasteLink")}
            />
          </div>
          <div className="space-y-1">
            <Input
              value={addr}
              onChange={(e) => { setAddr(e.target.value); setError(null) }}
              placeholder={t("settings.pairing.addressPlaceholder")}
            />
          </div>
          <div className="space-y-1">
            <Input
              value={token}
              onChange={(e) => { setToken(e.target.value); setError(null) }}
              placeholder={t("settings.pairing.tokenPlaceholder")}
            />
          </div>
          {error && (
            <div className="text-xs text-red-500">
              {error === "unreachable"
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
          <Button variant="default" size="sm" disabled={!ready || testing} onClick={() => { void handleConnect() }}>
            {testing ? t("settings.pairing.testing") : t("settings.pairing.connect")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
