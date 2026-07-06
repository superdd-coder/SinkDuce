import { useState, useMemo, useEffect, useCallback, useRef } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger, TabsIndicator } from "@/components/ui/tabs"
import { Loader2, X, ChevronRight, ChevronDown, Locate } from "lucide-react"
import { TiptapEditor } from "@/components/ui/tiptap-editor"
import type { Editor } from "@tiptap/core"
import { transformImageBlocks } from "@/lib/utils"
import { getFileChunks, getFilePreviewUrl, getDocSummary, getExtractedText, type ChunkDetail, type DocSummary } from "@/api/client"
import { useAppStore, type Source } from "@/stores/app-store"

interface SourceDetailPanelProps {
  source: Source | null
  onClose: () => void
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
  // Force scroll effect to re-run on every locate click
  const [locateTick, setLocateTick] = useState(0)
  const sourceContentRef = useRef<HTMLDivElement>(null)
  const sourceEditorRef = useRef<Editor | null>(null)
  // Store latest previewContent in ref so handleLocate can access it without stale closure
  const previewContentRef = useRef<string | null>(null)

  // sourceKey = internal file path for API calls
  const sourceKey = source?.metadata?.source as string || ""
  // displayName = human-readable filename for UI
  const displayName = (source?.metadata?.source_label as string) || sourceKey
  const collectionId = source?.metadata?.collection as string || ""
  const chunkId = source?.metadata?.id as string | undefined
  const { collections } = useAppStore()
  const collectionDisplay = collections.find(c => c.id === collectionId)?.name || collectionId
  const isPdfFile = _isPdf(displayName)

  // Reset file-level state when file changes
  useEffect(() => {
    setPreviewContent(null); previewContentRef.current = null
    setChunks([])
    setDocSummary(null)
    setExpandedParents(new Set())
    setActiveTab("preview")
  }, [collectionId, sourceKey])

  // Update highlight when selected source chunk changes
  useEffect(() => {
    const offset = source ? _getHighlightOffset(source) : undefined
    setHighlightOffset(offset)
    setHighlightPage(source?.metadata?.page_number as number | undefined)
    // Bump tick so scroll effect re-fires even if offset is the same
    setLocateTick(t => t + 1)
  }, [source?.metadata?.id])

  // Load chunks
  useEffect(() => {
    if (!collectionId || !sourceKey) return
    let cancelled = false
    setChunksLoading(true)
    getFileChunks(collectionId, sourceKey, 10000)
      .then((res) => { if (!cancelled) setChunks(res.chunks) })
      .catch((err) => {
        if (!cancelled) {
          console.warn("[SourceDetailPanel] Failed to load chunks:", collectionId, sourceKey, err)
          setChunks([])
        }
      })
      .finally(() => { if (!cancelled) setChunksLoading(false) })
    return () => { cancelled = true }
  }, [collectionId, sourceKey, chunkId])

  // Load source content: parsed/extracted text (works for PDF too via parsed.txt)
  useEffect(() => {
    if (!sourceKey) { setPreviewContent(null); return }
    let cancelled = false
    setPreviewLoading(true)
    getExtractedText(sourceKey, collectionId)
      .then((res) => {
        if (!cancelled) { setPreviewContent(res.text); previewContentRef.current = res.text }
      })
      .catch(() => { if (!cancelled) setPreviewContent(null); previewContentRef.current = null })
      .finally(() => { if (!cancelled) setPreviewLoading(false) })
    return () => { cancelled = true }
  }, [sourceKey, collectionId])

  // Load summary
  useEffect(() => {
    if (!sourceKey || !collectionId) { setDocSummary(null); return }
    let cancelled = false
    setSummaryLoading(true)
    getDocSummary(collectionId, sourceKey)
      .then(res => { if (!cancelled) setDocSummary(res) })
      .catch(() => { if (!cancelled) setDocSummary(null) })
      .finally(() => { if (!cancelled) setSummaryLoading(false) })
    return () => { cancelled = true }
  }, [sourceKey, collectionId])

