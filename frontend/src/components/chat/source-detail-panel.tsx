import { useState, useMemo, useEffect, useCallback, useRef } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Loader2, X, ChevronRight, ChevronDown, RefreshCw, Locate } from "lucide-react"
import { TiptapEditor } from "@/components/ui/tiptap-editor"
import { getFileChunks, getFilePreviewUrl, isPreviewable, getDocSummary, generateDocSummary, setDocSummaryInclude, getExtractedText, type ChunkDetail, type DocSummary } from "@/api/client"
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

  // If we have a char_offset, validate it and use precise offset-based highlighting
  if (offset !== undefined && offset >= 0 && offset < text.length) {
    const len = chunkLength && chunkLength > 0 ? chunkLength : highlight.length
    const end = Math.min(offset + len, text.length)
    const matchText = text.substring(offset, end)
    // Verify offset is plausible: first 20 chars of chunk should appear near offset
    const probe = highlight.substring(0, Math.min(20, highlight.length))
    if (probe && matchText.includes(probe)) {
      const before = text.substring(0, offset)
      const after = text.substring(end)
      return (
        <pre className="text-sm leading-relaxed whitespace-pre-wrap font-sans">
          {before}
          <mark className="bg-yellow-300 dark:bg-yellow-700 rounded px-0.5">{matchText}</mark>
          {after}
        </pre>
      )
    }
    // Offset doesn't match — fall through to substring matching
  }

  // Fallback: substring matching
  if (!highlight || highlight.length < 10) return <pre className="text-sm leading-relaxed whitespace-pre-wrap font-sans">{text}</pre>

  const tryFind = (): [number, number] => {
    // 1. Full chunk text — no overlap, perfect match
    let idx = text.indexOf(highlight)
    if (idx !== -1) return [idx, highlight.length]

    // 2. Split into paragraphs and find the real chunk boundaries
    const paragraphs = highlight.split("\n\n").filter(p => p.trim().length > 5)
    if (paragraphs.length < 2) {
      const trimmed = highlight.trim().substring(0, 100)
      idx = text.indexOf(trimmed)
      if (idx !== -1) return [idx, trimmed.length]
      return [-1, 0]
    }

    // Find last paragraph's position (definitely unique to this chunk)
    const lastPara = paragraphs[paragraphs.length - 1].trim()
    const lastIdx = text.indexOf(lastPara)
    if (lastIdx === -1) return [-1, 0]
    const lastEnd = lastIdx + lastPara.length

    // Walk backwards to find contiguous preceding paragraphs,
    // but stop if we enter a region that belongs to a PREVIOUS chunk
    // (i.e., the found position + paragraph length <= char_offset hint)
    let chunkStart = lastIdx
    for (let i = paragraphs.length - 2; i >= 0; i--) {
      const para = paragraphs[i].trim()
      if (para.length < 5) continue
      // Only search in the region BEFORE current chunkStart
      const found = text.lastIndexOf(para, chunkStart - 1)
      if (found === -1) break
      // Contiguity: paragraph should end right before chunkStart
      const gap = text.substring(found + para.length, chunkStart)
      if (gap.length > 4) break
      chunkStart = found
    }

    return [chunkStart, lastEnd - chunkStart]
  }

  const [idx, matchLen] = tryFind()
  if (idx === -1) return <pre className="text-sm leading-relaxed whitespace-pre-wrap font-sans">{text}</pre>

  const matchEnd = Math.min(idx + matchLen, text.length)
  return (
    <pre className="text-sm leading-relaxed whitespace-pre-wrap font-sans">
      {text.substring(0, idx)}
      <mark className="bg-yellow-300 dark:bg-yellow-700 rounded px-0.5">{text.substring(idx, matchEnd)}</mark>
      {text.substring(matchEnd)}
    </pre>
  )
}

