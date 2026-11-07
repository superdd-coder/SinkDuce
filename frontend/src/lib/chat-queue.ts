import type { ChatAttachment } from "@/api/chat-attachments"
import type { SessionConfig } from "@/api/session-config"
import type { ReportComment } from "@/lib/report-review"

export interface ChatTurn {
  id: string
  content: string
  attachments: ChatAttachment[]
  comments: ReportComment[]
  config: SessionConfig
}

export interface ChatQueue {
  running: ChatTurn | null
  pending: ChatTurn[]
  paused: boolean
}

/** Keep drafts attached to exactly one turn, including while its request is starting. */
export function reservedChatInputs(queue: ChatQueue | undefined) {
  const turns = queue?.running ? [queue.running, ...queue.pending] : queue?.pending ?? []
  return {
    attachments: new Set(turns.flatMap(turn => turn.attachments.map(file => file.id))),
    comments: new Set(turns.flatMap(turn => turn.comments.map(comment => comment.id))),
  }
}
