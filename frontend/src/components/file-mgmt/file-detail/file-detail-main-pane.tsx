import type { Dispatch, MutableRefObject, SetStateAction } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  TabsIndicator,
} from "@/components/ui/tabs"
import {
  ChevronRight,
  ChevronDown,
  RefreshCw,
  Crosshair,
  History,
  Loader2,
  Star,
} from "lucide-react"
import { cn, chunkHasImageFence, isImageOnlyChunk, transformImageBlocks } from "@/lib/utils"
import { SummarySection } from "./file-detail-parts"
import { ChunkInspect } from "./chunk-inspect"
import { IngestTracePane } from "./ingest-trace-pane"
import { TooltipProvider } from "@/components/ui/tooltip"
import { ChunkMd } from "@/components/shared/chunk-md"
import { TiptapEditor } from "@/components/ui/tiptap-editor"
import type { Editor } from "@tiptap/core"
import {
  generateDocSummary,
  type ChunkDetail,
  type DocSummary,
} from "@/api/client"
import type { FileDetail, FileVersion } from "@/types/file-mgmt"
import {
  RawFileViewer,
  resolveRawFilename,
} from "@/components/file-mgmt/raw-file-viewer"
import { toast } from "sonner"
import { _genKey, _markGenerating, _unmarkGenerating } from "./file-detail-utils"

export interface FileDetailMainPaneProps {
  collectionId: string
  fileId: string | null | undefined
  source: string | null | undefined
  docSource: string | null | undefined
  storageFileIdProp: string | null | undefined
  isIngesting: boolean
  activeTab: string
  handleTabChange: (tab: string) => void
  chunksTotal: number
  isHistoricalFocus: boolean
  focusVersionId: string | null | undefined
  focusVersion: FileVersion | null | undefined
  rollingBack: boolean
  actionBusy: boolean
  setRollbackConfirm: Dispatch<SetStateAction<boolean>>
  goToLabel: string | null
  handleGoToSource: () => void
  viewStorageFile: string | null | undefined
  currentRawUrl: string | null
  downloadUrl: string | null
  detail: FileDetail | null
  previewLoading: boolean
  isUnsupported: boolean
  chunksLoading: boolean
  previewContent: string | null | undefined
  sourceEditorRef: MutableRefObject<Editor | null>
  chunks: ChunkDetail[]
  isGenerating: boolean
  summaryLoading: boolean
  docSummary: DocSummary | null
  setRenderTick: Dispatch<SetStateAction<number>>
  setActiveTab: Dispatch<SetStateAction<string>>
  setDocSummary: Dispatch<SetStateAction<DocSummary | null>>
  groupedChunks: { parent: ChunkDetail; children: ChunkDetail[] }[] | null
  expandedParents: Set<string>
  toggleParent: (id: string) => void
  handleLocate: (chunk: ChunkDetail) => void
  expandedChunks: Set<string>
  highlightedIdx: number | null | undefined
  toggleChunkExpand: (id: string) => void
}

