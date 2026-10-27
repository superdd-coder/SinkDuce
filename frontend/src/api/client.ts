import { apiFetch, request } from "./http"
import { apiUrl, localApiUrl } from "@/api/origin"

// ── Health ──

export interface HealthInfo {
  status: string
  desktop?: boolean
  /** 远程模式瘦壳标志：只有 src/remote_shell 的 /health 会带（前端识别模式的唯一信号）。 */
  remote_shell?: boolean
  /** Dev-only: data/mock-update file present — force the update dialog. */
  mock_update?: boolean
  /** Advertised listen host for clients (wildcard binds become 127.0.0.1). */
  host?: string
  /** Actual API / MCP listen port (desktop is often 18910, not 18900). */
  port?: number
  /**
   * 对外（局域网）入口端口，容器部署才有（本机入口=port，只绑回环）。
   * 分享给其他设备时应使用它，而不是 port。
   */
  external_port?: number
  /** 容器形态显式信号：容器内自动检测的局域网地址不可信，分享 UI 走手动形态。 */
  in_container?: boolean
  /** Streamable HTTP MCP endpoint, e.g. http://127.0.0.1:18910/mcp */
  mcp_url?: string
  /** Desktop helper base URL, e.g. http://127.0.0.1:18950 */
  system_audio?: string
}

export const getHealth = () =>
  fetch(apiUrl("/health")).then((r) => r.json()) as Promise<HealthInfo>

/**
 * 本机后端的 /health（忽略当前来源）。
 *
 * 用于判断**这台电脑**的能力：是否桌面运行时、系统音频助手在不在。
 * 这些装在本机，问远端盒子永远是「没有」——桌面 App 连着远端盒子时
 * 会让系统音频采集误以为不可用。
 */
export const getLocalHealth = () =>
  fetch(localApiUrl("/health")).then((r) => r.json()) as Promise<HealthInfo>

// ── Version / Update ──

export interface VersionInfo {
  version: string
  repo: string
}

export const getVersion = () =>
  request<VersionInfo>("/version")

export const openDesktopExternalUrl = (url: string) =>
  request<{ ok: boolean }>("/desktop/open-url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  })

export interface DesktopAudioPermissions {
  microphone: "authorized" | "denied" | "restricted" | "notDetermined"
  screen_recording: boolean
  capturing: boolean
}

export type DesktopAudioSettingsTarget = "microphone" | "screen_recording"

export const getDesktopAudioPermissions = () =>
  request<DesktopAudioPermissions>("/desktop/audio-permissions", { local: true })

export const openDesktopAudioSettings = (target: DesktopAudioSettingsTarget) =>
  request<{ ok: boolean }>("/desktop/audio-settings", {
    method: "POST", local: true, body: JSON.stringify({ target }),
  })

export const fetchDesktopAudioPcm = (signal: AbortSignal) =>
  apiFetch("/desktop/pcm", { signal, cache: "no-store" }, { local: true })

export const stopDesktopAudioCapture = () =>
  request<{ ok: boolean }>("/desktop/sysaudio-stop", { method: "POST", local: true })

export const warmupDesktopAudioCapture = () =>
  request<{ ok: boolean }>("/desktop/sysaudio-warmup", { method: "POST", local: true })

export interface GitHubReleaseAsset {
  name: string
  browser_download_url: string
}

export interface GitHubRelease {
  tag_name: string
  html_url: string
  body: string
  assets?: GitHubReleaseAsset[]
}

/**
 * Latest-version check — Cloudflare R2 only (repo is private; the GitHub
 * Releases API 404s for outside users). latest.json is refreshed by CI on
 * every release: {version, tag, asset, sha256, size, updated_at}.
 * Synthesizes the legacy GitHubRelease shape so downstream logic is unchanged.
 */
const R2_PUBLIC_BASE = "https://pub-789a11bb112e4ed59d731554588eebfb.r2.dev"

interface R2LatestRelease {
  version: string
  tag?: string
  asset?: string
  notes?: string
}

export const checkLatestRelease = async (): Promise<GitHubRelease | null> => {
  try {
    const res = await fetch(`${R2_PUBLIC_BASE}/latest.json`)
    if (!res.ok) return null
    const latest = (await res.json()) as R2LatestRelease
    const version = (latest.version || "").replace(/^v/, "")
    if (!version) return null
    const assetName = latest.asset || `SinkDuce-macos-arm64-v${version}.dmg`
    const downloadUrl = `${R2_PUBLIC_BASE}/${assetName}`
    return {
      tag_name: latest.tag || `v${version}`,
      html_url: downloadUrl,
      body: latest.notes || "",
      assets: [{ name: assetName, browser_download_url: downloadUrl }],
    }
  } catch {
    return null
  }
}

export * from "./collections"
export * from "./config"
export * from "./meeting"
export * from "./speakers"
export * from "./notes"
export * from "./chat"
export * from "./chat-attachments"
export * from "./chat-reports"
export { getFileDetail } from "./file-mgmt"
export * from "./recall"
export * from "./hot-words"
export * from "./identity"
export * from "#edition/management-api"
export * from "./pairing"
