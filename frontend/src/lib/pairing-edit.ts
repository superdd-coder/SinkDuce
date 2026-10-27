/**
 * 「已连接设备」改地址（2026-09-17 用户要求）。
 *
 * 场景：盒子换了网络 / DHCP 换了 IP → 已连接列表里那条记录还指着旧地址，请求全挂。
 * 以前只能「移除 + 重新连接」，而重新连接要重新粘 API Key。这里补一个就地改地址的入口。
 *
 * **两条纪律**（都在这里集中，便于测试）：
 *
 * 1. **地址格式归一**：缺 scheme 补 `http://`、去掉尾部斜杠——与「连接」弹窗
 *    (`connect-box-dialog`) 完全同一套规则，否则两条路径会存出两种形状。
 * 2. **只允许改「同一台盒子」的地址**：改完地址要用 API Key 探测一次，探测回来的
 *    `box_id` 必须和这条记录原本的 `box_id` **一致**。若不一致，说明那个地址上是
 *    **另一台**盒子——此时覆盖记录等于把 A 的身份贴上 B 的内容（它的来源作用域键、
 *    已存的钥匙、界面偏好全会串味）。那种情况应当去「连接」新增，而不是改这条。
 */

import type { SavedConnection } from "@/api/origin"

/** 缺 scheme 补 http://，去尾部斜杠。空串返回 ""。 */
export function normalizeRemoteAddr(raw: string): string {
  let a = (raw || "").trim()
  if (!a) return ""
  if (!/^https?:\/\//i.test(a)) a = `http://${a}`
  return a.replace(/\/+$/, "")
}

/** 改完之后这条记录是否仍指向同一台盒子。 */
export function isSameBox(currentBoxId: string, probedBoxId: string): boolean {
  return !!probedBoxId && currentBoxId === probedBoxId
}

export interface AddressEdit {
  addr: string
  token: string
  /** 探测回来的名字（盒子改名了也顺手同步）。 */
  boxName: string
  lastChecked: string
}

/**
 * 就地更新一条连接记录。**按 `box_id` 匹配**（不是名字、不是地址）——
 * 名字可能重复、地址正是我们要改的那个，都不适合当主键。
 *
 * 找不到匹配项时原样返回（调用方自己决定要不要新增）。
 */
export function applyAddressEdit(
  connections: readonly SavedConnection[],
  boxId: string,
  edit: AddressEdit,
): SavedConnection[] {
  return connections.map((c) =>
    c.box_id === boxId
      ? {
          ...c,
          addr: edit.addr,
          token: edit.token,
          box_name: edit.boxName || c.box_name,
          last_checked: edit.lastChecked,
        }
      : c,
  )
}
