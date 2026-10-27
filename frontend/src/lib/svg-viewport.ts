export interface Size {
  width: number
  height: number
}

/** Translation uses viewport pixels; scale 1 is the fitted image size. */
export interface SvgViewport {
  scale: number
  x: number
  y: number
}

const MIN_SCALE = 0.25
const MAX_SCALE = 8

const positive = (value: number) => Number.isFinite(value) && value > 0
const finite = (value: number) => Number.isFinite(value) ? value : 0
const clampScale = (scale: number) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale))

/** Keep the inline artboard comfortable even for very tall/wide diagrams. */
export function inlineSvgAspectRatio(image: Size): number {
  if (!positive(image.width) || !positive(image.height)) return 1.6
  return Math.min(16 / 9, Math.max(4 / 3, image.width / image.height))
}

/** Fill the available viewport without cropping the vector image. */
export function fitSvgSize(image: Size, viewport: Size): Size {
  if (![image.width, image.height, viewport.width, viewport.height].every(positive)) {
    return { width: 0, height: 0 }
  }
  const scale = Math.min(viewport.width / image.width, viewport.height / image.height)
  return { width: image.width * scale, height: image.height * scale }
}

export function createSvgViewport(image: Size, viewport: Size): SvgViewport {
  const fitted = fitSvgSize(image, viewport)
  return {
    scale: 1,
    x: ((positive(viewport.width) ? viewport.width : 0) - fitted.width) / 2,
    y: ((positive(viewport.height) ? viewport.height : 0) - fitted.height) / 2,
  }
}

/** Use transform-origin: top left and translate(x, y) scale(scale). */
export function zoomSvgAt(
  view: SvgViewport,
  targetScale: number,
  anchor: { x: number; y: number },
): SvgViewport {
  const currentScale = positive(view.scale) ? view.scale : 1
  const scale = clampScale(Number.isFinite(targetScale) ? targetScale : currentScale)
  const x = finite(view.x)
  const y = finite(view.y)
  if (scale === currentScale) return { scale, x, y }
  const ratio = scale / currentScale
  const nextX = finite(anchor.x) - (finite(anchor.x) - x) * ratio
  const nextY = finite(anchor.y) - (finite(anchor.y) - y) * ratio
  return {
    scale,
    x: Number.isFinite(nextX) ? nextX : x,
    y: Number.isFinite(nextY) ? nextY : y,
  }
}

/** Keep smaller axes centered and larger axes covering the viewport. */
export function constrainSvgViewport(view: SvgViewport, fitted: Size, viewport: Size): SvgViewport {
  const sanitized = {
    scale: clampScale(positive(view.scale) ? view.scale : 1),
    x: finite(view.x),
    y: finite(view.y),
  }
  const width = fitted.width * sanitized.scale
  const height = fitted.height * sanitized.scale
  if (![fitted.width, fitted.height, viewport.width, viewport.height, width, height].every(positive)) {
    return sanitized
  }
  const constrainAxis = (position: number, imageSize: number, viewportSize: number) =>
    imageSize <= viewportSize
      ? (viewportSize - imageSize) / 2
      : Math.min(0, Math.max(viewportSize - imageSize, position))
  return {
    scale: sanitized.scale,
    x: constrainAxis(sanitized.x, width, viewport.width),
    y: constrainAxis(sanitized.y, height, viewport.height),
  }
}
