/**
 * In-app updater bridge to the Tauri shell (tauri-plugin-updater, Rust side).
 *
 * The shell downloads the update itself and swaps the bundle, so files never
 * carry the browser's quarantine stamp and macOS never shows "Open Anyway".
 * Old shells (without the updater commands) reject the invoke — callers must
 * fall back to the DMG download path.
 */

interface TauriWindow {
  __TAURI_INTERNALS__?: { invoke?: (cmd: string, args?: unknown) => Promise<unknown> }
  __TAURI__?: { core?: { invoke?: (cmd: string, args?: unknown) => Promise<unknown> } }
}

export type DesktopUpdatePhase =
  | "idle"
  | "checking"
  | "downloading"
  | "installing"
  | "ready"
  | "none_available"
  | "failed"

export interface DesktopUpdateStatus {
  phase: DesktopUpdatePhase
  received?: number
  total?: number | null
  version?: string
  message?: string
}

/** True only inside the Tauri webview — the browser/PWA must not see the button. */
export function desktopUpdaterAvailable(): boolean {
  const w = window as Window & TauriWindow
  return Boolean(w.__TAURI_INTERNALS__ || w.__TAURI__)
}

function invoke<T>(cmd: string, args?: unknown): Promise<T> {
  const w = window as Window & TauriWindow
  const fn = w.__TAURI_INTERNALS__?.invoke ?? w.__TAURI__?.core?.invoke
  if (!fn) return Promise.reject(new Error("not a desktop shell"))
  return fn(cmd, args) as Promise<T>
}

/** Start check → download → install in the shell. Progress flows via status. */
export function beginDesktopUpdate(): Promise<void> {
  return invoke<void>("updater_begin")
}

export async function desktopUpdateStatus(): Promise<DesktopUpdateStatus | null> {
  try {
    const status = await invoke<DesktopUpdateStatus>("updater_status")
    return status ?? null
  } catch {
    return null
  }
}

/** Kill sidecars, release the instance lock, swap to the new binary. Exits the app. */
export function restartDesktopAfterUpdate(): Promise<void> {
  return invoke<void>("updater_restart")
}
