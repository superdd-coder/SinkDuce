import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent } from "react"
import {
  getChatAttachmentTypes, getChatAttachments, uploadChatAttachment, retryChatAttachment, removeChatAttachment,
  type ChatAttachmentTypes,
} from "@/api/client"
import { useAppStore } from "@/stores/app-store"
import { mergeAttachmentDrafts, settleAttachmentUpload, type ChatAttachmentDraft } from "@/lib/chat-attachments"
import { newId } from "@/lib/uuid"
import { useT } from "@/i18n/use-t"
import { toast } from "sonner"
import { ApiError } from "@/api/http"

const EMPTY: ChatAttachmentDraft[] = []

export function useChatAttachments(sessionId: string | null, ensureSession?: () => Promise<string | null>, disabled = false) {
  const t = useT()
  const drafts = useAppStore((s) => sessionId ? s.chatAttachmentDrafts?.[sessionId] ?? EMPTY : EMPTY)
  const [types, setTypes] = useState<ChatAttachmentTypes | null>(null)
  const generation = useRef(0)
  const currentSid = useRef(sessionId)
  currentSid.current = sessionId
  const hasPending = drafts.some((d) => d.status === "processing" || d.status === "uploading")
  useEffect(() => {
    const refresh = () => { void getChatAttachmentTypes().then(setTypes).catch(() => {}) }
    refresh()
    window.addEventListener("focus", refresh)
    return () => window.removeEventListener("focus", refresh)
  }, [])

  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      const epoch = generation.current
      let pending = hasPending
      try {
        const rows = await getChatAttachments(sessionId)
        if (cancelled || epoch !== generation.current) return
        pending ||= rows.some((a) => a.status === "processing")
        const st = useAppStore.getState()
        st.setChatAttachmentDrafts(sessionId, (local) => mergeAttachmentDrafts(local, rows, st.consumedChatAttachments[sessionId] ?? []))
      } catch { /* Keep drafts through temporary connectivity errors. */ }
      if (!cancelled && pending) timer = setTimeout(refresh, 1500)
    }
    void refresh()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [sessionId, hasPending, disabled])

  const update = (sid: string, key: string, change: Partial<ChatAttachmentDraft>) => {
    useAppStore.getState().setChatAttachmentDrafts(sid, (rows) => rows.map((d) => d.key === key ? { ...d, ...change } : d))
  }
  const uploadOne = async (sid: string, key: string, file: File) => {
    if (!useAppStore.getState().chatAttachmentDrafts[sid]?.some((d) => d.key === key)) return
    update(sid, key, { status: "uploading", error: undefined })
    try {
      const a = await uploadChatAttachment(sid, file)
      // If the user removed this card while its transfer was in flight, remove the orphan as well.
      if (!useAppStore.getState().chatAttachmentDrafts[sid]?.some((d) => d.key === key)) {
        useAppStore.getState().setChatAttachmentDrafts(sid, (rows) => settleAttachmentUpload(rows, key, a))
        await removeChatAttachment(sid, a.id).catch(() => {})
        return
      }
      useAppStore.getState().setChatAttachmentDrafts(sid, (rows) => settleAttachmentUpload(rows, key, a))
    } catch (err) {
      update(sid, key, { status: "failed", error: err instanceof Error ? err.message : String(err) })
    }
  }
  const upload = async (files: File[]) => {
    if (disabled || !files.length) return
    const epoch = generation.current
    try {
      const limits = await getChatAttachmentTypes()
      setTypes(limits)
      const sid = ensureSession ? await ensureSession() : sessionId
      if (!sid || epoch !== generation.current) return
      if (sessionId && currentSid.current !== sessionId) return
      const current = useAppStore.getState().chatAttachmentDrafts[sid] ?? []
      if (current.length + files.length > limits.max_attachments) throw new Error(t("chat.attachments.tooMany"))
      if (files.some((f) => f.size > limits.max_file_bytes || !f.size)) throw new Error(t("chat.attachments.sizeLimit"))
      if (files.some((f) => !limits.extensions.includes("." + f.name.split(".").pop()?.toLowerCase()))) throw new Error(t("chat.attachments.unsupported"))
      const pending = files.map((file) => ({ key: newId(), filename: file.name, file, status: "uploading" as const }))
      useAppStore.getState().setChatAttachmentDrafts(sid, (rows) => [...rows, ...pending])
      // Sequential transfer avoids unbounded multipart memory; server parsing has a separate worker pool.
      for (const d of pending) {
        if (epoch !== generation.current) break
        await uploadOne(sid, d.key, d.file)
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }
  const remove = async (draft: ChatAttachmentDraft) => {
    const sid = sessionId
    if (!sid) return
    try {
      if (draft.attachment) await removeChatAttachment(sid, draft.attachment.id)
      useAppStore.getState().setChatAttachmentDrafts(sid, (rows) => rows.filter((d) => d.key !== draft.key))
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        useAppStore.getState().setChatAttachmentDrafts(sid, (rows) => rows.filter((d) => d.key !== draft.key))
      } else toast.error(String(err))
    }
  }
  const retry = async (draft: ChatAttachmentDraft) => {
    if (!sessionId || disabled) return
    if (!draft.attachment && draft.file) return uploadOne(sessionId, draft.key, draft.file)
    if (!draft.attachment) return
    update(sessionId, draft.key, { status: "processing", error: undefined })
    try {
      const a = await retryChatAttachment(sessionId, draft.attachment.id)
      update(sessionId, draft.key, { attachment: a, status: a.status, error: a.error })
    } catch (err) { update(sessionId, draft.key, { status: "failed", error: String(err) }) }
  }
  const reset = () => {
    generation.current += 1
    if (sessionId) useAppStore.getState().clearChatAttachmentDrafts(sessionId)
  }
  const dropProps = {
    onDragOver: (e: DragEvent) => { if (!disabled && e.dataTransfer.types.includes("Files")) e.preventDefault() },
    onDrop: (e: DragEvent) => { if (e.dataTransfer.files.length) { e.preventDefault(); void upload(Array.from(e.dataTransfer.files)) } },
    onPaste: (e: ClipboardEvent) => {
      const images = Array.from(e.clipboardData.items).filter((i) => i.type.startsWith("image/")).map((i) => i.getAsFile()).filter((f): f is File => !!f)
      if (images.length) { e.preventDefault(); void upload(images) }
    },
  }
  return { drafts, accept: types?.extensions.join(",") ?? "", typesReady: !!types, disabled,
    upload, remove, retry, reset, dropProps,
    ready: drafts.flatMap((d) => d.status === "ready" && d.attachment ? [d.attachment] : []),
    blocked: drafts.some((d) => d.status !== "ready") }
}

export type ChatAttachmentsManager = ReturnType<typeof useChatAttachments>
