import { useState, useEffect } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import { CardContent } from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Loader2 } from "lucide-react"
import {
  getDocSummary,
  getFilePreviewUrl,
  type ConflictItem,
  type DocSummary,
} from "@/api/client"
import {
  isRawViewerSupported,
  RawFileViewer,
  resolveRawFilename,
} from "@/components/file-mgmt/raw-file-viewer"

interface ConflictViewerDialogProps {
  conflict: ConflictItem | null
  collection: string
  onOpenChange: (open: boolean) => void
}

function ConflictQuote({ content }: { content: string }) {
  return (
    <div className="px-4 pt-3 pb-2 shrink-0">
      <p className="text-sm leading-relaxed whitespace-pre-wrap text-amber-600 dark:text-amber-400 font-medium border-l-2 border-amber-400 pl-3">
        &ldquo;{content}&rdquo;
      </p>
    </div>
  )
}

function SourcePanel({
  collection,
  source,
  label,
  content,
}: {
  collection: string
  source: string
  label: string
  content: string
}) {
  const [summary, setSummary] = useState<DocSummary | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)

  const filename = resolveRawFilename(label, source)
  const showRaw = isRawViewerSupported(filename)
  const previewUrl = getFilePreviewUrl(source, { collection })
  const defaultTab = showRaw ? "raw" : "summary"

  useEffect(() => {
    if (!collection || !source) return
    let cancelled = false
    setSummaryLoading(true)
    getDocSummary(collection, source)
      .then((res) => {
        if (!cancelled) setSummary(res)
      })
      .catch(() => {
        if (!cancelled) setSummary(null)
      })
      .finally(() => {
        if (!cancelled) setSummaryLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [collection, source])

  return (
    <div className="w-1/2 flex flex-col min-h-0">
      <div className="flex items-center gap-2 mb-2">
        <h4 className="text-sm font-medium text-muted-foreground truncate">
          {label}
        </h4>
      </div>
      <div className="flex-1 overflow-hidden rounded-lg border border-border min-h-0">
        <Tabs defaultValue={defaultTab} className="flex flex-col h-full">
          <TabsList variant="line" className="mx-2 mt-2">
            {showRaw && <TabsTrigger value="raw">Raw</TabsTrigger>}
            <TabsTrigger value="summary">Summary</TabsTrigger>
          </TabsList>

          {showRaw && (
            <TabsContent
              value="raw"
              className="flex-1 overflow-hidden min-h-0 flex flex-col mt-0"
            >
              <ConflictQuote content={content} />
              <div className="flex-1 min-h-0 px-2 pb-2">
                <RawFileViewer
                  url={previewUrl}
                  filename={filename}
                  downloadUrl={previewUrl}
                  className="h-full"
                />
              </div>
            </TabsContent>
          )}

          <TabsContent
            value="summary"
            className="flex-1 overflow-hidden min-h-0 mt-0"
          >
            <ScrollArea className="h-full">
              <CardContent className="p-4">
                {!showRaw && <ConflictQuote content={content} />}
                {summaryLoading ? (
                  <div className="flex items-center justify-center py-8 text-muted-foreground">
                    <Loader2 className="h-5 w-5 animate-spin mr-2" />
                    Loading summary...
                  </div>
                ) : summary ? (
                  <div className="space-y-4">
                    {summary.data.length > 0 && (
                      <div>
                        <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
                          Data Points
                        </h5>
                        <ul className="space-y-1">
                          {summary.data.map((item, i) => (
                            <li key={i} className="text-sm leading-relaxed">
                              {item}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {summary.facts.length > 0 && (
                      <div>
                        <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
                          Facts
                        </h5>
                        <ul className="space-y-1">
                          {summary.facts.map((item, i) => (
                            <li key={i} className="text-sm leading-relaxed">
                              {item}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {summary.insights.length > 0 && (
                      <div>
                        <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
                          Insights
                        </h5>
                        <ul className="space-y-1">
                          {summary.insights.map((item, i) => (
                            <li key={i} className="text-sm leading-relaxed">
                              {item}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {summary.data.length === 0 &&
                      summary.facts.length === 0 &&
                      summary.insights.length === 0 && (
                        <p className="text-sm text-muted-foreground">
                          No summary available.
                        </p>
                      )}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    No summary available.
                  </p>
                )}
              </CardContent>
            </ScrollArea>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}

export function ConflictViewerDialog({
  conflict,
  collection,
  onOpenChange,
}: ConflictViewerDialogProps) {
  return (
    <Dialog open={!!conflict} onOpenChange={(v) => onOpenChange(v)}>
      <DialogContent className="pm-dialog !max-w-[90vw] !w-[90vw] h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>Conflict</DialogTitle>
        </DialogHeader>

        <div className="flex-1 flex gap-4 overflow-hidden min-h-0">
          {conflict && (
            <>
              <SourcePanel
                collection={collection}
                source={conflict.source1}
                label={conflict.source1_label ?? conflict.source1}
                content={conflict.content1}
              />
              <SourcePanel
                collection={collection}
                source={conflict.source2}
                label={conflict.source2_label ?? conflict.source2}
                content={conflict.content2}
              />
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
