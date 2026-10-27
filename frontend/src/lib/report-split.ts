import { createViewRailResizeController } from "./view-rail-resize.ts"

export const DEFAULT_REPORT_SPLIT = 0.46
export const MIN_REPORT_SPLIT = 0.28
export const MAX_REPORT_SPLIT = 0.7

export function normalizeReportSplit(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return DEFAULT_REPORT_SPLIT
  return Math.round(Math.min(MAX_REPORT_SPLIT, Math.max(MIN_REPORT_SPLIT, value)) * 10000) / 10000
}

export function reportChatWidth(width: number, hostWidth: number): number {
  const min = Math.max(320, hostWidth * MIN_REPORT_SPLIT)
  const max = Math.max(min, Math.min(hostWidth - 372, hostWidth * MAX_REPORT_SPLIT))
  return Math.min(max, Math.max(min, width))
}

export function reportSplitKeyboardRatio(ratio: number, key: string, shift: boolean): number | undefined {
  if (key === "Home") return MIN_REPORT_SPLIT
  if (key === "End") return MAX_REPORT_SPLIT
  if (key === "ArrowLeft") return normalizeReportSplit(ratio - (shift ? 0.05 : 0.02))
  if (key === "ArrowRight") return normalizeReportSplit(ratio + (shift ? 0.05 : 0.02))
  return undefined
}
export function createReportResizeController(callbacks: { onPreview: (ratio: number | null) => void; onCommit: (ratio: number) => void }) {
  let hostWidth = 0
  return { ...createViewRailResizeController({
    onPreview: (width) => callbacks.onPreview(width === null ? null : width / hostWidth),
    onCommit: (width) => callbacks.onCommit(width / hostWidth),
  }, { propertyName: "--pm-report-left-w", normalizeWidth: (width) => reportChatWidth(width, hostWidth) }),
  setHostWidth(value: number) { hostWidth = value } }
}

/** The CSS offset is temporary; dragging continues to edit the underlying preference. */
export function reportResizeStartingWidth(visibleWidth: number, reviewOffset: number) {
  return visibleWidth + (Number.isFinite(reviewOffset) ? Math.max(0, reviewOffset) : 0)
}
