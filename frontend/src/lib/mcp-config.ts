/**
 * 为「MCP 客户端」生成可直接粘贴的 `mcp.json` 片段。
 *
 * 为什么单独成文件：这段 JSON 是用户**唯一**要手工搬进 Agent 配置的东西，
 * 最容易出错（地址写成局域网 IP、多塞一把钥匙）。抽成纯函数便于钉住。
 *
 * ── 2026-09-18 重写：从「每台盒子各自对 Agent 暴露」改为「只有本机一台」──
 *
 * **本机盒子的 MCP 就是 Hub**，外部 Agent 只连它（它再代你去问别的盒子）。
 * 所以这里生成的**只有本机那一份**，地址恒为回环：
 *
 * - **地址必须是 `127.0.0.1`**：MCP 只经回环可达（后端不变式，见
 *   `src/pairing/middleware.py`）。远端盒子上的 `/mcp` 现在对网络**返回 404**，
 *   所以旧版那种「远端 LAN 地址 + 一把钥匙」的配置**连不上**，已撤掉。
 *   注意不能拿 `/health` 上报的 host 直接用——若用户把 `SINKDUCE_HOST` 设成某个
 *   网卡地址，那个值不是回环，会被自家闸门拒。
 * - **不带钥匙**：回环 ⇒ 后端判为本机 ⇒ owner，与「本机前端访问本机后端」完全一致。
 *   而且 owner 是本地身份，签不出钥匙来 —— 想带也没有。
 *
 * 服务端名字仍带盒子名（`SinkDuce-<盒子名>`）：Agent 将来可能连多台 Hub
 * （每台设备一个），主流客户端会把 server 名拼进工具名（如 Claude Code 的
 * `mcp__<server>__<tool>`），名字不同才不会把两台盒子的结果混在一起。
 */

/** 服务端名字（`mcpServers` 的键）前缀。 */
export const MCP_SERVER_PREFIX = "SinkDuce"

/** Agent 只能从这里连 MCP —— 回环，不是局域网地址。 */
export const MCP_LOOPBACK_HOST = "127.0.0.1"

/**
 * 服务端名字。
 *
 * 规范化：空白折成 `-`（server 名通常不允许空格），其余原样保留 —— 不强行 ASCII 化，
 * 否则中文盒子名会变得认不出来（用户自己起的名字应当能对上）。
 *
 * 回退顺序：盒子名 → box_id → 裸 `SinkDuce`（全空时）。
 */
export function mcpServerKey(boxName: string, boxId: string): string {
  const raw = (boxName || "").trim() || (boxId || "").trim()
  const cleaned = raw.replace(/\s+/g, "-")
  return cleaned ? `${MCP_SERVER_PREFIX}-${cleaned}` : MCP_SERVER_PREFIX
}

/**
 * MCP 端点的回环 URL。端口用本进程真实监听的那个（桌面壳常是 18910）。
 *
 * 端口非法（空串 / 0 / NaN）时回退 18900：`0` 在 socket 语义里是「随便挑一个」，
 * 拼进 URL 就成了连不上的 `:0`，而 `/health` 拿不到时我们确实会走到这里。
 */
export function mcpLoopbackUrl(port: number | string): string {
  const n = typeof port === "number" ? port : Number.parseInt(String(port ?? "").trim(), 10)
  const safe = Number.isFinite(n) && n > 0 ? n : 18900
  return `http://${MCP_LOOPBACK_HOST}:${safe}/mcp`
}

export interface McpJsonInput {
  /** 展示名（盒子名）→ mcpServers 的键。 */
  boxName: string
  boxId: string
  /** 本进程 API/MCP 监听端口（来自 `/health` 的 `port`）。 */
  port: number | string
}

export interface McpJsonResult {
  /** 可直接粘贴的 JSON 文本。 */
  json: string
  /** 生成的端点 URL（UI 单独显示用）。 */
  url: string
}

export function buildMcpJson(input: McpJsonInput): McpJsonResult {
  const url = mcpLoopbackUrl(input.port)
  const payload = {
    mcpServers: {
      [mcpServerKey(input.boxName, input.boxId)]: { type: "http", url },
    },
  }
  return { json: JSON.stringify(payload, null, 2), url }
}
