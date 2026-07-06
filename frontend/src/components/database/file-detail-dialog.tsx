import { useState, useMemo, useEffect, useRef } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger, TabsIndicator } from "@/components/ui/tabs"
import { Loader2, ChevronRight, ChevronDown, RefreshCw } from "lucide-react"
import { cn, transformImageBlocks } from "@/lib/utils"
import { TiptapEditor } from "@/components/ui/tiptap-editor"
import type { Editor } from "@tiptap/core"
import { getFilePreviewUrl, getDocSummary, setDocSummaryInclude, generateDocSummary, getExtractedText, type ChunkDetail, type DocSummary } from "@/api/client"
import { useAppStore } from "@/stores/app-store"
import { toast } from "sonner"

// Module-level: survives component unmount across tab switches
const _generating = new Map<string, number>()  // key -> startedAt

function _genKey(collection: string, source: string) {
  return `${collection}::${source}`
}

function _markGenerating(key: string) {
  const now = Date.now()
  _generating.set(key, now)
  try { localStorage.setItem(`wk:gen:${key}`, String(now)) } catch { /* ignore */ }
}

function _unmarkGenerating(key: string) {
  _generating.delete(key)
  try { localStorage.removeItem(`wk:gen:${key}`) } catch { /* ignore */ }
}

function _isMarked(key: string): boolean {
  if (_generating.has(key)) return true
  // Recover from localStorage on page refresh, with 5-min TTL
  try {
    const raw = localStorage.getItem(`wk:gen:${key}`)
    if (raw) {
      const ts = Number(raw)
      if (Date.now() - ts < 300_000) {  // < 5 min
        _generating.set(key, ts)
        return true
      }
      // Expired — clean up
      localStorage.removeItem(`wk:gen:${key}`)
    }
  } catch { /* ignore */ }
  return false
}

interface FileDetailDialogProps {
  collection: string
  source: string | null
  displayName?: string
  fileType?: string
  originalExt?: string
  chunks: ChunkDetail[]
  chunksTotal: number
  loading: boolean
  onOpenChange: (open: boolean) => void
  openKey?: number
  highlightChunkIndex?: number
}

