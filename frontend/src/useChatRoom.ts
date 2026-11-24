import { useCallback, useEffect, useRef, useState } from 'react'
import { getMessages, mergeMessages, RequestError, sendMessage } from './chat'
import type { Message, Quote } from './chat'
import { subscribeToRoom } from './live'
import { sampleMessages } from './preview'

type Arrival = (ids: number[], animate: boolean) => void

export default function useChatRoom(room: number, demo: boolean, onArrival: Arrival, onMessage: (room: number, message: Message) => void) {
  const [messages, setMessages] = useState<Message[]>(demo ? sampleMessages : [])
  const [ready, setReady] = useState(demo)
  const [more, setMore] = useState(false)
  const [activity, setActivity] = useState<string | null>(null)
  const [typing, setTyping] = useState(false)
  const [loadError, setLoadError] = useState('')
  const known = useRef(new Set(demo ? sampleMessages.map(message => message.id) : []))
  const photosToRefresh = useRef(new Set<number>())
  const newest = useRef(messages.at(-1)?.id ?? 0)
  const pendingReads = useRef(new Map<number, string>())
  const demoTimers = useRef<number[]>([])
  const demoId = useRef(10)
  const demoPhotos = useRef<string[]>([])
  useEffect(() => () => {
    demoPhotos.current.forEach(url => URL.revokeObjectURL(url))
  }, [])

  const receive = useCallback((batch: Message[], source: 'history' | 'snapshot' | 'delivery' | 'receipt') => {
    const arrivals: number[] = []
    const incoming: Message[] = []
    for (const message of batch) {
      const unseen = !known.current.has(message.id)
      // The send receipt has the locally selected quote. A snapshot may have already checked its availability.
      if (!unseen && source === 'receipt') continue
      if (unseen && message.sender === 'character' && (source === 'delivery' || (source === 'snapshot' && message.id > newest.current))) {
        arrivals.push(message.id)
      }
      known.current.add(message.id)
      newest.current = Math.max(newest.current, message.id)
      if (message.attachment?.status === 'available' || message.source_post?.status === 'available') {
        photosToRefresh.current.add(message.id)
      } else photosToRefresh.current.delete(message.id)
      const readAt = pendingReads.current.get(message.id)
      pendingReads.current.delete(message.id)
      incoming.push(readAt ? { ...message, read_at: readAt, status: 'read' } : message)
    }
    if (arrivals.length) onArrival(arrivals, source === 'delivery')
    if (incoming.length) setMessages(old => mergeMessages(old, incoming))
  }, [onArrival])

  useEffect(() => {
    if (demo) return () => demoTimers.current.forEach(clearTimeout)
    let active = true
    let first = true
    let syncing = false
    let needsSync = false
    let typingTimer = 0
    let typingRevision = 0
    let connection = 0
    let synchronized = false

    function stopTyping() {
      clearTimeout(typingTimer)
      setTyping(false)
    }
    function showTypingUntil(expires?: string | null) {
      clearTimeout(typingTimer)
      const remaining = expires ? Date.parse(expires) - Date.now() : 0
      setTyping(remaining > 0)
      if (remaining > 0) typingTimer = window.setTimeout(stopTyping, remaining)
    }
    async function sync() {
      if (document.hidden || syncing) return
      syncing = true
      const revision = typingRevision
      const epoch = connection
      const through = newest.current
      const historical = new Set([...photosToRefresh.current].sort((a, b) => b - a))
      try {
        const history = await getMessages(room)
        let page = history
        let batch = history.messages
        while (active && !first && through > 0 && page.has_more && page.messages[0]?.id > through) {
          page = await getMessages(room, page.messages[0].id)
          batch = [...page.messages, ...batch]
        }
        if (!active) return
        setActivity(history.current_activity || null)
        if (revision === typingRevision) showTypingUntil(history.typing_until)
        receive(batch, first ? 'history' : 'snapshot')
        if (first || !through) setMore(history.has_more)
        first = false
        if (epoch === connection) synchronized = true
        setReady(true)
        setLoadError('')
        // The latest page cannot confirm the availability of photos already loaded further up the thread.
        // Start a page at each remaining photo; that page can refresh multiple loaded photos together.
        for (const message of batch) historical.delete(message.id)
        for (const id of historical) {
          if (!active || document.hidden) break
          const older = await getMessages(room, id + 1)
          if (!active) return
          receive(older.messages.filter(message => historical.delete(message.id)), 'history')
        }
      } catch (error) {
        if (!active) return
        if (error instanceof RequestError && error.status === 404) {
          setReady(false)
          window.location.replace('/home/')
        } else {
          setLoadError((error as Error).message)
        }
      } finally {
        syncing = false
        if (active && needsSync) {
          needsSync = false
          void sync()
        }
      }
    }
    function resync() {
      if (syncing) needsSync = true
      else void sync()
    }
    function resetConnection() {
      connection++
      typingRevision++
      synchronized = false
      stopTyping()
    }
    const disconnect = subscribeToRoom(room, event => {
      switch (event.type) {
        case 'message.read':
          if (!known.current.has(event.message_id)) pendingReads.current.set(event.message_id, event.read_at)
          setMessages(old => old.map(message => message.id === event.message_id
            ? { ...message, status: 'read', read_at: event.read_at } : message))
          break
        case 'message.new':
          // Typing belongs to the server's start/stop events. A repeated delivery cannot stop a later bubble.
          if (!known.current.has(event.message.id)) {
            receive([event.message], synchronized ? 'delivery' : first ? 'history' : 'snapshot')
            onMessage(room, event.message)
          }
          break
        case 'typing.start':
          typingRevision++
          if (event.expires_at) showTypingUntil(event.expires_at)
          else {
            clearTimeout(typingTimer)
            setTyping(true)
          }
          break
        case 'typing.stop':
          typingRevision++
          stopTyping()
          break
      }
    }, () => { resetConnection(); resync() }, resetConnection)
    void sync()
    const timer = window.setInterval(sync, 3000)
    const visibility = () => {
      if (document.hidden) {
        connection++
        synchronized = false
      }
      else resync()
    }
    const restored = (event: PageTransitionEvent) => { if (event.persisted) resync() }
    document.addEventListener('visibilitychange', visibility)
    window.addEventListener('pageshow', restored)
    window.addEventListener('online', resync)
    return () => {
      active = false
      clearInterval(timer)
      clearTimeout(typingTimer)
      disconnect()
      document.removeEventListener('visibilitychange', visibility)
      window.removeEventListener('pageshow', restored)
      window.removeEventListener('online', resync)
    }
  }, [room, demo, receive, onMessage])

  async function loadOlder(before: number, capturePosition: () => void) {
    const page = await getMessages(room, before)
    capturePosition()
    setMore(page.has_more)
    receive(page.messages, 'history')
  }

  async function send(text: string, quote: Quote | null, key: string, photo?: File) {
    const sent: Message = demo ? { id: demoId.current++, text, created_at: new Date().toISOString(),
      sender: 'user', kind: photo ? 'photo' : 'text', status: 'sent', read_at: null, source_post: quote }
      : await sendMessage(room, text, quote, key, photo)
    if (demo && photo) {
      sent.photo = URL.createObjectURL(photo)
      demoPhotos.current.push(sent.photo)
    }
    receive([sent], 'receipt')
    onMessage(room, sent)
    if (!demo) return
    demoTimers.current.forEach(clearTimeout)
    setTyping(false)
    demoTimers.current = [
      window.setTimeout(() => {
        const readAt = new Date().toISOString()
        setMessages(old => old.map(m => m.sender === 'user' && !m.read_at ? { ...m, status: 'read', read_at: readAt } : m))
        setTyping(true)
      }, 1000),
      window.setTimeout(() => {
        setTyping(false)
        const reply: Message = { id: demoId.current++, sender: 'character', kind: 'text', text: '지금 잠깐 쉬는 중 ㅋㅋ',
          created_at: new Date().toISOString(), source_post: null, read_at: null, status: 'sent' }
        receive([reply], 'delivery')
        onMessage(room, reply)
      }, 2600),
    ]
  }
  return { messages, ready, more, activity, typing, loadError, loadOlder, send }
}
