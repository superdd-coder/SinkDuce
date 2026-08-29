/** Minimal trail block — keep this free of the Zustand store. */
export type NextStepBlock = {
  type?: string
  isStreaming?: boolean
  toolStatus?: string
}

export function isToolBlockRunning(block: NextStepBlock): boolean {
  return (
    block.isStreaming === true ||
    block.toolStatus === "running" ||
    block.toolStatus === "awaiting_confirm"
  )
}

/** After a tool shows Done, the model is still reading results with no new row. */
export function isWaitingForNextStep(
  timeline: NextStepBlock[] | undefined,
  isStreaming: boolean,
  answerStarted: boolean,
): boolean {
  if (!isStreaming || answerStarted || !timeline?.length) return false
  const last = timeline[timeline.length - 1]
  if (last.type === "thinking") return !last.isStreaming
  if (last.type === "tool") return !isToolBlockRunning(last)
  return false
}
