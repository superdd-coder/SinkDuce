import { useEffect, useCallback, useRef, useSyncExternalStore } from "react"
import { streamBlueprint, type BlueprintStreamCallbacks, type BlueprintItem } from "@/api/client"

export type GenState = "idle" | "prefilling" | "streaming"

export interface BlueprintStreamState {
  summaryGenState: GenState
  blueprintGenState: GenState
  /** Accumulated markdown during streaming. */
  streamingMd: string
  /** Accumulated thinking text during prefilling (auto-cleared when real output begins). */
  thinkingText: string
  /** Whether streaming is currently active. */
  isStreaming: boolean
  /** Blueprint data from early-completion emission (available before full meeting refresh). */
  earlyBlueprint: BlueprintItem[] | null
}

export interface BlueprintStreamControls {
  start: () => void
  abort: () => void
  /** Call after meeting is refreshed post-streaming to switch summary to idle and clear streamingMd. */
  dismissStreaming: () => void
}

const IDLE: BlueprintStreamState = {
  summaryGenState: "idle",
  blueprintGenState: "idle",
  streamingMd: "",
  thinkingText: "",
  isStreaming: false,
  earlyBlueprint: null,
}

// ═══════════════════════════════════════════════════════════════════
// Global stream manager — survives component mount/unmount lifecycle
// ═══════════════════════════════════════════════════════════════════

interface StreamEntry {
  state: BlueprintStreamState
  controller: AbortController | null
}

const streams = new Map<string, StreamEntry>()
const listeners = new Set<() => void>()
/** Meetings whose streaming completed while no component was subscribed. */
const completedWhileAway = new Set<string>()

function getEntry(id: string): StreamEntry {
  let entry = streams.get(id)
  if (!entry) {
    entry = { state: { ...IDLE }, controller: null }
    streams.set(id, entry)
  }
  return entry
}

function notify() {
  listeners.forEach((fn) => fn())
}

export function startStream(meetingId: string) {
  const entry = getEntry(meetingId)

  // Abort existing stream for this meeting
  entry.controller?.abort()

  // Reset state
  entry.state = {
    ...IDLE,
    summaryGenState: "prefilling",
    isStreaming: true,
  }
  completedWhileAway.delete(meetingId)
  notify()

  const callbacks: BlueprintStreamCallbacks = {
    onState: (data) => {
      const e = streams.get(meetingId)
      if (!e) return
      const prev = e.state
      const summary = (data.summary ?? prev.summaryGenState) as GenState
      const blueprint = (data.blueprint ?? prev.blueprintGenState) as GenState
      const allDone = summary === "idle" && blueprint === "idle"

      // Track completion so components that mount later can auto-fetch
      if (allDone && prev.isStreaming) {
        completedWhileAway.add(meetingId)
      }

      if (summary === prev.summaryGenState && blueprint === prev.blueprintGenState && !allDone === prev.isStreaming) return
      e.state = { ...prev, summaryGenState: summary, blueprintGenState: blueprint, isStreaming: !allDone }
      notify()
    },
    onThinking: (text) => {
      const e = streams.get(meetingId)
      if (!e) return
      e.state = { ...e.state, thinkingText: e.state.thinkingText + text }
      notify()
    },
    onToken: (text) => {
      const e = streams.get(meetingId)
      if (!e) return
      e.state = { ...e.state, streamingMd: e.state.streamingMd + text, thinkingText: "" }
      notify()
    },
    onSummaryDone: () => {
      // State transitions via onState; no-op here.
    },
    onBlueprintDone: (data) => {
      const e = streams.get(meetingId)
      if (!e) return
      const prev = e.state
      const allDone = prev.summaryGenState === "idle"
      const newBp = (data.blueprint ?? []) as unknown as BlueprintItem[]
      if (prev.blueprintGenState === "idle" && !allDone === prev.isStreaming && prev.earlyBlueprint === newBp) return
      e.state = {
        ...prev,
        blueprintGenState: "idle",
        isStreaming: !allDone,
        earlyBlueprint: newBp,
      }
      notify()
    },
    onError: (_msg) => {
      const e = streams.get(meetingId)
      if (!e) return
      if (e.state === IDLE) return
      e.state = { ...IDLE }
      notify()
    },
  }

  entry.controller = streamBlueprint(meetingId, callbacks)
}

