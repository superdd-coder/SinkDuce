import { useEffect, useState } from "react"
import { Check, Copy } from "lucide-react"
import { toast } from "sonner"
import { getHealth, type HealthInfo } from "@/api/client"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export interface ConnectionInfo {
  host: string
  port: number
  mcpUrl: string
  desktop: boolean
}

function fromLocation(): Pick<ConnectionInfo, "host" | "port" | "mcpUrl"> {
  const { protocol, hostname, port: rawPort } = window.location
  const host = hostname || "127.0.0.1"
  const port = rawPort
    ? Number.parseInt(rawPort, 10)
    : protocol === "https:"
      ? 443
      : 80
  const origin = `${protocol}//${host}${rawPort ? `:${rawPort}` : ""}`
  return { host, port, mcpUrl: `${origin}/mcp` }
}

export function connectionFromHealth(health?: HealthInfo | null): ConnectionInfo {
  const fallback = typeof window === "undefined"
    ? { host: "127.0.0.1", port: 18900, mcpUrl: "http://127.0.0.1:18900/mcp" }
    : fromLocation()
  const port = typeof health?.port === "number" && health.port > 0
    ? health.port
    : fallback.port
  const host = (health?.host || fallback.host).trim() || "127.0.0.1"
  const mcpUrl = (health?.mcp_url || "").trim() || `http://${host}:${port}/mcp`
  return {
    host,
    port,
    mcpUrl,
    desktop: health?.desktop === true,
  }
}

function claudeSnippet(url: string): string {
  return JSON.stringify(
    {
      mcpServers: {
        sinkduce: {
          type: "http",
          url,
        },
      },
    },
    null,
    2,
  )
}

function CopyBtn({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      toast.error(`Could not copy ${label}`)
    }
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className={cn("pm-settings-copy", copied && "is-copied")}
      onClick={() => { void handleCopy() }}
    >
      {copied ? (
        <>
          <Check className="h-3.5 w-3.5" strokeWidth={2} />
          Copied
        </>
      ) : (
        <>
          <Copy className="h-3.5 w-3.5" strokeWidth={1.75} />
          Copy
        </>
      )}
    </Button>
  )
}

export function ConnectionInfoCard() {
  const [info, setInfo] = useState<ConnectionInfo>(() => connectionFromHealth())

  useEffect(() => {
    let cancelled = false
    getHealth()
      .then((health) => {
        if (!cancelled) setInfo(connectionFromHealth(health))
      })
      .catch(() => {
        if (!cancelled) setInfo(connectionFromHealth())
      })
    return () => {
      cancelled = true
    }
  }, [])

  const claude = claudeSnippet(info.mcpUrl)

  return (
    <section className="pm-settings-section">
      <div className="pm-settings-card">
        <div className="pm-settings-card-head-text">
          <h2 className="pm-settings-card-kicker">MCP</h2>
          <p className="pm-meta pm-settings-card-desc">
            This running instance. Point an agent here — not at Docker&rsquo;s
            18900 unless that is the process you opened.
          </p>
        </div>

        <div className="pm-settings-conn-rows">
          <div className="pm-settings-conn-row">
            <span className="pm-label">Port</span>
            <span className="pm-settings-conn-value">{info.port}</span>
          </div>
          <div className="pm-settings-conn-row">
            <span className="pm-label">Runtime</span>
            <span className="pm-settings-conn-value">
              {info.desktop ? "Desktop app" : "Docker / local server"}
            </span>
          </div>
          <div className="pm-settings-conn-row">
            <span className="pm-label">MCP URL</span>
            <div className="pm-settings-conn-url">
              <span className="pm-settings-conn-value pm-settings-conn-url-text">
                {info.mcpUrl}
              </span>
              <CopyBtn text={info.mcpUrl} label="MCP URL" />
            </div>
          </div>
        </div>

        <p className="pm-meta">
          {info.desktop
            ? "Keep the app running. The red close button hides it to the menu bar; Quit from the tray or Cmd+Q stops MCP."
            : "Start the backend first (docker compose up -d). MCP shares this process — no extra port."}
        </p>

        <div className="pm-settings-conn-snippet">
          <div className="pm-settings-conn-snippet-head">
            <h3 className="pm-settings-subhead">Claude Code / Cursor</h3>
            <CopyBtn text={claude} label="Claude snippet" />
          </div>
          <p className="pm-meta">
            Project <code className="pm-settings-conn-inline">.mcp.json</code>
            {" "}or{" "}
            <code className="pm-settings-conn-inline">~/.claude/.mcp.json</code>
            {" / "}
            <code className="pm-settings-conn-inline">~/.cursor/mcp.json</code>
          </p>
          <pre className="pm-settings-conn-pre">{claude}</pre>
        </div>
      </div>
    </section>
  )
}
