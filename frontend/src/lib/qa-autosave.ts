type SaveState = "dirty" | "saving" | "saved" | "error"

interface AutosaveOptions<T> {
  delay?: number
  equal?: (a: T, b: T) => boolean
  onState?: (state: SaveState) => void
  onSaved?: (value: T) => void
  onError?: (error: unknown) => void
}

/** One editor owns its pending value until the server acknowledges that exact value. */
export function createQaAutosave<T>(initial: T, save: (value: T) => Promise<void>, options: AutosaveOptions<T> = {}) {
  let value = initial
  let saved = initial
  let timer: ReturnType<typeof setTimeout> | undefined
  let running: Promise<boolean> | null = null
  const equal = options.equal || Object.is
  const dirty = () => !equal(value, saved)
  const clearTimer = () => { clearTimeout(timer); timer = undefined }

  function flush(): Promise<boolean> {
    clearTimer()
    if (running) return running
    running = (async () => {
      while (dirty()) {
        const target = value
        options.onState?.("saving")
        try {
          await save(target)
          saved = target
          options.onSaved?.(target)
        } catch (error) {
          clearTimer()
          options.onState?.("error")
          options.onError?.(error)
          return false
        }
      }
      clearTimer()
      options.onState?.("saved")
      return true
    })().finally(() => { running = null })
    return running
  }

  return {
    dirty,
    flush,
    edit(next: T) {
      value = next
      clearTimer()
      options.onState?.(running ? "saving" : dirty() ? "dirty" : "saved")
      if (!running && !dirty()) options.onSaved?.(value)
      if (dirty()) timer = setTimeout(() => { void flush() }, options.delay ?? 800)
    },
    reset(next: T) {
      if (running || dirty()) return false
      value = saved = next
      return true
    },
  }
}

/** Focus belongs to the editing session, independently of network save state. */
export function createQaEditorAutosave<T>(initial: T, save: (value: T) => Promise<void>, options: AutosaveOptions<T> & {
  onEditing?: (editing: boolean) => void
} = {}) {
  const autosave = createQaAutosave(initial, save, options)
  let editing = false
  function setEditing(active: boolean) {
    if (editing === active) return
    editing = active
    options.onEditing?.(active)
  }
  return {
    ...autosave,
    editing: () => editing,
    begin: () => setEditing(true),
    finish() { setEditing(false); return autosave.flush() },
  }
}

interface VersionedList { id: string; revision: number; version: { id: string } }

/** List mutations share one revision, including edits from different reply fields. */
export function createQaMutationQueue<T extends VersionedList>(request: (
  list: T, path: string, payload: Record<string, unknown>, method: string,
) => Promise<T>) {
  let tail: Promise<unknown> = Promise.resolve()
  const snapshots = new Map<string, T>()
  const key = (list: T) => `${list.id}/${list.version.id}`
  function remember(list: T) {
    const previous = snapshots.get(key(list))
    if (!previous || previous.revision < list.revision) snapshots.set(key(list), list)
  }
  return {
    remember,
    write(list: T, path: string, payload: Record<string, unknown>, method = "POST") {
      remember(list)
      const work = tail.catch(() => {}).then(async () => {
        const next = await request(snapshots.get(key(list)) || list, path, payload, method)
        remember(next)
        return next
      })
      tail = work
      return work
    },
  }
}