// ═══════════════════════════════════════════════════════════════════
// Hook — subscribes to stream state for a specific meetingId
// ═══════════════════════════════════════════════════════════════════

/**
 * Connect to the blueprint SSE stream for a meeting.
 *
 * The underlying SSE connection is managed globally — it survives component
 * unmount so that concurrent streams across multiple meetings keep running
 * even when the user switches away.  This hook merely *subscribes* to the
 * state for ``meetingId``.
 *
 * @param meetingId       The meeting to subscribe to (null = no meeting).
 * @param onCompletedAway Called when the hook mounts for a meeting whose
 *                        streaming finished while the user was viewing a
 *                        different meeting.  Use this to auto-fetch the
 *                        updated meeting data.
 */
export function useBlueprintStream(
  meetingId: string | null,
  onCompletedAway?: (meetingId: string) => void,
): [BlueprintStreamState, BlueprintStreamControls] {
  const meetingIdRef = useRef(meetingId)
  meetingIdRef.current = meetingId

  // ── subscribe to the global store ────────────────────────────
  const subscribe = useCallback((onStoreChange: () => void) => {
    listeners.add(onStoreChange)
    return () => { listeners.delete(onStoreChange) }
  }, [])

  const getSnapshot = useCallback((): BlueprintStreamState => {
    const id = meetingIdRef.current
    if (!id) return IDLE
    return getEntry(id).state
  }, [])

  const state = useSyncExternalStore(subscribe, getSnapshot)

  // ── Detect missed completion ──────────────────────────────────
  const prevMeetingIdRef = useRef(meetingId)
  useEffect(() => {
    const id = meetingId
    // When meetingId changes (or first mount), check if this meeting's
    // streaming completed while we were away.
    if (id && completedWhileAway.has(id)) {
      completedWhileAway.delete(id)
      // Defer so the parent can consume the state before we fire
      const tid = setTimeout(() => onCompletedAway?.(id), 0)
      return () => clearTimeout(tid)
    }
    prevMeetingIdRef.current = id
  }, [meetingId, onCompletedAway])

  // ── Detect completion while viewing ────────────────────────────
  // When isStreaming transitions from true → false for the current
  // meeting, trigger the completion callback immediately so the
  // breakdown area refreshes without waiting for a tab switch.
  const prevIsStreamingRef = useRef(false)
  useEffect(() => {
    const id = meetingIdRef.current
    const justCompleted = !state.isStreaming && prevIsStreamingRef.current
    prevIsStreamingRef.current = state.isStreaming
    if (id && justCompleted) {
      // Clean up the completedWhileAway marker (set by onState)
      completedWhileAway.delete(id)
      const tid = setTimeout(() => onCompletedAway?.(id), 0)
      return () => clearTimeout(tid)
    }
  }, [state.isStreaming, onCompletedAway])

  // ── Controls ──────────────────────────────────────────────────
  const start = useCallback(() => {
    const id = meetingIdRef.current
    if (id) startStream(id)
  }, [])

  const abort = useCallback(() => {
    const id = meetingIdRef.current
    if (!id) return
    const entry = streams.get(id)
    if (entry) {
      entry.controller?.abort()
      entry.state = { ...IDLE }
      completedWhileAway.delete(id)
      notify()
    }
  }, [])

  const dismissStreaming = useCallback(() => {
    const id = meetingIdRef.current
    if (!id) return
    const entry = streams.get(id)
    if (entry && (entry.state.summaryGenState !== "idle" || entry.state.streamingMd !== "")) {
      entry.state = {
        ...entry.state,
        summaryGenState: "idle",
        streamingMd: "",
      }
      notify()
    }
  }, [])

  return [state, { start, abort, dismissStreaming }]
}
