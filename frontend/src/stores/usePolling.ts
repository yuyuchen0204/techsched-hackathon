import { useCallback, useEffect, useRef, useState } from 'react'

export function usePolling<T>(fn: () => Promise<T>, intervalMs: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const alive = useRef(true)
  const tick = useCallback(async () => {
    try {
      const d = await fn()
      if (alive.current) { setData(d); setError(null) }
    } catch (e) {
      if (alive.current) setError((e as Error).message)
    } finally {
      if (alive.current) setLoading(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  useEffect(() => {
    alive.current = true
    tick()
    const id = setInterval(tick, intervalMs)
    return () => { alive.current = false; clearInterval(id) }
  }, [tick, intervalMs])
  return { data, error, loading, refresh: tick }
}
