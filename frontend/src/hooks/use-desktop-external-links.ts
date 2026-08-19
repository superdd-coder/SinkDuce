import { useEffect } from "react"
import { getHealth, openDesktopExternalUrl } from "@/api/client"
import { isDesktopExternalUrl } from "@/lib/desktop-external-url"

function pageOrigin(): string {
  if (typeof window === "undefined") return "http://127.0.0.1"
  return window.location.origin
}

function hrefFromOpenUrl(url: string | URL | undefined): string {
  if (!url) return ""
  return typeof url === "string" ? url : url.toString()
}

/**
 * WKWebView / Tauri swallows target=_blank and window.open.
 * On desktop, send http(s) off-app links to the system browser via the API.
 */
export function useDesktopExternalLinks() {
  useEffect(() => {
    let active = true
    let attached = false
    const originalOpen = window.open.bind(window)

    const openExternal = (href: string) => {
      void openDesktopExternalUrl(href).catch(() => {
        originalOpen(href, "_blank", "noopener,noreferrer")
      })
    }

    const onClick = (event: MouseEvent) => {
      if (event.defaultPrevented) return
      if (event.button !== 0 && event.button !== 1) return
      const target = event.target
      if (!(target instanceof Element)) return
      const anchor = target.closest("a")
      if (!anchor) return
      const href = anchor.getAttribute("href")
      if (!href || !isDesktopExternalUrl(href, pageOrigin())) return
      event.preventDefault()
      openExternal(new URL(href, pageOrigin()).href)
    }

    const patchedOpen: typeof window.open = (url, target, features) => {
      const href = hrefFromOpenUrl(url as string | URL | undefined)
      if (isDesktopExternalUrl(href, pageOrigin())) {
        openExternal(new URL(href, pageOrigin()).href)
        return null
      }
      return originalOpen(url, target, features)
    }

    const attach = () => {
      if (!active || attached) return
      attached = true
      document.addEventListener("click", onClick, true)
      window.open = patchedOpen
    }

    void getHealth()
      .then((health) => {
        if (health.desktop === true) attach()
      })
      .catch(() => {
        /* Docker / health down — leave native link behavior */
      })

    return () => {
      active = false
      if (attached) {
        document.removeEventListener("click", onClick, true)
        window.open = originalOpen
      }
    }
  }, [])
}
