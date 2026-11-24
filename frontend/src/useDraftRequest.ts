import { useLayoutEffect, useRef } from 'react'
import type { Quote } from './chat'

function newRequestId() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  // randomUUID requires HTTPS. getRandomValues also works on a local HTTP preview.
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64
  bytes[8] = (bytes[8] & 63) | 128
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-')
}

export function useDraftRequest(text: string, photo?: File, source?: Quote | null) {
  const id = useRef<string | null>(null)
  useLayoutEffect(() => { id.current = null }, [text, photo, source])
  return {
    key: () => id.current ??= newRequestId(),
    complete: () => { id.current = null },
  }
}
