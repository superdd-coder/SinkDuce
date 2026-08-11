/**
 * Markdown emphasis helpers.
 *
 * Important: never "trim interior spaces" with a pattern that requires
 * whitespace after the opening markers and before the closing markers
 * without first binding a complete pair. That matches the *gap between*
 * two adjacent bold spans:
 *   `**a** b **c**`  →  treats the middle ` b ` as inside markers  →  `**a**b**c**`
 * which drops the exterior spaces around bold (Meeting Summary sticky-bold bug).
 * Always match a full pair first (`**…**`), then strip only ends of the inner text.
 */

/** Strip spaces only *inside* a complete **bold** / *italic* pair. */
export function trimEmphasisInteriorSpaces(md: string): string {
  return (md || "")
    .replace(/\*\*([^*]+)\*\*/g, (_, inner: string) => {
      return `**${inner.replace(/^[ \t]+|[ \t]+$/g, "")}**`
    })
    .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, (_, inner: string) => {
      return `*${inner.replace(/^[ \t]+|[ \t]+$/g, "")}*`
    })
}
