/// <reference types="vite/client" />

/**
 * Types for our environment variables.
 *
 * Only variables prefixed with `VITE_` are exposed to the browser bundle.
 *
 * SECURITY: anything here is compiled into the JavaScript that ships to the
 * user and is fully readable by anyone who opens devtools. Never put an API
 * key, database URL, or any other secret in a VITE_ variable. Secrets belong
 * in the backend's `.env`, which never leaves the server.
 */
interface ImportMetaEnv {
  /** Base URL of the NEXORA backend, e.g. http://localhost:8000 */
  readonly VITE_API_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
