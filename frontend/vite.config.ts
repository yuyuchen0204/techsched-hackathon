import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

export default defineConfig(({ command }) => ({
  // local `vite dev` keeps serving at the root (matches scripts/dev.sh, README, e2e scripts);
  // `vite build` (production deploy) prefixes everything for the /techsched subpath.
  base: command === 'build' ? '/techsched/' : '/',
  plugins: [react(), tailwindcss()],
  server: {
    host: '127.0.0.1',
    port: 5174,
    proxy: {
      '/api': 'http://127.0.0.1:8100',
      '/health': 'http://127.0.0.1:8100',
    },
  },
}))