export function FileDetailMainPane(p: FileDetailMainPaneProps) {
  const {
    collectionId,
    fileId,
    source,
    docSource,
    storageFileIdProp,
    isIngesting,
    activeTab,
    handleTabChange,
    chunksTotal,
    isHistoricalFocus,
    focusVersionId,
    focusVersion,
    rollingBack,
    actionBusy,
    setRollbackConfirm,
    goToLabel,
    handleGoToSource,
    viewStorageFile,
    currentRawUrl,
    downloadUrl,
    detail,
    previewLoading,
    isUnsupported,
    chunksLoading,
    previewContent,
    sourceEditorRef,
    chunks,
    isGenerating,
    summaryLoading,
    docSummary,
    setRenderTick,
    setActiveTab,
    setDocSummary,
    groupedChunks,
    expandedParents,
    toggleParent,
    handleLocate,
    expandedChunks,
    highlightedIdx,
    toggleChunkExpand,
  } = p

  // Recreated each render so list order still shows the first table-source figure.
  const seenChunkImageIds = new Set<string>()

  return (
              <div className="pm-ws-main pm-ws-card pm-ws-card--main">
                <Tabs
                  value={isIngesting ? "raw" : activeTab}
                  onValueChange={handleTabChange}
                  className="flex flex-col h-full min-h-0"
                >
                  <div className="pm-ws-main-head flex items-center justify-between gap-2 shrink-0">
                    <TabsList
                      className={cn(
                        "pm-tabs !h-auto w-fit bg-transparent p-0 gap-1 border-0 rounded-none",
                        "relative shrink-0 items-center isolate"
                      )}
                    >
                      <TabsIndicator
                        renderBeforeHydration
                        className="pm-tabs-indicator"
                      />
                      <TabsTrigger
                        value="raw"
                        className={cn(
                          "pm-vtab relative z-[1]",
                          "!h-auto min-h-0",
                          "data-[state=active]:shadow-none data-active:bg-transparent",
                          "after:!opacity-0 after:!content-none"
                        )}
                      >
                        Preview
                      </TabsTrigger>
                      <TabsTrigger
                        value="source"
                        disabled={isIngesting}
                        title={
                          isIngesting
                            ? "Available after ingest finishes"
                            : undefined
                        }
                        className={cn(
                          "pm-vtab relative z-[1]",
                          "!h-auto min-h-0",
                          "data-[state=active]:shadow-none data-active:bg-transparent",
                          "after:!opacity-0 after:!content-none",
                          "disabled:opacity-40"
                        )}
                      >
                        Parse
                      </TabsTrigger>
                      <TabsTrigger
                        value="summary"
                        disabled={isIngesting}
                        title={
                          isIngesting
                            ? "Available after ingest finishes"
                            : undefined
                        }
                        className={cn(
                          "pm-vtab relative z-[1]",
                          "!h-auto min-h-0",
                          "data-[state=active]:shadow-none data-active:bg-transparent",
                          "after:!opacity-0 after:!content-none",
                          "disabled:opacity-40"
                        )}
                      >
                        Summary
                      </TabsTrigger>
                      <TabsTrigger
                        value="chunks"
                        disabled={isIngesting}
                        title={
                          isIngesting
                            ? "Available after ingest finishes"
                            : undefined
                        }
                        className={cn(
                          "pm-vtab relative z-[1]",
                          "!h-auto min-h-0",
                          "data-[state=active]:shadow-none data-active:bg-transparent",
                          "after:!opacity-0 after:!content-none",
                          "disabled:opacity-40"
                        )}
                      >
                        Chunks
                        {chunksTotal > 0 && !isIngesting && (
                          <span className="ml-1.5 tabular-nums pm-meta normal-case tracking-normal">
                            {chunksTotal}
                          </span>
                        )}
                      </TabsTrigger>
                      {fileId ? (
                        <TabsTrigger
                          value="ingest"
                          disabled={isIngesting}
                          title={
                            isIngesting
                              ? "Available after ingest finishes"
                              : "Parse / vision / summary / context steps"
                          }
                          className={cn(
                            "pm-vtab relative z-[1]",
                            "!h-auto min-h-0",
                            "data-[state=active]:shadow-none data-active:bg-transparent",
                            "after:!opacity-0 after:!content-none",
                            "disabled:opacity-40"
                          )}
                        >
                          Ingest
                        </TabsTrigger>
                      ) : null}
                    </TabsList>
                    <div className="flex items-center gap-2 shrink-0">
                      {isHistoricalFocus && focusVersionId && fileId && (
                        <button
                          type="button"
                          className="pm-ws-link shrink-0 inline-flex items-center gap-1"
                          disabled={rollingBack || actionBusy}
                          onClick={() => setRollbackConfirm(true)}
                          title="Make this version current and permanently delete newer versions"
                        >
                          <History className="h-3 w-3" strokeWidth={1.75} />
                          Roll back
                        </button>
                      )}
                      {goToLabel && (
                        <button
                          type="button"
                          className="pm-ws-link shrink-0"
                          onClick={handleGoToSource}
                        >
                          {goToLabel}
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Preview — original file; white stage inside main card */}
                  <TabsContent
                    value="raw"
                    className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
                  >
                    <div className="pm-ws-doc-stage">
                      <RawFileViewer
                        key={`raw:${focusVersionId || "current"}:${viewStorageFile || ""}`}
                        url={currentRawUrl}
                        filename={resolveRawFilename(
                          viewStorageFile,
                          focusVersion?.storage_file_id,
                          storageFileIdProp,
                          detail?.filename,
                          detail?.original_ext
                            ? `file.${detail.original_ext}`
                            : null,
                          // Note/meeting ingest → .md even when storage_file_id is a label
                          detail?.doc_kind === "note" ||
                            detail?.doc_kind === "meeting" ||
                            source?.startsWith("__note__:") ||
                            source?.startsWith("__meeting__:")
                            ? source || "document.md"
                            : null,
                          isHistoricalFocus ? undefined : detail?.display_name,
                          source
                        )}
                        downloadUrl={downloadUrl}
                        className="h-full !rounded-none !border-0 !bg-white"
                      />
                    </div>
                  </TabsContent>

                  {/* Parse — extracted / parsed text */}
                  <TabsContent
                    value="source"
                    className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
                  >
                    <div className="pm-ws-doc-stage">
                      {previewLoading ||
                      (!isUnsupported && chunksLoading && !previewContent) ? (
                        <div className="pm-ws-loading h-full">
                          <Loader2 className="h-5 w-5 animate-spin" />
                          Loading…
                        </div>
                      ) : isUnsupported ? (
                        <div className="pm-ws-empty h-full flex flex-col items-center justify-center gap-2 px-6">
                          <p>No parse text for this version (unsupported type).</p>
                        </div>
                      ) : previewContent ? (
                        <ScrollArea className="h-full">
                          <div className="p-4">
                            <TiptapEditor
                              value={transformImageBlocks(
                                previewContent,
                                collectionId,
                                // Empty file_id: in extract blocks → use managed id
                                fileId || undefined
                              )}
                              readonly
                              showToolbar={false}
                              onEditorReady={(e) => {
                                sourceEditorRef.current = e
                              }}
                            />
                          </div>
                        </ScrollArea>
                      ) : chunks.length > 0 ? (
                        /* Fallback: chunks for *this* focusVersionId / current only */
                        <ScrollArea className="h-full">
                          <div className="p-4 space-y-2">
                            {isHistoricalFocus ? (
                              <p className="pm-meta mb-2">
                                Parse text reconstructed from this version’s chunks
                              </p>
                            ) : null}
                            {chunks.map((chunk, i) => (
                              <p
                                key={chunk.id || i}
                                className="pm-ws-prose-item"
                              >
                                {chunk.text}
                              </p>
                            ))}
                          </div>
                        </ScrollArea>
                      ) : (
                        <div className="pm-ws-empty h-full flex flex-col items-center justify-center gap-2 px-4">
                          <p>No extracted text for this version.</p>
                        </div>
                      )}
                    </div>
                  </TabsContent>

                  {/* Summary */}
                  <TabsContent
                    value="summary"
                    className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
                  >
                    <ScrollArea className="pm-ws-doc-stage">
                      <div className="p-4">
                        {isGenerating ? (
                          <div className="pm-ws-loading flex-col py-8">
                            <Loader2 className="h-5 w-5 animate-spin" />
                            <p className="pm-meta">Generating summary…</p>
                          </div>
                        ) : isUnsupported ? (
                          <div className="pm-ws-empty flex flex-col items-center justify-center py-8 gap-2 px-4">
                            <p className="pm-meta">
                              No summary for this unsupported version.
                            </p>
                          </div>
                        ) : summaryLoading ? (
                          <div className="pm-ws-loading py-8">
                            <Loader2 className="h-5 w-5 animate-spin" />
                            Loading summary…
                          </div>
                        ) : docSummary ? (
                          <div className="space-y-4">
                            {isHistoricalFocus && (
                              <p className="pm-meta px-0.5">
                                Summary for this version (read-only). Re-summarize
                                is only available on the current version.
                              </p>
                            )}
                            {detail?.is_definitive && !isHistoricalFocus && (
                              <p className="pm-meta flex items-center gap-1.5 px-0.5">
                                <Star className="h-3 w-3 text-[var(--pm-green)] fill-[var(--pm-green)]" />
                                Definitive — included in Collection Summary
                              </p>
                            )}
                            {!isHistoricalFocus && (
                              <div className="flex justify-end">
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="pm-ws-action !text-[var(--pm-green)]"
                                  disabled={isGenerating}
                                  onClick={async () => {
                                    if (!source || !collectionId) return
                                    const key = _genKey(collectionId, source)
                                    _markGenerating(key)
                                    setRenderTick((k) => k + 1)
                                    setActiveTab("summary")
                                    try {
                                      await generateDocSummary(
                                        collectionId,
                                        source,
                                        {
                                          versionId:
                                            focusVersionId || undefined,
                                        }
                                      )
                                    } catch (err) {
                                      _unmarkGenerating(key)
                                      setRenderTick((k) => k + 1)
                                      toast.error(
                                        `Failed: ${err instanceof Error ? err.message : String(err)}`
                                      )
                                    }
                                  }}
                                >
                                  {isGenerating ? (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                                  ) : (
                                    <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
                                  )}
                                  Re-summarize
                                </Button>
                              </div>
                            )}
                            {docSummary.data.length > 0 && (
                              <SummarySection
                                title="Data Points"
                                items={docSummary.data}
                              />
                            )}
                            {docSummary.facts.length > 0 && (
                              <SummarySection
                                title="Facts"
                                items={docSummary.facts}
                              />
                            )}
                            {docSummary.insights.length > 0 && (
                              <SummarySection
                                title="Insights"
                                items={docSummary.insights}
                              />
                            )}
                            {docSummary.data.length === 0 &&
                              docSummary.facts.length === 0 &&
                              docSummary.insights.length === 0 && (
                                <p className="pm-meta">
                                  No summary available for this document.
                                </p>
                              )}
                          </div>
                        ) : isHistoricalFocus ? (
                          <div className="pm-ws-empty flex flex-col items-center justify-center py-8 gap-2 px-4">
                            <p className="pm-meta">
                              No summary stored for this version.
                            </p>
                            <p className="pm-meta max-w-sm">
                              Summarize / Re-summarize is only available for the
                              current version.
                            </p>
                          </div>
                        ) : (
                          <div className="pm-ws-empty flex flex-col items-center justify-center py-8 gap-3">
                            <p className="pm-meta">
                              No summary available for this document.
                            </p>
                            <Button
                              variant="outline"
                              size="sm"
                              className="!text-[var(--pm-green)]"
                              disabled={
                                !source ||
                                !collectionId ||
                                isGenerating ||
                                isUnsupported
                              }
                              onClick={async () => {
                                if (!source || !collectionId) return
                                const key = _genKey(collectionId, source)
                                _markGenerating(key)
                                setRenderTick((k) => k + 1)
                                setDocSummary(null)
                                setActiveTab("summary")
                                try {
                                  await generateDocSummary(
                                    collectionId,
                                    source,
                                    {
                                      versionId: focusVersionId || undefined,
                                    }
                                  )
                                } catch (err) {
                                  _unmarkGenerating(key)
                                  setRenderTick((k) => k + 1)
                                  toast.error(
                                    `Failed: ${err instanceof Error ? err.message : String(err)}`
                                  )
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
                  </TabsContent>

                  {/* Chunks — full left pane */}
                  <TabsContent
                    value="chunks"
                    className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
                  >
                    <div className="pm-ws-doc-stage flex flex-col">
                      <ScrollArea className="flex-1 min-h-0">
                        <TooltipProvider delay={320} closeDelay={80}>
                        <div className="p-3 space-y-2">
                          {chunksLoading ? (
                            <div className="pm-ws-loading py-12">
                              <Loader2 className="h-5 w-5 animate-spin" />
                              Loading chunks…
                            </div>
                          ) : chunks.length === 0 ? (
                            <div className="flex flex-col items-center justify-center gap-2 p-6 text-center">
                              <p className="pm-meta">
                                {isHistoricalFocus
                                  ? "No chunks for this old version."
                                  : isUnsupported
                                    ? "No chunks — current version is not supported for ingest."
                                    : "No chunks for this version."}
                              </p>
                              <p className="pm-meta max-w-sm leading-relaxed">
                                {isHistoricalFocus
                                  ? "Chunks are stored per version_id. Older uploads may predate version tracking, or this blob was never ingested. Preview/Parse still show the original file when available."
                                  : isUnsupported
                                    ? "Unsupported types skip RAG ingest. Previous version chunks are kept in history (All Files → Old versions / Log) and are not mixed into this view."
                                    : "Upload or re-ingest a supported file to create chunks for search and this tab."}
                              </p>
                            </div>
                          ) : groupedChunks ? (
                            groupedChunks.map((group) => {
                              const seenChildImageIds = new Set<string>()
                              const isExpanded = expandedParents.has(
                                group.parent.id
                              )
                              return (
                                <div
                                  key={group.parent.id}
                                  className="pm-ws-tile !p-0 overflow-hidden"
                                >
                                  <ChunkInspect chunk={group.parent}>
                                  <button
                                    type="button"
                                    className="w-full text-left p-3 hover:bg-[var(--pm-green-wash)] transition-colors flex items-start gap-2 bg-transparent border-0 cursor-pointer"
                                    onClick={() =>
                                      toggleParent(group.parent.id)
                                    }
                                  >
                                    {isExpanded ? (
                                      <ChevronDown className="h-4 w-4 mt-0.5 shrink-0" />
                                    ) : (
                                      <ChevronRight className="h-4 w-4 mt-0.5 shrink-0" />
                                    )}
                                    <div className="flex-1 min-w-0">
                                      <div className="flex items-center gap-2 mb-1">
                                        <Badge
                                          variant="default"
                                          className="pm-meta"
                                        >
                                          Parent #{group.parent.chunk_index}
                                        </Badge>
                                        <Badge
                                          variant="outline"
                                          className="pm-meta"
                                        >
                                          {group.children.length} children
                                        </Badge>
                                        <div
                                          role="button"
                                          tabIndex={0}
                                          title="Locate in Source"
                                          className="ml-auto p-0.5 rounded hover:bg-[var(--pm-green-wash)] text-[var(--pm-faint)] cursor-pointer"
                                          onClick={(e) => {
                                            e.stopPropagation()
                                            handleLocate(group.parent)
                                          }}
                                          onKeyDown={(e) => {
                                            if (
                                              e.key === "Enter" ||
                                              e.key === " "
                                            ) {
                                              e.preventDefault()
                                              e.stopPropagation()
                                              handleLocate(group.parent)
                                            }
                                          }}
                                        >
                                          <Crosshair className="h-3.5 w-3.5" />
                                        </div>
                                      </div>
                                      <div
                                        className={cn(
                                          !isImageOnlyChunk(group.parent.text) &&
                                            !chunkHasImageFence(group.parent.text) &&
                                            "line-clamp-3 overflow-hidden",
                                          "text-[var(--pm-muted)]"
                                        )}
                                      >
                                        <ChunkMd
                                          text={group.parent.text}
                                          collection={collectionId}
                                          fileId={fileId || undefined}
                                          source={docSource || undefined}
                                        />
                                      </div>
                                    </div>
                                  </button>
                                  </ChunkInspect>
                                  {isExpanded && (
                                    <div className="border-t border-[color-mix(in_srgb,var(--pm-ink)_7%,transparent)] bg-[color-mix(in_srgb,var(--pm-ink)_2%,transparent)] p-3 space-y-2 pl-8">
                                      {group.children.map((child) => (
                                        <ChunkInspect
                                          key={child.id}
                                          chunk={child}
                                          className="pm-ws-tile cursor-pointer"
                                        >
                                          <div
                                            onClick={() => handleLocate(child)}
                                            role="button"
                                            tabIndex={0}
                                            onKeyDown={(e) => {
                                              if (
                                                e.key === "Enter" ||
                                                e.key === " "
                                              )
                                                handleLocate(child)
                                            }}
                                          >
                                            <div className="flex items-center gap-2 mb-1">
                                              <Badge
                                                variant="secondary"
                                                className="pm-meta"
                                              >
                                                Child #{child.chunk_index}
                                              </Badge>
                                              <Crosshair className="h-3 w-3 ml-auto text-[var(--pm-faint)]" />
                                            </div>
                                            <ChunkMd
                                              text={child.text}
                                              collection={collectionId}
                                              fileId={fileId || undefined}
                                              source={docSource || undefined}
                                              skipImageIds={seenChildImageIds}
                                              recordSeen
                                            />
                                          </div>
                                        </ChunkInspect>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              )
                            })
                          ) : (
                            chunks.map((chunk) => {
                              const expanded = expandedChunks.has(chunk.id)
                              return (
                                <ChunkInspect
                                  key={chunk.id}
                                  chunk={chunk}
                                  className={cn(
                                    "pm-ws-tile transition-all",
                                    highlightedIdx === chunk.chunk_index
                                      ? "is-on"
                                      : ""
                                  )}
                                >
                                  <div className="flex items-center gap-2 mb-2">
                                    <Badge
                                      variant="outline"
                                      className="pm-meta"
                                    >
                                      Chunk #{chunk.chunk_index}
                                    </Badge>
                                    {chunk.heading_path && (
                                      <span className="pm-meta truncate">
                                        {chunk.heading_path}
                                      </span>
                                    )}
                                    <button
                                      type="button"
                                      title="Locate in Source"
                                      className="ml-auto p-0.5 rounded hover:bg-[var(--pm-green-wash)] text-[var(--pm-faint)]"
                                      onClick={() => handleLocate(chunk)}
                                    >
                                      <Crosshair className="h-3.5 w-3.5" />
                                    </button>
                                  </div>
                                  <button
                                    type="button"
                                    className="w-full text-left"
                                    onClick={() => toggleChunkExpand(chunk.id)}
                                  >
                                    <div
                                      className={cn(
                                        !expanded &&
                                          !isImageOnlyChunk(chunk.text) &&
                                          !chunkHasImageFence(chunk.text) &&
                                          "line-clamp-4 overflow-hidden"
                                      )}
                                    >
                                      <ChunkMd
                                        text={chunk.text}
                                        collection={collectionId}
                                        fileId={fileId || undefined}
                                        source={docSource || undefined}
                                        skipImageIds={seenChunkImageIds}
                                        recordSeen
                                      />
                                    </div>
                                    {!expanded &&
                                      (chunk.text?.length ?? 0) > 200 && (
                                        <span className="pm-ws-link mt-1 inline-block">
                                          Show more
                                        </span>
                                      )}
                                  </button>
                                </ChunkInspect>
                              )
                            })
                          )}
                        </div>
                        </TooltipProvider>
                      </ScrollArea>
                    </div>
                  </TabsContent>

                  <TabsContent
                    value="ingest"
                    className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
                  >
                    {fileId ? (
                      <IngestTracePane
                        collectionId={collectionId}
                        fileId={fileId}
                        versionId={focusVersionId}
                      />
                    ) : (
                      <div className="pm-ws-empty p-8">
                        <p className="pm-meta">Ingest trace is only stored for managed files.</p>
                      </div>
                    )}
                  </TabsContent>
                </Tabs>
              </div>
  )
}
