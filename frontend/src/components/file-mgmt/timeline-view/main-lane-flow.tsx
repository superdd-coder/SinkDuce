import { useEffect, useMemo, useRef } from 'react'

/**
 * Main-line lane highlight (波光车道).
 *
 * Green multi-layer water band behind the main chain lane: a static capsule
 * base + three sine "ribbon" layers + two faster glint waves, all drifting
 * rightward. Motion is one rAF loop writing SVG transform attributes —
 * CSS keyframes / WAAPI on SVG transforms proved unreliable in WebKit, and
 * calc()/var() division in animation-duration silently voids the animation.
 * Params baked from development_doc/main-line-flow-demo (R7:
 * amplitude soft, speed 1.5x, tint std); tuning knobs live in LAYERS below.
 */

/** px beyond head/tail node centers before the fade starts. */
export const MAIN_FLOW_EXTEND = 140
/** SVG viewport height; lane center sits at CY. Paths must stay inside.
 *  (Mask fade width 100px and capsule height 64px live in index.css.) */
const VIEW_H = 200
const CY = VIEW_H / 2
/** Top-edge amplitude; peak-to-trough = 2×AMP. */
const AMP = 3
/** Trough widening: bottom edge bulge, peaks at wave valleys. */
const BULGE = AMP * 1.2
/** Extra draw margin (≥2 wavelengths) beyond both ends so the loop never shows an edge. */
const X_MARGIN = 700

interface WaveLayer {
  /** Rest half-thickness (px). */
  hh: number
  /** Scale of AMP/BULGE for this layer. */
  af: number
  /** Wavelength (px). */
  lam: number
  /** Phase offset (rad). */
  ph: number
  /** Fill alpha (tint std baked in). */
  a: number
  /** Seconds per wavelength at 1.5× speed. */
  dur: number
}

const LAYERS: WaveLayer[] = [
  { hh: 32, af: 1.0, lam: 300, ph: 0, a: 0.128, dur: 12 },
  { hh: 23, af: 0.8, lam: 240, ph: 2.1, a: 0.088, dur: 8.67 },
  { hh: 15, af: 0.6, lam: 190, ph: 4.2, a: 0.06, dur: 6.67 },
  { hh: 4.5, af: 0.5, lam: 260, ph: 1.0, a: 0.144, dur: 5.33 },
  { hh: 3.5, af: 0.5, lam: 210, ph: 3.6, a: 0.112, dur: 4 },
]

/** Ribbon path: top edge is a sine; bottom edge stays in phase (constant
 *  thickness) and bulges only near wave troughs, like water pooling. */
function ribbonD(width: number, w: WaveLayer): string {
  const step = w.lam / 40
  const top: string[] = []
  const bot: string[] = []
  for (let x = -X_MARGIN; x <= width + X_MARGIN + step; x += step) {
    const s = Math.sin((2 * Math.PI * x) / w.lam + w.ph)
    top.push(`${x.toFixed(1)} ${(CY - w.hh + AMP * w.af * s).toFixed(1)}`)
    bot.push(
      `${x.toFixed(1)} ${(
        CY +
        w.hh +
        0.45 * AMP * w.af * s +
        BULGE * w.af * ((1 + s) / 2)
      ).toFixed(1)}`,
    )
  }
  return `M${top.join('L')}L${bot.reverse().join('L')}Z`
}

export function MainLaneFlow({ left, width }: { left: number; width: number }) {
  const pathRefs = useRef<(SVGPathElement | null)[]>([])
  const ds = useMemo(() => LAYERS.map((w) => ribbonD(width, w)), [width])

  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    let reduced = mq.matches
    const onChange = (e: MediaQueryListEvent) => {
      reduced = e.matches
    }
    mq.addEventListener?.('change', onChange)

    let raf = 0
    let t = 0
    let last = performance.now()
    const tick = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000)
      last = now
      if (!reduced) t += dt
      for (let i = 0; i < LAYERS.length; i++) {
        const el = pathRefs.current[i]
        const w = LAYERS[i]
        if (!el) continue
        // 0 → +λ then seamless wrap: rightward drift, one wavelength period.
        const x = ((t / w.dur) * w.lam) % w.lam
        el.setAttribute('transform', `translate(${x.toFixed(2)},0)`)
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(raf)
      mq.removeEventListener?.('change', onChange)
    }
  }, [])

  return (
    <div className="pm-timeline-main-flow" style={{ left, width }} aria-hidden>
      <div className="pm-timeline-main-flow-base" />
      <svg
        className="pm-timeline-main-flow-waves"
        width="100%"
        height={VIEW_H}
      >
        {LAYERS.map((w, i) => (
          <path
            key={i}
            ref={(el) => {
              pathRefs.current[i] = el
            }}
            d={ds[i]}
            style={{ fill: `rgba(26, 94, 61, ${w.a})` }}
          />
        ))}
      </svg>
    </div>
  )
}
