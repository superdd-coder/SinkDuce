import { useEffect, useCallback, useRef, useSyncExternalStore } from "react"
import { streamTranslation, type TranslationStreamCallbacks } from "@/api/client"

export type TranslationGenState = "idle" | "prefilling" | "streaming"

export interface TranslationStreamState {
  genState: TranslationGenState
  /** Accumulated markdown during streaming. */
  streamingMd: string
  /** Whether streaming is currently active. */
  isStreaming: boolean
  /** Final translated markdown once the stream completes ("" until done). */
  finalMd: string
  /** True when the result came from the on-disk cache rather than a fresh LLM call. */
  cached: boolean
  /** Last error message ("" when none). */
  error: string
  /** "remote" = backend-reported failure (revert+clear); "network" = transient drop (resumable). */
  errorKind: "remote" | "network" | ""
}

const IDLE: TranslationStreamState = {
  genState: "idle",
  streamingMd: "",
  isStreaming: false,
  finalMd: "",
  cached: false,
  error: "",
  errorKind: "",
}

// ═══════════════════════════════════════════════════════════════════
// Global stream manager — survives component mount/unmount lifecycle
// ═══════════════════════════════════════════════════════════════════

interface StreamEntry {
  state: TranslationStreamState
  controller: AbortController | null
}

/** Key: `${meetingId}::${tabId}::${lang}` */
const streams = new Map<string, StreamEntry>()
const listeners = new Set<() => void>()

function streamKey(meetingId: string, tabId: string, lang: string): string {
  return `${meetingId}::${tabId}::${lang}`
}

function getEntry(meetingId: string, tabId: string, lang: string): StreamEntry {
  const key = streamKey(meetingId, tabId, lang)
  let entry = streams.get(key)
  if (!entry) {
    entry = { state: { ...IDLE }, controller: null }
    streams.set(key, entry)
  }
  return entry
}

function notify() {
  listeners.forEach((fn) => fn())
}

// Abort every in-flight stream when the page is unloaded (refresh / close /
// navigate away).  This turns teardown rejections into AbortError — which the
// client swallows — instead of spurious network errors that would otherwise
// clear the user's persisted language view.  Registered once at module load.
if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => {
    streams.forEach((entry) => entry.controller?.abort())
  })
}

export function startTranslationStream(meetingId: string, tabId: string, lang: string) {
  const key = streamKey(meetingId, tabId, lang)
  const entry = getEntry(meetingId, tabId, lang)

  // Already streaming — don't interrupt an active generation
  if (entry.state.isStreaming) return

  // Abort existing stale stream for this key (if any)
  entry.controller?.abort()

  entry.state = { ...IDLE, genState: "prefilling", isStreaming: true }
  notify()

  const callbacks: TranslationStreamCallbacks = {
    onState: (data) => {
      const e = streams.get(key)
      if (!e) return
      const gen = (data.translation_gen ?? e.state.genState) as TranslationGenState
      if (gen === e.state.genState) return
      e.state = { ...e.state, genState: gen, isStreaming: gen !== "idle" }
      notify()
    },
    onToken: (text) => {
      const e = streams.get(key)
      if (!e) return
      e.state = { ...e.state, streamingMd: e.state.streamingMd + text }
      notify()
    },
    onDone: (data) => {
      const e = streams.get(key)
      if (!e) return
      e.state = {
        ...e.state,
        genState: "idle",
        isStreaming: false,
        finalMd: data.md,
        cached: data.cached,
      }
      notify()
    },
    onError: (msg, kind) => {
      const e = streams.get(key)
      if (!e) return
      e.state = { ...e.state, genState: "idle", isStreaming: false, error: msg, errorKind: kind }
      notify()
    },
  }

  entry.controller = streamTranslation(meetingId, tabId, lang, callbacks)
}

// ═══════════════════════════════════════════════════════════════════
// Hook — subscribes to stream state for a specific meeting+tab+lang
// ═══════════════════════════════════════════════════════════════════

export interface TranslationStreamHandlers {
  /** Fired once when a final translation is available (fresh or cached). */
  onDone?: (tabId: string, lang: string, md: string, cached: boolean) => void
  /** Fired once when the stream errors. kind "remote" = real backend failure;
   *  kind "network" = transient connection drop (resumable, don't clear state). */
  onError?: (tabId: string, lang: string, message: string, kind: "remote" | "network") => void
}

/**
 * Subscribe to a summary-translation SSE stream for one tab+language.
 *
 * The connection is managed globally — it survives component unmount so
 * generation continues across tab/view switches, and the backend replays
 * missed tokens on reconnect (page refresh).  `onDone`/`onError` fire with
 * fire-once semantics whether the completion happens while subscribed or
 * while the user was viewing a different tab/language.
 */
export function useTranslationStream(
  meetingId: string | null,
  tabId: string | null,
  lang: string | null,
  handlers?: TranslationStreamHandlers,
): TranslationStreamState {
  const meetingIdRef = useRef(meetingId)
  meetingIdRef.current = meetingId
  const tabIdRef = useRef(tabId)
  tabIdRef.current = tabId
  const langRef = useRef(lang)
  langRef.current = lang
  const handlersRef = useRef(handlers)
  handlersRef.current = handlers

  const subscribe = useCallback((onStoreChange: () => void) => {
    listeners.add(onStoreChange)
    return () => { listeners.delete(onStoreChange) }
  }, [])

  const getSnapshot = useCallback((): TranslationStreamState => {
    const mid = meetingIdRef.current
    const tid = tabIdRef.current
    const lg = langRef.current
    if (!mid || !tid || !lg) return IDLE
    return getEntry(mid, tid, lg).state
  }, [])

  const state = useSyncExternalStore(subscribe, getSnapshot)

  // Fire onDone once when a final result becomes available for this key.
  useEffect(() => {
    const mid = meetingIdRef.current
    const tid = tabIdRef.current
    const lg = langRef.current
    if (!mid || !tid || !lg || !state.finalMd) return
    handlersRef.current?.onDone?.(tid, lg, state.finalMd, state.cached)
    // Consume so it doesn't refire on re-mount.
    const entry = streams.get(streamKey(mid, tid, lg))
    if (entry && entry.state.finalMd === state.finalMd) {
      entry.state = { ...entry.state, finalMd: "" }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.finalMd])

  // Fire onError once when an error becomes available for this key.
  useEffect(() => {
    const mid = meetingIdRef.current
    const tid = tabIdRef.current
    const lg = langRef.current
    if (!mid || !tid || !lg || !state.error) return
    handlersRef.current?.onError?.(tid, lg, state.error, state.errorKind || "remote")
    const entry = streams.get(streamKey(mid, tid, lg))
    if (entry && entry.state.error === state.error) {
      entry.state = { ...entry.state, error: "", errorKind: "" }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.error])

  return state
}
