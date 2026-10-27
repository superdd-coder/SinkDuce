import { useCallback, useEffect, useRef, useState, type ReactNode } from "react"
import { ChevronDown, HardDrive, Monitor } from "lucide-react"

import {
  getActiveSourceId,
  isLoopbackPage,
  LOCAL_SOURCE,
  savedConnections,
  setActiveSourceId,
} from "@/api/origin"
import { getPairingBox } from "@/api/pairing"
import {
  probeSources,
  startSourceHealthLoop,
  stopSourceHealthLoop,
} from "@/api/source-health"
import { writeSourceScopedFor } from "@/api/source-scope"
import { useAppStore, type SourceHealthState } from "@/stores/app-store"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"
import { MenuItemDescription, MenuItemTitle, MenuSeparator, SoftMenu } from "@/components/ui/menu"

/**
 * 右上角「来源控制台」：盒子切换菜单 × 在线/离线指示合并体（HUB-7）。
 *
 * 收起态 = 状态圆点 + 当前来源名 + 箭头；点开 = 两行富条目菜单（名字/状态 +
 * 地址副行）。状态来自 source-health.ts 的统一探测循环（store 的 sourceHealth），
 * trigger 和菜单读同一份真实探测结果。
 */
export function SourcePill() {
  const t = useT()
  const [open, setOpen] = useState(false)
  // activeId 只随整页刷新变化（切换即 reload），挂载时读一次即稳。
  const [activeId] = useState(getActiveSourceId())
  // 连接清单每次打开菜单重读：新配对的盒子立即可见（修旧切换器首帧冻结）。
  const [conns, setConns] = useState(savedConnections)
  // 直连态（局域网设备直接打开盒子页面）：「本机」是谎话，显示盒子自己的名字。
  // 回环 hostname = 真本机，同步判定；非回环才去取盒子档案（同源 /api/pairing/box）。
  const localShell = isLoopbackPage()
  const [selfBoxName, setSelfBoxName] = useState<string | null>(null)
  useEffect(() => {
    if (localShell) return
    let alive = true
    getPairingBox()
      .then((b) => {
        if (alive) setSelfBoxName(b.box_name || null)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [localShell])
  const anchorRef = useRef<HTMLButtonElement>(null)
  const sourceHealth = useAppStore((s) => s.sourceHealth)
  const remoteShellMode = useAppStore((s) => s.remoteShellMode)
  const setSidebarView = useAppStore((s) => s.setSidebarView)

  // 探测循环与壳同寿（Header 常驻本组件）。
  useEffect(() => {
    startSourceHealthLoop()
    return () => stopSourceHealthLoop()
  }, [])

  const stateOf = (id: string): SourceHealthState => sourceHealth[id] ?? "unknown"

  const stateWord = (st: SourceHealthState): string | null =>
    st === "online"
      ? t("shell.switcher.online")
      : st === "offline"
        ? t("shell.switcher.offline")
        : st === "revoked"
          ? t("shell.switcher.revoked")
          : null

  const openMenu = useCallback(() => {
    setConns(savedConnections())
    probeSources()
  }, [])

  const pick = (id: string) => {
    setOpen(false)
    if (id === activeId) return
    // 切换遮罩：reload 本身不可避（API_BASE 是模块常量），但「点下去没反应
    // 然后闪白」是双重误点（点离线盒子 → 请求全炸 → 自动弹回本机再刷一次）
    // 的温床。遮罩把一次切换明确框定为一个动作。
    document.documentElement.dataset.switching = "1"
    setActiveSourceId(id)
    window.location.reload() // 整页刷新：所有数据/连接干净地指向新来源
  }

  // 点外关闭 + Esc 关闭（旧切换器两者皆无）。
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const target = e.target as HTMLElement | null
      if (target?.closest("[data-source-menu]")) return
      if (target?.closest("[data-source-pill-trigger]")) return
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false)
    }
    // 等开菜单的那次 click 冒泡完再挂监听，避免刚点开就被同一次点击关掉。
    const id = window.setTimeout(() => document.addEventListener("mousedown", onDown), 0)
    document.addEventListener("keydown", onKey)
    return () => {
      window.clearTimeout(id)
      document.removeEventListener("mousedown", onDown)
      document.removeEventListener("keydown", onKey)
    }
  }, [open])

  const currentConn = conns.find((c) => c.box_id === activeId)
  // LOCAL_SOURCE 条目的实际含义是「页面所在的这台盒子」：真本机显示「本机」，
  // 直连态显示盒子名（拿到前先用页面地址顶着，绝不显示误导性的「本机」）。
  const pageHost = typeof window !== "undefined" ? window.location.host : ""
  const selfName = localShell
    ? t("shell.switcher.local")
    : selfBoxName || pageHost || t("shell.switcher.local")
  const selfDesc = localShell ? t("shell.switcher.localDesc") : pageHost
  const currentName =
    activeId === LOCAL_SOURCE
      ? selfName
      : currentConn?.box_name || t("shell.switcher.unknown")
  const currentState = stateOf(activeId)
  const currentWord = stateWord(currentState)

  const renderRow = (id: string, name: string, desc: string, icon: ReactNode) => {
    const st = stateOf(id)
    const word = stateWord(st)
    const active = id === activeId
    // 离线/钥匙失效的**非当前**来源：照常展示但禁点。曾经照点不误 →
    // reload → 首批请求全炸 → 自动弹回本机再 reload —— 一次误点两次白屏。
    // unknown（探测还没回来）不拦，避免首帧全灰。
    const blocked = !active && (st === "offline" || st === "revoked")
    return (
      <button
        key={id}
        type="button"
        role="menuitem"
        disabled={blocked}
        title={blocked ? t("shell.switcher.blockedOffline") : undefined}
        onClick={() => pick(id)}
        className={cn(
          "pm-menu-item pm-source-row",
          active && "is-on",
          blocked && "is-blocked opacity-50 cursor-not-allowed",
        )}
      >
        {icon}
        <span className="pm-source-row-text">
          <MenuItemTitle>{name}</MenuItemTitle>
          <MenuItemDescription>{desc}</MenuItemDescription>
        </span>
        <span className="pm-source-row-status">
          <span className={cn("pm-source-dot is-sm", `is-${st}`)} aria-hidden />
          {word && (
            <span className={cn("pm-source-row-word", st === "revoked" && "is-revoked")}>
              {word}
            </span>
          )}
        </span>
      </button>
    )
  }

  return (
    <div className="relative shrink-0">
      <button
        type="button"
        ref={anchorRef}
        data-source-pill-trigger="true"
        onClick={() => {
          if (!open) openMenu()
          setOpen((v) => !v)
        }}
        className={cn("pm-source-pill", open && "is-open")}
        title={currentWord ? `${currentName} · ${currentWord}` : currentName}
      >
        <span className={cn("pm-source-dot", `is-${currentState}`)} aria-hidden />
        <span className="pm-source-pill-name">{currentName}</span>
        <ChevronDown className="pm-source-pill-chevron" strokeWidth={2} />
      </button>

      <SoftMenu
        open={open}
        portal
        align="end"
        anchorRef={anchorRef}
        className="pm-source-menu"
        data-source-menu="true"
      >
        <div className="pm-source-kicker">{t("shell.switcher.title")}</div>
        {/* 远程模式没有「本机」可去（瘦壳不是内容盒子）：只列远端盒子。 */}
        {!remoteShellMode &&
          renderRow(
            LOCAL_SOURCE,
            selfName,
            selfDesc,
            <Monitor className="h-4 w-4 shrink-0" strokeWidth={1.75} />,
          )}
        {conns.map((c) =>
          renderRow(
            c.box_id,
            c.box_name,
            c.addr.replace(/\/+$/, ""),
            <HardDrive className="h-4 w-4 shrink-0" strokeWidth={1.75} />,
          ),
        )}
        {conns.length === 0 && (
          <div className="px-2.5 pt-1 pb-1.5 text-xs text-muted-foreground">
            {t("shell.switcher.noConnections")}
          </div>
        )}
        <MenuSeparator />
        <button
          type="button"
          role="menuitem"
          className="pm-menu-item pm-source-manage"
          onClick={() => {
            setOpen(false)
            if (remoteShellMode) {
              // 远程模式的「管理」= 极简设置页（当前壳的壳级设置），
              // 不涉及本机配对（瘦壳没有配对面）。
              setSidebarView("llm_provider")
              return
            }
            if (activeId === LOCAL_SOURCE) {
              setSidebarView("llm_provider")
              return
            }
            // 配对管理的是**本机盒子**的钥匙（连在别人盒子上时配对卡不适用）。
            // 切回本机 = 整页刷新；目的地视图此刻就写进本机作用域的物理键
            // （activeId 还是远端，setSidebarView 会写错作用域），刷新后直达设置页。
            writeSourceScopedFor(LOCAL_SOURCE, "rag_sidebarView", "llm_provider")
            setActiveSourceId(LOCAL_SOURCE)
            window.location.reload()
          }}
        >
          {t("shell.switcher.manage")}
        </button>
      </SoftMenu>
    </div>
  )
}
