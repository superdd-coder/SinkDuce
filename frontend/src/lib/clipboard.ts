/**
 * 剪贴板写入 —— 带 secure context 兜底。
 *
 * `navigator.clipboard` 与其他 Web Crypto 家族能力一样是 secure-context-only：
 * 局域网设备用普通 HTTP 打开盒子页面时是 undefined（或拒绝 NotAllowedError）。
 * 兜底 = 瞬时 textarea + `document.execCommand("copy")`（已废弃但仍广泛可用，
 * 只作为降级路径）。
 */
export async function copyText(text: string): Promise<void> {
  const c = globalThis.navigator?.clipboard
  if (c && typeof c.writeText === "function") {
    try {
      await c.writeText(text)
      return
    } catch {
      /* 落到 execCommand 兜底 */
    }
  }
  const ta = document.createElement("textarea")
  ta.value = text
  ta.style.position = "fixed"
  ta.style.opacity = "0"
  document.body.appendChild(ta)
  ta.focus()
  ta.select()
  const ok = document.execCommand("copy")
  document.body.removeChild(ta)
  if (!ok) throw new Error("clipboard copy failed")
}