export function FileDetailDialog({ collection, source, displayName, fileType, originalExt, chunks, chunksTotal, loading, onOpenChange, openKey, highlightChunkIndex }: FileDetailDialogProps) {
  const [previewContent, setPreviewContent] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [expandedParents, setExpandedParents] = useState<Set<string>>(new Set())
  const [docSummary, setDocSummary] = useState<DocSummary | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [highlightOffset, setHighlightOffset] = useState<number | undefined>(undefined)
  const [highlightPage, setHighlightPage] = useState<number | undefined>(undefined)
  const [activeTab, setActiveTab] = useState("source")
  const sourceContentRef = useRef<HTMLDivElement>(null)
  const sourceEditorRef = useRef<Editor | null>(null)

  const [highlightedIdx, setHighlightedIdx] = useState<number | undefined>(undefined)

  // Sync from prop
  useEffect(() => {
    setHighlightedIdx(highlightChunkIndex)
  }, [highlightChunkIndex])

  // Scroll to highlighted chunk when chunks load
  useEffect(() => {
    if (highlightedIdx == null || loading || chunks.length === 0) return
    const timer = setTimeout(() => {
      const el = document.querySelector(`[data-chunk-index="${highlightedIdx}"]`)
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" })
        setTimeout(() => setHighlightedIdx(undefined), 3000)
      }
    }, 200)
    return () => clearTimeout(timer)
  }, [highlightedIdx, loading, chunks.length])

  const isPdf = source?.toLowerCase().endsWith(".pdf") || fileType === "pdf" || originalExt === "pdf"

  const genKey = collection && source ? _genKey(collection, source) : null
  const [, setRenderTick] = useState(0)
  // Force re-render when dialog reopens (openKey changes)
  useEffect(() => { setRenderTick(k => k + 1) }, [openKey])
  const isGenerating = !!(genKey && _isMarked(genKey))

  // Reset state when source changes
  useEffect(() => {
    setDocSummary(null)
    setPreviewContent(null)
    setHighlightOffset(undefined)
    setHighlightPage(undefined)
    setActiveTab("source")
  }, [source])

  // Poll while generating
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

  const handleLocate = (chunk: ChunkDetail) => {
    console.log("[FileDetail handleLocate]", JSON.stringify({
      char_offset: chunk.char_offset,
      length: chunk.text?.length,
      text: chunk.text?.substring(0, 80),
      chunk_id: chunk.id,
      chunk_index: chunk.chunk_index,
      chunk_type: chunk.chunk_type,
    }))
    setHighlightOffset(chunk.char_offset)
    if (chunk.page_number !== undefined) setHighlightPage(chunk.page_number)
    setActiveTab("source")
    // Delay to read previewContent after state settles
    setTimeout(() => {
      console.log("[FileDetail after locate]", JSON.stringify({
        highlightOffset: chunk.char_offset,
        highlightLength: chunk.text?.length,
        previewContentLen: previewContent?.length,
        previewContentStart: previewContent?.substring(chunk.char_offset ?? 0, (chunk.char_offset ?? 0) + 50),
      }))
    }, 100)
  }

  // Scroll to highlightOffset — map raw-markdown offset → ProseMirror position.
  useEffect(() => {
    if (highlightOffset === undefined) return
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
  }, [previewContent, highlightOffset])

  // Load source content: all files (including PDFs) load parsed/extracted text for Tiptap
  useEffect(() => {
    if (!source) { setPreviewContent(null); return }
    let cancelled = false
    setPreviewLoading(true)
    getExtractedText(source, collection)
      .then((res) => {
        console.log("[FileDetail getExtractedText OK]", { source, collection, textLen: res.text?.length, format: res.format })
        if (!cancelled) setPreviewContent(res.text)
      })
      .catch((err) => {
        console.error("[FileDetail getExtractedText FAIL]", { source, err })
        if (!cancelled) setPreviewContent(null)
      })
      .finally(() => { if (!cancelled) setPreviewLoading(false) })
    return () => { cancelled = true }
  }, [source, isPdf])

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

  // Navigation target derived from chunk metadata
  const firstChunk = chunks[0]
  const isNoteFile = firstChunk?.file_type === "note"
  const isRecordingFile = !!firstChunk?.meeting_id
  const noteId = isNoteFile ? (firstChunk?.note_id || (source?.startsWith("__note__:") ? source.slice("__note__:".length) : null)) : null
  const meetingId = isRecordingFile ? firstChunk?.meeting_id : null

  const { setActiveMeeting, setSidebarView, setPendingOpenNote } = useAppStore()

  const handleGoToSource = () => {
    if (isNoteFile && noteId) {
      setPendingOpenNote(noteId)
      setSidebarView("database")
      onOpenChange(false)
    } else if (isRecordingFile && meetingId) {
      setActiveMeeting(meetingId)
      setSidebarView("meeting")
      onOpenChange(false)
    }
  }

  const goToLabel = isNoteFile ? "GO TO THE NOTE" : isRecordingFile ? "GO TO THE MEETING" : null

  return (
    <Dialog open={!!source} onOpenChange={(v) => onOpenChange(v)}>
      <DialogContent className="!max-w-[90vw] !w-[90vw] h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="truncate font-light">{displayName || source}</span>
            <Badge variant="secondary" className="ml-2 shrink-0">{chunksTotal} chunks</Badge>
          </DialogTitle>
        </DialogHeader>

        <div className="flex-1 flex gap-4 overflow-hidden min-h-0">
          <div className="w-1/2 flex flex-col min-h-0">
            <Tabs value={activeTab} onValueChange={setActiveTab} className="flex flex-col h-full min-h-0">
              <div className="flex items-center justify-between mb-2">
                <TabsList variant="line" className="relative">
                  <TabsIndicator renderBeforeHydration />
                  <TabsTrigger value="source" className="font-light uppercase tracking-wider after:!opacity-0 data-[state=active]:text-primary">SOURCE</TabsTrigger>
                  {isPdf && <TabsTrigger value="raw" className="font-light uppercase tracking-wider after:!opacity-0 data-[state=active]:text-primary">RAW</TabsTrigger>}
                  <TabsTrigger value="summary" className="font-light uppercase tracking-wider after:!opacity-0 data-[state=active]:text-primary">SUMMARY</TabsTrigger>
                </TabsList>
                {goToLabel && (
                  <button
                    type="button"
                    className="text-[10px] font-medium uppercase tracking-[0.1em] text-primary hover:opacity-80 transition-opacity cursor-pointer t-sans-family"
                    style={{ background: "none", border: "none" }}
                    onClick={handleGoToSource}
                  >
                    {goToLabel}
                  </button>
                )}
              </div>

              <TabsContent key={`source-${activeTab}`} value="source" className="flex-1 overflow-hidden min-h-0 animate-tab-in">
                <div className="h-full overflow-hidden">
                  {loading || previewLoading ? (
                    <div className="flex items-center justify-center h-full text-muted-foreground">
                      <Loader2 className="h-5 w-5 animate-spin mr-2" />
                      Loading...
                    </div>
                  ) : previewContent !== null ? (
                    <ScrollArea className="h-full">
                      <div ref={sourceContentRef}>
                        <div className="p-4">
                          <TiptapEditor
                            value={previewContent ? transformImageBlocks(previewContent, collection) : ""}
                            readonly
                            showToolbar={false}
                            onEditorReady={(e) => { sourceEditorRef.current = e }}
                          />
                        </div>
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

              {isPdf && (
                <TabsContent key={`raw-${activeTab}`} value="raw" className="flex-1 overflow-hidden min-h-0 animate-tab-in">
                  <div className="h-full overflow-hidden">
                    {source && (
                      <iframe
                        key={highlightPage ?? "default"}
                        src={highlightPage
                          ? `${getFilePreviewUrl(source)}#page=${highlightPage}`
                          : getFilePreviewUrl(source)}
                        className="w-full h-full border-0"
                        title={`Raw PDF: ${source}`}
                      />
                    )}
                  </div>
                </TabsContent>
              )}

              <TabsContent key={`summary-${activeTab}`} value="summary" className="flex-1 overflow-hidden min-h-0 animate-tab-in">
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
                              className="font-light uppercase tracking-wider text-primary"
                              disabled={isGenerating}
                              onClick={async () => {
                                if (!source || !collection) return
                                const key = _genKey(collection, source)
                                _markGenerating(key)
                                setRenderTick(k => k + 1)
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
                              RE-SUMMARIZE
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
                            className="font-light uppercase tracking-wider text-primary border-primary"
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
                            SUMMARIZE
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
                        data-chunk-index={chunk.chunk_index}
                        className={cn(
                          "border rounded-lg p-3 cursor-pointer hover:bg-accent/50 transition-all",
                          highlightedIdx === chunk.chunk_index
                            ? "border-primary ring-2 ring-primary/30 bg-primary/5"
                            : "border-border",
                        )}
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
