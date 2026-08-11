import { useCallback, useEffect, useState, type RefObject } from "react"

export type ScrollEdgeFade = { top: boolean; bottom: boolean }

/**
 * Toggle top/bottom edge fades for a scroll container.
 * top  → content has scrolled past the top edge
 * bottom → more content remains below the viewport
 */
export function useScrollEdgeFade(
  scrollRef: RefObject<HTMLElement | null>,
  /** Re-measure when list length / identity changes */
  contentKey?: string | number,
  threshold = 4,
): ScrollEdgeFade {
  const [edgeFade, setEdgeFade] = useState<ScrollEdgeFade>({
    top: false,
    bottom: false,
  })

  const update = useCallback(() => {
    const el = scrollRef.current
    if (!el) {
      setEdgeFade({ top: false, bottom: false })
      return
    }
    const top = el.scrollTop > threshold
    const bottom =
      el.scrollHeight - el.scrollTop - el.clientHeight > threshold
    setEdgeFade((prev) =>
      prev.top === top && prev.bottom === bottom ? prev : { top, bottom },
    )
  }, [scrollRef, threshold])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return

    update()
    el.addEventListener("scroll", update, { passive: true })

    let ro: ResizeObserver | null = null
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(() => update())
      ro.observe(el)
      if (el.firstElementChild) ro.observe(el.firstElementChild)
    }

    return () => {
      el.removeEventListener("scroll", update)
      ro?.disconnect()
    }
  }, [update, contentKey, scrollRef])

  useEffect(() => {
    update()
  }, [update, contentKey])

  return edgeFade
}
