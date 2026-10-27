/**
 * 桌面壳重启桥（远程模式开关切换后的「立即重启」）。
 *
 * 与 desktop-updater.ts 同一套 invoke 手法：只在 Tauri webview 里可用，
 * 浏览器里调用方必须隐藏按钮（没有壳可重启）。
 */

import { toast } from "sonner"

interface TauriWindow {
  __TAURI_INTERNALS__?: { invoke?: (cmd: string, args?: unknown) => Promise<unknown> }
  __TAURI__?: { core?: { invoke?: (cmd: string, args?: unknown) => Promise<unknown> } }
}

/** True only inside the Tauri webview — the browser must not see the button. */
export function desktopShellAvailable(): boolean {
  const w = window as Window & TauriWindow
  return Boolean(w.__TAURI_INTERNALS__ || w.__TAURI__)
}

/** 重启桌面 App（壳会先收掉 sidecars 再整个换进程）。失败弹 toast 不静默——
 *  「按了没反应」是最差的失败形态（2026-09-22 实测：ACL 未声明 + 编译指纹烂掉
 *  双重叠加，按钮点了毫无动静）。 */
export async function restartDesktopApp(): Promise<void> {
  const w = window as Window & TauriWindow
  const fn = w.__TAURI_INTERNALS__?.invoke ?? w.__TAURI__?.core?.invoke
  if (!fn) throw new Error("not a desktop shell")
  try {
    await fn("restart_app")
  } catch (err) {
    const { tr } = await import("@/i18n/tr")
    toast.error(tr("remoteMode.gate.restartFailed"), { description: String(err).slice(0, 160) })
    throw err
  }
}
