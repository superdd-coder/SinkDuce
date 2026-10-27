export type OneshotSlotSnapshot = {
  visual_model_id: string
  default_chat_model: string
  enrichment_model: string
  meeting_model: string
  agentic_query_model: string
  note_distill_model: string
}

export function oneshotSlotSnapshot(
  providerId: string | undefined,
  refs: {
    chat: string
    visual: string
    library: string
    meeting: string
    agentic: string
    distill?: string
  },
): OneshotSlotSnapshot {
  const prefix = (model: string) => (providerId ? `${providerId}|${model}` : model)
  const library = refs.library || refs.visual
  return {
    visual_model_id: prefix(refs.visual || library),
    default_chat_model: prefix(refs.chat),
    enrichment_model: prefix(library),
    meeting_model: prefix(refs.meeting),
    agentic_query_model: prefix(refs.agentic),
    note_distill_model: prefix(refs.distill || library),
  }
}
