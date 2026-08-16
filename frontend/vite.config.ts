import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Tailwind v4 is a Vite plugin -- there is no tailwind.config.js and no
// PostCSS configuration. Design tokens live in src/index.css instead.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true, // fail loudly rather than silently moving to 5174,
                      // which would break the backend's CORS allow-list
  },
})
