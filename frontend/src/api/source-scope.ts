/**
 * 「按来源隔离」的本地状态键。
 *
 * 有些状态是**每台盒子各自一套**的——比如「当前选中的 collection」。以前这些键
 * 全局共用（`rag_activeCollection`），切到远端盒子 B 选中 X、再切回本机 A 时，
 * A 仍拿着 X 去请求，而 X 在 A 上不存在 → 文件树 404 →
 * 「加载文件夹失败：Collection 'X' not found」（2026-09-15 实测）。
 *
 * 做法：物理键带上来源 id。切来源后自然读不到对方的值，等于该来源的选择「重来」——
 * 这正是想要的行为，且不需要在切换时逐一清理。
 *
 * 注意：切换来源本来是整页 reload，所以这里只解决「读到的值属于另一台盒子」，
 * 不涉及运行中的跨来源串写。
 */
import { LOCAL_SOURCE, getActiveSourceId } from "./origin.ts"

/** 逻辑键 → 当前来源下的物理键，例如 `rag_activeCollection@local`。 */
export function sourceScopedKey(key: string): string {
  let source = LOCAL_SOURCE
  try {
    source = getActiveSourceId() || LOCAL_SOURCE
  } catch {
    /* 无 localStorage 时按本机 */
  }
  return `${key}@${source}`
}

/** 读一个按来源隔离的值；没存过就返回 fallback（**不**回退到旧全局键）。 */
export function readSourceScoped<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(sourceScopedKey(key))
    if (raw == null) return fallback
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

/** 写一个按来源隔离的值。 */
export function writeSourceScoped(key: string, value: unknown): void {
  try {
    localStorage.setItem(sourceScopedKey(key), JSON.stringify(value))
  } catch {
    /* 无痕模式等场景忽略 */
  }
}

/**
 * 写到**指定来源**作用域（不是当前来源）。
 *
 * 用于「切来源前布置目的地状态」：此刻 activeId 还是旧来源，
 * sourceScopedKey 会带错后缀，刷新切过去后才能读到。
 */
export function writeSourceScopedFor(source: string, key: string, value: unknown): void {
  try {
    localStorage.setItem(`${key}@${source}`, JSON.stringify(value))
  } catch {
    /* 无痕模式等场景忽略 */
  }
}
