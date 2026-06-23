import { useEffect, useState, useCallback } from "react"
import { getVersion, checkLatestRelease, type GitHubRelease } from "@/api/client"

const IGNORED_KEY = "sinkduce-ignored-versions"
const MOCK_KEY = "sinkduce-mock-update"

function getIgnoredVersions(): Set<string> {
  try {
    const raw = localStorage.getItem(IGNORED_KEY)
    if (raw) return new Set(JSON.parse(raw))
  } catch {}
  return new Set()
}

function compareVersions(a: string, b: string): number {
  const na = a.replace(/^v/, "").split(".").map(Number)
  const nb = b.replace(/^v/, "").split(".").map(Number)
  for (let i = 0; i < Math.max(na.length, nb.length); i++) {
    const da = na[i] || 0
    const db = nb[i] || 0
    if (da !== db) return da - db
  }
  return 0
}

export interface UpdateInfo {
  currentVersion: string
  latestVersion: string
  releaseUrl: string
  releaseBody: string
}

export function useUpdateCheck() {
  const [update, setUpdate] = useState<UpdateInfo | null>(null)
  const [ignored, setIgnored] = useState(false)
  const [currentVersion, setCurrentVersion] = useState<string>("—")

  useEffect(() => {
    let cancelled = false

    const check = async () => {
      // Dev mock first — takes priority
      if (typeof window !== "undefined" && window.localStorage) {
        try {
          if (localStorage.getItem(MOCK_KEY) === "1") {
            if (!cancelled) {
              setCurrentVersion("0.1.0")
              setUpdate({
                currentVersion: "0.1.0",
                latestVersion: "v0.2.0",
                releaseUrl: "https://github.com/superdd-coder/sinkduce/releases",
                releaseBody: "### Features\n- Added visual model support for Dashscope one-shot setup\n- Improved local model load/download separation\n\n### Fixes\n- Fixed transcription model auto-download on load click",
              })
              setIgnored(getIgnoredVersions().has("v0.2.0"))
            }
            return
          }
        } catch {}
      }

      // Real check
      try {
        const info = await getVersion()
        if (cancelled) return
        setCurrentVersion(info.version)
        const release: GitHubRelease | null = await checkLatestRelease(info.repo)
        if (!release || cancelled) return
        const latest = release.tag_name
        if (compareVersions(latest, info.version) > 0) {
          const ignoredVersions = getIgnoredVersions()
          setUpdate({
            currentVersion: info.version,
            latestVersion: latest,
            releaseUrl: release.html_url,
            releaseBody: release.body || "",
          })
          setIgnored(ignoredVersions.has(latest))
        }
      } catch {
        // Silently ignore
      }
    }
    check()
    const interval = setInterval(check, 6 * 60 * 60 * 1000)

    return () => { cancelled = true; clearInterval(interval) }
  }, [])

  const ignoreVersion = useCallback(() => {
    if (!update) return
    try {
      const ignoredVersions = getIgnoredVersions()
      ignoredVersions.add(update.latestVersion)
      localStorage.setItem(IGNORED_KEY, JSON.stringify([...ignoredVersions]))
    } catch {}
    setIgnored(true)
  }, [update])

  return { update, ignored, ignoreVersion, currentVersion }
}
