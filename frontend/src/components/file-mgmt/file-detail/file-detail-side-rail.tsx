import type { Dispatch, RefObject, SetStateAction } from "react"
import { Badge } from "@/components/ui/badge"
import {
  ChevronRight,
  ChevronDown,
  Upload,
  Archive,
  ArchiveRestore,
  SearchX,
  Trash2,
  Star,
  X,
  Loader2,
} from "lucide-react"
import { cn } from "@/lib/utils"
import {
  ActionMenuItem,
  LogMsgDeleteButton,
  NodeRow,
  PathRow,
} from "./file-detail-parts"
import { SoftMenu } from "@/components/ui/menu"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { FileDetail, FilePath, FileVersion, Message } from "@/types/file-mgmt"
import { MessageBody } from "@/components/file-mgmt/message-card"
import { formatTime, versionUpdateBody, type TimelineItem } from "./file-detail-utils"

export type FileDetailSidePanel = "paths" | "nodes" | "log"
export type FileDetailActionMenu = "archive" | "delete"
export type FileDetailTimelineFilter = "all" | "versions"
export type FileDetailLogListPhase = "out" | "in" | "idle"

export type FileDetailLogMsgOpen = {
  message: Message
  version?: FileVersion | null
}

export interface FileDetailSideRailProps {
  detail: FileDetail | null
  isManagedFile: boolean
  isHistoricalFocus: boolean
  actionBusy: boolean
  handleToggleDefinitive: () => void | Promise<void>
  focusVersionId: string | null | undefined
  focusVersion: FileVersion | null | undefined
  storageFileIdProp: string | null | undefined
  viewStorageFile: string | null | undefined
  openSide: FileDetailSidePanel | null
  toggleSide: (panel: FileDetailSidePanel) => void
  onNavigateToFolder?: (folderId: string) => void
  onOpenChange: (open: boolean) => void
  handlePromote: (path: FilePath) => void | Promise<void>
  handleUnpin: (path: FilePath) => void | Promise<void>
  setPreviewNodeId: Dispatch<SetStateAction<string | null>>
  timeline: TimelineItem[]
  msgBusy: boolean
  fileId: string | null | undefined
  setAddMsgDialogOpen: Dispatch<SetStateAction<boolean>>
  logScopeRef: RefObject<HTMLDivElement | null>
  timelineFilter: FileDetailTimelineFilter
  logScopeInd: { left: number; width: number }
  logScopeAllRef: RefObject<HTMLButtonElement | null>
  logScopeVerRef: RefObject<HTMLButtonElement | null>
  handleTimelineFilter: (next: FileDetailTimelineFilter) => void
  logListPhase: FileDetailLogListPhase
  setLogMsgOpen: Dispatch<SetStateAction<FileDetailLogMsgOpen | null>>
  handleDeleteMessage: (msg: Message) => void | Promise<void>
  deleteConfirm: boolean
  handleDelete: () => void | Promise<void>
  setDeleteConfirm: Dispatch<SetStateAction<boolean>>
  actionMenuRef: RefObject<HTMLDivElement | null>
  setActionMenu: Dispatch<SetStateAction<FileDetailActionMenu | null>>
  setUpdateDialogOpen: Dispatch<SetStateAction<boolean>>
  canArchiveCurrentPath: boolean
  canArchiveGlobally: boolean
  canRestore: boolean
  actionMenu: FileDetailActionMenu | null
  fileArchived: boolean
  contextNodeId: string | null | undefined
  handleRestore: () => void | Promise<void>
  handleArchiveCurrentPath: () => void | Promise<void>
  handleArchiveGlobally: () => void | Promise<void>
  canRemoveCurrentPath: boolean
  handleRemoveCurrentPath: () => void | Promise<void>
}

