import type { Message } from './chat'

type RoomEvent =
  | { type: 'message.read'; message_id: number; read_at: string }
  | { type: 'message.new'; message: Message }
  | { type: 'typing.start'; expires_at?: string }
  | { type: 'typing.stop' }

function parseEvent(data: string): RoomEvent | undefined {
  try {
    const event = JSON.parse(data)
    switch (event?.type) {
      case 'message.read':
        if (typeof event.message_id === 'number' && typeof event.read_at === 'string') return event
        break
      case 'message.new':
        if (typeof event.message?.id === 'number') return event
        break
      case 'typing.start':
      case 'typing.stop':
        return event
    }
  } catch {
    // The REST snapshot can recover a missed or unrecognized event.
  }
}

export function subscribeToRoom(
  room: number,
  onEvent: (event: RoomEvent) => void,
  onOpen: () => void,
  onDisconnect: () => void,
) {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = protocol + '//' + location.host + '/ws/chat/' + room + '/'
  let stopped = false
  let socket: WebSocket | null = null
  let retryTimer = 0
  let retryDelay = 1000

  function connect() {
    if (stopped || socket) return
    const connection = new WebSocket(url)
    socket = connection
    connection.addEventListener('open', () => {
      if (stopped) return
      retryDelay = 1000
      onOpen()
    })
    connection.addEventListener('message', ({ data }) => {
      if (stopped) return
      const event = parseEvent(data)
      if (event) onEvent(event)
    })
    connection.addEventListener('close', () => {
      socket = null
      if (stopped) return
      onDisconnect()
      retryTimer = window.setTimeout(connect, retryDelay)
      retryDelay = Math.min(15_000, retryDelay * 2)
    })
  }

  function resume() {
    clearTimeout(retryTimer)
    connect()
  }

  connect()
  window.addEventListener('online', resume)
  return () => {
    stopped = true
    clearTimeout(retryTimer)
    socket?.close()
    window.removeEventListener('online', resume)
  }
}