export function SourceDetailPanel({ source, onClose }: SourceDetailPanelProps) {
  const [previewContent, setPreviewContent] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [chunks, setChunks] = useState<ChunkDetail[]>([])
  const [chunksLoading, setChunksLoading] = useState(false)
  const [expandedParents, setExpandedParents] = useState<Set<string>>(new Set())
  const [docSummary, setDocSummary] = useState<DocSummary | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [extractedText, setExtractedText] = useState<string | null>(null)
  const [extractedLoading, setExtractedLoading] = useState(false)
  const extractedContentRef = useRef<HTMLDivElement>(null)
  const [activeTab, setActiveTab] = useState("preview")
  const [highlightOffset, setHighlightOffset] = useState<number | undefined>(undefined)
  const [highlightPage, setHighlightPage] = useState<number | undefined>(undefined)
  const textContentRef = useRef<HTMLDivElement>(null)

  const sourceName = source?.metadata?.source as string | undefined
  const collection = source?.metadata?.collection as string | undefined
  const chunkId = source?.metadata?.id as string | undefined
  const isPdfFile = sourceName ? _isPdf(sourceName) : false

  const genKey = collection && sourceName ? _genKey(collection, sourceName) : null
  const isGenerating = !!(genKey && _generating.has(genKey))

  // Reset state when source changes (not on chunk change within same source)
  useEffect(() => {
    setPreviewContent(null)
    setChunks([])
    setDocSummary(null)
    setExtractedText(null)
    setExtractedFormat("text")
    setExpandedParents(new Set())
    setHighlightOffset(source ? _getHighlightOffset(source) : undefined)
    setHighlightPage(source?.metadata?.page_number as number | undefined)
    setActiveTab("preview")
  }, [collection, sourceName])

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

  // Load preview text for non-PDF previewable files (for offset-based highlighting)
  useEffect(() => {
    if (!sourceName) { setPreviewContent(null); return }
    // Only fetch text for non-PDF previewable files (PDF uses iframe)
    if (isPreviewable(sourceName) && !_isPdf(sourceName)) {
      let cancelled = false
      setPreviewLoading(true)
      fetch(getFilePreviewUrl(sourceName))
        .then(res => res.ok ? res.text() : Promise.reject(new Error(`HTTP ${res.status}`)))
        .then(text => { if (!cancelled) setPreviewContent(text) })
        .catch(() => { if (!cancelled) setPreviewContent(null) })
        .finally(() => { if (!cancelled) setPreviewLoading(false) })
      return () => { cancelled = true }
    }
    // For PDFs and non-previewable files, use existing logic
    if (isPreviewable(sourceName)) { setPreviewContent(null); return }
    let cancelled = false
    setPreviewLoading(true)
    fetch(getFilePreviewUrl(sourceName))
      .then(res => res.ok ? res.text() : Promise.reject(new Error(`HTTP ${res.status}`)))
      .then(text => { if (!cancelled) setPreviewContent(text) })
      .catch(() => { if (!cancelled) setPreviewContent(null) })
      .finally(() => { if (!cancelled) setPreviewLoading(false) })
    return () => { cancelled = true }
  }, [sourceName])

  // Load extracted text
  useEffect(() => {
    if (!sourceName) { setExtractedText(null); return }
    let cancelled = false
    setExtractedLoading(true)
    getExtractedText(sourceName)
      .then((res) => {
        if (!cancelled) {
          setExtractedText(res.text)
          setExtractedFormat(res.format)
        }
      })
      .catch(() => { if (!cancelled) { setExtractedText(null); setExtractedFormat("text") } })
      .finally(() => { if (!cancelled) setExtractedLoading(false) })
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

  // Scroll to highlighted chunk offset when text content loads
  useEffect(() => {
    if (highlightOffset === undefined || isPdfFile) return
    // For markdown, highlight isn't rendered in preview — switch to chunks tab
    if (sourceName?.toLowerCase().endsWith(".md")) {
      setActiveTab("chunks")
      return
    }
    if (!previewContent) return
    const attemptScroll = () => {
      const el = textContentRef.current?.querySelector("mark")
      if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); return true }
      // Fallback: search the ScrollArea viewport
      const viewport = textContentRef.current?.closest("[data-radix-scroll-area-viewport]") as HTMLElement | null
      if (viewport) {
        const mark = viewport.querySelector("mark")
        if (mark) { mark.scrollIntoView({ behavior: "smooth", block: "center" }); return true }
      }
      return false
    }
    if (!attemptScroll()) {
      // Retry in case DOM is still settling
      const timer = setTimeout(attemptScroll, 100)
      return () => clearTimeout(timer)
    }
  }, [previewContent, highlightOffset, isPdfFile, sourceName, chunkId])

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

  const handleLocate = useCallback((offset?: number, pageNumber?: number) => {
    setHighlightOffset(offset)
    if (pageNumber !== undefined) setHighlightPage(pageNumber)
    setActiveTab("extracted")
  }, [])


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
        <p className="text-xs text-muted-foreground leading-relaxed whitespace-pre-wrap">
          {source.text}
        </p>
      </div>

      {/* Tabs */}
      <div className="flex-1 flex flex-col min-h-0 px-2">
        <Tabs value={activeTab} onValueChange={setActiveTab} className="flex flex-col h-full min-h-0">
          <TabsList variant="line" className="mb-1 shrink-0">
            <TabsTrigger value="preview" className="text-xs">Source</TabsTrigger>
            <TabsTrigger value="extracted" className="text-xs">Extracted</TabsTrigger>
            <TabsTrigger value="chunks" className="text-xs">Chunks{chunks.length > 0 ? ` (${chunks.length})` : ""}</TabsTrigger>
            <TabsTrigger value="summary" className="text-xs">Summary</TabsTrigger>
          </TabsList>

          {/* Preview Tab */}
          <TabsContent value="preview" className="flex-1 overflow-hidden min-h-0">
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
                  <div ref={textContentRef} className="p-4">
                    <TiptapEditor
                      value={(() => {
                        const hlOffset = source?.metadata?.char_offset as number | undefined
                        const hlLength = source?.text?.length || 0
                        if (typeof hlOffset === "number" && hlLength > 0 && hlOffset >= 0 && hlOffset < previewContent.length) {
                          const before = previewContent.substring(0, hlOffset)
                          const match = previewContent.substring(hlOffset, hlOffset + hlLength)
                          const after = previewContent.substring(hlOffset + hlLength)
                          return before + "<mark>" + match + "</mark>" + after
                        }
                        return previewContent
                      })()}
                      readonly
                      showToolbar={false}
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

          {/* Extracted Tab */}
          <TabsContent value="extracted" className="flex-1 overflow-hidden min-h-0">
            <div className="flex-1 overflow-hidden rounded-lg border border-border h-full">
              {extractedLoading ? (
                <div className="flex items-center justify-center h-full text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin mr-2" />
                  Loading extracted text...
                </div>
              ) : extractedText !== null ? (
                <ScrollArea className="h-full">
                  <div ref={extractedContentRef} className="p-3">
                    <TiptapEditor value={extractedText} readonly showToolbar={false} />
                  </div>
                </ScrollArea>
              ) : (
                <div className="flex items-center justify-center h-full text-muted-foreground">
                  <p className="text-sm">No extracted text available.</p>
                </div>
              )}
            </div>
          </TabsContent>

          {/* Chunks Tab */}
          <TabsContent value="chunks" className="flex-1 overflow-hidden min-h-0">
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
                                  onClick={(e) => { e.stopPropagation(); handleLocate(group.parent.char_offset, group.parent.page_number) }}
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
                                    onClick={(e) => { e.stopPropagation(); handleLocate(child.char_offset, child.page_number) }}
                                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.stopPropagation(); handleLocate(child.char_offset, child.page_number) } }}
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
                          onClick={() => handleLocate(chunk.char_offset, chunk.page_number)}
                          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") handleLocate(chunk.char_offset, chunk.page_number) }}
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
          <TabsContent value="summary" className="flex-1 overflow-hidden min-h-0">
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
                          Re-summarize
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
                        Summarize
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
