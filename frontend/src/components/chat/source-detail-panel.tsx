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
import {
  isRawViewerSupported,
  RawFileViewer,
  resolveRawFilename,
} from "@/components/file-mgmt/raw-file-viewer"

interface SourceDetailPanelProps {
  source: Source | null
  onClose: () => void
}

function _getHighlightOffset(source: Source): number | undefined {
  const v = source.metadata?.char_offset
  return typeof v === "number" ? v : undefined
}

/** Canonical Qdrant source key from retrieval metadata (accept file: / file_id). */
function resolveSourceKey(meta: Record<string, unknown> | undefined): string {
  if (!meta) return ""
  const raw = String(meta.source ?? "").trim()
  const fileId = String(meta.file_id ?? "").trim()
  if (
    raw.startsWith("__file__:") ||
    raw.startsWith("__note__:") ||
    raw.startsWith("__meeting__:")
  ) {
    return raw
  }
  // MCP / alias form
  if (raw.startsWith("file:") && !raw.startsWith("file://")) {
    return `__file__:${raw.slice("file:".length).trim()}`
  }
  if (fileId) return `__file__:${fileId}`
  return raw
}

/** Prefer collection id; fall back to name→id lookup from store. */
function resolveCollectionId(
  meta: Record<string, unknown> | undefined,
  cols: { id: string; name: string }[]
): string {
  const raw = String(meta?.collection ?? "").trim()
  if (!raw) return ""
  if (cols.some((c) => c.id === raw)) return raw
  const byName = cols.find((c) => c.name === raw)
  return byName?.id || raw
}

