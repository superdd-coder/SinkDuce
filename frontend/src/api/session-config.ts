/**
 * 会话配置的恢复裁剪。
 *
 * 会话里存的配置可能已经失效（provider 被删、模型下线、知识库被删），直接灌回
 * store 会让人一发送就报错。这里逐项校验，产出「哪几项可以安全恢复」：
 * 某个字段为 `null` 表示**别恢复这一项**（保持现状），而非重置为空。
 *
 * 关键约束：**provider 与 model 必须成对有效**。只恢复 model 而不恢复 provider，
 * 会得到一个指向别家 provider 的模型名（后端对未知组合是静默降级，用户只会莫名
 * 用了别的模型）。
 */

export interface SessionConfig {
  provider_id?: string | null
  model?: string | null
  web_search?: boolean
  thinking?: boolean
  collections?: string[]
}

/** 校验所需的最小上下文（列表未加载时宁可不恢复，也不误清）。 */
export interface RestoreContext {
  providers: {
    id: string
    model?: string
    selected_models?: string[]
    function_call_model_ids?: string[]
  }[]
  collectionIds: Set<string>
}

export interface RestorableConfig {
  provider_id: string | null
  model: string | null
  web_search: boolean | null
  thinking: boolean | null
  collections: string[] | null
}

function usableModels(p: RestoreContext["providers"][number]): string[] {
  const all =
    p.selected_models && p.selected_models.length > 0
      ? p.selected_models
      : p.model
        ? [p.model]
        : []
  const fc = p.function_call_model_ids ?? []
  return fc.length === 0 ? all : all.filter((m) => fc.includes(m))
}

/** Empty means all projects. Metadata loading or deletion must never widen an
 * explicit scope to that sentinel; keep unavailable IDs until the user chooses. */
export function retainCollectionScope(ids: string[], available: Set<string>): string[] {
  const valid = ids.filter((id) => available.has(id))
  return ids.length > 0 && valid.length === 0 ? [...ids] : valid
}

export function pickRestorableConfig(
  config: SessionConfig | null | undefined,
  ctx: RestoreContext,
): RestorableConfig {
  const none: RestorableConfig = {
    provider_id: null,
    model: null,
    web_search: null,
    thinking: null,
    collections: null,
  }
  if (!config || typeof config !== "object") return none

  const out: RestorableConfig = { ...none }

  // ── provider + model（成对）──
  const wantProvider = config.provider_id ?? null
  const wantModel = config.model ?? null
  if (wantProvider) {
    const prov = ctx.providers.find((p) => p.id === wantProvider)
    if (prov) {
      out.provider_id = prov.id
      if (wantModel && usableModels(prov).includes(wantModel)) {
        out.model = wantModel
      }
      // 模型失效 → model 保持 null，让上层用该 provider 的默认
    }
    // provider 失效 → 两者都不恢复（绝不能只留 model）
  }
  // 没存 provider 时不恢复 model（无从判断它属于谁）

  // ── 布尔项：false 是有效值，只有「没存」才是 null ──
  if (typeof config.web_search === "boolean") out.web_search = config.web_search
  if (typeof config.thinking === "boolean") out.thinking = config.thinking

  // ── 知识库：不能把未加载或全失效的指定范围变成「全部项目」──
  if (Array.isArray(config.collections)) {
    out.collections = retainCollectionScope(config.collections, ctx.collectionIds)
  }

  return out
}

/**
 * 把一个会话的配置解析成**完整的一元组**（缺失项落默认）。
 *
 * 与 `pickRestorableConfig` 的分工：后者回答「哪些存过的值还能安全复用」
 * （null = 这一项没得可恢复）；这里把它的结果补上默认值，得到可以直接写进
 * store 的确定值。
 *
 * 为什么需要「补默认」：打开一个**已有会话**时，如果只写有值的项、跳过没值的项，
 * store 里就会留着上一个会话的值 —— 一旦发送就把错配置写进本会话，级联污染
 * （2026-09-16 用户报的「所有会话都变成同一套」）。
 *
 * 注意与**新建会话**的区别：新建会话不调用本函数（保持"粘手"，沿用当前值）。
 */
export interface ResolvedSessionConfig {
  /** null = 不指定，由上层挑默认 provider */
  provider_id: string | null
  /** null = 用该 provider 的默认模型 */
  model: string | null
  thinking: boolean
  web_search: boolean
  collections: string[]
}

export const SESSION_CONFIG_DEFAULTS = {
  thinking: true,
  web_search: false,
  collections: [] as string[],
}

export function resolveSessionConfig(
  config: SessionConfig | null | undefined,
  ctx: RestoreContext,
): ResolvedSessionConfig {
  const picked = pickRestorableConfig(config, ctx)
  return {
    // provider 有效就保留（模型若失效则为 null，上层会用该 provider 的默认模型）
    provider_id: picked.provider_id,
    model: picked.model,
    thinking: picked.thinking ?? SESSION_CONFIG_DEFAULTS.thinking,
    web_search: picked.web_search ?? SESSION_CONFIG_DEFAULTS.web_search,
    collections: picked.collections ?? [...SESSION_CONFIG_DEFAULTS.collections],
  }
}

// ── 会话配置的本地缓存（按会话隔离）────────────────────────────
//
// 为什么需要它：`loadSessionMessages` 有「消息从内存缓存直接返回」的快路径，
// 那条路上不会重新请求服务端，也就拿不到 config。若不在本地记住每个会话的
// 配置，切回已缓存会话时 store 里还是上一个会话的配置——一旦发送就会把错的
// 配置写进这个会话，级联污染（表现为「所有会话都变成同一套」）。
//
// 缓存的是**裁剪前**的原始 config（会话可能在其他设备上被改过，重新拉取时会刷新）。

const _configBySession = new Map<string, SessionConfig>()

/** 记住某会话的配置（切会话/发送时调用）。 */
export function rememberSessionConfig(
  sessionId: string,
  config: SessionConfig | null | undefined,
): void {
  if (!sessionId) return
  _configBySession.set(sessionId, config && typeof config === "object" ? config : {})
}

/** 取某会话的配置；没记录过返回 undefined（调用方保持现状，不恢复）。 */
export function recallSessionConfig(sessionId: string): SessionConfig | undefined {
  return _configBySession.get(sessionId)
}

/** 删除会话时清理，避免 id 复用拿到陈旧配置。 */
export function forgetSessionConfig(sessionId: string): void {
  _configBySession.delete(sessionId)
}
