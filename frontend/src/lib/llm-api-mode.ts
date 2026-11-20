import type { LLMApiMode } from "@/api/config"

export function resolveLLMApiMode(baseUrl: string, selected?: LLMApiMode | null): LLMApiMode {
  if (selected) return selected
  const input = baseUrl.trim()
  if (!/^https:\/\//i.test(input)) return "chat_completions"
  try {
    const { hostname } = new URL(input)
    if (hostname === "api.openai.com" || hostname.endsWith(".api.openai.com")) return "responses"
  } catch {
    // The URL can be incomplete while the user is typing it.
  }
  return "chat_completions"
}
