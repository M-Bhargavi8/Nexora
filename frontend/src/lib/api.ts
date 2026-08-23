/**
 * Typed client for the NEXORA backend.
 *
 * Every network call goes through here rather than calling `fetch` inside
 * components. That gives one place to add error handling, timeouts, and
 * credential handling -- instead of forty places to forget them.
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

export interface User {
  id: string
  email: string
  is_active: boolean
  created_at: string
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
 * Turn a FastAPI error body into one readable sentence.
 *
 * FastAPI answers in two different shapes: `{detail: "message"}` for a raised
 * HTTPException, and `{detail: [{loc, msg, ...}]}` for a validation failure.
 * Rendering the second shape naively puts `[object Object]` in front of the
 * user, so both are handled here.
 */
function extractErrorMessage(body: unknown, status: number): string {
  if (typeof body === 'object' && body !== null && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail

    if (typeof detail === 'string') return detail

    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string }
      if (typeof first?.msg === 'string') {
        // Pydantic prefixes messages with "Value error, "; drop it.
        return first.msg.replace(/^Value error,\s*/, '')
      }
    }
  }
  return `Request failed (HTTP ${status})`
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  body?: unknown
  timeoutMs?: number
  /** Status codes to return normally instead of throwing. */
  allowStatuses?: number[]
}

async function request<T>(
  path: string,
  {
    method = 'GET',
    body,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    allowStatuses = [],
  }: RequestOptions = {},
): Promise<{ data: T; status: number }> {
  // AbortController is how a fetch is cancelled. Without it a hung backend
  // leaves the UI spinning indefinitely.
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(`${API_BASE_URL}${API_PREFIX}${path}`, {
      method,
      headers: {
        Accept: 'application/json',
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      // CRITICAL for cookie authentication.
      //
      // The session cookie is httpOnly, so JavaScript cannot read or attach
      // it. `credentials: 'include'` is what tells the browser to send it on
      // cross-origin requests -- and to store the Set-Cookie that comes back.
      //
      // Omit this and login appears to succeed, the cookie is silently
      // discarded, and every subsequent request is 401. It is the single
      // most common bug in cookie-based auth setups, and it produces no
      // error message of its own.
      credentials: 'include',
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

    if (!response.ok && !allowStatuses.includes(response.status)) {
      throw new ApiError(extractErrorMessage(data, response.status), response.status)
    }

    return { data, status: response.status }
  } catch (error) {
    if (error instanceof ApiError) throw error

    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(`Request timed out after ${timeoutMs / 1000}s`)
    }

    // A TypeError from fetch means the request never completed: the backend
    // is down, the URL is wrong, or CORS blocked it.
    throw new ApiError(`Cannot reach the API at ${API_BASE_URL}. Is the backend running?`)
  } finally {
    clearTimeout(timeout)
  }
}

// --- Health ---------------------------------------------------------------

/** Liveness: is the API process up? */
export async function getHealth(): Promise<HealthResponse> {
  const { data } = await request<HealthResponse>('/health')
  return data
}

/**
 * Readiness: are the API's dependencies reachable?
 *
 * 503 is allowed through rather than thrown, because "degraded" is a valid
 * answer we want to render -- not an error to swallow.
 */
export async function getReadiness(): Promise<ReadinessResponse> {
  const { data } = await request<ReadinessResponse>('/health/ready', {
    // The backend bounds its own database probe at ~3s; allow headroom.
    timeoutMs: 8_000,
    allowStatuses: [503],
  })
  return data
}

// --- Authentication -------------------------------------------------------

export async function signup(email: string, password: string): Promise<User> {
  const { data } = await request<User>('/auth/signup', {
    method: 'POST',
    body: { email, password },
  })
  return data
}

export async function login(email: string, password: string): Promise<User> {
  const { data } = await request<User>('/auth/login', {
    method: 'POST',
    body: { email, password },
  })
  return data
}

export async function logout(): Promise<void> {
  await request<{ message: string }>('/auth/logout', { method: 'POST' })
}

/**
 * Who is signed in, or null.
 *
 * 401 is an expected answer here -- it simply means "nobody" -- so it is
 * returned as null rather than thrown. The frontend cannot read the httpOnly
 * session cookie itself, so asking the server is the only way to find out
 * whether the cookie it holds is still valid.
 */
export async function getCurrentUser(): Promise<User | null> {
  const { data, status } = await request<User>('/auth/me', {
    allowStatuses: [401],
  })
  return status === 401 ? null : data
}

export { API_BASE_URL, API_PREFIX }
