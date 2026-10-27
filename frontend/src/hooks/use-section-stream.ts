import { useEffect, useCallback, useRef, useSyncExternalStore } from "react"
import { streamSectionGenerate, type SectionStreamCallbacks } from "@/api/client"

export type SectionGenState = "idle" | "prefilling" | "streaming"

export interface SectionStreamState {
  genState: SectionGenState
  /** Accumulated markdown during streaming. */
  streamingMd: string
  /** Accumulated thinking text during prefilling (auto-cleared when real output begins). */
  thinkingText: string
  /** Whether streaming is currently active. */
  isStreaming: boolean
}

export interface SectionStreamControls {
  start: () => void
  abort: () => void
  /**
   * Clear settled streaming buffer for the currently bound tab.
   * Prefer {@link dismissSectionStream} with explicit ids from async handlers.
   */
  dismiss: () => void
}

const IDLE: SectionStreamState = {
  genState: "idle",
  streamingMd: "",
  thinkingText: "",
  isStreaming: false,
}

// ═══════════════════════════════════════════════════════════════════
// Global stream manager — survives component mount/unmount lifecycle
// ═══════════════════════════════════════════════════════════════════

interface StreamEntry {
  state: SectionStreamState
  controller: AbortController | null
}

/** Key: `${meetingId}::${tabId}` */
const streams = new Map<string, StreamEntry>()
const listeners = new Set<() => void>()
/** Sections whose streaming completed while no component was subscribed. */
const completedWhileAway = new Set<string>()

function streamKey(meetingId: string, tabId: string): string {
  return `${meetingId}::${tabId}`
}

