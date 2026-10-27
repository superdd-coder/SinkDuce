/**
 * 会议权限判定（后端 `/meetings` / 会议组响应里带的 `permissions`）。
 *
 * **为什么单独一个文件**：和 collection 那边同一类问题——"能不能做"的判断散在
 * 列表、详情、分组多处，各写一遍迟早有一处漏掉，而漏掉的那处就是用户看到的
 * bug：「没有删除权限的人还能看到删除按钮」。
 *
 * **纪律：严格 `=== true`。** 缺省（老后端 / 字段缺失）一律按"没有权限"处理：
 * 少画一个按钮的代价是零，多画一个会失败的按钮的代价是用户点了一下才发现。
 *
 * 注意这里只是**化妆**：真正的墙在 `store.delete_meeting` / `_set_meeting_archived`
 * 等处的 `authorize`。这些函数回答的是"后端会不会放行"，与墙同源（后端把
 * 执行路径那次判定直接回传），所以按钮可见性与实际放行与否不会分叉。
 */

/** 后端给单个会议的权限标记。 */
export interface MeetingPermissions {
  read: boolean
  /**
   * 能否改这个会议（改名 / 归档 / 删除 / 丢弃 / 选热词库 / 上传音频…）。
   *
   * 后端算的是**含创建者所有权**的答案：member 只有 `create` 档时，对自己建的会议
   * 这里是 true，对别人建的 false。字段名从 `operate` 改来（2026-09-17 档位重定），
   * 读的时候兼容旧名——老后端仍回 `operate`。
   */
  write?: boolean
  operate?: boolean
}

interface HasMeetingPerms {
  permissions?: MeetingPermissions
}

/**
 * 能否对这个会议做改名 / 归档 / 删除 / 丢弃 / 选热词等一切写入。
 *
 * 这些都落 meeting 的 `manage` 档，**并且创建者对自建会议一律放行**（后端判定）。
 * 所以这个函数直接采信后端的答案，不在前端重算规则——两处各算一套迟早分叉。
 */
export function canOperateMeeting(meeting: HasMeetingPerms | null | undefined): boolean {
  const p = meeting?.permissions
  if (!p) return false
  // 兼容旧字段名，避免前端比后端先升级时把按钮全藏了。
  return p.write === true || p.operate === true
}

/**
 * 能否删 / 归档 / 改名这个**分组**。
 *
 * 组是跨会议的容器（组里可能混着别人的会议），所以后端给的是"角色整体有没有
 * operate 档"这个保守口径，而不是逐会议判定——逐会判定代表不了"删整组"这一下
 * （默认档下 member 至少能删自己建的，于是每个组看起来都能删，但删整组会牵连
 * 别人的会议）。
 */
export function canOperateMeetingGroup(group: { can_operate?: boolean } | null | undefined): boolean {
  return group?.can_operate === true
}

/**
 * 能不能新建会议（meeting 域 `create` 档，无具体会议 → 创建者例外不参与）。
 *
 * 数据来自 `/identity/me` 的 `can_create.meeting`（后端与 store 建会那道墙同一
 * 引擎现算）。没 create 档的人不该看到"新建会议"按钮——点了也注定 403。
 *
 * **本机直通**：`is_local` 是后端签发的身份（本机自动钥匙 = owner 的实例），
 * 永远能建——盒主不会因为连着旧后端（还没有 can_create 字段）把自己的
 * 新建按钮也丢了。
 */
export function canCreateMeeting(
  me: { is_local?: boolean; can_create?: { meeting?: boolean } } | null | undefined,
): boolean {
  return me?.is_local === true || me?.can_create?.meeting === true
}
