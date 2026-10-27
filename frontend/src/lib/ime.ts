/** Keyboard-ish event shape used by chat composers (React or native). */
export type ImeKeyEvent = {
  key: string
  shiftKey: boolean
  keyCode?: number
  which?: number
  isComposing?: boolean
  nativeEvent?: { isComposing?: boolean; keyCode?: number }
}

export function isImeComposing(e: ImeKeyEvent): boolean {
  const composing = e.nativeEvent?.isComposing ?? e.isComposing ?? false
  const code = e.nativeEvent?.keyCode ?? e.keyCode ?? e.which ?? 0
  return composing || code === 229
}

/** Enter that should send a chat message. Shift+Enter and IME confirm are excluded. */
export function isChatSubmitEnter(e: ImeKeyEvent): boolean {
  return e.key === "Enter" && !e.shiftKey && !isImeComposing(e)
}

/**
 * IME confirm on Chrome/Safari often fires compositionend, then a Keydown
 * Enter with isComposing=false. Swallow that trailing Enter until the next frame.
 */
export function createImeEnterGuard() {
  let composing = false
  let justEnded = false

  return {
    onCompositionStart() {
      composing = true
      justEnded = false
    },
    onCompositionEnd() {
      composing = false
      justEnded = true
    },
    clearJustEnded() {
      justEnded = false
    },
    isSubmitEnter(e: ImeKeyEvent): boolean {
      if (e.key !== "Enter" || e.shiftKey) return false
      if (composing || justEnded || isImeComposing(e)) return false
      return true
    },
  }
}
