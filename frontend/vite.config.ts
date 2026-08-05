import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import path from "path"
import fileViewerRenderers from "@file-viewer/vite-plugin"

const API_PORT = process.env.API_PORT || "18900"
const UI_PORT = Number(process.env.UI_PORT) || 5173
// Prefer 127.0.0.1 over localhost — on macOS localhost often resolves to ::1,
// which can hit a Docker-published IPv6 listener instead of local uvicorn.
const API_TARGET = `http://127.0.0.1:${API_PORT}`

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    // Pre-copy Worker/WASM/fonts into public + dist so Docker install ships engines
    // (no first-open CDN fetch). Formats: Office + PDF + md/txt.
    fileViewerRenderers({
      formats: [
        "pdf",
        "docx",
        "doc",
        "xlsx",
        "xls",
        "csv",
        "pptx",
        "ppt",
        "md",
        "markdown",
        "txt",
      ],
      // Prefer install-time assets under /file-viewer/
      copyAssets: {
        mode: "both",
        baseDir: "file-viewer",
      },
      // Keep renderer chunks local to the build (still same-origin static files)
      chunkStrategy: "renderer",
      inject: true,
      autoPresets: false,
    }),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: UI_PORT,
    proxy: {
      // SSE: avoid proxy buffering so chat tokens flush incrementally
      "/api/logs/stream": {
        target: API_TARGET,
        changeOrigin: true,
        timeout: 0,
        proxyTimeout: 0,
      },
      "/api/sessions": {
        target: API_TARGET,
        changeOrigin: true,
        timeout: 0,
        proxyTimeout: 0,
      },
      // Catch-all API proxy. ws:true is required so meeting realtime-transcribe
      // (and any other WebSocket under /api) is upgraded; without it Vite ignores
      // the upgrade and the browser sees close 1006 / "closed before established".
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
        ws: true,
        // Realtime ASR + SSE can stay open for a long time
        timeout: 0,
        proxyTimeout: 0,
      },
      "/health": API_TARGET,
    },
  },
})
