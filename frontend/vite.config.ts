// defineConfig comes from vitest/config rather than vite, because that is the
// version whose type includes the `test` block below. It is a superset -- the
// vite options behave identically.
import { defineConfig } from 'vitest/config'
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
  // Vitest reuses this same config, so tests get the identical TypeScript and
  // JSX handling as the app. One pipeline, no second toolchain to keep in step.
  test: {
    // A DOM implementation in Node, so components can be rendered and queried
    // without a browser.
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // Tailwind produces no assertions and costs real time to process.
    css: false,
    // Reset spies between tests, so one test's mocked return value cannot
    // silently satisfy the next one's assertion.
    restoreMocks: true,
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
