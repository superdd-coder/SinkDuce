import { useEffect, useState } from "react"
import { ChevronRight, MonitorDown, Plus, RefreshCw } from "lucide-react"

import {
  getActiveSourceId,
  LOCAL_SOURCE,
  savedConnections,
  setActiveSourceId,
  type SavedConnection,
} from "@/api/origin"
import { patchShellSettings } from "@/api/shell-settings"
import { useAppStore, type SourceHealthState } from "@/stores/app-store"
import { desktopShellAvailable, restartDesktopApp } from "@/lib/desktop-restart"
import { evaluateRemoteGate } from "@/lib/remote-gate"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"
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
import { ConnectBoxDialog } from "@/components/llm-provider/connect-box-dialog"

/**
 * 远程模式「先去配对」引导态。
 *
 * 远程模式下这台 App 只是一个入口壳：没有任何可用的已配对盒子（没配过 /
 * 没选中 / 选中的那台离线或钥匙被吊销）时，模态引导盖住主界面——而不是
 * 把用户扔进一个所有请求都在静默报错的空壳。盒子恢复在线后自动消失。
 *
 * 与钥匙门 PairingGate 是两回事：那是「缺本机钥匙」，这是「没有可去的远端」。
 * 形态对齐 premium 弹窗配方（pm-dialog-header--premium）；盒子行是独立软卡
 * （.pm-remote-gate-row，来源药丸行同基因：状态点光晕 + 状态词 + 悬停绿轨）。
 * footer 按钮自适应宽度横排——不要给它们 w-full（sm:flex-row 下会横向溢出，
 * 2026-09-22 实测）。
 */
