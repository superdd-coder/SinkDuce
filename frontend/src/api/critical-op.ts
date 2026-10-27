/**
 * 关键操作注册表（2026-09-19 前端加固 #7）。
 *
 * 远端盒子掉线时请求层会自动「切回本机 + 整页 reload」。但 reload 会**掐断
 * 正在进行中的录制 PCM 流和文件上传**——本该只影响那台盒子的事故，被回退
 * 动作放大成「把我的录音弄丢了」。这里登记「进行中的关键操作」，请求层
 * 在回退前检查：有活动操作 → 推迟回退（页面进入降级态，操作完成后补做）。
 */

const active = new Set<symbol>()
const finishListeners: Array<() => void> = []

/** 全部关键操作结束时会触发一次（供 origin.ts 补做被推迟的回退）。 */
export function onAllOpsFinished(fn: () => void): () => void {
  finishListeners.push(fn)
  return () => {
    const i = finishListeners.indexOf(fn)
    if (i >= 0) finishListeners.splice(i, 1)
  }
}

/** 标记一个关键操作开始。返回结束函数（幂等，重复调用无副作用）。 */
export function beginCriticalOp(): () => void {
  const token = Symbol("critical-op")
  active.add(token)
  let done = false
  return () => {
    if (done) return
    done = true
    active.delete(token)
    if (active.size === 0) {
      for (const fn of [...finishListeners]) {
        try {
          fn()
        } catch {
          /* 监听器故障不阻塞操作收尾 */
        }
      }
    }
  }
}

export function hasCriticalOps(): boolean {
  return active.size > 0
}

/** 包装一段异步操作为关键操作（上传等：进行中不掉线 reload）。 */
export async function withCriticalOp<T>(fn: () => Promise<T>): Promise<T> {
  const end = beginCriticalOp()
  try {
    return await fn()
  } finally {
    end()
  }
}
