import { useCallback, useEffect, useState } from 'react'

export function usePageData<T>(load: () => Promise<T>, { pollMs, enabled = true }: {
  pollMs?: number; enabled?: boolean
} = {}) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const retry = useCallback(() => setAttempt(previous => previous + 1), [])

  useEffect(() => {
    if (!enabled) return
    let active = true
    let loading = false

    async function refresh() {
      if (document.hidden || loading) return
      loading = true
      try {
        const next = await load()
        if (active) {
          setData(next)
          setError('')
        }
      } catch (error) {
        if (active) setError((error as Error).message)
      } finally {
        loading = false
      }
    }

    void refresh()
    const restored = (event: PageTransitionEvent) => { if (event.persisted) void refresh() }
    window.addEventListener('pageshow', restored)
    window.addEventListener('focus', refresh)
    window.addEventListener('online', refresh)
    document.addEventListener('visibilitychange', refresh)
    const timer = pollMs === undefined ? undefined : window.setInterval(refresh, pollMs)
    return () => {
      active = false
      window.removeEventListener('pageshow', restored)
      window.removeEventListener('focus', refresh)
      window.removeEventListener('online', refresh)
      document.removeEventListener('visibilitychange', refresh)
      window.clearInterval(timer)
    }
  }, [load, pollMs, enabled, attempt])

  return { data, error, retry }
}
