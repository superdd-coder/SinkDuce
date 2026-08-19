import { request } from "./http"

// ── Health ──

export interface HealthInfo {
  status: string
  desktop?: boolean
  /** Dev-only: data/mock-update file present — force the update dialog. */
  mock_update?: boolean
  /** Advertised listen host for clients (wildcard binds become 127.0.0.1). */
  host?: string
  /** Actual API / MCP listen port (desktop is often 18910, not 18900). */
  port?: number
  /** Streamable HTTP MCP endpoint, e.g. http://127.0.0.1:18910/mcp */
  mcp_url?: string
  /** Desktop helper base URL, e.g. http://127.0.0.1:18950 */
  system_audio?: string
}

export const getHealth = () =>
  fetch("/health").then((r) => r.json()) as Promise<HealthInfo>

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
 * Check GitHub Releases for the latest version.
 * Uses a public endpoint (no auth needed, 60 req/hr per IP).
 */
export const checkLatestRelease = async (repo: string): Promise<GitHubRelease | null> => {
  try {
    const res = await fetch(`https://api.github.com/repos/${repo}/releases/latest`)
    if (!res.ok) return null
    return res.json()
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
export * from "./recall"
export * from "./hot-words"
