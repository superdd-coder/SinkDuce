import { useState, useMemo, useEffect, useCallback, useRef } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Loader2, ChevronRight, ChevronDown, RefreshCw } from "lucide-react"
import { TiptapEditor } from "@/components/ui/tiptap-editor"
import { getFilePreviewUrl, isPreviewable, getDocSummary, setDocSummaryInclude, generateDocSummary, getExtractedText, type ChunkDetail, type DocSummary } from "@/api/client"
import { toast } from "sonner"

// Module-level: survives component unmount across tab switches
const _generating = new Map<string, number>()  // key -> startedAt

function _genKey(collection: string, source: string) {
  return `${collection}::${source}`
}

function _markGenerating(key: string) {
  _generating.set(key, Date.now())
  try { localStorage.setItem(`wk:gen:${key}`, "1") } catch { /* ignore */ }
}

function _unmarkGenerating(key: string) {
  _generating.delete(key)
  try { localStorage.removeItem(`wk:gen:${key}`) } catch { /* ignore */ }
}

function _isMarked(key: string): boolean {
  if (_generating.has(key)) return true
  // Recover from localStorage on page refresh
  try {
    if (localStorage.getItem(`wk:gen:${key}`) === "1") {
      _generating.set(key, Date.now())
      return true
    }
  } catch { /* ignore */ }
  return false
}

interface FileDetailDialogProps {
  collection: string
  source: string | null
  chunks: ChunkDetail[]
  chunksTotal: number
  loading: boolean
  onOpenChange: (open: boolean) => void
  openKey?: number
}