export function RemoteModeGate() {
  const t = useT()
  const remoteShellMode = useAppStore((s) => s.remoteShellMode)
  const sourceHealth = useAppStore((s) => s.sourceHealth)
  const remoteGateReason = useAppStore((s) => s.remoteGateReason)
  const setRemoteGateReason = useAppStore((s) => s.setRemoteGateReason)
  // 容器部署：开关归 .env 管，界面上只显示模式 + 指路，不给关闭按钮。
  const container = useAppStore((s) => s.containerDeployment)

  const [activeId] = useState(getActiveSourceId())
  const [conns, setConns] = useState<SavedConnection[]>(savedConnections)
  const [connectOpen, setConnectOpen] = useState(0) // 递增 key：每次点击强制重挂载弹窗
  const [connectShown, setConnectShown] = useState(false)
  const [leaving, setLeaving] = useState(false) // 已请求关闭远程模式，等重启
  const [busy, setBusy] = useState(false)

  const evaluated = evaluateRemoteGate({
    connections: conns,
    activeSourceId: activeId,
    health: sourceHealth,
  })
  const reason = remoteGateReason ?? evaluated.reason
  const show = remoteShellMode && (evaluated.show || remoteGateReason !== null)

  // 活动盒子恢复在线（探活循环翻绿）：清掉失联标记，引导态自然消失。
  useEffect(() => {
    if (
      remoteGateReason &&
      activeId !== LOCAL_SOURCE &&
      sourceHealth[activeId] === "online"
    ) {
      setRemoteGateReason(null)
    }
  }, [remoteGateReason, activeId, sourceHealth, setRemoteGateReason])

  if (!show) return null

  // unreachable/revoked 才是「出事了」：红色。没选中/没配过只是指路，不着火色。
  const severe = reason === "unreachable" || reason === "revoked"

  const switchTo = (boxId: string) => {
    setActiveSourceId(boxId)
    window.location.reload()
  }

  const closeRemoteMode = async () => {
    setBusy(true)
    try {
      await patchShellSettings({ remote_mode: false })
      setLeaving(true)
    } catch {
      /* 瘦壳不可达等：按钮保持可重试 */
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={show}>
      <DialogContent className="pm-remote-gate sm:max-w-md" showCloseButton={false}>
        <DialogHeader className="pm-dialog-header--premium">
          <DialogKicker>{t("remoteMode.gate.kicker")}</DialogKicker>
          <DialogTitle>{t("remoteMode.gate.title")}</DialogTitle>
          <DialogDescription>{t("remoteMode.gate.desc")}</DialogDescription>
        </DialogHeader>

        {reason && (
          <p
            className={cn(
              "-mt-1.5 text-[12px] leading-relaxed",
              severe ? "text-red-500" : "text-muted-foreground",
            )}
          >
            {t(`remoteMode.gate.reason.${reason}`)}
          </p>
        )}

        {conns.length > 0 && (
          <div className="pm-remote-gate-list">
            {conns.map((c) => {
              const st: SourceHealthState = sourceHealth[c.box_id] ?? "unknown"
              const active = c.box_id === activeId
              const pickable = !active && (st === "online" || st === "unknown")
              // 状态词借来源切换器的既有键（在线/离线/钥匙失效）
              const word =
                st === "online"
                  ? t("shell.switcher.online")
                  : st === "offline"
                    ? t("shell.switcher.offline")
                    : st === "revoked"
                      ? t("shell.switcher.revoked")
                      : null
              return (
                <button
                  key={c.box_id}
                  type="button"
                  disabled={!pickable}
                  data-pickable={pickable}
                  onClick={() => switchTo(c.box_id)}
                  className={cn(
                    "pm-remote-gate-row",
                    active && "is-active",
                    !pickable && "cursor-default",
                  )}
                >
                  <span
                    className={cn(
                      "pm-source-dot",
                      st === "online" && "is-online",
                      st === "offline" && "is-offline",
                      st === "revoked" && "is-revoked",
                    )}
                    aria-hidden
                  />
                  <span className="pm-remote-gate-row__text">
                    <span className="pm-remote-gate-row__name">{c.box_name}</span>
                    <span className="pm-remote-gate-row__addr">
                      {c.addr.replace(/\/+$/, "")}
                    </span>
                  </span>
                  <span className="pm-remote-gate-row__side">
                    {word && (
                      <span
                        className={cn(
                          "pm-remote-gate-row__word",
                          st === "online" && "is-online",
                          st === "revoked" && "is-revoked",
                        )}
                      >
                        {word}
                      </span>
                    )}
                    {pickable && (
                      <ChevronRight className="pm-remote-gate-row__go" strokeWidth={1.75} />
                    )}
                  </span>
                </button>
              )
            })}
          </div>
        )}

        {leaving ? (
          <div className="pm-remote-restart-note">
            <p>{t("remoteMode.gate.restartRequired")}</p>
            <div className="pm-remote-restart-note__actions">
              {desktopShellAvailable() && (
                <Button size="sm" onClick={() => void restartDesktopApp()}>
                  <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} />
                  {t("remoteMode.gate.restartNow")}
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => setLeaving(false)}>
                {t("common.cancel")}
              </Button>
            </div>
            {!desktopShellAvailable() && (
              <p className="mt-1 text-xs text-muted-foreground">
                {t("remoteMode.gate.restartManual")}
              </p>
            )}
          </div>
        ) : container ? (
          <p className="rounded-lg border border-border bg-muted/40 px-3 py-2.5 text-xs leading-relaxed text-muted-foreground">
            {t("remoteMode.containerHint")}
          </p>
        ) : null}

        {!leaving && !container && (
          <DialogFooter className="mt-1 w-full">
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => void closeRemoteMode()}>
              <MonitorDown className="h-3.5 w-3.5" strokeWidth={1.75} />
              {t("remoteMode.gate.closeMode")}
            </Button>
            <Button
              onClick={() => {
                setConnectOpen((n) => n + 1)
                setConnectShown(true)
              }}
            >
              <Plus className="h-3.5 w-3.5" strokeWidth={1.75} />
              {t("remoteMode.gate.connect")}
            </Button>
          </DialogFooter>
        )}

        {connectShown && (
          <ConnectBoxDialog
            key={connectOpen}
            open={connectShown}
            onOpenChange={(open) => {
              if (!open) setConnectShown(false)
            }}
            onConnected={() => {
              setConns(savedConnections())
              setConnectShown(false)
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
