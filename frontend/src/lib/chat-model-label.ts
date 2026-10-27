/** The backend uses its configured default unless both override fields exist. */
export function chatModelLabel(
  providerId: string | null,
  model: string | null,
  defaultLabel: string,
): string {
  return providerId && model ? model : defaultLabel
}
