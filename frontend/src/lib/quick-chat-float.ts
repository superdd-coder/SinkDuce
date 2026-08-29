/**
 * Quick Chat inner panel motion.
 * Stay fully opaque and only translate — fading a transparent-host card
 * overlays Todo / Messages as a ghost. Card shadow is a CSS drop-shadow
 * on the outer host so it follows the clipped sliding pixels (not delayed).
 */
export function qcFloatSlideMotion(showFloat: boolean): {
  transform: string
  opacity: number
} {
  return {
    transform: showFloat ? "translateX(0)" : "translateX(calc(100% + 20px))",
    opacity: 1,
  }
}