  // Scroll to highlightOffset — map raw-markdown offset → ProseMirror position.
  useEffect(() => {
    const offset = highlightOffset
    if (offset === undefined) return
    const raw = previewContentRef.current
    if (!raw || raw.length <= 1) return
    // Delay slightly to ensure React has committed the tab switch / DOM update
    const timer = setTimeout(() => {
      const editor = sourceEditorRef.current
      if (!editor || (editor as any).isDestroyed) return
      const textLen = editor.state.doc.textContent.length
      if (textLen <= 1) return
      const textTarget = Math.round(offset * (textLen / raw.length))
      let lo = 1, hi = editor.state.doc.content.size
      while (lo < hi) {
        const mid = Math.floor((lo + hi) / 2)
        if (editor.state.doc.textBetween(0, mid).length < textTarget) lo = mid + 1
        else hi = mid
      }
      const resolved = editor.state.doc.resolve(lo)
      const domPos = editor.view.domAtPos(resolved.pos)
      const node = domPos.node
      const el = node.nodeType === 3 ? node.parentElement : node as HTMLElement
      el?.scrollIntoView({ behavior: "smooth", block: "start" })
    }, 100)
    return () => clearTimeout(timer)
  }, [previewContent, highlightOffset, locateTick])

  // ── Chunk grouping ─────────────────────────────────────────────
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
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const handleLocate = useCallback((offset?: number, pageNumber?: number, _length?: number) => {
    setHighlightOffset(offset)
    if (pageNumber !== undefined) setHighlightPage(pageNumber)
    setActiveTab("preview")
    setLocateTick(t => t + 1)
  }, [])

  if (!source || !sourceKey) return null

  return (
    <div className="h-full flex flex-col border-l border-border bg-background">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <span className="text-sm font-medium truncate" title={displayName}>{displayName}</span>
          <Badge variant="outline" className="text-[10px] px-1.5 py-0 shrink-0">
            {(source.score * 100).toFixed(0)}%
          </Badge>
          {collectionDisplay && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0 shrink-0">{collectionDisplay}</Badge>
          )}
        </div>
        <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0 ml-2" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </div>

      {/* Current chunk preview bar */}
      <div className="px-4 py-2 border-b border-border/50 shrink-0 bg-muted/20 max-h-32 overflow-y-auto">
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
            {isPdfFile && <TabsTrigger value="raw" className="font-light uppercase tracking-wider after:!opacity-0 data-[state=active]:text-primary">RAW</TabsTrigger>}
            <TabsTrigger value="chunks" className="font-light uppercase tracking-wider after:!opacity-0 data-[state=active]:text-primary">CHUNKS{chunks.length > 0 ? ` (${chunks.length})` : ""}</TabsTrigger>
            <TabsTrigger value="summary" className="font-light uppercase tracking-wider after:!opacity-0 data-[state=active]:text-primary">SUMMARY</TabsTrigger>
          </TabsList>

          {/* Source Tab */}
          <TabsContent key={`preview-${activeTab}`} value="preview" className="flex-1 overflow-hidden min-h-0 animate-tab-in">
            <div className="flex-1 overflow-hidden rounded-lg border border-border h-full">
              {previewLoading || chunksLoading ? (
                <div className="flex items-center justify-center h-full text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin mr-2" />
                  Loading...
                </div>
              ) : previewContent !== null ? (
                <ScrollArea className="h-full">
                  <div ref={sourceContentRef} className="p-3">
                    <TiptapEditor
                      value={previewContent ? transformImageBlocks(previewContent, collectionId) : ""}
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

          {/* Raw PDF Tab */}
          {isPdfFile && (
            <TabsContent key={`raw-${activeTab}`} value="raw" className="flex-1 overflow-hidden min-h-0 animate-tab-in">
              <div className="flex-1 overflow-hidden rounded-lg border border-border h-full">
                <iframe
                  key={highlightPage ?? "default"}
                  src={highlightPage
                    ? `${getFilePreviewUrl(sourceKey)}#page=${highlightPage}`
                    : getFilePreviewUrl(sourceKey)}
                  className="w-full h-full border-0"
                  title={`Raw PDF: ${displayName}`}
                />
              </div>
            </TabsContent>
          )}

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
                            role="button" tabIndex={0}
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
                                    role="button" tabIndex={0}
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
                          role="button" tabIndex={0}
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
                  {summaryLoading ? (
                    <div className="flex items-center justify-center py-8 text-muted-foreground">
                      <Loader2 className="h-5 w-5 animate-spin mr-2" />
                      Loading summary...
                    </div>
                  ) : docSummary ? (
                    <div className="space-y-4">
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
                      {docSummary.data.length === 0 && docSummary.facts.length === 0 && docSummary.insights.length === 0 && (
                        <p className="text-sm text-muted-foreground text-center py-4">No summary data available.</p>
                      )}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground text-center py-8">No summary available.</p>
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
