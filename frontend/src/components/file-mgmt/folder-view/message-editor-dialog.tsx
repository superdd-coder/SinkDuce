import {
  useState,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
} from "react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { MESSAGE_EDITOR_PLACEHOLDER } from "@/components/ui/tiptap-editor"
import { Clock, GitBranch, Loader2, Paperclip, Pencil } from "lucide-react"
import type { Chain, Message, NodeDetail, NodeGroup } from "@/types/file-mgmt"
import {
  getNodeDetail,
  getNodeMessages,
  listChains,
  listGroups,
  updateMessage,
} from "@/api/file-mgmt"
import { formatMessageSourceTag, MessageBody } from "../message-card"
import { MiniChainGraph } from "../mini-chain-graph"
import { cn } from "@/lib/utils"

/** Content pane sequential fade — open/close symmetric (ENGINEERING §4) */
const PANE_OUT_MS = 140

/** Plain excerpt for list rows — avoid markdown heading clutter in the deck. */
function messagePlainExcerpt(body: string): string {
  return (body || "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`[^`]*`/g, " ")
    .replace(/!\[.*?\]\(.*?\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/[*_~>|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
}

interface MessageEditorDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  initialContent: string
  onSave: (content: string) => void
  /** Initial open mode; user can switch to edit via in-dialog button */
  readonly?: boolean
  /** When set and owner is node, show detail + chain graph layout */
  message?: Message | null
  collectionId?: string
  /** Active folder scope — folder msgs with this owner show "Current folder" */
  highlightFolderId?: string | null
  folderMsgsAreCurrentScope?: boolean
  /**
   * Green domain kicker (File / Folder / Node / Branch / Root).
   * Prefer explicit pass from parent when adding; viewing can derive from message.
   */
  kicker?: string | null
  /** Optional guidance under the title (premium dialog stack). */
  description?: string | null
  /** In-app navigation to timeline (must not open a new window) */
  onNavigateToNode?: (nodeId: string, chainId: string) => void
  /**
   * Parent sync when user picks another message on the same node.
   * Prefer this so save still targets the correct message_id.
   */
  onSelectNodeMessage?: (msg: Message) => void
}

/** Map owner_type → short green kicker label */
function kickerFromOwnerType(ownerType: string | undefined | null): string | null {
  const ot = (ownerType || "").toLowerCase()
  if (ot === "file") return "File"
  if (ot === "folder") return "Folder"
  if (ot === "node") return "Node"
  if (ot === "collection") return "Root"
  if (ot === "system_version") return "Version"
  return null
}

export function MessageEditorDialog({
  open,
  onOpenChange,
  title,
  initialContent,
  onSave,
  readonly = true,
  message = null,
  collectionId,
  highlightFolderId = null,
  folderMsgsAreCurrentScope = false,
  kicker = null,
  description = null,
  onNavigateToNode,
  onSelectNodeMessage,
}: MessageEditorDialogProps) {
  /** Currently viewed/edited message (can switch via node message list). */
  const [activeMsg, setActiveMsg] = useState<Message | null>(message)
  const [content, setContent] = useState(initialContent)
  const [editing, setEditing] = useState(!readonly)
  const [saving, setSaving] = useState(false)

  const isNodeMsg =
    !!activeMsg &&
    (activeMsg.owner_type || "").toLowerCase() === "node" &&
    !!activeMsg.owner_id
  const isSystem = activeMsg?.author_type === "system"
  const sourceTag = activeMsg
    ? formatMessageSourceTag(activeMsg, {
        highlightFolderId,
        folderMsgsAreCurrentScope,
      })
    : null

  const [nodeDetail, setNodeDetail] = useState<NodeDetail | null>(null)
  const [nodeMsgs, setNodeMsgs] = useState<Message[]>([])
  const [groups, setGroups] = useState<NodeGroup[]>([])
  const [chains, setChains] = useState<Chain[]>([])
  const [nodeLoading, setNodeLoading] = useState(false)
  /** Message pane opacity — sequential fade when switching list items */
  const [paneFading, setPaneFading] = useState(false)
  const paneSwitchGen = useRef(0)
  const paneTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  /**
   * Freeze timeline target for this dialog open.
   * Switching messages on the same node must NOT remount / re-fetch MiniChainGraph.
   */
  const timelineNodeIdRef = useRef<string | null>(null)

  // Sync when parent opens / switches message (key remount or prop change)
  useEffect(() => {
    if (open) {
      setActiveMsg(message)
      setContent(initialContent)
      setEditing(!readonly)
      setPaneFading(false)
      // Lock timeline to the node we opened with (not later list picks)
      const ot = (message?.owner_type || "").toLowerCase()
      if (ot === "node" && message?.owner_id) {
        timelineNodeIdRef.current = message.owner_id
      } else if (!message) {
        // Add-message mode
        timelineNodeIdRef.current = null
      }
      return
    }
    // ── Closing / closed: freeze painted UI for silk exit ──
    // Do NOT clear nodeDetail / msgs / timeline / focus here — that unmounts
    // controls mid-fade and reads as “residue”. Parents clear payload after 320ms.
    if (paneTimerRef.current) {
      clearTimeout(paneTimerRef.current)
      paneTimerRef.current = null
    }
    setPaneFading(false)
  }, [open, message?.message_id, initialContent, readonly, message])

  const ownerId = activeMsg?.owner_id
  /** Stable timeline node — prefer freeze, fall back to current owner */
  const timelineNodeId =
    timelineNodeIdRef.current ||
    (isNodeMsg ? ownerId : null) ||
    null

  useEffect(() => {
    // While closed, keep last board for exit paint — no clear, no loading spin
    if (!open) return
    if (!isNodeMsg || !collectionId || !ownerId) {
      // Opened as non-node (add / file msg) — board not needed
      if (!isNodeMsg) {
        setNodeDetail(null)
        setNodeMsgs([])
        setChains([])
      }
      return
    }
    let cancelled = false
    setNodeLoading(true)
    // Node board data is per-node; ownerId stable while switching msgs on same node
    Promise.all([
      getNodeDetail(collectionId, ownerId),
      getNodeMessages(collectionId, ownerId),
      listGroups(collectionId),
      listChains(collectionId),
    ])
      .then(([d, msgs, gs, cs]) => {
        if (cancelled) return
        setNodeDetail(d)
        setNodeMsgs(msgs)
        setGroups(gs)
        setChains(cs)
      })
      .catch(() => {
        if (!cancelled) {
          setNodeDetail(null)
          setNodeMsgs([])
          setChains([])
        }
      })
      .finally(() => {
        if (!cancelled) setNodeLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, isNodeMsg, collectionId, ownerId])

  const handleOpenChange = useCallback(
    (o: boolean) => {
      if (o) {
        setActiveMsg(message)
        setContent(initialContent)
        setEditing(!readonly)
      }
      onOpenChange(o)
    },
    [initialContent, onOpenChange, readonly, message]
  )

  const handleSave = useCallback(async () => {
    const trimmed = content.trim()
    if (!trimmed) return
    setSaving(true)
    try {
      // New message: no activeMsg yet — always create via parent onSave
      if (!activeMsg) {
        onSave(trimmed)
        setEditing(false)
        onOpenChange(false)
        return
      }
      // Same message the parent opened (or parent did not pass message): parent onSave
      if (!message || activeMsg.message_id === message.message_id) {
        onSave(trimmed)
        setEditing(false)
        onOpenChange(false)
        return
      }
      // Switched to another node message inside the dialog — patch via API
      if (collectionId) {
        const updated = await updateMessage(collectionId, activeMsg.message_id, {
          body: trimmed,
          version: activeMsg.version,
        })
        setActiveMsg(updated)
        setNodeMsgs((prev) =>
          prev.map((m) => (m.message_id === updated.message_id ? updated : m))
        )
        setContent(updated.body || "")
        setEditing(false)
        onSelectNodeMessage?.(updated)
      } else {
        onSave(trimmed)
        setEditing(false)
        onOpenChange(false)
      }
    } finally {
      setSaving(false)
    }
  }, [
    content,
    activeMsg,
    message,
    onSave,
    onOpenChange,
    collectionId,
    onSelectNodeMessage,
  ])

  const groupName = nodeDetail?.group_id
    ? groups.find((g) => g.group_id === nodeDetail.group_id)?.name ?? null
    : null

  /** Branch chain tag — only when node is not on the main chain. */
  const branchName = useMemo(() => {
    if (!nodeDetail?.chain_id) return null
    const chain = chains.find((c) => c.chain_id === nodeDetail.chain_id)
    if (!chain || chain.is_main) return null
    const t = (chain.title || "").trim()
    return t || "Branch"
  }, [nodeDetail?.chain_id, chains])

  const sortedNodeMsgs = useMemo(() => {
    return [...nodeMsgs].sort((a, b) => {
      const ta = a.created_at || ""
      const tb = b.created_at || ""
      return tb.localeCompare(ta)
    })
  }, [nodeMsgs])

  /**
   * List focus id — drives the sliding wash **immediately** on click.
   * Separate from activeMsg so content can fade 140ms later without
   * the indicator waiting (which looked like “jump then slide”).
   */
  const [listFocusId, setListFocusId] = useState<string | null>(
    () => message?.message_id ?? null
  )

  // Keep list focus in sync when dialog opens / parent message changes
  useEffect(() => {
    if (open) {
      setListFocusId(message?.message_id ?? activeMsg?.message_id ?? null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-anchor on open/message id
  }, [open, message?.message_id])

  /**
   * Switch list selection:
   * 1) focus wash slides immediately (listFocusId)
   * 2) content sequential fade: hide 140ms → swap → show 180ms
   * Timeline stays frozen. Do NOT notify parent (avoids message_id key remount).
   */
  const handleSelectNodeMessage = useCallback(
    (m: Message) => {
      if (m.message_id === listFocusId && m.message_id === activeMsg?.message_id)
        return
      // 1) Focus moves now — no delay
      setListFocusId(m.message_id)

      if (m.message_id === activeMsg?.message_id) return

      // 2) Content fades independently
      const gen = ++paneSwitchGen.current
      if (paneTimerRef.current) clearTimeout(paneTimerRef.current)
      setPaneFading(true)
      paneTimerRef.current = setTimeout(() => {
        if (gen !== paneSwitchGen.current) return
        setActiveMsg(m)
        setContent(m.body || "")
        setEditing(false)
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            if (gen !== paneSwitchGen.current) return
            setPaneFading(false)
          })
        })
        paneTimerRef.current = null
      }, PANE_OUT_MS)
    },
    [listFocusId, activeMsg?.message_id]
  )

  // ── Sliding focus indicator (list) — tracks listFocusId, not activeMsg ──
  const msgListRef = useRef<HTMLDivElement>(null)
  const msgItemRefs = useRef(
    new Map<string, HTMLButtonElement | HTMLDivElement>()
  )
  const focusIndRef = useRef({ top: 0, height: 0, ready: false })
  const [focusInd, setFocusInd] = useState({
    top: 0,
    height: 0,
    ready: false,
  })
  /** First paint places without tween; later moves slide */
  const focusCanTweenRef = useRef(false)

  const measureFocusInd = useCallback(() => {
    const id = listFocusId
    const list = msgListRef.current
    const el = id ? msgItemRefs.current.get(id) : null
    if (!list || !el) return

    // Position relative to list's scrollport content (works with padding + scroll)
    const top = el.offsetTop
    const height = el.offsetHeight
    const prev = focusIndRef.current
    if (
      prev.ready &&
      Math.abs(prev.top - top) < 0.5 &&
      Math.abs(prev.height - height) < 0.5
    ) {
      return
    }
    const next = { top, height, ready: true }
    focusIndRef.current = next
    setFocusInd(next)
    // After first successful layout, enable sliding
    if (!focusCanTweenRef.current) {
      // Next frame so the first place doesn't animate from 0
      requestAnimationFrame(() => {
        focusCanTweenRef.current = true
        list?.classList.add("is-focus-tween")
      })
    }
  }, [listFocusId])

  useLayoutEffect(() => {
    measureFocusInd()
  }, [measureFocusInd, sortedNodeMsgs.length, nodeLoading, listFocusId])

  useEffect(() => {
    const list = msgListRef.current
    if (!list) return
    // Observe each row so height changes re-measure without hard jumps
    const ro = new ResizeObserver(() => measureFocusInd())
    ro.observe(list)
    for (const el of msgItemRefs.current.values()) ro.observe(el)
    return () => ro.disconnect()
  }, [measureFocusInd, nodeLoading, sortedNodeMsgs.length])

  /**
   * Silk open/close (same as todo / Note / File detail):
   * opacity + scale on popup, opacity on overlay — 280ms symmetric.
   * !animate-none beats DialogContent default animate-in/out keyframes (twMerge).
   * Parent must keep mounted while open→false so exit can finish.
   */
  const silkShell = cn(
    "pm-dialog pm-dialog--silk",
    "!animate-none data-open:!animate-none data-closed:!animate-none"
  )

  const bodyClass =
    "prose prose-sm max-w-none leading-relaxed break-words [&_p]:my-2 font-[family-name:var(--pm-ff-prose)]"

  /**
   * Card title actions (not dialog chrome):
   * Idle → pill Edit; editing → Cancel + Save slide out (same as Version Update).
   */
  const canEditCard = editing || !isSystem
  const handleCancelEdit = () => {
    setEditing(false)
    setContent(activeMsg?.body || initialContent)
  }
  const messageCardActions = canEditCard ? (
    <div className={cn("pm-log-msg-card-actions", editing && "is-editing")}>
      <div className="pm-log-msg-expand" aria-hidden={!editing}>
        <div className="pm-log-msg-expand-inner">
          {activeMsg ? (
            <button
              type="button"
              className="pm-btn-ghost pm-btn-xs pm-log-msg-action-btn"
              disabled={saving}
              tabIndex={editing ? 0 : -1}
              onClick={handleCancelEdit}
            >
              Cancel
            </button>
          ) : null}
          <button
            type="button"
            className="pm-btn-pri pm-btn-xs pm-log-msg-action-btn"
            disabled={!content.trim() || saving}
            tabIndex={editing ? 0 : -1}
            onClick={() => void handleSave()}
          >
            {saving ? (
              <>
                <Loader2 className="h-3 w-3 animate-spin" />
                Saving…
              </>
            ) : (
              "Save"
            )}
          </button>
        </div>
      </div>
      {activeMsg && !isSystem ? (
        <button
          type="button"
          className="pm-log-msg-edit"
          aria-expanded={editing}
          aria-label="Edit message"
          tabIndex={editing ? -1 : 0}
          onClick={() => {
            if (!editing) setEditing(true)
          }}
        >
          <Pencil className="h-3.5 w-3.5" strokeWidth={1.75} />
          Edit
        </button>
      ) : null}
    </div>
  ) : null

  const resolvedKicker =
    (kicker && kicker.trim()) ||
    kickerFromOwnerType(activeMsg?.owner_type) ||
    null

  const titleRow = (
    <>
      {resolvedKicker ? (
        <DialogKicker>{resolvedKicker}</DialogKicker>
      ) : null}
      <DialogTitle className="flex items-center gap-2 min-w-0 text-left">
        <span className="shrink-0">{title}</span>
        {sourceTag && (
          <span
            className={cn(
              "pm-meta normal-case tracking-normal px-1.5 py-0.5 rounded truncate max-w-[12rem]",
              sourceTag.isCurrentFolder
                ? "text-[var(--pm-green)] bg-[var(--pm-green-soft)]"
                : "text-[var(--pm-muted)] bg-[rgba(18,20,16,0.05)]"
            )}
            title={sourceTag.full}
          >
            {sourceTag.label}
          </span>
        )}
      </DialogTitle>
      {description?.trim() ? (
        <DialogDescription className="pm-msg-dialog-desc">
          {description.trim()}
        </DialogDescription>
      ) : null}
    </>
  )

  /**
   * Message body card — no key remount on switch (hard cut).
   * Body crossfades via paneFading; chrome (Edit/Save) stays put.
   */
  const messageCard = (
    <div className="pm-msg-dialog-card flex-1 min-h-0 flex flex-col">
      {messageCardActions && (
        <div className="pm-msg-dialog-card-h">
          <span
            className="pm-label"
            style={{
              textTransform: "none",
              letterSpacing: "0.02em",
            }}
          >
            Message
          </span>
          <div className="ml-auto shrink-0">{messageCardActions}</div>
        </div>
      )}
      <div
        className={cn(
          "pm-msg-pane-body flex-1 min-h-0 flex flex-col",
          paneFading && "is-fading"
        )}
      >
        {editing ? (
          <div className="flex-1 min-h-0 flex flex-col pm-msg-editor-host">
            <MarkdownEditor
              value={content}
              onChange={setContent}
              minHeight={isNodeMsg ? "140px" : "280px"}
              placeholder={MESSAGE_EDITOR_PLACEHOLDER}
              showToolbar
              flush
              className="flex-1 min-h-0"
            />
          </div>
        ) : (
          <div className="p-5 pm-prose max-w-none flex-1 overflow-auto">
            <MessageBody
              body={content || activeMsg?.body || initialContent}
              className={bodyClass}
            />
          </div>
        )}
      </div>
    </div>
  )

  /**
   * Single Dialog instance for both layouts — remounting between single/node
   * trees killed silk enter/exit. Overlay + popup share silk 280ms fade.
   */
  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        showCloseButton
        overlayClassName="pm-dialog-overlay--silk"
        className={cn(
          silkShell,
          /* overflow visible on shell? keep hidden for radius; stage pad owns shadow gutter */
          "pm-msg-dialog flex flex-col overflow-hidden gap-0 p-0",
          isNodeMsg
            ? "w-[min(1200px,92vw)] max-w-[92vw] sm:max-w-[92vw] h-[88vh]"
            : "w-[min(1200px,90vw)] max-w-[90vw] sm:max-w-[90vw] h-[85vh]"
        )}
      >
        <DialogHeader className="pm-msg-dialog-chrome pm-msg-dialog-chrome--title-only shrink-0">
          {titleRow}
        </DialogHeader>

        {!isNodeMsg ? (
          <div className="pm-msg-dialog-stage flex-1 min-h-0 flex flex-col">
            {messageCard}
          </div>
        ) : (
        <div className="pm-msg-dialog-stage flex-1 min-h-0 flex flex-col gap-3">
          {/*
            Upper row — File detail language:
            left = main white message card
            right = stacked pm-ws-side-cards (Node · Messages)
            overflow visible so soft float shadows are not clipped.
          */}
          <div className="pm-msg-dialog-upper flex-1 min-h-0 flex gap-3">
            <div className="pm-msg-dialog-main flex-[1.4] min-w-0 min-h-0 flex flex-col">
              {messageCard}
            </div>

            {/* Right rail — same modules as File detail side (soft float cards + gap) */}
            <div className="pm-ws-side pm-node-rail min-w-0 min-h-0">
              {nodeLoading ? (
                <section className="pm-ws-side-card flex-1 flex items-center justify-center gap-2 py-10">
                  <Loader2 className="h-4 w-4 animate-spin text-[var(--pm-faint)]" />
                  <span className="pm-meta">Loading…</span>
                </section>
              ) : !nodeDetail ? (
                <section className="pm-ws-side-card flex-1 flex items-center justify-center px-5 py-10">
                  <p className="pm-meta">Node not found</p>
                </section>
              ) : (
                <>
                  {/* Identity — compact side card, not a form */}
                  <section className="pm-ws-side-card shrink-0">
                    <div className="pm-ws-side-h">
                      <span
                        className="pm-label"
                        style={{
                          textTransform: "none",
                          letterSpacing: "0.02em",
                        }}
                      >
                        Node
                      </span>
                    </div>
                    <div className="pm-ws-side-pad pt-0">
                      {/* Title left · group/branch tags right (vertical stack) */}
                      <div className="pm-node-id-row">
                        <div className="pm-node-id-main min-w-0 flex-1">
                          <p className="pm-node-id-title">
                            {(nodeDetail.title || "").trim() || "Untitled"}
                          </p>
                          {(nodeDetail.event_time ||
                            (nodeDetail.attachments?.length ?? 0) > 0) && (
                            <p className="pm-meta mt-1">
                              {[
                                nodeDetail.event_time
                                  ? nodeDetail.event_time.slice(0, 10)
                                  : null,
                                (nodeDetail.attachments?.length ?? 0) > 0
                                  ? `${nodeDetail.attachments.length} file${
                                      nodeDetail.attachments.length === 1
                                        ? ""
                                        : "s"
                                    }`
                                  : null,
                              ]
                                .filter(Boolean)
                                .join(" · ")}
                            </p>
                          )}
                        </div>
                        {(groupName || branchName) && (
                          <div className="pm-node-tag-col shrink-0">
                            {groupName ? (
                              <span className="pm-node-tag" title={groupName}>
                                {groupName}
                              </span>
                            ) : null}
                            {branchName ? (
                              <span
                                className="pm-node-tag is-branch"
                                title={`Branch · ${branchName}`}
                              >
                                <GitBranch
                                  className="h-3 w-3 shrink-0"
                                  strokeWidth={1.75}
                                />
                                {branchName}
                              </span>
                            ) : null}
                          </div>
                        )}
                      </div>
                      {(nodeDetail.attachments?.length ?? 0) > 0 ? (
                        <ul className="pm-ws-list mt-2.5">
                          {nodeDetail.attachments.map((a) => (
                            <li key={a.file_id} className="pm-ws-list-item">
                              <div className="flex items-center gap-2 min-w-0">
                                <Paperclip
                                  className="h-3 w-3 shrink-0 text-[var(--pm-faint)]"
                                  strokeWidth={1.75}
                                />
                                <span className="truncate">
                                  {a.display_name || a.filename || a.file_id}
                                </span>
                              </div>
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  </section>

                  {/* Messages — shell overflow visible (shadow); list clips scroll */}
                  <section
                    className="pm-ws-side-card pm-node-msgs-card min-h-0 flex flex-col"
                    style={{ flex: "1 1 0", minHeight: 0 }}
                  >
                    <div className="pm-ws-side-h">
                      <span
                        className="pm-label"
                        style={{
                          textTransform: "none",
                          letterSpacing: "0.02em",
                        }}
                      >
                        Messages
                      </span>
                      <span className="pm-count-pill ml-auto">
                        {sortedNodeMsgs.length}
                      </span>
                    </div>
                    {sortedNodeMsgs.length === 0 ? (
                      <div className="pm-ws-side-pad pt-0">
                        <p className="pm-meta">No messages yet</p>
                      </div>
                    ) : (
                      <div
                        ref={msgListRef}
                        className="pm-node-msg-list flex-1 min-h-0 overflow-y-auto"
                      >
                        {/*
                          Sliding focus wash — animate `top`/`height` (tab pill language).
                          Tracks listFocusId immediately on click (not delayed activeMsg).
                        */}
                        <div
                          className={cn(
                            "pm-node-msg-focus",
                            focusInd.ready && "is-ready"
                          )}
                          style={{
                            top: focusInd.top,
                            height: focusInd.height,
                          }}
                          aria-hidden
                        />
                        {sortedNodeMsgs.map((m) => {
                          const focused = m.message_id === listFocusId
                          const excerpt = messagePlainExcerpt(m.body || "")
                          const time = m.created_at
                            ? new Date(m.created_at).toLocaleString(undefined, {
                                month: "short",
                                day: "numeric",
                                hour: "2-digit",
                                minute: "2-digit",
                              })
                            : ""
                          return (
                            <div
                              key={m.message_id}
                              ref={(el) => {
                                if (el) msgItemRefs.current.set(m.message_id, el)
                                else msgItemRefs.current.delete(m.message_id)
                              }}
                              role="button"
                              tabIndex={0}
                              onClick={() => handleSelectNodeMessage(m)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" || e.key === " ") {
                                  e.preventDefault()
                                  handleSelectNodeMessage(m)
                                }
                              }}
                              className={cn(
                                "pm-msg-card pm-node-msg-row",
                                focused && "is-focused"
                              )}
                            >
                              <div className="flex items-center gap-1.5 mb-0.5 min-w-0">
                                <Clock
                                  className="h-3 w-3 shrink-0 text-[var(--pm-faint)]"
                                  strokeWidth={1.75}
                                />
                                {time ? (
                                  <span className="pm-meta shrink-0 tabular-nums">
                                    {time}
                                  </span>
                                ) : null}
                                {m.edited_at ? (
                                  <span className="pm-meta italic shrink-0">
                                    edited
                                  </span>
                                ) : null}
                              </div>
                              <p className="pm-node-msg-excerpt">
                                {excerpt || "Empty message"}
                              </p>
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </section>
                </>
              )}
            </div>
          </div>

          {/* Timeline — shell keeps shadow; graph clips inside body */}
          <div className="pm-msg-dialog-card pm-msg-dialog-card--timeline h-[32%] min-h-[140px] shrink-0 flex flex-col">
            <div className="pm-ws-side-h shrink-0">
              <span
                className="pm-label"
                style={{ textTransform: "none", letterSpacing: "0.02em" }}
              >
                Timeline
              </span>
              <span className="pm-meta ml-auto">
                Click a node to open Timeline view
              </span>
            </div>
            <div className="pm-msg-dialog-card-body flex-1 min-h-0">
              {collectionId && timelineNodeId ? (
                <MiniChainGraph
                  key={timelineNodeId}
                  collectionId={collectionId}
                  nodeId={timelineNodeId}
                  className="h-full min-h-0"
                  onNodeClick={(nid, cid) => {
                    onNavigateToNode?.(nid, cid)
                  }}
                />
              ) : null}
            </div>
          </div>
        </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
