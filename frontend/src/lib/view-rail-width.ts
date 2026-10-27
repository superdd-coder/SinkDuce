export const DEFAULT_VIEW_RAIL_WIDTH = 228
export const MIN_VIEW_RAIL_WIDTH = 180
export const MAX_VIEW_RAIL_WIDTH = 480

export function normalizeViewRailWidth(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return DEFAULT_VIEW_RAIL_WIDTH
  return Math.min(MAX_VIEW_RAIL_WIDTH, Math.max(MIN_VIEW_RAIL_WIDTH, value))
}