export function FileDetailSideRail(p: FileDetailSideRailProps) {
  const {
    detail,
    isManagedFile,
    isHistoricalFocus,
    actionBusy,
    handleToggleDefinitive,
    focusVersionId,
    focusVersion,
    storageFileIdProp,
    viewStorageFile,
    openSide,
    toggleSide,
    onNavigateToFolder,
    onOpenChange,
    handlePromote,
    handleUnpin,
    setPreviewNodeId,
    timeline,
    msgBusy,
    fileId,
    setAddMsgDialogOpen,
    logScopeRef,
    timelineFilter,
    logScopeInd,
    logScopeAllRef,
    logScopeVerRef,
    handleTimelineFilter,
    logListPhase,
    setLogMsgOpen,
    handleDeleteMessage,
    deleteConfirm,
    handleDelete,
    setDeleteConfirm,
    actionMenuRef,
    setActionMenu,
    setUpdateDialogOpen,
    canArchiveCurrentPath,
    canArchiveGlobally,
    canRestore,
    actionMenu,
    fileArchived,
    contextNodeId,
    handleRestore,
    handleArchiveCurrentPath,
    handleArchiveGlobally,
    canRemoveCurrentPath,
    handleRemoveCurrentPath,
  } = p

  return (
    <>
              {/* ── Right: Metadata / Paths / Nodes / Log as float cards ── */}
              <div className="pm-ws-side">
                {!detail ? (
                  <div className="pm-ws-side-card flex-1 flex items-center justify-center p-6 text-center border-dashed">
                    <p className="pm-meta leading-relaxed max-w-[220px]">
                      {isManagedFile
                        ? "Could not load file management metadata."
                        : "This document is not a managed file. Paths, versions, and archive actions are unavailable. You can still read Source, Summary, and Chunks."}
                    </p>
                  </div>
                ) : (
                <>
                {/* Metadata — compact always-open card; definitive in title row */}
                <section className="pm-ws-side-card pm-ws-side-card--meta shrink-0">
                  <div className="pm-ws-side-h">
                    <span
                      className="pm-label"
                      style={{ textTransform: "none", letterSpacing: "0.02em" }}
                    >
                      Metadata
                    </span>
                    {isHistoricalFocus ? (
                      <span className="pm-meta ml-1.5">this version</span>
                    ) : null}
                    <div className="ml-auto shrink-0">
                      <TooltipProvider delay={300}>
                        <Tooltip>
                          <TooltipTrigger
                            render={
                              <button
                                type="button"
                                className={cn(
                                  "pm-ws-definitive pm-ws-definitive--header",
                                  detail?.is_definitive && "is-on"
                                )}
                                disabled={actionBusy || !detail}
                                onClick={() => void handleToggleDefinitive()}
                                aria-pressed={!!detail?.is_definitive}
                              >
                                <Star
                                  className={cn(
                                    "pm-ws-definitive-star h-3.5 w-3.5",
                                    detail?.is_definitive && "is-filled"
                                  )}
                                />
                                <span className="pm-ws-definitive-label">
                                  {detail?.is_definitive
                                    ? "Definitive"
                                    : "Mark definitive"}
                                </span>
                              </button>
                            }
                          />
                          <TooltipContent
                            side="bottom"
                            className="max-w-[240px]"
                          >
                            Definitive files feed Collection Summary (and show a
                            star). Summary is kept if you clear the flag.
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </div>
                  </div>
                  <div className="pm-ws-side-pad pm-ws-side-pad--meta">
                    <dl className="pm-ws-meta-grid">
                      {isHistoricalFocus &&
                      (focusVersionId || focusVersion || storageFileIdProp) ? (
                        <>
                          <dt>Filename</dt>
                          <dd
                            className="truncate"
                            title={viewStorageFile || storageFileIdProp || ""}
                          >
                            {viewStorageFile || storageFileIdProp || "—"}
                          </dd>
                          <dt>File ID</dt>
                          <dd
                            className="font-mono truncate pm-meta"
                            title={detail.file_id}
                          >
                            {detail.file_id}
                          </dd>
                          <dt>Version</dt>
                          <dd>
                            v{focusVersion?.version_no ?? "—"}
                            {focusVersion?.archived ? " · archived" : ""}
                            {" · old"}
                          </dd>
                          <dt>Created</dt>
                          <dd>
                            {formatTime(
                              focusVersion?.created_at || detail?.created_at
                            )}
                          </dd>
                          <dt>Versions</dt>
                          <dd>{detail?.versions?.length ?? 0}</dd>
                          {focusVersion?.commit_message ? (
                            <>
                              <dt>Note</dt>
                              <dd
                                className="truncate"
                                title={focusVersion.commit_message}
                              >
                                {focusVersion.commit_message}
                              </dd>
                            </>
                          ) : null}
                        </>
                      ) : (
                        <>
                          <dt>Filename</dt>
                          <dd
                            className="truncate"
                            title={
                              detail?.display_name || detail?.filename || ""
                            }
                          >
                            {detail?.display_name || detail?.filename || "—"}
                          </dd>
                          <dt>File ID</dt>
                          <dd
                            className="font-mono truncate pm-meta"
                            title={detail.file_id}
                          >
                            {detail.file_id}
                          </dd>
                          <dt>Created</dt>
                          <dd>{formatTime(detail?.created_at)}</dd>
                          <dt>Versions</dt>
                          <dd>{detail?.versions?.length ?? 0}</dd>
                        </>
                      )}
                    </dl>
                  </div>
                </section>

                {/*
                  Paths / Nodes / Log — fixed-height accordion stack
                  (Overview .pm-rail-lower language). Exactly one expanded;
                  default Log. Expanded card flex-grows to fill remaining height.
                */}
                <div className="pm-ws-side-lower">
                  {/* Paths */}
                  <section
                    className={cn(
                      "pm-ws-side-card",
                      openSide === "paths" && "is-expanded"
                    )}
                  >
                    <div className="pm-collapse-h shrink-0">
                      <button
                        type="button"
                        className="pm-collapse-h-main"
                        aria-expanded={openSide === "paths"}
                        aria-label="Toggle Paths"
                        onClick={() => toggleSide("paths")}
                      >
                        <span
                          className={cn(
                            "pm-rail-chev",
                            openSide === "paths" && "is-open"
                          )}
                          aria-hidden
                        >
                          <ChevronRight className="size-3.5" strokeWidth={2} />
                        </span>
                        <span
                          className="pm-label"
                          style={{
                            textTransform: "none",
                            letterSpacing: "0.02em",
                          }}
                        >
                          Paths
                        </span>
                        <span className="pm-count-pill">
                          {detail?.paths?.length ?? 0}
                        </span>
                      </button>
                    </div>
                    <div
                      className={cn(
                        "pm-ws-side-collapse",
                        openSide === "paths" && "is-open"
                      )}
                    >
                      <div className="pm-ws-side-collapse-inner">
                        <div className="pm-ws-side-pad pt-0">
                          {(detail?.paths?.length ?? 0) === 0 ? (
                            <p className="pm-meta">
                              No folder paths (orphan / root file)
                            </p>
                          ) : (
                            <ul className="pm-ws-list">
                              {detail!.paths.map((p) => {
                                const srcNode = p.source_node_id
                                  ? detail!.nodes.find(
                                      (n) => n.node_id === p.source_node_id
                                    )
                                  : undefined
                                const folderHasPinned =
                                  !!p.folder_id &&
                                  detail!.paths.some(
                                    (q) =>
                                      q.folder_id === p.folder_id &&
                                      !q.source_node_id
                                  )
                                const canUnpin =
                                  !p.source_node_id &&
                                  !!p.folder_id &&
                                  (detail!.paths.some(
                                    (q) =>
                                      q.folder_id === p.folder_id &&
                                      !!q.source_node_id
                                  ) ||
                                    detail!.nodes.length > 0)
                                return (
                                  <PathRow
                                    key={p.path_id}
                                    path={p}
                                    sourceNodeTitle={
                                      srcNode?.title?.trim() ||
                                      (p.source_node_id
                                        ? "Untitled node"
                                        : null)
                                    }
                                    folderHasPinned={folderHasPinned}
                                    canUnpin={canUnpin}
                                    busy={actionBusy}
                                    onNavigate={() => {
                                      if (p.folder_id && onNavigateToFolder) {
                                        onNavigateToFolder(p.folder_id)
                                        onOpenChange(false)
                                      }
                                    }}
                                    onPromote={() => void handlePromote(p)}
                                    onUnpin={() => void handleUnpin(p)}
                                  />
                                )
                              })}
                            </ul>
                          )}
                        </div>
                      </div>
                    </div>
                  </section>

                  {/* Nodes */}
                  <section
                    className={cn(
                      "pm-ws-side-card",
                      openSide === "nodes" && "is-expanded"
                    )}
                  >
                    <div className="pm-collapse-h shrink-0">
                      <button
                        type="button"
                        className="pm-collapse-h-main"
                        aria-expanded={openSide === "nodes"}
                        aria-label="Toggle Nodes"
                        onClick={() => toggleSide("nodes")}
                      >
                        <span
                          className={cn(
                            "pm-rail-chev",
                            openSide === "nodes" && "is-open"
                          )}
                          aria-hidden
                        >
                          <ChevronRight className="size-3.5" strokeWidth={2} />
                        </span>
                        <span
                          className="pm-label"
                          style={{
                            textTransform: "none",
                            letterSpacing: "0.02em",
                          }}
                        >
                          Nodes
                        </span>
                        <span className="pm-count-pill">
                          {detail?.nodes?.length ?? 0}
                        </span>
                      </button>
                    </div>
                    <div
                      className={cn(
                        "pm-ws-side-collapse",
                        openSide === "nodes" && "is-open"
                      )}
                    >
                      <div className="pm-ws-side-collapse-inner">
                        <div className="pm-ws-side-pad pt-0">
                          {(detail?.nodes?.length ?? 0) === 0 ? (
                            <p className="pm-meta">Not attached to any node</p>
                          ) : (
                            <ul className="pm-ws-list">
                              {detail!.nodes.map((n) => (
                                <NodeRow
                                  key={n.node_id}
                                  node={n}
                                  onClick={() => setPreviewNodeId(n.node_id)}
                                />
                              ))}
                            </ul>
                          )}
                        </div>
                      </div>
                    </div>
                  </section>

                  {/* Log — default open */}
                  <section
                    className={cn(
                      "pm-ws-side-card pm-ws-side-card--log",
                      openSide === "log" && "is-expanded"
                    )}
                  >
                    <div className="pm-collapse-h shrink-0">
                      <button
                        type="button"
                        className="pm-collapse-h-main"
                        aria-expanded={openSide === "log"}
                        aria-label="Toggle Log"
                        onClick={() => toggleSide("log")}
                      >
                        <span
                          className={cn(
                            "pm-rail-chev",
                            openSide === "log" && "is-open"
                          )}
                          aria-hidden
                        >
                          <ChevronRight className="size-3.5" strokeWidth={2} />
                        </span>
                        <span
                          className="pm-label"
                          style={{
                            textTransform: "none",
                            letterSpacing: "0.02em",
                          }}
                        >
                          Log
                        </span>
                        <span className="pm-count-pill">{timeline.length}</span>
                      </button>
                      <div className="pm-collapse-h-actions items-center gap-1.5">
                        <button
                          type="button"
                          className="pm-ws-log-add shrink-0"
                          disabled={msgBusy || !fileId}
                          onClick={() => setAddMsgDialogOpen(true)}
                        >
                          Add
                        </button>
                        <div
                          ref={logScopeRef}
                          className="pm-ws-scope shrink-0"
                          data-on={timelineFilter}
                        >
                          <span
                            className="pm-ws-scope-ind"
                            aria-hidden
                            style={{
                              transform: `translateX(${logScopeInd.left}px)`,
                              width: logScopeInd.width,
                              opacity: logScopeInd.width > 0 ? 1 : 0,
                            }}
                          />
                          <button
                            ref={logScopeAllRef}
                            type="button"
                            className={cn(
                              "pm-ws-scope-btn",
                              timelineFilter === "all" && "is-on"
                            )}
                            onClick={() => handleTimelineFilter("all")}
                          >
                            All
                          </button>
                          <button
                            ref={logScopeVerRef}
                            type="button"
                            className={cn(
                              "pm-ws-scope-btn",
                              timelineFilter === "versions" && "is-on"
                            )}
                            onClick={() => handleTimelineFilter("versions")}
                          >
                            Versions
                          </button>
                        </div>
                      </div>
                    </div>
                    <div
                      className={cn(
                        "pm-ws-side-collapse",
                        openSide === "log" && "is-open"
                      )}
                    >
                      <div className="pm-ws-side-collapse-inner">
                        <div className="pm-ws-side-pad pt-0 pm-ws-side-log-body">
                          <ul
                            className={cn(
                              "pm-ws-log-list",
                              logListPhase === "out" && "is-out",
                              logListPhase === "in" && "is-in"
                            )}
                          >
                            {timeline.length === 0 ? (
                              <p className="pm-meta px-2 py-1">No log yet</p>
                            ) : (
                              timeline.map((item) => {
                                if (item.kind === "version") {
                                  /**
                                   * Orphan file version (no paired system_version message).
                                   * Still open dual-pane when we can recover a message by
                                   * time/body; otherwise show note only (not editable yet).
                                   */
                                  const orphanMsg =
                                    detail?.messages?.find((m) => {
                                      if (
                                        (m.owner_type || "").toLowerCase() !==
                                        "system_version"
                                      )
                                        return false
                                      if (
                                        m.created_at &&
                                        item.version.created_at &&
                                        m.created_at === item.version.created_at
                                      )
                                        return true
                                      const body = (m.body || "").trim()
                                      const cm = (
                                        item.version.commit_message || ""
                                      ).trim()
                                      return !!body && !!cm && body === cm
                                    }) ?? null
                                  return (
                                    <li
                                      key={item.id}
                                      className={cn(
                                        "pm-ws-log-item is-version",
                                        orphanMsg && "is-clickable group"
                                      )}
                                      onClick={
                                        orphanMsg
                                          ? () =>
                                              setLogMsgOpen({
                                                message: orphanMsg,
                                                version: item.version,
                                              })
                                          : undefined
                                      }
                                    >
                                      <div className="flex items-center gap-1.5 mb-1 min-w-0">
                                        <span
                                          className="pm-ws-log-dot"
                                          aria-hidden
                                        />
                                        <Badge
                                          variant="secondary"
                                          className="pm-ws-badge is-live shrink-0"
                                        >
                                          version update
                                        </Badge>
                                        {item.version.archived && (
                                          <span className="pm-meta uppercase shrink-0">
                                            archived
                                          </span>
                                        )}
                                        <span className="pm-meta shrink-0">
                                          v{item.version.version_no}
                                        </span>
                                        <span className="ml-auto pm-meta tabular-nums shrink-0 text-right">
                                          {formatTime(item.created_at)}
                                        </span>
                                      </div>
                                      <p className="pm-meta text-[var(--pm-faint)]">
                                        {versionUpdateBody(
                                          item.version.commit_message
                                        )}
                                      </p>
                                    </li>
                                  )
                                }

                                const msg = item.message
                                const isVer = item.isVersionUpdate
                                const canDelete =
                                  !isVer && msg.author_type !== "system"
                                const displayBody = isVer
                                  ? versionUpdateBody(msg.body)
                                  : msg.body || ""

                                return (
                                  <li
                                    key={item.id}
                                    className={cn(
                                      "pm-ws-log-item is-clickable group",
                                      isVer && "is-version"
                                    )}
                                    onClick={() =>
                                      setLogMsgOpen({
                                        message: msg,
                                        version: item.version ?? null,
                                      })
                                    }
                                  >
                                    <div className="flex items-center gap-1.5 mb-1 min-w-0">
                                      {isVer ? (
                                        <>
                                          <span
                                            className="pm-ws-log-dot"
                                            aria-hidden
                                          />
                                          <Badge
                                            variant="secondary"
                                            className="pm-ws-badge is-live shrink-0"
                                          >
                                            version update
                                          </Badge>
                                          {item.version && (
                                            <span className="pm-meta shrink-0">
                                              v{item.version.version_no}
                                            </span>
                                          )}
                                          {item.version?.archived && (
                                            <span className="pm-meta uppercase shrink-0">
                                              archived
                                            </span>
                                          )}
                                        </>
                                      ) : (
                                        <>
                                          <Badge
                                            variant="secondary"
                                            className="pm-ws-badge shrink-0"
                                          >
                                            message
                                          </Badge>
                                          {msg.author_id &&
                                            msg.author_id !== "local" &&
                                            msg.author_id !== "user" && (
                                              <span className="pm-meta shrink-0">
                                                {msg.author_id}
                                              </span>
                                            )}
                                        </>
                                      )}
                                      {msg.edited_at && (
                                        <Badge
                                          variant="outline"
                                          className="pm-ws-badge shrink-0"
                                        >
                                          edited
                                        </Badge>
                                      )}
                                      <div
                                        className={cn(
                                          "ml-auto flex items-center gap-1.5 shrink-0",
                                          /* keep actions visible while armed (same as message rail) */
                                        )}
                                      >
                                        {canDelete && (
                                          <LogMsgDeleteButton
                                            disabled={msgBusy}
                                            onConfirm={() =>
                                              void handleDeleteMessage(msg)
                                            }
                                          />
                                        )}
                                        <span className="pm-meta tabular-nums text-right">
                                          {formatTime(item.created_at)}
                                        </span>
                                      </div>
                                    </div>
                                    <MessageBody
                                      body={displayBody}
                                      className={cn(
                                        "pm-ws-msg-md line-clamp-4",
                                        "max-w-none",
                                        "[&_p]:my-0.5 [&_ul]:my-0.5 [&_ol]:my-0.5 [&_li]:my-0"
                                      )}
                                    />
                                  </li>
                                )
                              })
                            )}
                          </ul>
                        </div>
                      </div>
                    </div>
                  </section>
                </div>

                {/* Bottom action dock — pinned under rail cards */}
                <div className="pm-ws-side-actions">
                  {deleteConfirm ? (
                    <div className="rounded-[var(--pm-r-sm)] bg-[color-mix(in_srgb,var(--pm-danger)_6%,transparent)] p-2.5 space-y-2">
                      <p className="pm-title text-[var(--pm-danger)]">
                        Permanently delete this file?
                      </p>
                      <p className="pm-meta">
                        All paths will be removed:
                      </p>
                      <ul className="pm-meta space-y-0.5 max-h-24 overflow-y-auto">
                        {(detail?.paths?.length ?? 0) === 0 ? (
                          <li className="italic">
                            (no folder paths — orphan file)
                          </li>
                        ) : (
                          detail!.paths.map((p) => (
                            <li key={p.path_id} className="truncate">
                              {p.folder_path || p.folder_id || "—"}
                              {p.source_node_id
                                ? " (via timeline node)"
                                : " (pinned to folder)"}
                            </li>
                          ))
                        )}
                      </ul>
                      <div className="pm-ws-side-actions-row">
                        <button
                          type="button"
                          className="pm-ws-foot-btn pm-ws-foot-btn--danger"
                          disabled={actionBusy}
                          onClick={() => void handleDelete()}
                        >
                          {actionBusy ? (
                            <Loader2 className="animate-spin" />
                          ) : (
                            <Trash2 />
                          )}
                          Confirm
                        </button>
                        <button
                          type="button"
                          className="pm-ws-foot-btn pm-ws-foot-btn--ghost"
                          onClick={() => setDeleteConfirm(false)}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div
                      ref={actionMenuRef}
                      className="pm-ws-side-actions-row overflow-visible"
                    >
                      <button
                        type="button"
                        className="pm-ws-foot-btn pm-ws-foot-btn--pri"
                        disabled={actionBusy}
                        onClick={() => {
                          setActionMenu(null)
                          setUpdateDialogOpen(true)
                        }}
                      >
                        <Upload />
                        Update
                      </button>

                      {/* Archive dropdown — context-aware path vs global */}
                      {(canArchiveCurrentPath ||
                        canArchiveGlobally ||
                        canRestore) && (
                        <div className="relative">
                          <button
                            type="button"
                            className={cn(
                              "pm-ws-foot-btn pm-ws-foot-btn--ghost",
                              actionMenu === "archive" && "is-on"
                            )}
                            disabled={actionBusy}
                            title="Archive options"
                            onClick={() =>
                              setActionMenu((m) =>
                                m === "archive" ? null : "archive"
                              )
                            }
                          >
                            <Archive />
                            Archive
                            <ChevronDown className="opacity-50 !w-3 !h-3" />
                          </button>
                          <SoftMenu
                            open={actionMenu === "archive"}
                            className="absolute left-0 bottom-full mb-1.5 z-50 min-w-[260px] pm-menu--drop-up pm-files-menu"
                          >
                            {canRestore && (
                              <ActionMenuItem
                                icon={
                                  <ArchiveRestore className="h-3.5 w-3.5" />
                                }
                                title="Restore"
                                description={
                                  fileArchived
                                    ? "Re-enable search (and restore current path if archived)."
                                    : contextNodeId
                                      ? "Restore this file's path(s) for the current node."
                                      : "Restore this file's current path."
                                }
                                onClick={() => {
                                  setActionMenu(null)
                                  void handleRestore()
                                }}
                              />
                            )}
                            {canArchiveCurrentPath && (
                              <ActionMenuItem
                                icon={<Archive className="h-3.5 w-3.5" />}
                                title="Archive current path"
                                description={
                                  contextNodeId
                                    ? "Grey out node-related path(s) only (group + branch). Leaves other mounts active."
                                    : "Grey out this file on the current folder path only."
                                }
                                onClick={() => {
                                  setActionMenu(null)
                                  void handleArchiveCurrentPath()
                                }}
                              />
                            )}
                            {canArchiveGlobally && (
                              <ActionMenuItem
                                icon={<SearchX className="h-3.5 w-3.5" />}
                                title="Archive globally"
                                description="Exclude this file from search everywhere."
                                onClick={() => {
                                  setActionMenu(null)
                                  void handleArchiveGlobally()
                                }}
                              />
                            )}
                          </SoftMenu>
                        </div>
                      )}

                      {/* Delete dropdown: remove path + permanent delete */}
                      <div className="relative">
                        <button
                          type="button"
                          className={cn(
                            "pm-ws-foot-btn pm-ws-foot-btn--danger",
                            actionMenu === "delete" && "is-on"
                          )}
                          disabled={actionBusy}
                          title="Remove or delete"
                          onClick={() =>
                            setActionMenu((m) =>
                              m === "delete" ? null : "delete"
                            )
                          }
                        >
                          <Trash2 />
                          Delete
                          <ChevronDown className="opacity-50 !w-3 !h-3" />
                        </button>
                        <SoftMenu
                          open={actionMenu === "delete"}
                          className="absolute right-0 bottom-full mb-1.5 z-50 min-w-[260px] pm-menu--drop-up pm-files-menu"
                        >
                          {canRemoveCurrentPath && (
                            <ActionMenuItem
                              icon={<X className="h-3.5 w-3.5" />}
                              title="Remove current path"
                              description={
                                contextNodeId
                                  ? "Detach from this node and remove its path(s) (group + branch). Other mounts stay."
                                  : "Remove this file from the current folder path only."
                              }
                              onClick={() => {
                                setActionMenu(null)
                                void handleRemoveCurrentPath()
                              }}
                            />
                          )}
                          <ActionMenuItem
                            icon={<Trash2 className="h-3.5 w-3.5" />}
                            title="Delete file globally"
                            description="Permanently delete this file everywhere."
                            destructive
                            onClick={() => {
                              setActionMenu(null)
                              setDeleteConfirm(true)
                            }}
                          />
                        </SoftMenu>
                      </div>
                    </div>
                  )}
                </div>
                </>
                )}
              </div>
    </>
  )
}
