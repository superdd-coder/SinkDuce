import { useState } from "react"
import { QRCodeSVG } from "qrcode.react"
import { toast } from "sonner"

import {
  issuePairingToken,
  type IssuedPairingToken,
  type PairingBoxInfo,
} from "@/api/pairing"
import { RolePicker } from "#edition/role-picker"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { FieldLabel } from "@/components/ui/field-label"
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
import { cn } from "@/lib/utils"
import { CopyBtn } from "./copy-button"
import { defaultShareAddr, shareUrl } from "./pairing-shared"

/** 有效期快捷档；"custom" = 用输入框自定义天数。 */
type TtlChoice = "never" | 7 | 30 | "custom"
const TTL_PRESETS: Exclude<TtlChoice, "custom">[] = ["never", 7, 30]

interface CreateApiKeyDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  box: PairingBoxInfo | null
  /** 容器部署：自动检测地址不可信 → 不出二维码/配对链接，只发 API Key 本体。 */
  inContainer: boolean
  sharePort: string
  /** 创建成功后通知父级刷新钥匙列表。 */
  onIssued: () => void
}

/**
 * 创建 API Key（对齐 LLM 控制台心智）：起名 → 创建 → 弹窗内展示一次。
 * 展示态非容器给二维码 + 配对链接 + Key 全文；容器只给 Key 全文。
 * 关闭后 Key 永不可见（后端只存 hint）。
 */
