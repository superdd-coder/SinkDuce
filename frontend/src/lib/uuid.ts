/**
 * 客户端唯一 id 生成 —— 唯一出口。
 *
 * `crypto.randomUUID` 是 secure-context-only API：本机（127.0.0.1 / HTTPS）可用，
 * 但局域网设备用普通 HTTP 打开盒子页面时它**不存在**，直接调用会当场抛
 * TypeError（2026-09-16 实测：远端设备 Chat / QuickChat 发不出消息的真根因）。
 *
 * 所以所有本地生成 id 的地方都必须走 `newId()`：
 * 1. crypto.randomUUID 存在（安全上下文）→ 直接用；
 * 2. crypto.getRandomValues —— 同属 Web Crypto 但**不**受安全上下文限制，拼 RFC 4122 v4；
 * 3. 引擎过老连 getRandomValues 都没有 → 时间戳 + 随机数（仅需唯一性场景足够）。
 */
export function newId(): string {
  const c = globalThis.crypto
  if (c && typeof c.randomUUID === "function") return c.randomUUID()
  if (c && typeof c.getRandomValues === "function") {
    const b = c.getRandomValues(new Uint8Array(16))
    b[6] = (b[6] & 0x0f) | 0x40
    b[8] = (b[8] & 0x3f) | 0x80
    let h = ""
    for (const byte of b) h += byte.toString(16).padStart(2, "0")
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
  }
  return `id-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}
