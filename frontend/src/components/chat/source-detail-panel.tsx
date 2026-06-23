import { useState, useMemo, useEffect, useCallback, useRef } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger, TabsIndicator } from "@/components/ui/tabs"
import { Loader2, X, ChevronRight, ChevronDown, RefreshCw, Locate } from "lucide-react"
import { TiptapEditor } from "@/components/ui/tiptap-editor"
import type { Editor } from "@tiptap/core"
import { getFileChunks, getFilePreviewUrl, getDocSummary, generateDocSummary, setDocSummaryInclude, getExtractedText, type ChunkDetail, type DocSummary } from "@/api/client"
import type { Source } from "@/stores/app-store"
import { toast } from "sonner"

interface SourceDetailPanelProps {
  source: Source | null
  onClose: () => void
}

const _generating = new Map<string, number>()

function _genKey(collection: string, source: string) {
  return `${collection}::${source}`
}

function _markGenerating(key: string) {
  _generating.set(key, Date.now())
  try { localStorage.setItem(`wk:sgen:${key}`, "1") } catch { /* ignore */ }
}

function _unmarkGenerating(key: string) {
  _generating.delete(key)
  try { localStorage.removeItem(`wk:sgen:${key}`) } catch { /* ignore */ }
}

function _isPdf(name: string) {
  return name.toLowerCase().endsWith(".pdf")
}

function _getHighlightOffset(source: Source): number | undefined {
  const v = source.metadata?.char_offset
  return typeof v === "number" ? v : undefined
}

