import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { bootstrapLocalKey, getLocalHealth, startAccessTokenWarmer } from './api/client'
import { setRemoteShellActive } from './api/origin'
import { useAppStore } from './stores/app-store'
import { startIdentityRevalidation } from './lib/identity-revalidate'
import { bumpDomain } from './lib/domain-refresh'

// 本机静默领钥匙：必须在首屏任何 /api 请求之前完成，否则门卫会把它们挡成 401。
// best-effort（远端设备上会静默失败），所以不阻塞渲染太久。
async function start() {
  // 远程模式识别：本机 /health 的 remote_shell 标志只有瘦壳会带。必须先于
  // bootstrapLocalKey（远程模式没有本机钥匙可领）与一切业务请求（来源语义
  // 依赖它）。/health 是门卫豁免端点，提前打没有鉴权次序问题。
  try {
    const local = await getLocalHealth()
    if (local && local.remote_shell) {
      setRemoteShellActive(true)
      useAppStore.getState().setRemoteShellMode(true)
    }
    if (local && local.in_container) {
      useAppStore.getState().setContainerDeployment(true)
    }
  } catch {
    /* 本机 health 不可达：按正常模式走（与今天行为一致） */
  }
  if (!useAppStore.getState().remoteShellMode) await bootstrapLocalKey()
  // 长短钥匙的暖票循环：给当前通道定时换短票；换不到就全程回落长钥匙，零风险
  startAccessTokenWarmer()
  // RBAC 身份画像：UI 靠它隐藏无权入口（化妆）。必须在首屏请求前发起，
  // 但不阻塞渲染——拿到后 store 更新，订阅方自动收窄。
  void useAppStore.getState().refreshIdentity()
  // 权限变了要能**自己发现**：启动时取的 identity 与库列表是缓存，管理员在另一端
  // 改了这把钥匙的角色 / 改了某个库的 ACL，这边不刷新就一直是旧的。切回本页时
  // 重校验一次（见 lib/identity-revalidate 里为什么不用事件流）。
  // 会议/热词/人物三域也带权限字段，一并 bump 让各自视图重取。
  startIdentityRevalidation({
    refreshIdentity: () => useAppStore.getState().refreshIdentity(),
    refreshCollections: () => useAppStore.getState().fetchCollections(),
    refreshSessions: () => useAppStore.getState().refreshSessionList(),
    refreshDomains: () => {
      bumpDomain('meetings')
      bumpDomain('hot_words')
      bumpDomain('people')
    },
  })
  createRoot(document.getElementById('root')!, {
    onUncaughtError: (error, errorInfo) => {
      (window as any).__rlog = (window as any).__rlog || []
      ;(window as any).__rlog.push({
        msg: String(error).slice(0, 200),
        stack: (error as Error)?.stack?.slice(0, 900),
        components: errorInfo?.componentStack?.slice(0, 1200),
      })
    },
  }).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}

void start()
