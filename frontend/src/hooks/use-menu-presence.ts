import { useEffect, useState } from "react"

/** Keep the surface mounted until its CSS exit transition has finished. */
export const MENU_DURATION = 220

export function useMenuPresence(open: boolean, duration = MENU_DURATION) {
  const [mounted, setMounted] = useState(open)
  const [shown, setShown] = useState(false)
  // Mount in the opening render so autofocus can see the surface on the next frame.
  if (open && !mounted) setMounted(true)

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    let firstFrame = 0
    let secondFrame = 0
    let timer: ReturnType<typeof setTimeout> | undefined

    if (open) {
      firstFrame = requestAnimationFrame(() => {
        if (reduced) setShown(true)
        else secondFrame = requestAnimationFrame(() => setShown(true))
      })
    } else {
      firstFrame = requestAnimationFrame(() => {
        setShown(false)
        timer = setTimeout(() => setMounted(false), reduced ? 0 : duration)
      })
    }
    return () => {
      cancelAnimationFrame(firstFrame)
      cancelAnimationFrame(secondFrame)
      clearTimeout(timer)
    }
  }, [open, duration])

  return { mounted, shown }
}
