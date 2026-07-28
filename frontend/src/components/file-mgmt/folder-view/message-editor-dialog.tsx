import { useState, useCallback } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { MarkdownEditor } from "@/components/ui/markdown-editor"

interface MessageEditorDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  initialContent: string
  onSave: (content: string) => void
  readonly?: boolean
}

export function MessageEditorDialog({
  open,
  onOpenChange,
  title,
  initialContent,
  onSave,
  readonly = false,
}: MessageEditorDialogProps) {
  const [content, setContent] = useState(initialContent)

  const handleOpenChange = useCallback(
    (o: boolean) => {
      if (o) {
        setContent(initialContent)
      }
      onOpenChange(o)
    },
    [initialContent, onOpenChange],
  )

  const handleSave = useCallback(() => {
    const trimmed = content.trim()
    if (!trimmed) return
    onSave(trimmed)
    onOpenChange(false)
  }, [content, onSave, onOpenChange])

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="w-[1200px] max-w-[90vw] sm:max-w-[90vw] h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="text-sm">{title}</DialogTitle>
        </DialogHeader>
        <div className="flex-1 min-h-0 overflow-auto">
          {readonly ? (
            <div className="prose prose-sm dark:prose-invert max-w-none p-4 text-sm leading-relaxed">
              <MarkdownEditor
                value={initialContent}
                minHeight="280px"
                readonly
                showToolbar={false}
              />
            </div>
          ) : (
            <MarkdownEditor
              value={content}
              onChange={setContent}
              minHeight="280px"
              placeholder="Write a message in Markdown..."
              showToolbar
            />
          )}
        </div>
        {!readonly && (
          <DialogFooter className="gap-2 pt-2">
            <Button variant="outline" size="xs" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button size="xs" onClick={handleSave} disabled={!content.trim()}>
              Save
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  )
}
