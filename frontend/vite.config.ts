import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const frontendRoot = fileURLToPath(new URL('.', import.meta.url))
const proxyTarget = process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  resolve: {
    // ui/security-map is consumed as source; keep one React and one React Flow
    // instance even when its own node_modules is installed.
    dedupe: ['react', 'react-dom', '@xyflow/react'],
  },
  server: {
    port: 3000,
    fs: {
      // Production map sources live in the sibling ui/security-map package.
      allow: [frontendRoot, fileURLToPath(new URL('..', import.meta.url))],
    },
    proxy: {
      '/api': {
        target: proxyTarget,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
            // Backend checks Origin/Host for the public demo; make dev proxying
            // look like a direct same-origin request to the backend.
            proxyReq.setHeader('origin', proxyTarget)
            proxyReq.setHeader('referer', `${proxyTarget}/`)
          })
        },
      },
    },
  },
})
