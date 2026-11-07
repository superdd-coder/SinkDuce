export type MenuAnchor = { left: number; right: number; top: number; bottom: number; width: number }

/** Place the entire surface, including a connected child, inside the viewport. */
export function placeFloatingMenu({ anchor, width, height, viewportWidth, viewportHeight,
  align = "start", placement = "bottom", rootWidth,
}: {
  anchor: MenuAnchor; width: number; height: number; viewportWidth: number; viewportHeight: number
  align?: "start" | "center" | "end"; placement?: "bottom" | "right"; rootWidth?: number
}) {
  const pad = 12, gap = 8
  const maxWidth = Math.max(0, viewportWidth - pad * 2)
  const usedWidth = Math.min(width, maxWidth)
  const clampX = (x: number) => Math.max(pad, Math.min(x, viewportWidth - pad - usedWidth))
  let side: "left" | "right" = "right"
  let left = align === "end" ? anchor.right - usedWidth
    : align === "center" ? anchor.left + anchor.width / 2 - usedWidth / 2 : anchor.left
  if (rootWidth) {
    const rootStart = align === "end" ? anchor.right - rootWidth
      : align === "center" ? anchor.left + (anchor.width - rootWidth) / 2 : anchor.left
    const rootEnd = rootStart + rootWidth
    const fitsRight = rootStart + usedWidth <= viewportWidth - pad
    const fitsLeft = rootEnd - usedWidth >= pad
    side = !fitsRight && (fitsLeft || rootStart > viewportWidth - rootEnd) ? "left" : "right"
    left = side === "left" ? rootEnd - usedWidth : rootStart
  } else if (placement === "right") {
    side = anchor.right + gap + usedWidth <= viewportWidth - pad ? "right" : "left"
    left = side === "right" ? anchor.right + gap : anchor.left - gap - usedWidth
  }
  const below = Math.max(0, viewportHeight - pad - anchor.bottom - gap)
  const above = Math.max(0, anchor.top - gap - pad)
  const flip: "up" | "down" = height > below && above > below ? "up" : "down"
  const maxHeight = placement === "right"
    ? Math.max(0, viewportHeight - pad * 2)
    : Math.min(Math.max(below, above, 0), Math.max(0, viewportHeight - pad * 2))
  const available = placement === "right" ? maxHeight : flip === "up" ? above : below
  const usedHeight = Math.min(height, available, maxHeight)
  let top = placement === "right" ? anchor.top : flip === "up" ? anchor.top - gap - usedHeight : anchor.bottom + gap
  top = Math.max(pad, Math.min(top, viewportHeight - pad - usedHeight))
  return { top, left: clampX(left), maxWidth, maxHeight: Math.max(0, Math.min(available, viewportHeight - pad - top)), side, flip }
}

/** Reserve horizontal room for a possible child before opening it. The root's
 * viewport rectangle depends only on its own content, never on the child. */
export function placeConnectedMenu({ anchor, rootWidth, rootHeight, childWidth, childHeight,
  viewportWidth, viewportHeight, align = "start",
}: {
  anchor: MenuAnchor; rootWidth: number; rootHeight: number; childWidth: number; childHeight: number | null
  viewportWidth: number; viewportHeight: number; align?: "start" | "center" | "end"
}) {
  const padding = 10, gap = 8
  const maxWidth = Math.max(0, viewportWidth - 24)
  const scale = Math.min(1, Math.max(0, maxWidth - padding * 2 - gap) / (rootWidth + childWidth))
  const rootW = rootWidth * scale, childW = childWidth * scale
  const widthWithChild = rootW + childW + gap + padding * 2
  const position = placeFloatingMenu({
    anchor, width: widthWithChild, height: rootHeight + padding * 2,
    rootWidth: rootW + padding * 2, viewportWidth, viewportHeight, align,
  })
  const rootH = Math.min(rootHeight, Math.max(0, position.maxHeight - padding * 2))
  const rootTop = position.top + padding
  const rootLeft = position.left + padding + (position.side === "left" ? childW + gap : 0)
  const hasChild = childHeight != null
  const childH = hasChild ? Math.max(rootH, Math.min(childHeight, Math.max(0, position.maxHeight - padding * 2))) : 0
  // A drop-up child grows upward from the root's bottom; a drop-down child
  // grows downward from its top. Neither can stretch or displace the root.
  const childTop = position.flip === "up" ? rootTop + rootH - childH : rootTop
  const top = (hasChild ? Math.min(rootTop, childTop) : rootTop) - padding
  const left = hasChild ? position.left : rootLeft - padding
  const height = (hasChild ? Math.max(rootH, childH) : rootH) + padding * 2
  return {
    ...position, top, left, height,
    width: hasChild ? widthWithChild : rootW + padding * 2,
    root: { top: rootTop - top, left: rootLeft - left, width: rootW, height: rootH },
    child: hasChild ? {
      top: childTop - top, left: position.side === "left" ? padding : padding + rootW + gap,
      width: childW, height: childH,
    } : null,
  }
}
