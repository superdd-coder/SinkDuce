import { HelpHint } from "@/components/shared/help-hint"
import { useEffect, useState } from "react"
import { Check, Copy } from "lucide-react"
import { toast } from "sonner"
import { getHealth, type HealthInfo } from "@/api/client"
import { getPairingBox } from "@/api/pairing"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { copyText } from "@/lib/clipboard"
import { buildMcpJson, mcpLoopbackUrl } from "@/lib/mcp-config"
import { useT } from "@/i18n/use-t"

interface ConnectionInfo {
  host: string
  port: number
  mcpUrl: string
  desktop: boolean
}

/**
 * Agent 该连的地址**恒为回环**（2026-09-18）—— 别用 `/health` 上报的 host。
 *
 * MCP 只经回环可达（后端不变式，见 `src/pairing/middleware.py`）。若用户把
 * `SINKDUCE_HOST` 设成某个网卡地址，那 host 不是回环，照它生成的配置会被自家闸门
 * 拒掉；桌面壳端口常是 18910，所以**端口**仍以 `/health` 上报的为准。
 */
function connectionFromHealth(health?: HealthInfo | null): ConnectionInfo {
  const port =
    typeof health?.port === "number" && health.port > 0 ? health.port : 18900
  const mcpUrl = mcpLoopbackUrl(port)
  return {
    host: new URL(mcpUrl).hostname,
    port,
    mcpUrl,
    desktop: health?.desktop === true,
  }
}

function CopyBtn({ text, label }: { text: string; label: string }) {
  const t = useT()
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await copyText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      toast.error(t("settings.couldNotCopy", { label }))
    }
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className={cn("pm-settings-copy", copied && "is-copied")}
      onClick={() => { void handleCopy() }}
    >
      {copied ? (
        <>
          <Check className="h-3.5 w-3.5" strokeWidth={2} />
          {t("common.copied")}
        </>
      ) : (
        <>
          <Copy className="h-3.5 w-3.5" strokeWidth={1.75} />
          {t("common.copy")}
        </>
      )}
    </Button>
  )
}

export function ConnectionInfoCard() {
  const t = useT()
  const [info, setInfo] = useState<ConnectionInfo>(() => connectionFromHealth())
  // 盒子名进 server key（`SinkDuce-<盒子名>`）：将来 Agent 可能连多台设备的 Hub，
  // 主流客户端把 server 名拼进工具名，名字不同才不会把两台的答案混在一起。
  const [box, setBox] = useState<{ box_name: string; box_id: string } | null>(null)

  useEffect(() => {
    let cancelled = false
    getHealth()
      .then((health) => {
        if (!cancelled) setInfo(connectionFromHealth(health))
      })
      .catch(() => {
        if (!cancelled) setInfo(connectionFromHealth())
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    getPairingBox()
      .then((b) => {
        if (!cancelled) setBox({ box_name: b.box_name, box_id: b.box_id })
      })
      .catch(() => {
        /* 拿不到就退回裸 SinkDuce（mcpServerKey 自带回退） */
      })
    return () => {
      cancelled = true
    }
  }, [])

  const cfg = buildMcpJson({
    boxName: box?.box_name ?? "",
    boxId: box?.box_id ?? "",
    port: info.port,
  })

  return (
    <section className="pm-settings-section">
      <div className="pm-settings-card">
        <div className="pm-settings-card-head-text">
          <h2 className="pm-settings-card-kicker">MCP<HelpHint>{t("settings.mcpHelp")}</HelpHint></h2>
        </div>

        <div className="pm-settings-conn-rows">
          <div className="pm-settings-conn-row">
            <span className="pm-label">{t("settings.port")}</span>
            <span className="pm-settings-conn-value">{info.port}</span>
          </div>
          <div className="pm-settings-conn-row">
            <span className="pm-label">{t("settings.runtime")}</span>
            <span className="pm-settings-conn-value">
              {info.desktop ? t("settings.desktopApp") : t("settings.dockerLocal")}
            </span>
          </div>
          <div className="pm-settings-conn-row">
            <span className="pm-label">{t("settings.mcpUrl")}</span>
            <div className="pm-settings-conn-url">
              <span className="pm-settings-conn-value pm-settings-conn-url-text">
                {info.mcpUrl}
              </span>
              <CopyBtn text={info.mcpUrl} label={t("settings.mcpUrl")} />
            </div>
          </div>
        </div>

        <p className="pm-meta">
          {info.desktop
            ? t("settings.mcpKeepRunning")
            : t("settings.mcpDockerHelp")}
        </p>

        <div className="pm-settings-conn-snippet">
          <div className="pm-settings-conn-snippet-head">
            <h3 className="pm-settings-subhead">{t("settings.claudeCursor")}<HelpHint label={t("settings.claudeCursor")}>{t("settings.mcpConfigHint")}</HelpHint></h3>
            <CopyBtn text={cfg.json} label={t("settings.claudeCursor")} />
          </div>
          <pre className="pm-settings-conn-pre">{cfg.json}</pre>
          <p className="pm-meta">
            {t("settings.mcpLoopbackOnly")}
          </p>
        </div>
      </div>
    </section>
  )
}
