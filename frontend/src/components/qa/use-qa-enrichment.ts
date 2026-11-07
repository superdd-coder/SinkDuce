import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { annotateQaList, getQaList, type QaListDetail } from "@/api/client"
import { reconcileQaSnapshot } from "@/lib/qa-snapshot"

export function useQaEnrichment(collectionId: string, data: QaListDetail | null, onResult: (next: QaListDetail) => void) {
  const current = useRef(data)
  const accept = useRef(onResult)
  useLayoutEffect(() => { current.current = data; accept.current = onResult }, [data, onResult])
  const controller = useRef<AbortController | null>(null)
  const [running, setRunning] = useState(false)
  const [failed, setFailed] = useState(false)
  const run = useCallback(async () => {
    const list = current.current
    if (!list?.can_edit || controller.current) return
    const request = new AbortController()
    controller.current = request
    setRunning(true); setFailed(false)
    try {
      let next = await annotateQaList(collectionId, list, request.signal)
      for (let attempt = 0; !request.signal.aborted; attempt++) {
        const previous = current.current
        if (previous?.id === next.id && previous.version.id === next.version.id) {
          const merged = reconcileQaSnapshot(previous, next)
          if (merged !== previous) { current.current = merged; accept.current(merged) }
        }
        if (next.annotation_state !== "running" || attempt >= 100) {
          setFailed(!!next.annotation_pending)
          break
        }
        await new Promise(resolve => setTimeout(resolve, 1500))
        if (request.signal.aborted) break
        next = await getQaList(collectionId, list.id, list.version.id, request.signal)
      }
    } catch {
      if (!request.signal.aborted) setFailed(true)
    } finally {
      if (controller.current === request) { controller.current = null; setRunning(false) }
    }
  }, [collectionId])
  useEffect(() => {
    const start = setTimeout(() => {
      setRunning(false); setFailed(false)
      if (current.current?.annotation_pending && current.current.can_edit) void run()
    }, 0)
    return () => { clearTimeout(start); controller.current?.abort(); controller.current = null }
  }, [collectionId, data?.id, data?.version.id, data?.can_edit, run])
  return { running, failed, retry: run }
}
