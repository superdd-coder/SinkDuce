import { request } from "./http"

// ── Health ──

export const getHealth = () =>
  fetch("/health").then((r) => r.json()) as Promise<{ status: string }>

// ── Version / Update ──

export interface VersionInfo {
  version: string
  repo: string
}

export const getVersion = () =>
  request<VersionInfo>("/version")

export interface GitHubRelease {
  tag_name: string
  html_url: string
  body: string
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
export * from "./notes"
export * from "./chat"
export * from "./recall"
export * from "./hot-words"
