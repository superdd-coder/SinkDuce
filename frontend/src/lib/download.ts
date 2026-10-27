/** Keep the object URL alive until the browser has begun reading the download. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  try {
    anchor.href = url
    anchor.download = filename.replace(/[\x00-\x1f\x7f\\/]/g, '_')
    document.body.appendChild(anchor)
    anchor.click()
  } finally {
    anchor.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
}
