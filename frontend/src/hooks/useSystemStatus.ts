/**
 * Custom hook that loads and refreshes the backend's system status.
 *
 * Why a hook rather than fetching inside the component: it separates *how we
 * get the data* from *how we display it*. The component stays declarative,
 * and this logic can be reused or tested on its own.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ApiError,
  getHealth,
  getReadiness,
  type HealthResponse,
  type ReadinessResponse,
} from '../lib/api'

export interface SystemStatus {
  isLoading: boolean
  health: HealthResponse | null
  readiness: ReadinessResponse | null
  /** Set only when the API itself is unreachable, not when a dependency is down. */
  error: string | null
  lastCheckedAt: Date | null
  latencyMs: number | null
  refresh: () => void
}

export function useSystemStatus(): SystemStatus {
  const [isLoading, setIsLoading] = useState(true)
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastCheckedAt, setLastCheckedAt] = useState<Date | null>(null)
  const [latencyMs, setLatencyMs] = useState<number | null>(null)

  // Guards against setting state after the component unmounts, which React
  // warns about and which also matters under StrictMode's double-invoked
  // effects in development.
  const isMounted = useRef(true)

  const load = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    const startedAt = performance.now()

    // allSettled, not all: the database being down must not hide the fact
    // that the API itself answered successfully. They are separate signals.
    const [healthResult, readinessResult] = await Promise.allSettled([
      getHealth(),
      getReadiness(),
    ])

    if (!isMounted.current) return

    setLatencyMs(Math.round(performance.now() - startedAt))
    setLastCheckedAt(new Date())

    if (healthResult.status === 'fulfilled') {
      setHealth(healthResult.value)
    } else {
      setHealth(null)
      const reason = healthResult.reason
      setError(
        reason instanceof ApiError
          ? reason.message
          : 'The API is unreachable.',
      )
    }

    setReadiness(
      readinessResult.status === 'fulfilled' ? readinessResult.value : null,
    )
    setIsLoading(false)
  }, [])

  useEffect(() => {
    isMounted.current = true
    void load()
    return () => {
      isMounted.current = false
    }
  }, [load])

  const refresh = useCallback(() => {
    isMounted.current = true
    void load()
  }, [load])

  return { isLoading, health, readiness, error, lastCheckedAt, latencyMs, refresh }
}