function getEntry(meetingId: string, tabId: string): StreamEntry {
  const key = streamKey(meetingId, tabId)
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

/** Snapshot stream state for any section (used by Section rail ready/streaming flags). */
export function getSectionStreamState(
  meetingId: string,
  tabId: string,
): SectionStreamState {
  return getEntry(meetingId, tabId).state
}

/** Subscribe to all section stream updates (rail model re-render). */
export function subscribeSectionStreams(onChange: () => void): () => void {
  listeners.add(onChange)
  return () => {
    listeners.delete(onChange)
  }
}

/** True once tokens are flowing (Streaming badge). */
export function sectionStreamHasOutput(state: SectionStreamState): boolean {
  return (
    state.genState === "streaming" ||
    (state.isStreaming && state.streamingMd.length > 0)
  )
}

/**
 * True while generation is in flight or partial stream buffer remains —
 * section list item is openable (watch live or review partial).
 */
export function sectionStreamIsOpenable(state: SectionStreamState): boolean {
  return (
    state.isStreaming ||
    state.genState !== "idle" ||
    state.streamingMd.length > 0
  )
}

/**
 * Clear settled streaming UI buffer for a specific section.
 * Never wipes an in-flight stream (keeps Streaming badge + tokens intact).
 */
export function dismissSectionStream(meetingId: string, tabId: string): void {
  const key = streamKey(meetingId, tabId)
  const entry = streams.get(key)
  if (!entry) return
  // Active SSE must keep genState / streamingMd — clearing them makes the rail
  // drop Streaming + ready and blocks re-entry into the live section.
  if (entry.state.isStreaming) return
  if (entry.state.genState === "idle" && entry.state.streamingMd === "") return
  entry.state = {
    ...entry.state,
    genState: "idle",
    streamingMd: "",
    thinkingText: "",
  }
  notify()
}

export function startSectionStream(meetingId: string, tabId: string) {
  const key = streamKey(meetingId, tabId)
  const entry = getEntry(meetingId, tabId)

  // Already streaming — don't interrupt an active generation
  if (entry.state.isStreaming) return

  // Abort existing stale stream for this section (if any)
  entry.controller?.abort()

  // Reset state
  entry.state = {
    ...IDLE,
    genState: "prefilling",
    isStreaming: true,
  }
  completedWhileAway.delete(key)
  notify()

  const callbacks: SectionStreamCallbacks = {
    onState: (data) => {
      const e = streams.get(key)
      if (!e) return
      const prev = e.state
      const gen = (data.section_gen ?? prev.genState) as SectionGenState
      const done = gen === "idle"
      if (gen === prev.genState && done === !prev.isStreaming) return
      e.state = { ...prev, genState: gen, isStreaming: !done }
      notify()
    },
    onThinking: (text) => {
      const e = streams.get(key)
      if (!e) return
      e.state = { ...e.state, thinkingText: e.state.thinkingText + text }
      notify()
    },
    onToken: (text) => {
      const e = streams.get(key)
      if (!e) return
      e.state = {
        ...e.state,
        streamingMd: e.state.streamingMd + text,
        thinkingText: "",
        genState: "streaming",
        isStreaming: true,
      }
      notify()
    },
    onSectionDone: () => {
      const e = streams.get(key)
      if (!e) return
      e.state = { ...e.state, genState: "idle", isStreaming: false }
      completedWhileAway.add(key)
      notify()
    },
    onError: (_msg) => {
      const e = streams.get(key)
      if (!e) return
      if (e.state === IDLE) return
      e.state = { ...IDLE }
      notify()
    },
  }

  entry.controller = streamSectionGenerate(meetingId, tabId, callbacks)
}

// ═══════════════════════════════════════════════════════════════════
// Hook — subscribes to stream state for a specific meeting+tab
// ═══════════════════════════════════════════════════════════════════

/**
 * Connect to the section generation SSE stream.
 *
 * The underlying SSE connection is managed globally — it survives component
 * unmount so generation continues even when the user switches tabs.
 *
 * @param meetingId       The meeting to subscribe to (null = no meeting).
 * @param tabId           The section tab to subscribe to (null = no section).
 * @param onCompletedAway Called when the hook mounts for a section whose
 *                        streaming finished while the user was viewing a
 *                        different tab.  Use this to auto-fetch the updated
 *                        meeting data.
 */
export function useSectionStream(
  meetingId: string | null,
  tabId: string | null,
  onCompletedAway?: (meetingId: string, tabId: string) => void,
): [SectionStreamState, SectionStreamControls] {
  const meetingIdRef = useRef(meetingId)
  meetingIdRef.current = meetingId
  const tabIdRef = useRef(tabId)
  tabIdRef.current = tabId

  // ── subscribe to the global store ────────────────────────────
  const subscribe = useCallback((onStoreChange: () => void) => {
    listeners.add(onStoreChange)
    return () => { listeners.delete(onStoreChange) }
  }, [])

  // Key getSnapshot on meetingId/tabId so selection changes re-read the store
  // without waiting for a notify() (refs alone can leave a stale IDLE snapshot).
  const getSnapshot = useCallback((): SectionStreamState => {
    if (!meetingId || !tabId) return IDLE
    return getEntry(meetingId, tabId).state
  }, [meetingId, tabId])

  const state = useSyncExternalStore(subscribe, getSnapshot)

  // ── Detect missed completion ──────────────────────────────────
  useEffect(() => {
    const mid = meetingId
    const tid = tabId
    if (mid && tid) {
      const key = streamKey(mid, tid)
      if (completedWhileAway.has(key)) {
        completedWhileAway.delete(key)
        const tidTimeout = setTimeout(() => onCompletedAway?.(mid, tid), 0)
        return () => clearTimeout(tidTimeout)
      }
    }
  }, [meetingId, tabId, onCompletedAway])

  // ── Controls ──────────────────────────────────────────────────
  const start = useCallback(() => {
    const mid = meetingIdRef.current
    const tid = tabIdRef.current
    if (mid && tid) startSectionStream(mid, tid)
  }, [])

  const abort = useCallback(() => {
    const mid = meetingIdRef.current
    const tid = tabIdRef.current
    if (!mid || !tid) return
    const key = streamKey(mid, tid)
    const entry = streams.get(key)
    if (entry) {
      entry.controller?.abort()
      entry.state = { ...IDLE }
      completedWhileAway.delete(key)
      notify()
    }
  }, [])

  const dismiss = useCallback(() => {
    const mid = meetingIdRef.current
    const tid = tabIdRef.current
    if (mid && tid) dismissSectionStream(mid, tid)
  }, [])

  return [state, { start, abort, dismiss }]
}
