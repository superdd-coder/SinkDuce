import { useRef } from "react"
import { Progress } from "@/components/ui/progress"
import { Clock, Loader2, CheckCircle2, XCircle, StopCircle, RefreshCw } from "lucide-react"
import { type TaskInfo } from "@/api/client"

interface UploadSectionProps {
  hasActiveTasks: boolean
  tasks: TaskInfo[]
  allowedFileTypes?: string[]
  onUpload: (files: FileList | null) => void
  onClearCompleted: () => Promise<{ message: string }>
  onRefreshTasks: () => void
  onCancelTask?: (taskId: string) => void
  onRetryTask?: (taskId: string) => void
}

function TaskStatusIcon({ status }: { status: string }) {
  switch (status) {
    case "pending":
      return <Clock className="h-4 w-4 text-muted-foreground" />
    case "processing":
      return <Loader2 className="h-4 w-4 text-blue-500 animate-spin" />
    case "completed":
      return <CheckCircle2 className="h-4 w-4 text-green-500" />
    case "failed":
      return <XCircle className="h-4 w-4 text-red-500" />
    default:
      return null
  }
}

const ALL_FILE_TYPES = ["pdf", "txt", "md", "docx", "xlsx", "pptx", "csv"]

export function UploadSection({ hasActiveTasks, tasks, allowedFileTypes, onUpload, onClearCompleted, onRefreshTasks, onCancelTask, onRetryTask }: UploadSectionProps) {
  const fileRef = useRef<HTMLInputElement>(null)

  const effectiveTypes = allowedFileTypes && allowedFileTypes.length > 0 ? allowedFileTypes : ALL_FILE_TYPES
  const acceptAttr = effectiveTypes.map(t => `.${t}`).join(",")
  const typesLabel = effectiveTypes.map(t => t.toUpperCase()).join(", ")

  const handleClearCompleted = async () => {
    try {
      await onClearCompleted()
      onRefreshTasks()
    } catch {
      // ignore
    }
  }

  return (
    <div className="space-y-6">
      {/* Upload zone */}
      <div>
        <div
          className="text-[11px] font-normal uppercase tracking-[0.12em] mb-2.5 text-muted-foreground/80"
        >
          Upload Files
        </div>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept={acceptAttr}
          className="hidden"
          onChange={(e) => { onUpload(e.target.files); if (fileRef.current) fileRef.current.value = "" }}
        />
        <div
          className="p-6 text-center cursor-pointer transition-colors hover:opacity-80 border border-dashed border-border"
          onClick={() => fileRef.current?.click()}
          onDrop={(e) => { e.preventDefault(); onUpload(e.dataTransfer.files) }}
          onDragOver={(e) => e.preventDefault()}
        >
          <div className="text-[10px] font-medium uppercase tracking-[0.15em] mb-1 text-muted-foreground">
            {hasActiveTasks ? "Processing… Upload more" : "Drop files to upload"}
          </div>
          <div className="text-[11px] text-muted-foreground" style={{ opacity: 0.7 }}>
            {typesLabel}
          </div>
        </div>
      </div>

      {/* Task queue */}
      {tasks.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-2.5">
            <div
              className="text-[11px] font-normal uppercase tracking-[0.12em] text-muted-foreground/80"
            >
              Upload Queue
              {hasActiveTasks && (
                <span className="ml-2 font-normal opacity-60">
                  · {tasks.filter((t) => t.status === "processing").length} processing
                </span>
              )}
            </div>
            <button
              type="button"
              onClick={handleClearCompleted}
              disabled={!tasks.some((t) => t.status === "completed" || t.status === "failed")}
              className="text-[10px] font-medium uppercase tracking-[0.1em] cursor-pointer transition-opacity hover:opacity-80 text-muted-foreground t-sans-family"
              style={{
                background: "none",
                border: "0.5px solid var(--color-border)",
                padding: "3px 8px",
                borderRadius: "2px",
                opacity: tasks.some((t) => t.status === "completed" || t.status === "failed") ? 1 : 0.3,
              }}
            >
              Clear
            </button>
          </div>

          <div>
            {tasks.map((task) => (
              <div
                key={task.id}
                className="py-2.5 border-b border-b border-dashed border-border"
              >
                <div className="flex items-center gap-3">
                  <TaskStatusIcon status={task.status} />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium truncate text-foreground">{task.filename}</p>
                    {task.message && (
                      <p className="text-[11px] text-muted-foreground">{task.message}</p>
                    )}
                  </div>
                  {task.status === "completed" && task.result && (
                    <span className="text-[10px] font-medium shrink-0 text-primary">
                      {task.result.chunks_count} chunks
                    </span>
                  )}
                  {task.status === "processing" && onCancelTask && (
                    <button
                      type="button"
                      className="shrink-0 cursor-pointer text-muted-foreground"
                      style={{ background: "none", border: "none" }}
                      onClick={() => onCancelTask(task.id)}
                      title="Stop"
                    >
                      <StopCircle className="h-3.5 w-3.5" />
                    </button>
                  )}
                  {task.status === "failed" && onRetryTask && (
                    <button
                      type="button"
                      className="shrink-0 cursor-pointer text-muted-foreground"
                      style={{ background: "none", border: "none" }}
                      onClick={() => onRetryTask(task.id)}
                      title="Retry"
                    >
                      <RefreshCw className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
                {task.status === "processing" && (
                  <Progress value={task.progress} className="h-0.5 mt-2" />
                )}
                {task.status === "failed" && task.error && (
                  <p className="text-[11px] mt-1 pl-7" style={{ color: "#dc2626" }}>{task.error}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
