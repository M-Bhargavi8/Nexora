/**
 * Typed client for the NEXORA backend.
 *
 * Every network call goes through here rather than calling `fetch` inside
 * components. That gives one place to add authentication headers (Phase 2),
 * error handling, and timeouts -- instead of forty places to forget them.
 */

/** The API base URL comes from the environment, never hard-coded. */
const API_BASE_URL: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
const API_PREFIX = '/api/v1'

/** Requests are abandoned after this long so the UI can never hang forever. */
const DEFAULT_TIMEOUT_MS = 10_000

// --- Response types (mirror the backend's Pydantic schemas) ---------------

export interface HealthResponse {
  status: 'ok'
  app: string
  version: string
  environment: string
}

export interface DependencyCheck {
  name: string
  healthy: boolean
  detail: string | null
}

export interface ReadinessResponse {
  status: 'ready' | 'degraded'
  checks: DependencyCheck[]
}

/** An API call that failed in a way the UI should explain to the user. */
export class ApiError extends Error {
  // Declared and assigned explicitly rather than as a constructor parameter
  // property (`constructor(readonly status?: number)`). This project enables
  // TypeScript's `erasableSyntaxOnly`, which only permits type syntax that
  // can be stripped away 1:1 to produce JavaScript -- and parameter
  // properties emit real assignment code, so they are not allowed.
  readonly status?: number

  constructor(message: string, status?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/**
 * Perform a request and return the parsed body along with the HTTP status.
 *
 * The status is returned rather than thrown on, because some endpoints use a
 * non-2xx status *and* a meaningful body -- `/health/ready` answers 503 with
 * a full report of which dependency is down. Throwing there would discard
 * exactly the information we want to display.
 */
async function request<T>(
  path: string,
  { timeoutMs = DEFAULT_TIMEOUT_MS }: { timeoutMs?: number } = {},
): Promise<{ data: T; status: number }> {
  // AbortController is how a fetch is cancelled. Without it a hung backend
  // leaves the UI spinning indefinitely.
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(`${API_BASE_URL}${API_PREFIX}${path}`, {
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    })

    let data: T
    try {
      data = (await response.json()) as T
    } catch {
      throw new ApiError(
        `Server returned a non-JSON response (HTTP ${response.status})`,
        response.status,
      )
    }

    return { data, status: response.status }
  } catch (error) {
    if (error instanceof ApiError) throw error

    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(`Request timed out after ${timeoutMs / 1000}s`)
    }

    // A TypeError from fetch means the request never completed: the backend
    // is down, the URL is wrong, or CORS blocked it.
    throw new ApiError(
      `Cannot reach the API at ${API_BASE_URL}. Is the backend running?`,
    )
  } finally {
    clearTimeout(timeout)
  }
}

/** Liveness: is the API process up? */
export async function getHealth(): Promise<HealthResponse> {
  const { data } = await request<HealthResponse>('/health')
  return data
}

/**
 * Readiness: are the API's dependencies reachable?
 *
 * Returns the report for both 200 and 503, since "degraded" is a valid
 * answer we want to render rather than an error to swallow.
 */
export async function getReadiness(): Promise<ReadinessResponse> {
  const { data } = await request<ReadinessResponse>('/health/ready', {
    // The backend bounds its own database probe at ~3s; allow headroom.
    timeoutMs: 8_000,
  })
  return data
}

export { API_BASE_URL }
