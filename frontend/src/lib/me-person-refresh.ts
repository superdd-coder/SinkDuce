/**
 * Me identity changed in People — todo lists and Mine filters stay mounted.
 */

export const ME_PERSON_REFRESH_EVENT = "sinkduce:me-person-refresh"

export type MePersonRefreshDetail = {
  personId: string | null
}

export function triggerMePersonRefresh(personId: string | null) {
  if (typeof window === "undefined") return
  window.dispatchEvent(
    new CustomEvent(ME_PERSON_REFRESH_EVENT, { detail: { personId } }),
  )
}

export function onMePersonRefresh(
  handler: (detail: MePersonRefreshDetail) => void,
): () => void {
  if (typeof window === "undefined") return () => {}
  const listener = (e: Event) => {
    const ce = e as CustomEvent<MePersonRefreshDetail>
    if (ce.detail) handler(ce.detail)
  }
  window.addEventListener(ME_PERSON_REFRESH_EVENT, listener)
  return () => window.removeEventListener(ME_PERSON_REFRESH_EVENT, listener)
}
