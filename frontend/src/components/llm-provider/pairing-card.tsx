import { HelpHint } from "@/components/shared/help-hint"
import { useCallback, useEffect, useState } from "react"
import { KeyRound, Pencil, Trash2 } from "lucide-react"
import { toast } from "sonner"

import {
  getPairingBox,
  getStoredOwnerKey,
  storeOwnerKey,
  listPairingTokens,
  revokePairingToken,
  updatePairingBox,
  type PairingBoxInfo,
  type PairingTokenItem,
} from "@/api/pairing"
import { getHealth } from "@/api/client"
import { removeConnection, savedConnections, type SavedConnection } from "@/api/origin"
import { Button } from "@/components/ui/button"
import { useAppStore } from "@/stores/app-store"
import { Input } from "@/components/ui/input"
import { FieldLabel } from "@/components/ui/field-label"
import { useT } from "@/i18n/use-t"
import { cn } from "@/lib/utils"
import { CreateApiKeyDialog } from "./create-api-key-dialog"
import { ConnectBoxDialog } from "./connect-box-dialog"
import { EditBoxAddressDialog } from "./edit-box-address-dialog"
import { useRelTime } from "./pairing-shared"
import { TokenRoleControl } from "#edition/token-role-control"

/** 有效期剩余天数在取数时刻计算（渲染期调时钟会被 react-hooks/purity 拦下）；null = 永久。 */
function withDaysLeft(tokens: PairingTokenItem[]): (PairingTokenItem & { days_left: number | null })[] {
  const nowMs = Date.now()
  return tokens.map((tk) => ({
    ...tk,
    days_left: tk.expires_at
      ? Math.ceil((new Date(tk.expires_at).getTime() - nowMs) / 86_400_000)
      : null,
  }))
}

/**
 * 「局域网共享」大卡（2026-09-16 API Key 重构 + 用户三轮打磨）：
 * - 子区「分享本机」：创建 API Key 弹窗（对齐 LLM 控制台），列表只留名称+最后活跃+移除；
 *   容器部署下自动检测地址不可信 → 只显示对外端口，无二维码/链接，IP 指引在创建弹窗内。
 * - 子区「已连接的设备」：只列已配对设备；「连接」弹窗（可选粘贴配对链接自动回填，
 *   地址 + API Key 必填）。
 */