export function SourceDetailPanel({ source, onClose }: SourceDetailPanelProps) {
  const [previewContent, setPreviewContent] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [chunks, setChunks] = useState<ChunkDetail[]>([])
  const [chunksLoading, setChunksLoading] = useState(false)
  const [expandedParents, setExpandedParents] = useState<Set<string>>(new Set())
  const [docSummary, setDocSummary] = useState<DocSummary | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  /** parsed = extracted text · preview = original file (was "raw") */
  const [activeTab, setActiveTab] = useState("parsed")
  const [highlightOffset, setHighlightOffset] = useState<number | undefined>(undefined)
  // Force scroll effect to re-run on every locate click
  const [locateTick, setLocateTick] = useState(0)
  const sourceContentRef = useRef<HTMLDivElement>(null)
  const sourceEditorRef = useRef<Editor | null>(null)
  // Store latest previewContent in ref so handleLocate can access it without stale closure
  const previewContentRef = useRef<string | null>(null)

  const { collections } = useAppStore()
  // sourceKey = canonical Qdrant source for API calls
  const sourceKey = resolveSourceKey(source?.metadata)
  // displayName = human-readable filename for UI
  const displayName =
    (source?.metadata?.source_label as string) || sourceKey
  const collectionId = resolveCollectionId(source?.metadata, collections)
  const chunkId = source?.metadata?.id as string | undefined
  const collectionDisplay =
    collections.find((c) => c.id === collectionId)?.name || collectionId
  const metaExt =
    (source?.metadata?.original_ext as string | undefined) ||
    (source?.metadata?.file_type as string | undefined)
  const rawFilename = resolveRawFilename(
    displayName,
    metaExt && !String(metaExt).includes("/") ? `file.${metaExt}` : null,
    sourceKey
  )
  const showRawTab = isRawViewerSupported(rawFilename)
  const rawPreviewUrl =
    collectionId && sourceKey
      ? getFilePreviewUrl(sourceKey, { collection: collectionId })
      : sourceKey
        ? getFilePreviewUrl(sourceKey)
        : null

  // Reset file-level state when file changes
  useEffect(() => {
    setPreviewContent(null); previewContentRef.current = null
    setChunks([])
    setDocSummary(null)
    setExpandedParents(new Set())
    setActiveTab("parsed")
  }, [collectionId, sourceKey])

  // Update highlight when selected source chunk changes
  useEffect(() => {
    const offset = source ? _getHighlightOffset(source) : undefined
    setHighlightOffset(offset)
    // Bump tick so scroll effect re-fires even if offset is the same
    setLocateTick(t => t + 1)
  }, [source?.metadata?.id])

  // Load chunks for this document (full file, not only the hit)
  useEffect(() => {
    if (!collectionId || !sourceKey) {
      setChunks([])
      setChunksLoading(false)
      return
    }
    let cancelled = false
    setChunksLoading(true)
    getFileChunks(collectionId, sourceKey, 10000)
      .then((res) => {
        if (cancelled) return
        const list = Array.isArray(res.chunks) ? res.chunks : []
        setChunks(list)
        // If still empty, try file: alias once (older payloads)
        if (list.length === 0 && sourceKey.startsWith("__file__:")) {
          const alias = `file:${sourceKey.slice("__file__:".length)}`
          return getFileChunks(collectionId, alias, 10000)
            .then((res2) => {
              if (!cancelled) setChunks(Array.isArray(res2.chunks) ? res2.chunks : [])
            })
            .catch(() => {
              /* keep empty */
            })
        }
      })
      .catch((err) => {
        if (!cancelled) {
          console.warn(
            "[SourceDetailPanel] Failed to load chunks:",
            collectionId,
            sourceKey,
            err,
          )
          setChunks([])
        }
      })
      .finally(() => {
        if (!cancelled) setChunksLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [collectionId, sourceKey])

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

  const handleLocate = useCallback((offset?: number, _pageNumber?: number, _length?: number) => {
    setHighlightOffset(offset)
    setActiveTab("parsed")
    setLocateTick(t => t + 1)
  }, [])

  if (!source || !sourceKey) return null

  return (
    <div className="pm-chat-source-panel">
      {/* Header — no hard divider */}
      <div className="pm-chat-source-panel-head">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <h2 className="pm-chat-source-panel-title" title={displayName}>
            {displayName}
          </h2>
          <Badge variant="outline" className="shrink-0">
            {(source.score * 100).toFixed(0)}%
          </Badge>
          {collectionDisplay && (
            <Badge variant="secondary" className="shrink-0">
              {collectionDisplay}
            </Badge>
          )}
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          className="shrink-0"
          onClick={onClose}
          aria-label="Close source panel"
        >
          <X className="size-4" />
        </Button>
      </div>

      {/* Current chunk excerpt */}
      <div className="pm-chat-source-panel-excerpt">
        <div className="flex items-start gap-2">
          <p className="flex-1 min-w-0 whitespace-pre-wrap m-0">
            {source.text}
          </p>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            className="shrink-0 mt-0.5"
            title="Locate in preview"
            onClick={() =>
              handleLocate(
                _getHighlightOffset(source),
                source.metadata?.page_number as number | undefined,
                source.text?.length,
              )
            }
          >
            <Locate className="size-3" />
          </Button>
        </div>
      </div>

      {/* Tabs — pill default (PRIMITIVES) */}
      <div className="pm-chat-source-panel-body">
        <Tabs value={activeTab} onValueChange={setActiveTab} className="flex flex-col h-full min-h-0">
          <TabsList className="mb-2 shrink-0 w-fit">
            <TabsIndicator className="pm-tabs-indicator" renderBeforeHydration />
            <TabsTrigger value="parsed">Parsed</TabsTrigger>
            {showRawTab && <TabsTrigger value="preview">Preview</TabsTrigger>}
            <TabsTrigger value="chunks">
              Chunks{chunks.length > 0 ? ` · ${chunks.length}` : ""}
            </TabsTrigger>
            <TabsTrigger value="summary">Summary</TabsTrigger>
          </TabsList>

          {/* Parsed — extracted text (locate-in-doc targets this) */}
          <TabsContent
            key={`parsed-${activeTab}`}
            value="parsed"
            className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
          >
            <div className="pm-chat-source-nested h-full">
              {previewLoading || chunksLoading ? (
                <div className="flex items-center justify-center h-full gap-2">
                  <Loader2 className="size-4 animate-spin text-[var(--pm-faint)]" />
                  <span className="pm-meta">Loading…</span>
                </div>
              ) : previewContent !== null ? (
                <ScrollArea className="h-full">
                  <div ref={sourceContentRef} className="p-3">
                    <TiptapEditor
                      value={
                        previewContent
                          ? transformImageBlocks(
                              previewContent,
                              collectionId,
                              sourceKey.startsWith("__file__:")
                                ? sourceKey.slice("__file__:".length)
                                : undefined,
                            )
                          : ""
                      }
                      readonly
                      showToolbar={false}
                      onEditorReady={(e) => {
                        sourceEditorRef.current = e
                      }}
                    />
                  </div>
                </ScrollArea>
              ) : (
                <ScrollArea className="h-full">
                  <CardContent className="p-4 space-y-2">
                    {chunks.map((chunk, i) => (
                      <p key={i} className="pm-meta whitespace-pre-wrap leading-relaxed m-0">
                        {chunk.text}
                      </p>
                    ))}
                  </CardContent>
                </ScrollArea>
              )}
            </div>
          </TabsContent>

          {/* Preview — original file; tools = Search + Download only; no frame border */}
          {showRawTab && (
            <TabsContent
              key={`preview-${activeTab}`}
              value="preview"
              className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
            >
              <div className="pm-chat-source-nested pm-chat-source-nested--flush h-full">
                <RawFileViewer
                  url={rawPreviewUrl}
                  filename={rawFilename}
                  downloadUrl={rawPreviewUrl}
                  tools="download-search"
                  className="h-full"
                />
              </div>
            </TabsContent>
          )}

          <TabsContent
            key={`chunks-${activeTab}`}
            value="chunks"
            className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
          >
            <div className="pm-chat-source-nested h-full">
              <ScrollArea className="h-full">
                <CardContent className="p-3 space-y-2">
                  {chunksLoading ? (
                    <div className="flex items-center justify-center py-8 gap-2">
                      <Loader2 className="size-4 animate-spin text-[var(--pm-faint)]" />
                      <span className="pm-meta">Loading chunks…</span>
                    </div>
                  ) : chunks.length === 0 ? (
                    <p className="pm-meta py-4 text-center">No chunks</p>
                  ) : groupedChunks ? (
                    groupedChunks.map((group) => {
                      const isExpanded = expandedParents.has(group.parent.id)
                      const isTargetParent = group.parent.id === chunkId
                      return (
                        <div
                          key={group.parent.id}
                          className={`pm-chat-chunk-card overflow-hidden ${isTargetParent ? "is-target" : ""}`}
                        >
                          <div
                            className="w-full text-left flex items-start gap-2 cursor-pointer"
                            onClick={() => toggleParent(group.parent.id)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter" || e.key === " ") toggleParent(group.parent.id)
                            }}
                            role="button"
                            tabIndex={0}
                          >
                            {isExpanded ? (
                              <ChevronDown className="size-3.5 mt-0.5 shrink-0 text-[var(--pm-faint)]" />
                            ) : (
                              <ChevronRight className="size-3.5 mt-0.5 shrink-0 text-[var(--pm-faint)]" />
                            )}
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-1.5 mb-1 flex-wrap">
                                <Badge variant={isTargetParent ? "default" : "outline"}>
                                  Parent #{group.parent.chunk_index}
                                </Badge>
                                <Badge variant="outline">{group.children.length} children</Badge>
                                {group.parent.section_label && (
                                  <Badge variant="secondary">{group.parent.section_label}</Badge>
                                )}
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="icon-xs"
                                  className="ml-auto"
                                  title="Locate in preview"
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    handleLocate(
                                      group.parent.char_offset,
                                      group.parent.page_number,
                                      group.parent.text?.length,
                                    )
                                  }}
                                >
                                  <Locate className="size-3" />
                                </Button>
                              </div>
                              <p className="pm-meta whitespace-pre-wrap line-clamp-2 m-0">
                                {group.parent.text}
                              </p>
                            </div>
                          </div>
                          {isExpanded && (
                            <div className="mt-2 pt-2 space-y-2 pl-6 border-t border-[color-mix(in_srgb,var(--pm-ink)_6%,transparent)]">
                              <div>
                                <p className="pm-label mb-1">Full text</p>
                                <p className="pm-meta whitespace-pre-wrap m-0 leading-relaxed">
                                  {group.parent.text}
                                </p>
                              </div>
                              {group.parent.context && (
                                <div className="pl-2.5 border-l-2 border-[color-mix(in_srgb,var(--pm-green)_30%,transparent)]">
                                  <p className="pm-meta italic m-0">{group.parent.context}</p>
                                </div>
                              )}
                              {group.children.map((child) => {
                                const isTargetChild = child.id === chunkId
                                return (
                                  <div
                                    key={child.id}
                                    className={`pm-chat-chunk-card ${isTargetChild ? "is-target" : ""}`}
                                    onClick={(e) => {
                                      e.stopPropagation()
                                      handleLocate(child.char_offset, child.page_number, child.text?.length)
                                    }}
                                    onKeyDown={(e) => {
                                      if (e.key === "Enter" || e.key === " ") {
                                        e.stopPropagation()
                                        handleLocate(child.char_offset, child.page_number, child.text?.length)
                                      }
                                    }}
                                    role="button"
                                    tabIndex={0}
                                  >
                                    <div className="flex items-center gap-1.5 mb-1.5">
                                      <Badge variant={isTargetChild ? "default" : "secondary"}>
                                        Child #{child.chunk_index}
                                      </Badge>
                                      <Locate className="ml-auto size-3 text-[var(--pm-faint)] shrink-0" />
                                    </div>
                                    {child.context && (
                                      <div className="mb-1.5 pl-2.5 border-l-2 border-[color-mix(in_srgb,var(--pm-green)_30%,transparent)]">
                                        <p className="pm-meta italic m-0">{child.context}</p>
                                      </div>
                                    )}
                                    <p className="pm-meta whitespace-pre-wrap m-0 leading-relaxed">
                                      {child.text}
                                    </p>
                                  </div>
                                )
                              })}
                            </div>
                          )}
                        </div>
                      )
                    })
                  ) : (
                    chunks.map((chunk) => {
                      const isTarget = chunk.id === chunkId
                      return (
                        <div
                          key={chunk.id}
                          className={`pm-chat-chunk-card ${isTarget ? "is-target" : ""}`}
                          onClick={() =>
                            handleLocate(chunk.char_offset, chunk.page_number, chunk.text?.length)
                          }
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              handleLocate(chunk.char_offset, chunk.page_number, chunk.text?.length)
                            }
                          }}
                          role="button"
                          tabIndex={0}
                        >
                          <div className="flex items-center gap-1.5 mb-1.5 flex-wrap">
                            <Badge variant={isTarget ? "default" : "outline"}>
                              Chunk #{chunk.chunk_index}
                            </Badge>
                            {chunk.section_label && (
                              <Badge variant="secondary">{chunk.section_label}</Badge>
                            )}
                            {chunk.context && (
                              <span className="pm-meta italic">with context</span>
                            )}
                            {isTarget && (
                              <span className="pm-meta text-[var(--pm-green)]">← retrieved</span>
                            )}
                            <Locate className="ml-auto size-3 text-[var(--pm-faint)] shrink-0" />
                          </div>
                          {chunk.context && (
                            <div className="mb-1.5 pl-2.5 border-l-2 border-[color-mix(in_srgb,var(--pm-green)_30%,transparent)]">
                              <p className="pm-meta italic m-0">{chunk.context}</p>
                            </div>
                          )}
                          <p className="pm-meta whitespace-pre-wrap m-0 leading-relaxed">
                            {chunk.text}
                          </p>
                        </div>
                      )
                    })
                  )}
                </CardContent>
              </ScrollArea>
            </div>
          </TabsContent>

          <TabsContent
            key={`summary-${activeTab}`}
            value="summary"
            className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
          >
            <div className="pm-chat-source-nested h-full">
              <ScrollArea className="h-full">
                <CardContent className="p-4">
                  {summaryLoading ? (
                    <div className="flex items-center justify-center py-8 gap-2">
                      <Loader2 className="size-4 animate-spin text-[var(--pm-faint)]" />
                      <span className="pm-meta">Loading summary…</span>
                    </div>
                  ) : docSummary ? (
                    <div className="space-y-4">
                      {docSummary.data.length > 0 && (
                        <div>
                          <h5 className="pm-label mb-2">Data Points</h5>
                          <ul className="space-y-1">
                            {docSummary.data.map((item, i) => (
                              <li key={i} className="pm-prose max-w-none">
                                {item}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {docSummary.facts.length > 0 && (
                        <div>
                          <h5 className="pm-label mb-2">Facts</h5>
                          <ul className="space-y-1">
                            {docSummary.facts.map((item, i) => (
                              <li key={i} className="pm-prose max-w-none">
                                {item}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {docSummary.insights.length > 0 && (
                        <div>
                          <h5 className="pm-label mb-2">Insights</h5>
                          <ul className="space-y-1">
                            {docSummary.insights.map((item, i) => (
                              <li key={i} className="pm-prose max-w-none">
                                {item}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {docSummary.data.length === 0 &&
                        docSummary.facts.length === 0 &&
                        docSummary.insights.length === 0 && (
                          <p className="pm-meta text-center py-4">No summary data available.</p>
                        )}
                    </div>
                  ) : (
                    <p className="pm-meta text-center py-8">No summary available.</p>
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
