import { useId } from "react"
import { cn } from "../../lib/utils"

const LAKE_PATH =
  "M 471.4 176.0 L 573.6 276.0 Q 645.0 346.0 573.6 416.0 L 471.4 516.0 " +
  "Q 400.0 586.0 328.6 516.0 L 226.4 416.0 Q 155.0 346.0 226.4 276.0 " +
  "L 328.6 176.0 Q 400.0 106.0 471.4 176.0 Z"

/**
 * The internal lake scene: three wave bands + suspended color clouds.
 * Flow keyframes (index.css, scoped under .qc-logo) animate these classes;
 * their displacement is multiplied by --flow-amp so the liquid swells
 * from rest and settles back instead of snapping.
 */
function Scene({ u }: { u: (k: string) => string }) {
  return (
    <g transform="rotate(-8 400 346)" filter={u("cloud")}>
      <g className="sway">
        <path className="b1" d="M 120 300 C 220 240 320 280 420 290 C 540 302 620 250 740 280 L 740 700 L 120 700 Z" fill="#9DB894" opacity="0.9" />
        <path className="b2" d="M 120 380 C 220 330 340 360 440 366 C 560 373 640 330 740 360 L 740 700 L 120 700 Z" fill="#5E8A63" opacity="0.95" />
        <path className="b3" d="M 120 460 C 240 420 380 450 500 446 C 620 442 680 410 740 430 L 740 700 L 120 700 Z" fill="#24422F" opacity="0.95" />
        <ellipse className="e1" cx="300" cy="360" rx="130" ry="60" fill="#C9D8BE" opacity="0.7" />
        <ellipse className="e2" cx="500" cy="250" rx="90" ry="40" fill="#F2EDD5" opacity="0.6" />
        <ellipse className="e3" cx="470" cy="480" rx="120" ry="55" fill="#16301F" opacity="0.55" />
        <ellipse className="e4" cx="260" cy="470" rx="80" ry="40" fill="#7FA07E" opacity="0.6" />
        <ellipse className="e5" cx="420" cy="240" rx="55" ry="22" fill="#F7F5EC" opacity="0.6" />
      </g>
    </g>
  )
}

/**
 * Quick-Chat FAB mark — the lake-orb logo inlined (not <img>) so hosts can
 * drive its liquid flow. `flowing` holds full amplitude (QuickChat open);
 * hover-while-closed is CSS-only (.quick-chat-btn:hover). SVG defs ids are
 * document-global, hence the per-instance prefix.
 */
export function LogoMark({
  className,
  flowing = false,
}: {
  className?: string
  flowing?: boolean
}) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "")
  const p = (k: string) => `qcmark${uid}${k}`
  const u = (k: string) => `url(#${p(k)})`
  return (
    <svg
      viewBox="0 0 800 620"
      className={cn("qc-logo", flowing && "flow-on", className)}
      aria-hidden="true"
    >
      <defs>
        <clipPath id={p("lake")}>
          <path d={LAKE_PATH} />
        </clipPath>
        <filter id={p("featheredge")} x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="1.2" />
        </filter>
        <filter id={p("cloud")} x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="14" />
        </filter>
        <filter id={p("puffblur")} x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="15" />
        </filter>
        <filter id={p("refrblur")} x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="11" />
        </filter>
        <filter id={p("soft")} x="-15%" y="-15%" width="130%" height="130%">
          <feGaussianBlur stdDeviation="9" />
        </filter>
        <filter id={p("glint")} x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="7" />
        </filter>
        <filter id={p("aurablur")} x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="10" />
        </filter>
        <linearGradient id={p("aura")} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#E3E2D0" />
          <stop offset="0.5" stopColor="#C6CFBB" />
          <stop offset="1" stopColor="#9FB39F" />
        </linearGradient>
        <radialGradient id={p("ring")} gradientUnits="userSpaceOnUse" cx="400" cy="346" r="248">
          <stop offset="0.90" stopColor="#ffffff" stopOpacity="0" />
          <stop offset="0.965" stopColor="#ffffff" stopOpacity="0.85" />
          <stop offset="1" stopColor="#ffffff" stopOpacity="1" />
        </radialGradient>
        <mask id={p("mRing")}>
          <rect x="100" y="60" width="600" height="580" fill={u("ring")} />
        </mask>
        <mask id={p("mFeather")}>
          <path d={LAKE_PATH} fill="#ffffff" filter={u("featheredge")} />
        </mask>
        <linearGradient id={p("fadeRight")} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0" />
          <stop offset="0.5" stopColor="#ffffff" stopOpacity="0" />
          <stop offset="1" stopColor="#ffffff" stopOpacity="1" />
        </linearGradient>
        <mask id={p("mRight")}>
          <rect x="320" y="80" width="430" height="500" fill={u("fadeRight")} />
        </mask>
      </defs>

      <path d={LAKE_PATH} fill={u("aura")} opacity="0.5" filter={u("aurablur")} />

      <g mask={u("mFeather")}>
        <g clipPath={u("lake")}>
          <rect x="60" y="80" width="700" height="540" fill="#EFEDDA" />
          <Scene u={u} />
          <path d={LAKE_PATH} fill="none" stroke="#12271C" strokeOpacity="0.07" strokeWidth="30" filter={u("puffblur")} />
        </g>
        <g clipPath={u("lake")} mask={u("mRight")} filter={u("soft")}>
          <rect x="60" y="80" width="700" height="540" fill="#EFEDDA" />
          <Scene u={u} />
          <path d={LAKE_PATH} fill="none" stroke="#12271C" strokeOpacity="0.07" strokeWidth="30" filter={u("puffblur")} />
        </g>

        <g clipPath={u("lake")} mask={u("mRing")} opacity="0.22">
          <g filter={u("refrblur")}>
            <g transform="scale(1.07) translate(-28 -24)">
              <Scene u={u} />
            </g>
            <rect x="60" y="60" width="700" height="580" fill="#FCFAF0" opacity="0.58" />
          </g>
        </g>

        <g clipPath={u("lake")} opacity="0.55">
          <path d={LAKE_PATH} fill="none" stroke="#FFFDF2" strokeOpacity="0.45" strokeWidth="9" filter={u("glint")} />
        </g>
      </g>
    </svg>
  )
}
