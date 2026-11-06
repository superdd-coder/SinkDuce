import { useCallback, useEffect, useRef, useState } from "react"

// Conditional Q&A dialogs stay mounted until the shared dialog's exit completes.
export function useQaModal(onClosed: () => void) {
  const [open, setOpen] = useState(false)
  const pending = useRef<(() => void) | null>(null)
  useEffect(() => {
    const frame = requestAnimationFrame(() => setOpen(true))
    return () => cancelAnimationFrame(frame)
  }, [])
  const exit = useCallback((after: () => void) => {
    if (pending.current) return
    pending.current = after
    setOpen(false)
  }, [])
  const close = useCallback(() => exit(onClosed), [exit, onClosed])
  const onOpenChangeComplete = useCallback((visible: boolean) => {
    if (visible || !pending.current) return
    const after = pending.current
    pending.current = null
    after()
  }, [])
  return { open, close, exit, onOpenChangeComplete }
}
