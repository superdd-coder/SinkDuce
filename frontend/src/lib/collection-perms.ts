/**
 * 库权限判定（后端 `/collections` 每项带的 `permissions`）。
 *
 * **为什么单独放一个文件**：这个判断同时被三处用来"把入口/写入关掉"——
 * 设置入口、面板的自动保存、以及"上次停在设置页"的纠正。三处各写一遍迟早有一处
 * 漏掉，而漏掉的那一处就是用户看到的 bug：没有权限的人一进设置页就弹"保存失败"。
 * 集中在这里 + 有测试，回归时至少会红一处。
 *
 * **纪律：严格 `=== true`。** 缺省（老后端 / 列表还没回来）一律按"没有"处理：
 * 少画一个入口、少存一次，代价是零；反过来乐观放行会发出一个注定 403 的请求，
 * 用户看到的就是那句吓人的报错。
 */

/** 后端在 `/collections` 里算好的四档开关。 */
export interface CollectionPermissions {
  read: boolean
  write: boolean
  delete: boolean
  config: boolean
}

interface HasPerms {
  id: string
  permissions?: CollectionPermissions
}

/**
 * 能不能打开这个库的设置面板（= 有没有 config 档）。
 *
 * 面板是 autosave（无保存按钮），没有 config 档的人一进去就会 POST `/config`，
 * 被拒后弹"保存失败"——那是个自己根本改不动的界面，不该给他进。
 */
export function canOpenCollectionConfig(
  collections: readonly HasPerms[],
  collectionId: string | null | undefined,
): boolean {
  if (!collectionId) return false
  return collections.find((c) => c.id === collectionId)?.permissions?.config === true
}

/**
 * 面板的自动保存该不该跑。
 *
 * **与"能不能打开"是同一个答案**（都是 config 档）：面板只可能在能打开时打开，
 * 所以这里不需要第二条乐观通道——多一条通道就是一个新的失败窗口。
 */
export function canWriteCollectionConfig(
  collections: readonly HasPerms[],
  collectionId: string | null | undefined,
): boolean {
  return canOpenCollectionConfig(collections, collectionId)
}

/**
 * 权限信号是否已到（列表里有没有这个库的 `permissions`）。
 *
 * 用来区分"确实没权限"和"还没知道"——纠正"上次停在设置页"时要等信号到齐再动，
 * 否则会在拿到权限之前把 owner 从设置页误踢出去。
 */
export function collectionPermsKnown(
  collections: readonly HasPerms[],
  collectionId: string | null | undefined,
): boolean {
  if (!collectionId) return false
  return collections.find((c) => c.id === collectionId)?.permissions !== undefined
}

/**
 * 能不能新建库（盒子级 `collection.create` 能力）。
 *
 * 数据来自 `/identity/me` 的 `can_create.collection`——后端用与墙同一引擎
 * （policy.can）现算，所以这里只是采信答案，不在前端重算规则。
 * 没权限的人不该看到那个点了一定 403 的"新建"按钮。
 *
 * **本机直通**：`is_local` 是后端签发的身份（本机自动钥匙 = owner 的实例），
 * 永远能建——盒主不会因为连着旧后端（还没有 can_create 字段）把自己的
 * 新建按钮也丢了。
 */
export function canCreateCollection(
  me: { is_local?: boolean; can_create?: { collection?: boolean } } | null | undefined,
): boolean {
  return me?.is_local === true || me?.can_create?.collection === true
}
