/** GitHub Release asset used by the desktop update dialog. */
export const DESKTOP_DMG_ASSET = "SinkDuce-macos-arm64.dmg"

export interface ReleaseAsset {
  name: string
  browser_download_url: string
}

/** Prefer the stable DMG name; otherwise the first `.dmg` on the release. */
export function pickDesktopDownloadUrl(
  assets: ReleaseAsset[] | null | undefined,
): string | null {
  if (!assets?.length) return null
  const named = assets.find((a) => a.name === DESKTOP_DMG_ASSET)
  if (named?.browser_download_url) return named.browser_download_url
  const dmg = assets.find((a) => a.name.toLowerCase().endsWith(".dmg"))
  return dmg?.browser_download_url || null
}
