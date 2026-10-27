import { request } from "./http"

/**
 * 瘦壳的壳级设置面（`/api/shell-settings`，仅本机回环可达）。
 *
 * 只在远程模式下存在（`src/remote_shell/app.py`）；正常模式打它会 404，
 * 调用方自行容错。白名单键仅 locale / remote_mode —— 这是壳自己的设置
 * （config.yaml 顶层），永远不要把 provider/密钥类配置塞进这条通道。
 */

export interface ShellSettings {
  locale: "en" | "zh-CN"
  remote_mode: boolean
}

export const getShellSettings = () => request<ShellSettings>("/shell-settings", { local: true })

export const patchShellSettings = (patch: Partial<ShellSettings>) =>
  request<ShellSettings>("/shell-settings", {
    local: true,
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  })
