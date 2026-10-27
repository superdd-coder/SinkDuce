/**
 * Hub（出站）钥匙托管的镜像同步。
 *
 * ── 为什么要有这个文件（2026-09-18 Hub 设计 §6）────────────────
 *
 * Hub = 本机盒子的 MCP，它要「代你去问别的盒子」，就得知道
 * 「有哪些盒子 + 各自的钥匙」。而这件事此前只存在于**浏览器 localStorage**
 * （`sinkduce.pairing.connections`）——后端一无所知。
 *
 * 所以这里把浏览器那份**镜像**给后端（出站存储在 `data/hub/boxes.json`）。
 *
 * **为什么是镜像而不是搬走**：`origin.ts` 是前端「当前来源」的唯一事实源，
 * 切来源 = 写 origin + 整页 reload，18 处调用点都建立在它**同步**读取 localStorage
 * 之上。改成异步读后端要动整条链且无收益——浏览器仍需那份来直连远端。
 * 于是：浏览器继续持有（同步、它自己用），后端镜像一份（Hub 用）。
 *
 * ── 三条纪律 ──────────────────────────────────────────────────
 *
 * 1. **尽力而为，绝不打扰用户**：镜像失败（后端没起来 / 页面正由远端盒子提供 /
 *    网络抖动）不能影响「连接盒子」这个主流程。所以调用方一律 fire-and-forget，
 *    错误只记 debug。
 * 2. **整体替换**：后端用浏览器这份整体覆盖，于是删除天然被覆盖、不会两侧漂移。
 *    见后端 `src/hub/store.py::replace_boxes`。
 * 3. **只在「本机来源」下同步**：`/api/hub/boxes` 只许本机（那是凭据库）。
 *    若页面正由远端盒子提供，相对路径打的是远端 → 404，所以先判来源，省一次白跑。
 */

// 带扩展名的导入：本模块会被 node --test 直接加载（测试），Node 的解析器不像
// bundler 那样能补 `.ts`，漏了扩展名在**全量跑**时会报 ERR_MODULE_NOT_FOUND
// （单独跑却过——因为那时模块缓存里已有带扩展名的实例）。与 origin.ts 的写法一致。
import { request } from "./http.ts"
import {
  isLocalSourceActive,
  isLoopbackPage,
  isRemoteShellActive,
  savedConnections,
  type SavedConnection,
} from "./origin.ts"

/**
 * 把浏览器那份连接列表整体推给后端。
 *
 * 返回是否真的发出去了（`false` = 当前不是本机来源，跳过）。
 * 调用方通常**不关心**返回值——它是 fire-and-forget 的。
 */
// ── 失败补发（2026-09-19 前端加固 #8）─────────────────────────
//
// 镜像是 fire-and-forget：后端重启一次（或那次请求恰好赶上网络抖动），
// 浏览器这份清单就**永久**错过同步——Hub 从此不知道还有别的盒子，而界面上
// 什么异常都不会显示（静默漂移）。修法：失败时记下「待重发」，在可靠的
// 同步点（标签页回前台 / 下次任何 saveConnections）补发。
let pendingMirror = false

/**
 * 若有未送达的镜像，立即重推一次。
 *
 * 挂到 visibilitychange（回前台）上：后端重启往往发生在用户离开标签页期间，
 * 回前台是唯一确定性的「再试一次」时机。内部自带本机来源判定，远端页面
 * 调用是安全的 no-op。返回是否真的发出去了。
 */
export async function flushPendingMirror(): Promise<boolean> {
  if (!pendingMirror) return false
  return mirrorConnectionsToHub()
}

export async function mirrorConnectionsToHub(
  list?: SavedConnection[],
): Promise<boolean> {
  // 三个前提取其一即可：**当前来源是本机**，或**远程模式瘦壳**（此时瘦壳的
  // /api/hub/boxes 就是本机入口——Hub 转发的数据源，新配对的盒子必须镜像
  // 过去，否则瘦壳上的 MCP 对新盒子失明）；且**这个页面确实由本机伺服**。
  //
  // 只判前者不够：`isLocalSourceActive()` 看的是「有没有活动的远端连接记录」，而
  // 「用局域网 IP 直接打开盒子页面」时**没有任何连接记录** → 它返回 true，
  // 可这个页面其实是远端给的，`/hub/boxes` 会打过去 → 撞门卫 401。
  // `isLoopbackPage()` 才是「页面来自本机」的那个信号（见 origin.ts 的说明）。
  if ((!isLocalSourceActive() && !isRemoteShellActive()) || !isLoopbackPage()) return false
  const boxes = (list ?? savedConnections()).map((c) => ({
    box_id: c.box_id,
    box_name: c.box_name,
    addr: c.addr,
    token: c.token,
    last_checked: c.last_checked,
  }))
  try {
    await request<{ boxes: unknown[] }>("/hub/boxes", {
      method: "PUT",
      local: true,
      // 后台同步：失败**不要**弹「需要配对钥匙」——用户什么都没点，
      // 冒出一个他无从判断的弹窗比不弹更糟。见 RequestOptions.silent。
      silent: true,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ boxes }),
    })
    pendingMirror = false
    return true
  } catch (err) {
    // 尽力而为：后端老版本没有这个端点、或本机判定不成立时会失败——
    // 那是「Hub 暂时没拿到钥匙」，不是用户操作失败，所以不弹错。
    // 标记待重发：等回前台 / 下次连接变更时补发（否则后端重启一次就永久漂移）。
    pendingMirror = true
    console.debug("[hub] connection mirror skipped:", err)
    return false
  }
}
