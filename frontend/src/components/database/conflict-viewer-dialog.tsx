import { useState, useEffect } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import { CardContent } from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Loader2 } from "lucide-react"
import { getDocSummary, getFilePreviewUrl, getExtractedText, type ConflictItem, type DocSummary } from "@/api/client"

interface ConflictViewerDialogProps {
  conflict: ConflictItem | null
  collection: string
  onOpenChange: (open: boolean) => void
}

function SourcePanel({ collection, source, label, content }: { collection: string; source: string; label: string; content: string }) {
  const [summary, setSummary] = useState<DocSummary | null>(null)
  const [previewContent, setPreviewContent] = useState<string | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [previewLoading, setPreviewLoading] = useState(false)

  // Use the human-readable label to detect PDF (PDF is rendered via iframe).
  // For all other types (including notes, which have no extension), fall back
  // to getExtractedText which works for both files and notes.
  const isPdf = label.toLowerCase().endsWith(".pdf")

  useEffect(() => {
    if (!collection || !source) return
    let cancelled = false

    // Load summary
    setSummaryLoading(true)
    getDocSummary(collection, source)
      .then((res) => { if (!cancelled) setSummary(res) })
      .catch(() => { if (!cancelled) setSummary(null) })
      .finally(() => { if (!cancelled) setSummaryLoading(false) })

    // Load preview. PDF uses iframe; everything else (incl. notes) uses
    // getExtractedText — same approach as FileDetailDialog.
    if (isPdf) {
      setPreviewContent(null)
    } else {
      setPreviewLoading(true)
      getExtractedText(source, collection)
        .then((res) => { if (!cancelled) setPreviewContent(res.text) })
        .catch(() => { if (!cancelled) setPreviewContent(null) })
        .finally(() => { if (!cancelled) setPreviewLoading(false) })
    }

    return () => { cancelled = true }
  }, [collection, source, isPdf])

  return (
    <div className="w-1/2 flex flex-col min-h-0">
      <div className="flex items-center gap-2 mb-2">
        <h4 className="text-sm font-medium text-muted-foreground truncate">{label}</h4>
      </div>
      <div className="flex-1 overflow-hidden rounded-lg border border-border min-h-0">
        <Tabs defaultValue="source" className="flex flex-col h-full">
          <TabsList variant="line" className="mx-2 mt-2">
            <TabsTrigger value="source">Source</TabsTrigger>
            <TabsTrigger value="summary">Summary</TabsTrigger>
          </TabsList>

          <TabsContent value="source" className="flex-1 overflow-hidden min-h-0">
            <div className="h-full">
              {/* Conflicting content highlight */}
              <div className="px-4 pt-3 pb-2">
                <p className="text-sm leading-relaxed whitespace-pre-wrap text-amber-600 dark:text-amber-400 font-medium border-l-2 border-amber-400 pl-3">
                  "{content}"
                </p>
              </div>
              {/* Source file preview */}
              {isPdf ? (
                <iframe
                  src={getFilePreviewUrl(source)}
                  className="w-full h-[calc(100%-60px)] border-0"
                  title={`Preview: ${label}`}
                />
              ) : previewLoading ? (
                <div className="flex items-center justify-center py-8 text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin mr-2" />
                  Loading source...
                </div>
              ) : previewContent !== null ? (
                <ScrollArea className="h-[calc(100%-60px)]">
                  <CardContent className="px-4 pb-4">
                    <pre className="text-sm leading-relaxed whitespace-pre-wrap t-sans-family">{previewContent}</pre>
                  </CardContent>
                </ScrollArea>
              ) : (
                <div className="flex items-center justify-center py-8 text-muted-foreground">
                  <p className="text-sm">Preview not available for this file type.</p>
                </div>
              )}
            </div>
          </TabsContent>

          <TabsContent value="summary" className="flex-1 overflow-hidden min-h-0">
            <ScrollArea className="h-full">
              <CardContent className="p-4">
                {summaryLoading ? (
                  <div className="flex items-center justify-center py-8 text-muted-foreground">
                    <Loader2 className="h-5 w-5 animate-spin mr-2" />
                    Loading summary...
                  </div>
                ) : summary ? (
                  <div className="space-y-4">
                    {summary.data.length > 0 && (
                      <div>
                        <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Data Points</h5>
                        <ul className="space-y-1">
                          {summary.data.map((item, i) => (
                            <li key={i} className="text-sm leading-relaxed">{item}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {summary.facts.length > 0 && (
                      <div>
                        <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Facts</h5>
                        <ul className="space-y-1">
                          {summary.facts.map((item, i) => (
                            <li key={i} className="text-sm leading-relaxed">{item}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {summary.insights.length > 0 && (
                      <div>
                        <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Insights</h5>
                        <ul className="space-y-1">
                          {summary.insights.map((item, i) => (
                            <li key={i} className="text-sm leading-relaxed">{item}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {summary.data.length === 0 && summary.facts.length === 0 && summary.insights.length === 0 && (
                      <p className="text-sm text-muted-foreground">No summary available.</p>
                    )}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">No summary available.</p>
                )}
              </CardContent>
            </ScrollArea>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}

export function ConflictViewerDialog({ conflict, collection, onOpenChange }: ConflictViewerDialogProps) {
  return (
    <Dialog open={!!conflict} onOpenChange={(v) => onOpenChange(v)}>
      <DialogContent className="!max-w-[90vw] !w-[90vw] h-[85vh] flex flex-col">
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
