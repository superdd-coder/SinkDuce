/** GitHub Release DMG name: SinkDuce-macos-arm64-vX.Y.Z.dmg */

export function desktopDmgAssetName(tagOrVersion: string): string {
  const ver = (tagOrVersion || "").trim().replace(/^v/i, "")
  return `SinkDuce-macos-arm64-v${ver}.dmg`
}

export interface ReleaseAsset {
  name: string
  browser_download_url: string
}

/** Only the versioned official DMG for that tag — never another .dmg or .app. */
export function pickDesktopDownloadUrl(
  assets: ReleaseAsset[] | null | undefined,
  tagOrVersion: string,
): string | null {
  if (!assets?.length || !tagOrVersion) return null
  const name = desktopDmgAssetName(tagOrVersion)
  const named = assets.find((a) => a.name === name)
  return named?.browser_download_url || null
}

export function detectDesktopClient(healthDesktop?: boolean): boolean {
  if (healthDesktop === true) return true
  if (typeof window === "undefined") return false
  const w = window as Window & { __TAURI_INTERNALS__?: unknown; __TAURI__?: unknown }
  return Boolean(w.__TAURI_INTERNALS__ || w.__TAURI__)
}


/** Desktop waits for the versioned DMG; Docker only needs a newer tag. */
export function shouldOfferUpdate(opts: {
  desktop: boolean
  versionNewer: boolean
  downloadUrl: string | null
}): boolean {
  if (!opts.versionNewer) return false
  if (opts.desktop) return Boolean(opts.downloadUrl)
  return true
}
