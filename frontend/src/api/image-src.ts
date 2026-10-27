/**
 * 文档内嵌图片的渲染 URL 解析。
 *
 * 存储里的 src 刻意保存为**相对路径**（`/api/notes/.../images/a.jpg`），这样笔记
 * 内容不绑定某台盒子的地址、可自由迁移。但渲染时必须按当前来源解析：
 *
 * - 本机来源：保持同源相对路径（浏览器自然命中本机后端）；
 * - 远端盒子：解析成该盒子的绝对地址。否则相对路径会打到**本机**后端，而图在
 *   对方手上 → 裂图（2026-09-15 实测确认的真实 bug）；
 * - 两种情形都附配对钥匙：`<img>` 由浏览器发起，带不了请求头，只能走 `?token=`。
 *
 * 绝对地址（外链 / data: / blob:）原样返回，不改写。
 */
import { apiUrl, withTokenQuery } from "./origin.ts"

export function resolveImageSrc<T extends string | null | undefined>(src: T): T {
  if (!src) return src
  // 只处理自家 API 的相对路径；外链、data:、blob: 等一律不动
  if (!src.startsWith("/api/")) return src
  return withTokenQuery(apiUrl(src)) as T
}