export function SourceDetailPanel({ source, onClose }: SourceDetailPanelProps) {
  const [previewContent, setPreviewContent] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [chunks, setChunks] = useState<ChunkDetail[]>([])
  const [chunksLoading, setChunksLoading] = useState(false)
  const [expandedParents, setExpandedParents] = useState<Set<string>>(new Set())
  const [docSummary, setDocSummary] = useState<DocSummary | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [activeTab, setActiveTab] = useState("preview")
  const [highlightOffset, setHighlightOffset] = useState<number | undefined>(undefined)
  const [highlightPage, setHighlightPage] = useState<number | undefined>(undefined)
  const sourceContentRef = useRef<HTMLDivElement>(null)
  const sourceEditorRef = useRef<Editor | null>(null)

  const sourceName = source?.metadata?.source as string | undefined
  const collection = source?.metadata?.collection as string | undefined
  const chunkId = source?.metadata?.id as string | undefined
  const isPdfFile = sourceName ? _isPdf(sourceName) : false

  const genKey = collection && sourceName ? _genKey(collection, sourceName) : null
  const isGenerating = !!(genKey && _generating.has(genKey))

  // Reset file-level state only when the file changes
  useEffect(() => {
    setPreviewContent(null)
    setChunks([])
    setDocSummary(null)
    setExpandedParents(new Set())
    setActiveTab("preview")
  }, [collection, sourceName])

  // Update highlight position when selected source chunk changes
  // (same file, different chunk — must fire even though collection/sourceName unchanged)
  useEffect(() => {
    const offset = source ? _getHighlightOffset(source) : undefined
    setHighlightOffset(offset)
    setHighlightPage(source?.metadata?.page_number as number | undefined)
  }, [source?.metadata?.id])

  // Load chunks
  useEffect(() => {
    if (!collection || !sourceName) return
    let cancelled = false
    setChunksLoading(true)
    getFileChunks(collection, sourceName, 10000)
      .then((res) => { if (!cancelled) setChunks(res.chunks) })
      .catch((err) => {
        if (!cancelled) {
          console.warn("[SourceDetailPanel] Failed to load chunks:", collection, sourceName, err)
          setChunks([])
        }
      })
      .finally(() => { if (!cancelled) setChunksLoading(false) })
    return () => { cancelled = true }
  }, [collection, sourceName, chunkId])

  // Load source content: PDF uses iframe, non-PDF loads parsed/extracted text
  useEffect(() => {
    if (!sourceName) { setPreviewContent(null); return }
    // PDF files render via iframe — no text preview needed
    if (_isPdf(sourceName)) { setPreviewContent(null); return }
    let cancelled = false
    setPreviewLoading(true)
    getExtractedText(sourceName)
      .then((res) => {
        if (!cancelled) setPreviewContent(res.text)
      })
      .catch(() => { if (!cancelled) setPreviewContent(null) })
      .finally(() => { if (!cancelled) setPreviewLoading(false) })
    return () => { cancelled = true }
  }, [sourceName])

  // Load summary
  useEffect(() => {
    if (!sourceName || !collection) { setDocSummary(null); return }
    let cancelled = false
    setSummaryLoading(true)
    getDocSummary(collection, sourceName)
      .then(res => { if (!cancelled) setDocSummary(res) })
      .catch(() => { if (!cancelled) setDocSummary(null) })
      .finally(() => { if (!cancelled) setSummaryLoading(false) })
    return () => { cancelled = true }
  }, [sourceName, collection])

  // Poll while generating summary
  useEffect(() => {
    if (!isGenerating || !collection || !sourceName) return
    const poll = setInterval(async () => {
      try {
        const current = await getDocSummary(collection, sourceName)
        if (current) {
          clearInterval(poll)
          _unmarkGenerating(genKey!)
          setDocSummary(current)
        }
      } catch { /* ignore */ }
    }, 2000)
    return () => clearInterval(poll)
  }, [isGenerating, collection, sourceName, genKey])

  // Scroll to highlightOffset — map raw-markdown offset → ProseMirror position.
  // Uses textContent (not content.size) for ratio: textContent strips node-gate
  // overhead, leaving only the syntax-char drift which is roughly proportional.
  useEffect(() => {
    if (highlightOffset === undefined || isPdfFile) return
    if (!previewContent) return
    const editor = sourceEditorRef.current
    if (!editor || (editor as any).isDestroyed) return
    const rawLen = previewContent.length
    const textLen = editor.state.doc.textContent.length
    if (rawLen <= 1 || textLen <= 1) return
    // Estimate text position: how many non-syntax chars precede highlightOffset
    const textTarget = Math.round(highlightOffset * (textLen / rawLen))
    // Binary-search the ProseMirror position where cumulative text reaches textTarget
    let lo = 1
    let hi = editor.state.doc.content.size
    while (lo < hi) {
      const mid = Math.floor((lo + hi) / 2)
      if (editor.state.doc.textBetween(0, mid).length < textTarget) lo = mid + 1
      else hi = mid
    }
    const resolved = editor.state.doc.resolve(lo)
    const domPos = editor.view.domAtPos(resolved.pos)
    const node = domPos.node
    const el = node.nodeType === 3 /* TEXT_NODE */ ? node.parentElement : node as HTMLElement
    el?.scrollIntoView({ behavior: "smooth", block: "start" })
  }, [previewContent, highlightOffset, isPdfFile])

  const isParentChild = chunks.some(c => c.chunk_type === "parent")

  const groupedChunks = useMemo(() => {
    if (!isParentChild) return null
    const groups: Array<{ parent: ChunkDetail; children: ChunkDetail[] }> = []
    let curParent: ChunkDetail | null = null
    let curChildren: ChunkDetail[] = []
    for (const c of chunks) {
      if (c.chunk_type === "parent") {
        if (curParent) groups.push({ parent: curParent, children: curChildren })
        curParent = c
        curChildren = []
      } else if (c.chunk_type === "child") {
        curChildren.push(c)
      }
    }
    if (curParent) groups.push({ parent: curParent, children: curChildren })
    return groups
  }, [chunks, isParentChild])

  const toggleParent = useCallback((id: string) => {
    setExpandedParents(prev => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }, [])

  const handleLocate = useCallback((offset?: number, pageNumber?: number, _length?: number) => {
    console.log("[handleLocate]", { offset, _length, pageNumber, previewLen: previewContent?.length })
    setHighlightOffset(offset)
    if (pageNumber !== undefined) setHighlightPage(pageNumber)
    setActiveTab("preview")
  }, [previewContent])


  if (!source || !sourceName) return null

  return (
    <div className="h-full flex flex-col border-l border-border bg-background">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <span className="text-sm font-medium truncate" title={sourceName}>{sourceName}</span>
          <Badge variant="outline" className="text-[10px] px-1.5 py-0 shrink-0">
            {(source.score * 100).toFixed(0)}%
          </Badge>
          {collection && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0 shrink-0">{collection}</Badge>
          )}
        </div>
        <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0 ml-2" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </div>

      {/* Chunk preview (compact, always visible) */}
      <div className="px-4 py-2 border-b border-border/50 shrink-0 bg-muted/20 max-h-40 overflow-y-auto">
        <div className="flex items-start gap-2">
          <p className="text-xs text-muted-foreground leading-relaxed whitespace-pre-wrap flex-1 min-w-0">
            {source.text}
          </p>
          <button
            className="p-0.5 rounded hover:bg-accent text-muted-foreground hover:text-foreground transition-colors shrink-0 mt-0.5"
            title="Locate in preview"
            onClick={() => handleLocate(
              _getHighlightOffset(source),
              source.metadata?.page_number as number | undefined,
              source.text?.length
            )}
          >
            <Locate className="h-3 w-3" />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex-1 flex flex-col min-h-0 px-2">
        <Tabs value={activeTab} onValueChange={setActiveTab} className="flex flex-col h-full min-h-0">
          <TabsList variant="line" className="mb-1 shrink-0 relative">
            <TabsIndicator renderBeforeHydration />
            <TabsTrigger value="preview" className="font-light uppercase tracking-wider after:!opacity-0 data-[state=active]:text-primary">SOURCE</TabsTrigger>
            <TabsTrigger value="chunks" className="font-light uppercase tracking-wider after:!opacity-0 data-[state=active]:text-primary">CHUNKS{chunks.length > 0 ? ` (${chunks.length})` : ""}</TabsTrigger>
            <TabsTrigger value="summary" className="font-light uppercase tracking-wider after:!opacity-0 data-[state=active]:text-primary">SUMMARY</TabsTrigger>
          </TabsList>

          {/* Preview Tab */}
          <TabsContent key={`preview-${activeTab}`} value="preview" className="flex-1 overflow-hidden min-h-0 animate-tab-in">
            <div className="flex-1 overflow-hidden rounded-lg border border-border h-full">
              {previewLoading || chunksLoading ? (
                <div className="flex items-center justify-center h-full text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin mr-2" />
                  Loading...
                </div>
              ) : isPdfFile && sourceName ? (
                /* PDF: iframe with optional page jump */
                <iframe
                  key={highlightPage ?? "default"}
                  src={highlightPage
                    ? `${getFilePreviewUrl(sourceName)}#page=${highlightPage}`
                    : getFilePreviewUrl(sourceName)}
                  className="w-full h-full border-0"
                  title={`Preview: ${sourceName}`}
                />
              ) : previewContent !== null ? (
                <ScrollArea className="h-full">
                  <div ref={sourceContentRef} className="p-3">
                    <TiptapEditor
                      value={previewContent ?? ""}
                      readonly
                      showToolbar={false}
                      onEditorReady={(e) => { sourceEditorRef.current = e }}
                    />
                  </div>
                </ScrollArea>
              ) : (
                <ScrollArea className="h-full">
                  <CardContent className="p-4 space-y-2">
                    {chunks.map((chunk, i) => (
                      <p key={i} className="text-sm leading-relaxed whitespace-pre-wrap">{chunk.text}</p>
                    ))}
                  </CardContent>
                </ScrollArea>
              )}
            </div>
          </TabsContent>

          {/* Chunks Tab */}
          <TabsContent key={`chunks-${activeTab}`} value="chunks" className="flex-1 overflow-hidden min-h-0 animate-tab-in">
            <div className="flex-1 overflow-hidden rounded-lg border border-border h-full">
              <ScrollArea className="h-full">
                <CardContent className="p-3 space-y-2">
                  {chunksLoading ? (
                    <div className="flex items-center justify-center py-8 text-muted-foreground">
                      <Loader2 className="h-5 w-5 animate-spin mr-2" />
                      Loading chunks...
                    </div>
                  ) : chunks.length === 0 ? (
                    <p className="text-sm text-muted-foreground py-4 text-center">No chunks</p>
                  ) : groupedChunks ? (
                    groupedChunks.map(group => {
                      const isExpanded = expandedParents.has(group.parent.id)
                      const isTargetParent = group.parent.id === chunkId
                      return (
                        <div key={group.parent.id} className={`border rounded-lg overflow-hidden ${isTargetParent ? "border-primary ring-1 ring-primary/30" : "border-border"}`}>
                          <div
                            className="w-full text-left p-2.5 hover:bg-accent/50 transition-colors flex items-start gap-2 cursor-pointer"
                            onClick={() => toggleParent(group.parent.id)}
                            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") toggleParent(group.parent.id) }}
                            role="button"
                            tabIndex={0}
                          >
                            {isExpanded ? <ChevronDown className="h-3.5 w-3.5 mt-0.5 shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 mt-0.5 shrink-0" />}
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-1.5 mb-1">
                                <Badge variant={isTargetParent ? "default" : "outline"} className="text-[10px]">Parent #{group.parent.chunk_index}</Badge>
                                <Badge variant="outline" className="text-[10px]">{group.children.length} children</Badge>
                                {group.parent.section_label && (
                                  <Badge variant="secondary" className="text-[10px]">{group.parent.section_label}</Badge>
                                )}
                                <button
                                  className="ml-auto p-0.5 rounded hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
                                  title="Locate in preview"
                                  onClick={(e) => { e.stopPropagation(); handleLocate(group.parent.char_offset, group.parent.page_number, group.parent.text?.length) }}
                                >
                                  <Locate className="h-3 w-3" />
                                </button>
                              </div>
                              <p className="text-xs leading-relaxed whitespace-pre-wrap text-muted-foreground line-clamp-2">{group.parent.text}</p>
                            </div>
                          </div>
                          {isExpanded && (
                            <div className="border-t border-border bg-muted/30 p-2.5 space-y-2 pl-7">
                              <div>
                                <p className="text-xs text-muted-foreground font-medium mb-1">Full text:</p>
                                <p className="text-xs leading-relaxed whitespace-pre-wrap">{group.parent.text}</p>
                              </div>
                              {group.parent.context && (
                                <div className="pl-2.5 border-l-2 border-primary/30">
                                  <p className="text-[11px] text-muted-foreground italic">{group.parent.context}</p>
                                </div>
                              )}
                              {group.children.map(child => {
                                const isTargetChild = child.id === chunkId
                                return (
                                  <div
                                    key={child.id}
                                    className={`border rounded-lg p-2.5 bg-background cursor-pointer hover:bg-accent/50 transition-colors ${isTargetChild ? "border-primary ring-1 ring-primary/30" : "border-border"}`}
                                    onClick={(e) => { e.stopPropagation(); handleLocate(child.char_offset, child.page_number, child.text?.length) }}
                                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.stopPropagation(); handleLocate(child.char_offset, child.page_number, child.text?.length) } }}
                                    role="button"
                                    tabIndex={0}
                                  >
                                    <div className="flex items-center gap-1.5 mb-1.5">
                                      <Badge variant={isTargetChild ? "default" : "secondary"} className="text-[10px]">Child #{child.chunk_index}</Badge>
                                      <Locate className="ml-auto h-3 w-3 text-muted-foreground shrink-0" />
                                    </div>
                                    {child.context && (
                                      <div className="mb-1.5 pl-2.5 border-l-2 border-primary/30">
                                        <p className="text-[11px] text-muted-foreground italic">{child.context}</p>
                                      </div>
                                    )}
                                    <p className="text-xs leading-relaxed whitespace-pre-wrap">{child.text}</p>
                                  </div>
                                )
                              })}
                            </div>
                          )}
                        </div>
                      )
                    })
                  ) : (
                    chunks.map(chunk => {
                      const isTarget = chunk.id === chunkId
                      return (
                        <div
                          key={chunk.id}
                          className={`border rounded-lg p-2.5 cursor-pointer hover:bg-accent/50 transition-colors ${isTarget ? "border-primary ring-1 ring-primary/30 bg-primary/5" : "border-border"}`}
                          onClick={() => handleLocate(chunk.char_offset, chunk.page_number, chunk.text?.length)}
                          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") handleLocate(chunk.char_offset, chunk.page_number, chunk.text?.length) }}
                          role="button"
                          tabIndex={0}
                        >
                          <div className="flex items-center gap-1.5 mb-1.5">
                            <Badge variant={isTarget ? "default" : "outline"} className="text-[10px]">Chunk #{chunk.chunk_index}</Badge>
                            {chunk.section_label && (
                              <Badge variant="secondary" className="text-[10px]">{chunk.section_label}</Badge>
                            )}
                            {chunk.context && <span className="text-[10px] text-muted-foreground italic">with context</span>}
                            {isTarget && <span className="text-[10px] text-primary font-medium">← retrieved</span>}
                            <Locate className="ml-auto h-3 w-3 text-muted-foreground shrink-0" />
                          </div>
                          {chunk.context && (
                            <div className="mb-1.5 pl-2.5 border-l-2 border-primary/30">
                              <p className="text-[11px] text-muted-foreground italic">{chunk.context}</p>
                            </div>
                          )}
                          <p className="text-xs leading-relaxed whitespace-pre-wrap">{chunk.text}</p>
                        </div>
                      )
                    })
                  )}
                </CardContent>
              </ScrollArea>
            </div>
          </TabsContent>

          {/* Summary Tab */}
          <TabsContent key={`summary-${activeTab}`} value="summary" className="flex-1 overflow-hidden min-h-0 animate-tab-in">
            <div className="flex-1 overflow-hidden rounded-lg border border-border h-full">
              <ScrollArea className="h-full">
                <CardContent className="p-4">
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
                      <button
                        type="button"
                        onClick={async () => {
                          if (!sourceName || !collection) return
                          const include = docSummary.include_in_summary === false
                          try {
                            await setDocSummaryInclude(collection, sourceName, include)
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
                          variant="ghost" size="sm"
                          className="font-light uppercase tracking-wider text-primary"
                          disabled={isGenerating}
                          onClick={async () => {
                            if (!sourceName || !collection) return
                            _markGenerating(genKey!)
                            setDocSummary(null)
                            try { await generateDocSummary(collection, sourceName) }
                            catch (err) {
                              _unmarkGenerating(genKey!)
                              toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
                            }
                          }}
                        >
                          {isGenerating ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" /> : <RefreshCw className="h-3.5 w-3.5 mr-1.5" />}
                          RE-SUMMARIZE
                        </Button>
                      </div>
                      {docSummary.data.length > 0 && (
                        <div>
                          <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Data Points</h5>
                          <ul className="space-y-1">{docSummary.data.map((item, i) => <li key={i} className="text-sm">{item}</li>)}</ul>
                        </div>
                      )}
                      {docSummary.facts.length > 0 && (
                        <div>
                          <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Facts</h5>
                          <ul className="space-y-1">{docSummary.facts.map((item, i) => <li key={i} className="text-sm">{item}</li>)}</ul>
                        </div>
                      )}
                      {docSummary.insights.length > 0 && (
                        <div>
                          <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Insights</h5>
                          <ul className="space-y-1">{docSummary.insights.map((item, i) => <li key={i} className="text-sm">{item}</li>)}</ul>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="flex flex-col items-center justify-center py-8 gap-3">
                      <p className="text-sm text-muted-foreground">No summary available.</p>
                      <Button
                        variant="outline" size="sm"
                        className="font-light uppercase tracking-wider text-primary border-primary"
                        disabled={!sourceName || !collection || isGenerating}
                        onClick={async () => {
                          if (!sourceName || !collection) return
                          _markGenerating(genKey!)
                          setDocSummary(null)
                          try { await generateDocSummary(collection, sourceName) }
                          catch (err) {
                            _unmarkGenerating(genKey!)
                            toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
                          }
                        }}
                      >
                        <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
                        SUMMARIZE
                      </Button>
                    </div>
                  )}
                </CardContent>
              </ScrollArea>
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
