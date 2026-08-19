/** True when the URL should leave the desktop WebView for the system browser. */
export function isDesktopExternalUrl(href: string, pageOrigin: string): boolean {
  const raw = (href || "").trim()
  if (!raw) return false
  try {
    const url = new URL(raw, pageOrigin)
    if (url.protocol !== "http:" && url.protocol !== "https:") return false
    return url.origin !== new URL(pageOrigin).origin
  } catch {
    return false
  }
}
