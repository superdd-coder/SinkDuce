import { replyRuns } from "@/lib/qa-reply-format"

export function QaReplyText({ body, className = "" }: { body: string; className?: string }) {
  return <p className={`qa-reply-text ${className}`}>{replyRuns(body).map((run, i) => {
    const text = run.italic ? <em>{run.text}</em> : run.text
    return run.bold ? <strong key={i}>{text}</strong> : <span key={i}>{text}</span>
  })}</p>
}