export function PairingCard() {
  const t = useT()
  const rel = useRelTime()

  // ── 分享本机（本机是被连的一方）──
  const [box, setBox] = useState<PairingBoxInfo | null>(null)
  const [tokens, setTokens] = useState<(PairingTokenItem & { days_left: number | null })[]>([])
  // 已连接设备的实时在线状态（探测循环维护；与右上角药丸同一份事实源）
  const sourceHealth = useAppStore((s) => s.sourceHealth)
  // 分享给其他设备用的端口：容器部署的对外口（18901）与本机口（18900）不同，
  // 以 /health 上报的 external_port 为准；桌面/裸机没有对外口，用当前地址栏端口。
  const [sharePort, setSharePort] = useState(window.location.port || "18900")
  // 容器形态显式信号（/health.in_container）：切手动地址形态，不出二维码/链接。
  const [inContainer, setInContainer] = useState(false)
  const [editingName, setEditingName] = useState(false)
  const [nameDraft, setNameDraft] = useState("")
  const [boxLocked, setBoxLocked] = useState(false)
  const [lastKeyArmed, setLastKeyArmed] = useState(false)
  // 移除「已连接的设备」的二次确认（arm 模式同 lastKey）：钥匙关闭后永不可见，
  // 误删 = 对方必须重新配对全流程，比误删最后一把 Key 更贵，必须有确认。
  const [removeArmedId, setRemoveArmedId] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  // 受控 Dialog 父置 open=true 不回调 onOpenChange，弹窗内「打开时重置」不生效；
  // 改用递增序号当 key：每次点按钮强制重挂载，关闭时 key 不变、退场动画照常。
  const [createSeq, setCreateSeq] = useState(0)

  // ── 已连接的设备（我是发起方）──
  const [connections, setConnections] = useState<SavedConnection[]>([])
  const [connectOpen, setConnectOpen] = useState(false)
  const [connectSeq, setConnectSeq] = useState(0)
  // 改地址：受控 Dialog 每次点开递增 seq 强制重挂载 → 弹窗内 useState 以该条记录为初值，
  // 不依赖 onOpenChange 回调（父置 open=true 时它不触发，见上一条注释的同一个坑）。
  const [editTarget, setEditTarget] = useState<SavedConnection | null>(null)
  const [editSeq, setEditSeq] = useState(0)

  const refresh = useCallback(async (boxId?: string) => {
    let current: PairingBoxInfo | null = null
    try {
      current = await getPairingBox(boxId)
      setBox(current)
      setBoxLocked(false)
      // 自愈：用通配钥匙成功的，把它归位到正确的 box_id 名下
      if (!boxId && current?.box_id) {
        const held = getStoredOwnerKey()
        if (held) storeOwnerKey(current.box_id, held)
      }
    } catch {
      setBox(current)
      setBoxLocked(true) // 设备已有主人钥匙且本浏览器没有 → 只读提示态
    }
    try {
      const tl = await listPairingTokens(boxId || current?.box_id)
      setTokens(withDaysLeft(tl.tokens))
    } catch {
      setTokens([])
    }
    setConnections(savedConnections())
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // 对外端口 + 容器信号：容器部署的对外口（/health.external_port）优先，退回地址栏端口。
  useEffect(() => {
    let alive = true
    getHealth()
      .then((h) => {
        if (!alive) return
        if (typeof h.external_port === "number" && h.external_port > 0) {
          setSharePort(String(h.external_port))
        }
        setInContainer(h.in_container === true)
      })
      .catch(() => {
        /* /health 不可达：保持地址栏端口与非容器形态 */
      })
    return () => {
      alive = false
    }
  }, [])

  const activeKeys = tokens.filter((tk) => !tk.revoked_at && (tk.days_left === null || tk.days_left > 0))
  const visibleKeys = tokens.filter((tk) => !tk.revoked_at)

  const handleRename = async () => {
    const name = nameDraft.trim()
    if (!name || !box) {
      setEditingName(false)
      return
    }
    try {
      const updated = await updatePairingBox({ box_name: name }, box?.box_id)
      setBox((prev) => ({ ...(prev ?? { box_id: updated.box_id, addresses: [] }), ...updated }))
      toast.success(t("common.saved"))
    } catch {
      toast.error(t("settings.pairing.issueFailed"))
    }
    setEditingName(false)
  }

  const handleRemove = async (id: string) => {
    const target = tokens.find((tk) => tk.id === id)
    const isActiveTarget = !!target && !target.revoked_at && (target.days_left === null || target.days_left > 0)
    // 最后一把活跃 Key：移除后外部访问将全部关闭，需二次确认
    if (isActiveTarget && activeKeys.length === 1 && !lastKeyArmed) {
      setLastKeyArmed(true)
      toast.warning(t("settings.pairing.lastKeyWarn"))
      return
    }
    setLastKeyArmed(false)
    try {
      await revokePairingToken(id, box?.box_id)
      const tl = await listPairingTokens(box?.box_id)
      setTokens(withDaysLeft(tl.tokens))
      toast.success(t("settings.pairing.revoked"))
    } catch {
      toast.error(t("settings.pairing.revokeFailed"))
    }
  }

  return (
    <>
      <section className="pm-settings-section">
        <div className="pm-settings-card">
          <div className="pm-settings-card-head">
            <div className="pm-settings-card-head-text min-w-0">
              <h2 className="pm-settings-card-kicker">{t("settings.pairing.lanTitle")}<HelpHint label={t("settings.pairing.lanTitle")}>{t("settings.pairing.lanDesc")}</HelpHint></h2>
            </div>
          </div>

          <div className="pm-settings-card-body">
            {/* ── 分享本机：API Key（被连的一方）── */}
            <div className="pm-settings-subcard">
            <div className="flex items-center justify-between gap-3 pb-3">
              <h3 className="pm-settings-subhead">{t("settings.pairing.hostTitle")}</h3>
              <div className="flex items-center gap-2 shrink-0">
                <span className="pm-settings-provider-meta">
                  <KeyRound className="mr-1 inline h-3.5 w-3.5 align-[-2px]" strokeWidth={1.75} />
                  {t("settings.pairing.activeKeys", { used: String(activeKeys.length) })}
                </span>
                {!boxLocked && (
                  <Button
                  variant="default"
                  size="sm"
                  onClick={() => { setCreateSeq(s => s + 1); setCreateOpen(true) }}
                >
                    {t("settings.pairing.createKey")}
                  </Button>
                )}
              </div>
            </div>

            {/* 设备名 — 小标题 + 名称（悬停铅笔改名），旁边不放端口 */}
            <div className="pb-2">
              <FieldLabel>{t("settings.pairing.nameLabel")}</FieldLabel>
              {editingName ? (
                <div className="flex flex-wrap items-center gap-2">
                  <Input
                    className="max-w-48"
                    value={nameDraft}
                    autoFocus
                    onChange={(e) => setNameDraft(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") { void handleRename() } }}
                  />
                  <Button variant="secondary" size="sm" onClick={() => { void handleRename() }}>
                    {t("settings.pairing.saveName")}
                  </Button>
                </div>
              ) : (
                <div className="flex items-center gap-1">
                  <span className="pm-settings-provider-name">{box?.box_name || "…"}</span>
                  {!boxLocked && (
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      className="text-[var(--pm-faint)] hover:text-[var(--pm-ink)]"
                      title={t("settings.pairing.rename")}
                      onClick={() => { setNameDraft(box?.box_name || ""); setEditingName(true) }}
                    >
                      <Pencil className="h-3.5 w-3.5" strokeWidth={1.75} />
                    </Button>
                  )}
                </div>
              )}
            </div>

            {/* 地址与端口 — 独立一行；容器形态不展示自动检测地址，只给对外端口 */}
            {box && !boxLocked && (
              <div className="pb-4">
                <FieldLabel>
                  {inContainer ? t("settings.pairing.externalPort") : t("settings.pairing.addresses")}
                </FieldLabel>
                <div className="flex flex-wrap items-center gap-1.5">
                  {inContainer ? (
                    <span className="px-2 py-0.5 rounded-md bg-black/5 text-xs font-mono leading-5">
                      :{sharePort}
                    </span>
                  ) : (
                    <>
                      {box.addresses.length === 0 && (
                        <span className="pm-settings-provider-meta leading-5">
                          {t("settings.pairing.noAddresses")}
                        </span>
                      )}
                      {box.addresses.map((a) => (
                        <span key={a} className="px-2 py-0.5 rounded-md bg-black/5 text-xs font-mono leading-5">
                          {a}:{sharePort}
                        </span>
                      ))}
                    </>
                  )}
                </div>
              </div>
            )}

            {visibleKeys.length > 0 ? (
              <div className="divide-y">
                {visibleKeys.map((tk) => {
                  const isLastActive = activeKeys.length === 1 && activeKeys[0].id === tk.id
                  return (
                    <div key={tk.id} className="flex items-center gap-3 py-2.5">
                      <span className="pm-settings-provider-name flex-1 min-w-0 truncate leading-5">{tk.name}</span>
                      {tk.role && <TokenRoleControl id={tk.id} role={tk.role} boxId={box?.box_id}
                        onChanged={(role) => setTokens((prev) => prev.map((item) => item.id === tk.id ? { ...item, role } : item))} />}
                      {tk.days_left !== null && (
                        <span className={cn("pm-settings-provider-meta shrink-0 leading-5", tk.days_left <= 0 && "opacity-60")}>
                          {tk.days_left > 0
                            ? t("settings.pairing.expiresIn", { n: String(tk.days_left) })
                            : t("settings.pairing.expired")}
                        </span>
                      )}
                      <span className="pm-settings-provider-meta shrink-0 leading-5">
                        {t("settings.pairing.lastSeen")}: {rel(tk.last_seen)}
                      </span>
                      <Button
                        variant="ghost"
                        size="sm"
                        className={cn(isLastActive && lastKeyArmed && "text-red-500")}
                        onClick={() => { void handleRemove(tk.id) }}
                      >
                        <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} />
                        {isLastActive && lastKeyArmed ? t("settings.pairing.confirmRevoke") : t("settings.pairing.revoke")}
                      </Button>
                    </div>
                  )
                })}
              </div>
            ) : (
              !boxLocked && (
                <p className="pm-settings-provider-meta leading-5 py-2">{t("settings.pairing.noKeys")}</p>
              )
            )}

            {boxLocked && (
              <div className="rounded-lg border p-3 text-xs text-muted-foreground">
                {t("settings.pairing.boxLocked")}
              </div>
            )}
            </div>

            {/* ── 已连接的设备（我是发起方）── */}
            <div className="pm-settings-subcard">
            <div className="flex items-center justify-between gap-3 pb-2">
              <h3 className="pm-settings-subhead">{t("settings.pairing.clientTitle")}</h3>
              <Button variant="default" size="sm" onClick={() => { setConnectSeq(s => s + 1); setConnectOpen(true) }}>
                {t("settings.pairing.connect")}
              </Button>
            </div>

            {connections.length > 0 ? (
              <div className="divide-y">
                {connections.map((c) => (
                  <div key={c.box_id}>
                    <div className="flex items-center gap-3 py-2.5">
                      <span
                        className={cn("pm-source-dot", `is-${sourceHealth[c.box_id] ?? "unknown"}`)}
                        aria-hidden
                        title={t("settings.pairing.healthUnknown") }
                      />
                      <span className="pm-settings-provider-name shrink-0 min-w-0 truncate leading-5">{c.box_name}</span>
                      <span className="text-xs text-muted-foreground font-mono min-w-0 truncate flex-1 leading-5">{c.addr}</span>
                      <span className="pm-settings-provider-meta shrink-0 leading-5">
                        {t("settings.pairing.lastChecked")}: {rel(c.last_checked)}
                      </span>
                      <Button
                        variant="ghost"
                        size="sm"
                        title={t("settings.pairing.editAddrButton")}
                        onClick={() => {
                          setEditTarget(c)
                          setEditSeq((n) => n + 1)
                        }}
                      >
                        <Pencil className="h-3.5 w-3.5" strokeWidth={1.75} />
                        {t("settings.pairing.editAddrButton")}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className={cn(removeArmedId === c.box_id && "text-red-500")}
                        onClick={() => {
                          if (removeArmedId !== c.box_id) {
                            setRemoveArmedId(c.box_id)
                            return
                          }
                          setRemoveArmedId(null)
                          removeConnection(c.box_id)
                          setConnections(savedConnections())
                        }}
                      >
                        <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} />
                        {removeArmedId === c.box_id ? t("settings.pairing.confirmRemove") : t("settings.pairing.remove")}
                      </Button>
                    </div>
                    {/*
                      这里**故意没有 MCP 按钮**（2026-09-18 撤掉）。
                      MCP 只经回环可达（后端不变式），远端盒子的 /mcp 对网络返回 404，
                      所以「复制这台远端的 mcp.json」生成的东西**必然连不上**。
                      指向 Agent 的配置只有一份：本机 Hub 那份，在上面 MCP 卡片里。
                      Agent 连上 Hub 之后由 Hub 代为访问其他盒子 —— 见
                      development_doc/2026-09-18-mcp-hub-architecture-design.md。
                    */}
                  </div>
                ))}
              </div>
            ) : (
              <p className="pm-settings-provider-meta leading-5 py-2">{t("settings.pairing.clientEmpty")}</p>
            )}
            </div>
          </div>
        </div>
      </section>

      <CreateApiKeyDialog
        key={createSeq}
        open={createOpen}
        onOpenChange={setCreateOpen}
        box={box}
        inContainer={inContainer}
        sharePort={sharePort}
        onIssued={() => { void refresh(box?.box_id) }}
      />
      <ConnectBoxDialog
        key={connectSeq}
        open={connectOpen}
        onOpenChange={setConnectOpen}
        onConnected={() => setConnections(savedConnections())}
      />
      {editTarget && (
        <EditBoxAddressDialog
          key={editSeq}
          open
          onOpenChange={(o) => {
            if (!o) setEditTarget(null)
          }}
          connection={editTarget}
          onSaved={() => setConnections(savedConnections())}
        />
      )}
    </>
  )
}