export function CreateApiKeyDialog({ open, onOpenChange, box, inContainer, sharePort, onIssued }: CreateApiKeyDialogProps) {
  const t = useT()
  const [step, setStep] = useState<"form" | "reveal">("form")
  const [name, setName] = useState("")
  const [ttlChoice, setTtlChoice] = useState<TtlChoice>("never")
  // RBAC：这把钥匙的角色。默认 member（最小权限）。
  const [role, setRole] = useState("member")
  const [customDays, setCustomDays] = useState("")
  const [issuing, setIssuing] = useState(false)
  const [issued, setIssued] = useState<IssuedPairingToken | null>(null)
  const [issuedAddr, setIssuedAddr] = useState("")

  // 重置策略：父级在每次点「创建」时递增 key 强制重挂载本组件。受控 Dialog 在
  // 父组件直接置 open=true 时**不会回调 onOpenChange**（它只回报用户交互），
  // 所以「打开时重置」挂在这里永远不跑；挂 effect 又会撞 set-state-in-effect。
  // 重挂载让 useState 默认值天然是全新表单，且关闭时 key 不变、退场动画照常。
  const handleCreate = async () => {
    const n = name.trim()
    if (!n) {
      // Input 必填 + 按钮禁用双保险，这里兜住回车直提
      return
    }
    const parsedCustom = Number.parseInt(customDays, 10)
    if (ttlChoice === "custom" && (!Number.isFinite(parsedCustom) || parsedCustom < 1)) {
      return // 自定义天数非法：按钮已禁用，这里兜住回车直提
    }
    const ttlDays = ttlChoice === "never" ? null : ttlChoice === "custom" ? parsedCustom : ttlChoice
    setIssuing(true)
    try {
      const result = await issuePairingToken(n, box?.box_id, ttlDays, role)
      const firstAddr =
        box?.advertised_addr ||
        (box?.addresses[0] ? `http://${box.addresses[0]}:${sharePort}` : window.location.origin)
      setIssuedAddr(defaultShareAddr() || firstAddr)
      setIssued(result)
      setStep("reveal")
      onIssued()
    } catch (err) {
      const code = (err as { code?: string }).code
      if (code === "invalid_ttl") {
        toast.error(t("settings.pairing.invalidTtl"))
      } else if (code === "pairing_required") {
        toast.error(t("settings.pairing.needOwnerKey"))
      } else {
        toast.error(t("settings.pairing.issueFailed"))
      }
    } finally {
      setIssuing(false)
    }
  }

  const url = issued && box ? shareUrl(issuedAddr, box, issued.token, sharePort) : ""
  const customDaysValid =
    Number.isFinite(Number.parseInt(customDays, 10)) && Number.parseInt(customDays, 10) >= 1

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="pm-apikey-dialog">
        <DialogHeader>
          <DialogKicker>{t("settings.pairing.gateKicker")}</DialogKicker>
          <DialogTitle>
            {step === "form" ? t("settings.pairing.createKey") : t("settings.pairing.keyCreatedTitle")}
          </DialogTitle>
          {step === "form" && <DialogDescription>{t("settings.pairing.createKeyDesc")}</DialogDescription>}
        </DialogHeader>

        {step === "form" ? (
          <>
            <div className="space-y-1.5">
              <Input
                value={name}
                autoFocus
                placeholder={t("settings.pairing.keyName")}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && name.trim() && !issuing) {
                    void handleCreate()
                  }
                }}
              />
            </div>
            <div className="space-y-1.5">
              <FieldLabel>{t("settings.pairing.expiryLabel")}</FieldLabel>
              <div className="flex flex-wrap items-center gap-1.5">
                {TTL_PRESETS.map((days) => (
                  <button
                    key={String(days)}
                    type="button"
                    className={cn(
                      "px-2.5 py-1 rounded-md text-xs leading-5 transition-colors",
                      ttlChoice === days
                        ? "bg-[var(--pm-ink)] text-[var(--pm-on)]"
                        : "bg-black/5 text-[var(--pm-text)] hover:bg-black/10",
                    )}
                    onClick={() => {
                      setTtlChoice(days)
                      setCustomDays("")
                    }}
                  >
                    {days === "never"
                      ? t("settings.pairing.expiryNever")
                      : t("settings.pairing.expiryDays", { n: String(days) })}
                  </button>
                ))}
                <Input
                  className="h-7 w-28 text-xs"
                  type="number"
                  min={1}
                  placeholder={t("settings.pairing.expiryCustom")}
                  value={customDays}
                  onChange={(e) => {
                    setCustomDays(e.target.value)
                    setTtlChoice("custom")
                  }}
                />
              </div>
            </div>
            <RolePicker value={role} onChange={setRole} />
            <DialogFooter>
              <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
                {t("common.cancel")}
              </Button>
              <Button
                variant="default"
                size="sm"
                disabled={!name.trim() || issuing || (ttlChoice === "custom" && !customDaysValid)}
                onClick={() => { void handleCreate() }}
              >
                {issuing ? t("settings.pairing.creating") : t("settings.pairing.createKey")}
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            {issued && !inContainer && box && (
              <div className="flex items-start gap-3 min-w-0">
                <div className="rounded-md bg-white p-1.5 shrink-0 shadow-[var(--pm-shadow-sm)]">
                  <QRCodeSVG value={url} size={112} />
                </div>
                <div className="flex-1 min-w-0 space-y-2.5">
                  <div className="min-w-0">
                    <FieldLabel>{t("settings.pairing.addresses")}</FieldLabel>
                    <Input value={issuedAddr} onChange={(e) => setIssuedAddr(e.target.value)} />
                  </div>
                  <div className="min-w-0">
                    <FieldLabel>{t("settings.pairing.shareUrl")}</FieldLabel>
                    <div className="flex items-center gap-1 min-w-0">
                      <code className="text-xs flex-1 min-w-0 truncate font-mono">{url}</code>
                      <CopyBtn text={url} label={t("settings.pairing.shareUrl")} />
                    </div>
                  </div>
                </div>
              </div>
            )}
            {issued && (
              <div className="space-y-1.5 min-w-0">
                <FieldLabel>{t("settings.pairing.keyLabel")}</FieldLabel>
                <div className="flex items-center gap-1 rounded-md bg-white px-2.5 py-2 shadow-[var(--pm-shadow-sm)] min-w-0">
                  <code className="text-xs flex-1 min-w-0 truncate font-mono">{issued.token}</code>
                  <CopyBtn text={issued.token} label={t("settings.pairing.keyLabel")} />
                </div>
                {inContainer && (
                  <div className="rounded-md bg-white px-3 py-2.5 shadow-[var(--pm-shadow-sm)]">
                    <p className="pm-settings-provider-meta whitespace-normal">
                      {t("settings.pairing.dockerAddrHint", { port: sharePort })}
                    </p>
                  </div>
                )}
                <p className="text-xs text-amber-600">{t("settings.pairing.keyCreated")}</p>
              </div>
            )}
            <DialogFooter>
              <Button variant="default" size="sm" onClick={() => onOpenChange(false)}>
                {t("settings.pairing.done")}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
