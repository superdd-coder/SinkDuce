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

/**
 * Strip spaces only *inside* a complete **bold** / *italic* pair.
 * Inner runs exclude newlines: a line-leading `*` is a CommonMark bullet
 * marker, and a newline-crossing pair would consume the next list item's
 * `*` as a closing delimiter, shredding the list (Data & Facts `*` leak).
 */
export function trimEmphasisInteriorSpaces(md: string): string {
  return (md || "")
    .replace(/\*\*([^*\n]+)\*\*/g, (_, inner: string) => {
      return `**${inner.replace(/^[ \t]+|[ \t]+$/g, "")}**`
    })
    .replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, (_, inner: string) => {
      return `*${inner.replace(/^[ \t]+|[ \t]+$/g, "")}*`
    })
}
