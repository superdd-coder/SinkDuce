/** Pick the transcript row that should stay focused at `time` seconds. */

export function findPlayingSegmentIndex(
  segments: Array<{ start: number; end: number }>,
  time: number | null | undefined,
): number {
  if (time == null || !Number.isFinite(time) || segments.length === 0) return -1
  const idx = segments.findIndex((seg) => seg.start <= time && time < seg.end)
  if (idx !== -1) return idx
  for (let i = segments.length - 1; i >= 0; i--) {
    if (segments[i].start <= time) return i
  }
  return -1
}
