import { HelpHint } from "@/components/shared/help-hint"
import { useState } from "react"
import { RefreshCw } from "lucide-react"

import { useAppStore } from "@/stores/app-store"
import { patchShellSettings } from "@/api/shell-settings"
import { desktopShellAvailable, restartDesktopApp } from "@/lib/desktop-restart"
import { useT } from "@/i18n/use-t"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { SettingsSwitch } from "./settings-switch"

/**
 * 远程模式的极简设置页：只有界面语言、远程模式开关、开发者模式三项。
 *
 * 用户故事原话「Settings 只剩语言选择、远程模式的开关、开发者模式的开关」。
 * 这里没有 provider/嵌入/重排等任何配置卡——那些属于重后端，远程模式下
 * 它们配置在远端盒子上，不由入口壳管理。
 *
 * 语言卡的持久化行为由 store.setLocale 分支处理（写瘦壳而非远端盒子），
 * 本组件不感知差异。
 */
export function RemoteSettingsView() {
  const t = useT()
  const locale = useAppStore((s) => s.locale)
  const setLocale = useAppStore((s) => s.setLocale)
  const developerMode = useAppStore((s) => s.developerMode)
  const toggleDeveloperMode = useAppStore((s) => s.toggleDeveloperMode)
  // 容器部署：远程模式由 .env 决定，UI 只读（显示模式 + 指路 .env）。
  const container = useAppStore((s) => s.containerDeployment)

  const [leaving, setLeaving] = useState(false)
  const [busy, setBusy] = useState(false)

  const closeRemoteMode = async () => {
    setBusy(true)
    try {
      await patchShellSettings({ remote_mode: false })
      setLeaving(true)
    } catch {
      toast.error(t("remoteMode.settings.closeFailed"))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="pm-settings flex-1 min-h-0 overflow-y-auto">
      <div className="pm-settings-inner">
        <header className="pm-settings-mast">
          <h1 className="pm-settings-page-title">{t("remoteMode.settings.title")}</h1>
          <p className="pm-settings-page-desc">{t("remoteMode.settings.desc")}</p>
        </header>

        {/* 界面语言 */}
        <section className="pm-settings-section">
          <div className="pm-settings-card">
            <div className="pm-settings-card-head">
              <div className="pm-settings-card-head-text min-w-0">
                <h2 className="pm-settings-card-kicker">{t("settings.interfaceLanguage")}<HelpHint label={t("settings.interfaceLanguage")}>{t("settings.interfaceLanguageHelp")}</HelpHint></h2>
              </div>
              <DropdownSelect
                value={locale}
                onChange={(v) => setLocale(v === "zh-CN" ? "zh-CN" : "en")}
                options={[
                  { value: "en", label: t("settings.langEn") },
                  { value: "zh-CN", label: t("settings.langZhCN") },
                ]}
              />
            </div>
          </div>
        </section>

        {/* 远程模式 */}
        <section className="pm-settings-section">
          <div className="pm-settings-card">
            <div className="pm-settings-card-head">
              <div className="pm-settings-card-head-text min-w-0">
                <h2 className="pm-settings-card-kicker">{t("remoteMode.settings.modeTitle")}</h2>
                <p className="pm-settings-card-desc">
                  {container
                    ? t("remoteMode.containerHint")
                    : t("remoteMode.settings.modeDesc")}
                </p>
              </div>
              {container ? (
                <span className="shrink-0 rounded-full border border-border px-2.5 py-1 text-xs text-muted-foreground">
                  {t("remoteMode.settings.on")}
                </span>
              ) : (
                <SettingsSwitch
                  id="pm-remote-mode-off"
                  label={t("remoteMode.settings.modeTitle")}
                  checked
                  disabled={busy}
                  onCheckedChange={(next) => {
                    if (next) return // 远程模式下只会往外关（开了也没有本地后端可回去）
                    void closeRemoteMode()
                  }}
                />
              )}
            </div>
            {leaving && (
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
            )}
          </div>
        </section>

        {/* 开发者模式 */}
        <section className="pm-settings-section">
          <div className="pm-settings-card">
            <div className="pm-settings-card-head">
              <div className="pm-settings-card-head-text min-w-0">
                <h2 className="pm-settings-card-kicker">{t("settings.developerMode")}<HelpHint label={t("settings.developerMode")}>{t("settings.developerModeDesc")}</HelpHint></h2>
              </div>
              <SettingsSwitch
                id="pm-remote-dev"
                label={t("settings.developerMode")}
                checked={developerMode}
                onCheckedChange={() => toggleDeveloperMode()}
              />
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}