export function FileDetailDialog({ collection, source, chunks, chunksTotal, loading, onOpenChange, openKey }: FileDetailDialogProps) {
  const [previewContent, setPreviewContent] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [expandedParents, setExpandedParents] = useState<Set<string>>(new Set())
  const [docSummary, setDocSummary] = useState<DocSummary | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [extractedText, setExtractedText] = useState<string | null>(null)
  const [extractedLoading, setExtractedLoading] = useState(false)
  const extractedContentRef = useRef<HTMLDivElement>(null)
  const [highlightOffset, setHighlightOffset] = useState<number | undefined>(undefined)
  const [highlightPage, setHighlightPage] = useState<number | undefined>(undefined)
  const [activeTab, setActiveTab] = useState("source")
  const textContentRef = useRef<HTMLDivElement>(null)

  const isPdf = source?.toLowerCase().endsWith(".pdf")

  const genKey = collection && source ? _genKey(collection, source) : null
  const [, setRenderTick] = useState(0)
  // Force re-render when dialog reopens (openKey changes)
  useEffect(() => { setRenderTick(k => k + 1) }, [openKey])
  const isGenerating = !!(genKey && _isMarked(genKey))

  // Reset state when source changes
  useEffect(() => {
    setDocSummary(null)
    setPreviewContent(null)
    setExtractedText(null)
    setHighlightOffset(undefined)
    setHighlightPage(undefined)
    setHighlightText("")
    setHighlightLength(undefined)
    setActiveTab("source")
  }, [source])

  // Poll while generating (recovers on mount if module-level flag is set)
  useEffect(() => {
    if (!isGenerating || !collection || !source) return
    const key = genKey!
    const startedAt = _generating.get(key) || Date.now()
    const poll = setInterval(async () => {
      try {
        const current = await getDocSummary(collection, source)
        if (current) {
          clearInterval(poll)
          _unmarkGenerating(key)
          setDocSummary(current)
        } else if (Date.now() - startedAt > 300_000) {
          clearInterval(poll)
          _unmarkGenerating(key)
          toast.error("Summary generation timed out")
        }
      } catch { /* ignore */ }
    }, 2000)
    return () => clearInterval(poll)
  }, [isGenerating, collection, source, genKey])

  // Force re-render when openKey changes (to pick up module-level state)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {}, [openKey])

  const isParentChild = chunks.some((c) => c.chunk_type === "parent")

  // Group chunks: parents with their children
  const groupedChunks = useMemo(() => {
    if (!isParentChild) return null
    const groups: Array<{ parent: ChunkDetail; children: ChunkDetail[] }> = []
    let currentParent: ChunkDetail | null = null
    let currentChildren: ChunkDetail[] = []
    for (const c of chunks) {
      if (c.chunk_type === "parent") {
        if (currentParent) groups.push({ parent: currentParent, children: currentChildren })
        currentParent = c
        currentChildren = []
      } else if (c.chunk_type === "child") {
        currentChildren.push(c)
      }
    }
    if (currentParent) groups.push({ parent: currentParent, children: currentChildren })
    return groups
  }, [chunks, isParentChild])

  const toggleParent = (id: string) => {
    setExpandedParents((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleLocate = useCallback((chunk: ChunkDetail) => {
    setHighlightOffset(chunk.char_offset)
    if (chunk.page_number !== undefined) setHighlightPage(chunk.page_number)
    setHighlightText(chunk.text || "")
    setHighlightLength(chunk.text?.length)
    // Navigate to extracted tab (offsets match chunk positions)
    setActiveTab("extracted")
  }, [])

  // Scroll to highlighted chunk offset when text content loads
  useEffect(() => {
    if (highlightOffset === undefined || isPdf) return
    if (source?.toLowerCase().endsWith(".md")) return
    if (!previewContent) return
    const attemptScroll = () => {
      const el = textContentRef.current?.querySelector("mark")
      if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); return true }
      const viewport = textContentRef.current?.closest("[data-radix-scroll-area-viewport]") as HTMLElement | null
      if (viewport) {
        const mark = viewport.querySelector("mark")
        if (mark) { mark.scrollIntoView({ behavior: "smooth", block: "center" }); return true }
      }
      return false
    }
    if (!attemptScroll()) {
      const timer = setTimeout(attemptScroll, 100)
      return () => clearTimeout(timer)
    }
  }, [previewContent, highlightOffset, isPdf, source])

  // Scroll to highlighted chunk in extracted tab
  useEffect(() => {
    if (highlightOffset === undefined || activeTab !== "extracted") return
    if (!extractedText) return
    const attemptScroll = () => {
      const el = extractedContentRef.current?.querySelector("mark")
      if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); return true }
      const viewport = extractedContentRef.current?.closest("[data-radix-scroll-area-viewport]") as HTMLElement | null
      if (viewport) {
        const mark = viewport.querySelector("mark")
        if (mark) { mark.scrollIntoView({ behavior: "smooth", block: "center" }); return true }
      }
      return false
    }
    if (!attemptScroll()) {
      const timer = setTimeout(attemptScroll, 100)
      return () => clearTimeout(timer)
    }
  }, [extractedText, highlightOffset, activeTab])

  // Load preview whenever source changes
  useEffect(() => {
    if (!source) {
      setPreviewContent(null)
      return
    }
    // For non-PDF previewable files, fetch text for offset-based highlighting
    if (isPreviewable(source) && !isPdf) {
      let cancelled = false
      setPreviewLoading(true)
      fetch(getFilePreviewUrl(source))
        .then((res) => { if (!res.ok) throw new Error(`HTTP ${res.status}`); return res.text() })
        .then((text) => { if (!cancelled) setPreviewContent(text) })
        .catch(() => { if (!cancelled) setPreviewContent(null) })
        .finally(() => { if (!cancelled) setPreviewLoading(false) })
      return () => { cancelled = true }
    }
    // For PDFs, use iframe
    if (isPdf) {
      setPreviewContent(null)
      return
    }
    // For non-previewable formats, fetch raw text
    let cancelled = false
    setPreviewLoading(true)
    fetch(getFilePreviewUrl(source))
      .then((res) => { if (!res.ok) throw new Error(`HTTP ${res.status}`); return res.text() })
      .then((text) => { if (!cancelled) setPreviewContent(text) })
      .catch(() => { if (!cancelled) setPreviewContent(null) })
      .finally(() => { if (!cancelled) setPreviewLoading(false) })
    return () => { cancelled = true }
  }, [source, isPdf])

  // Load extracted text
  useEffect(() => {
    if (!source) { setExtractedText(null); return }
    let cancelled = false
    setExtractedLoading(true)
    getExtractedText(source)
      .then((res) => {
        if (!cancelled) {
          setExtractedText(res.text)
        }
      })
      .finally(() => { if (!cancelled) setExtractedLoading(false) })
    return () => { cancelled = true }
  }, [source])

  // Load doc summary when source or collection changes
  useEffect(() => {
    if (!source || !collection) {
      setDocSummary(null)
      return
    }
    let cancelled = false
    setSummaryLoading(true)
    getDocSummary(collection, source)
      .then((res) => { if (!cancelled) setDocSummary(res) })
      .catch(() => { if (!cancelled) setDocSummary(null) })
      .finally(() => { if (!cancelled) setSummaryLoading(false) })
    return () => { cancelled = true }
  }, [source, collection])

  return (
    <Dialog open={!!source} onOpenChange={(v) => onOpenChange(v)}>
      <DialogContent className="!max-w-[90vw] !w-[90vw] h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="truncate">{source}</span>
            <Badge variant="secondary" className="ml-2 shrink-0">{chunksTotal} chunks</Badge>
          </DialogTitle>
        </DialogHeader>

        <div className="flex-1 flex gap-4 overflow-hidden min-h-0">
          <div className="w-1/2 flex flex-col min-h-0">
            <Tabs value={activeTab} onValueChange={setActiveTab} className="flex flex-col h-full min-h-0">
              <TabsList variant="line" className="mb-2">
                <TabsTrigger value="source">Source</TabsTrigger>
                <TabsTrigger value="extracted">Extracted</TabsTrigger>
                <TabsTrigger value="summary">Summary</TabsTrigger>
              </TabsList>

              <TabsContent value="source" className="flex-1 overflow-hidden min-h-0">
                <div className="h-full overflow-hidden">
                  {loading || previewLoading ? (
                    <div className="flex items-center justify-center h-full text-muted-foreground">
                      <Loader2 className="h-5 w-5 animate-spin mr-2" />
                      Loading...
                    </div>
                  ) : isPdf && source ? (
                    <iframe
                      key={highlightPage ?? "default"}
                      src={highlightPage
                        ? `${getFilePreviewUrl(source)}#page=${highlightPage}`
                        : getFilePreviewUrl(source)}
                      className="w-full h-full border-0"
                      title={`Preview: ${source}`}
                    />
                  ) : previewContent !== null ? (
                    <ScrollArea className="h-full">
                      <div className="p-4">
                        <TiptapEditor
                          value={previewContent}
                          readonly
                          showToolbar={false}
                        />
                      </div>
                    </ScrollArea>
                  ) : (
                    <ScrollArea className="h-full">
                      <div className="p-4 space-y-2">
                        {chunks.map((chunk, i) => (
                          <p key={i} className="text-sm leading-relaxed whitespace-pre-wrap">{chunk.text}</p>
                        ))}
                      </div>
                    </ScrollArea>
                  )}
                </div>
              </TabsContent>

              <TabsContent value="extracted" className="flex-1 overflow-hidden min-h-0">
                <div className="h-full overflow-hidden">
                  {extractedLoading ? (
                    <div className="flex items-center justify-center h-full text-muted-foreground">
                      <Loader2 className="h-5 w-5 animate-spin mr-2" />
                      Loading extracted text...
                    </div>
                  ) : extractedText !== null ? (
                    <ScrollArea className="h-full">
                      <div ref={extractedContentRef}>
                        <div className="p-4">
                          <TiptapEditor
                            value={extractedText}
                            readonly
                            showToolbar={false}
                          />
                        </div>
                      </div>
                    </ScrollArea>
                  ) : (
                    <div className="flex items-center justify-center h-full text-muted-foreground">
                      <p className="text-sm">No extracted text available.</p>
                    </div>
                  )}
                </div>
              </TabsContent>

              <TabsContent value="summary" className="flex-1 overflow-hidden min-h-0">
                <div className="h-full overflow-hidden">
                  <ScrollArea className="h-full">
                    <div className="p-4">
                      {isGenerating ? (
                        <div className="flex flex-col items-center justify-center py-8 gap-3 text-muted-foreground">
                          <Loader2 className="h-5 w-5 animate-spin" />
                          <p className="text-sm">Generating summary...</p>
                        </div>
                      ) : summaryLoading ? (
                        <div className="flex items-center justify-center py-8 text-muted-foreground">
                          <Loader2 className="h-5 w-5 animate-spin mr-2" />
                          Loading summary...
                        </div>
                      ) : docSummary ? (
                        <div className="space-y-4">
                          {/* Include in Collection Summary toggle */}
                          <button
                            type="button"
                            onClick={async () => {
                              if (!source || !collection) return
                              const include = docSummary.include_in_summary === false
                              try {
                                await setDocSummaryInclude(collection, source, include)
                                setDocSummary({ ...docSummary, include_in_summary: include })
                              } catch { /* ignore */ }
                            }}
                            className={`flex items-center gap-2.5 w-full p-2.5 rounded-lg border text-sm transition-colors ${
                              docSummary.include_in_summary !== false
                                ? "border-primary/30 bg-primary/5 text-foreground"
                                : "border-border bg-muted/50 text-muted-foreground"
                            }`}
                          >
                            <span className={`flex items-center justify-center w-5 h-5 rounded border-2 transition-colors ${
                              docSummary.include_in_summary !== false
                                ? "border-primary bg-primary text-primary-foreground"
                                : "border-muted-foreground/40 bg-background"
                            }`}>
                              {docSummary.include_in_summary !== false && (
                                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                                </svg>
                              )}
                            </span>
                            Include in Collection Summary
                          </button>
                          <div className="flex justify-end">
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={isGenerating}
                              onClick={async () => {
                                if (!source || !collection) return
                                const key = _genKey(collection, source)
                                _markGenerating(key)
                                setRenderTick(k => k + 1)
                                setDocSummary(null)
                                setActiveTab("summary")
                                try {
                                  await generateDocSummary(collection, source)
                                } catch (err) {
                                  _unmarkGenerating(key)
                                  setRenderTick(k => k + 1)
                                  toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
                                }
                              }}
                            >
                              {isGenerating ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" /> : <RefreshCw className="h-3.5 w-3.5 mr-1.5" />}
                              Re-summarize
                            </Button>
                          </div>
                          {docSummary.data.length > 0 && (
                            <div>
                              <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Data Points</h5>
                              <ul className="space-y-1">
                                {docSummary.data.map((item, i) => (
                                  <li key={i} className="text-sm leading-relaxed">{item}</li>
                                ))}
                              </ul>
                            </div>
                          )}
                          {docSummary.facts.length > 0 && (
                            <div>
                              <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Facts</h5>
                              <ul className="space-y-1">
                                {docSummary.facts.map((item, i) => (
                                  <li key={i} className="text-sm leading-relaxed">{item}</li>
                                ))}
                              </ul>
                            </div>
                          )}
                          {docSummary.insights.length > 0 && (
                            <div>
                              <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Insights</h5>
                              <ul className="space-y-1">
                                {docSummary.insights.map((item, i) => (
                                  <li key={i} className="text-sm leading-relaxed">{item}</li>
                                ))}
                              </ul>
                            </div>
                          )}
                          {docSummary.data.length === 0 && docSummary.facts.length === 0 && docSummary.insights.length === 0 && (
                            <p className="text-sm text-muted-foreground">No summary available for this document.</p>
                          )}
                        </div>
                      ) : (
                        <div className="flex flex-col items-center justify-center py-8 gap-3">
                          <p className="text-sm text-muted-foreground">No summary available for this document.</p>
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={!source || !collection || isGenerating}
                            onClick={async () => {
                              if (!source || !collection) return
                              const key = _genKey(collection, source)
                              _markGenerating(key)
                              setRenderTick(k => k + 1)
                              setDocSummary(null)
                              setActiveTab("summary")
                              try {
                                await generateDocSummary(collection, source)
                              } catch (err) {
                                _unmarkGenerating(key)
                                setRenderTick(k => k + 1)
                                toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
                              }
                            }}
                          >
                            <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
                            Summarize
                          </Button>
                        </div>
                      )}
                    </div>
                  </ScrollArea>
                </div>
              </TabsContent>
            </Tabs>
          </div>

          <div className="w-1/2 flex flex-col">
            <h4 className="text-sm font-medium mb-2 text-muted-foreground">Chunks</h4>
            <div className="flex-1 overflow-hidden rounded-lg border border-border">
              <ScrollArea className="h-full">
                <div className="p-4 space-y-3">
                  {loading ? (
                    <div className="flex items-center justify-center h-full text-muted-foreground">
                      <Loader2 className="h-5 w-5 animate-spin mr-2" />
                      Loading...
                    </div>
                  ) : chunks.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No chunks</p>
                  ) : groupedChunks ? (
                    // Parent-child mode: expandable tree view
                    groupedChunks.map((group) => {
                      const isExpanded = expandedParents.has(group.parent.id)
                      return (
                        <div key={group.parent.id} className="border border-border rounded-lg overflow-hidden">
                          <button
                            className="w-full text-left p-3 hover:bg-accent/50 transition-colors flex items-start gap-2"
                            onClick={() => toggleParent(group.parent.id)}
                          >
                            {isExpanded ? <ChevronDown className="h-4 w-4 mt-0.5 shrink-0" /> : <ChevronRight className="h-4 w-4 mt-0.5 shrink-0" />}
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2 mb-1">
                                <Badge variant="default" className="text-[10px]">Parent #{group.parent.chunk_index}</Badge>
                                <Badge variant="outline" className="text-[10px]">{group.children.length} children</Badge>
                                {group.parent.heading_path && (
                                  <span className="text-[10px] text-muted-foreground truncate">{group.parent.heading_path}</span>
                                )}
                                <button
                                  className="ml-auto p-0.5 rounded hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
                                  title="Locate in preview"
                                  onClick={(e) => { e.stopPropagation(); handleLocate(group.parent) }}
                                />
                              </div>
                              <p className="text-sm leading-relaxed whitespace-pre-wrap text-muted-foreground line-clamp-3">{group.parent.text}</p>
                            </div>
                          </button>
                          {isExpanded && (
                            <div className="border-t border-border bg-muted/30 p-3 space-y-2 pl-8">
                              {/* Parent full text */}
                              <div className="mb-3">
                                <p className="text-xs text-muted-foreground font-medium mb-1">Parent full text:</p>
                                <p className="text-sm leading-relaxed whitespace-pre-wrap">{group.parent.text}</p>
                              </div>
                              {group.parent.context && (
                                <div className="mb-3 pl-3 border-l-2 border-primary/30">
                                  <p className="text-xs text-muted-foreground italic">{group.parent.context}</p>
                                </div>
                              )}
                              {/* Children */}
                              {group.children.map((child) => (
                                <div
                                  key={child.id}
                                  className="border border-border rounded-lg p-3 bg-background cursor-pointer hover:bg-accent/50 transition-colors"
                                  onClick={() => handleLocate(child)}
                                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") handleLocate(child) }}
                                  role="button"
                                  tabIndex={0}
                                >
                                  <div className="flex items-center gap-2 mb-2">
                                    <Badge variant="secondary" className="text-[10px]">Child #{child.chunk_index}</Badge>
                                    {child.context && (
                                      <span className="text-[10px] text-muted-foreground italic">with context</span>
                                    )}
                                    
                                  </div>
                                  {child.context && (
                                    <div className="mb-2 pl-3 border-l-2 border-primary/30">
                                      <p className="text-xs text-muted-foreground italic">{child.context}</p>
                                    </div>
                                  )}
                                  <p className="text-sm leading-relaxed whitespace-pre-wrap">{child.text}</p>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )
                    })
                  ) : (
                    // Normal mode: flat list
                    chunks.map((chunk) => (
                      <div
                        key={chunk.id}
                        className="border border-border rounded-lg p-3 cursor-pointer hover:bg-accent/50 transition-colors"
                        onClick={() => handleLocate(chunk)}
                        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") handleLocate(chunk) }}
                        role="button"
                        tabIndex={0}
                      >
                        <div className="flex items-center gap-2 mb-2">
                          <Badge variant="outline" className="text-[10px]">Chunk #{chunk.chunk_index}</Badge>
                          {chunk.heading_path && (
                            <span className="text-[10px] text-muted-foreground truncate">{chunk.heading_path}</span>
                          )}
                          {!chunk.heading_path && chunk.section_label && (
                            <Badge variant="secondary" className="text-[10px]">{chunk.section_label}</Badge>
                          )}
                          {chunk.context && (
                            <span className="text-[10px] text-muted-foreground italic">with context</span>
                          )}
                          
                        </div>
                        {chunk.context && (
                          <div className="mb-2 pl-3 border-l-2 border-primary/30">
                            <p className="text-xs text-muted-foreground italic">{chunk.context}</p>
                          </div>
                        )}
                        <p className="text-sm leading-relaxed whitespace-pre-wrap">{chunk.text}</p>
                      </div>
                    ))
                  )}
                </div>
              </ScrollArea>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
