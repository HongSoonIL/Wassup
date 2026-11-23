export type Message = {
  id: number
  text: string
  sender: 'user' | 'character'
  kind: 'text' | 'photo'
  status: 'sent' | 'read'
  created_at: string
  read_at: string | null
  photo?: string
}
export type Friend = {
  room_id: number
  character: { id: number; name: string; profile_image: string | null; status_message: string }
  last_message: Message | null
  unread_count: number
}
export type History = { messages: Message[]; has_more: boolean }

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { cache: 'no-store', ...init })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || '연결을 확인하고 다시 시도해 주세요.')
  }
  return res.json()
}

export function getFriends() {
  return request<{ friends: Friend[] }>('/api/friends/')
}

export function getMessages(room: number, before?: number) {
  const query = before ? '?before=' + before : ''
  return request<History>('/api/rooms/' + room + '/messages/' + query)
}

export async function sendMessage(room: number, text: string): Promise<Message> {
  const csrf = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1]
  if (!csrf) throw new Error('세션을 확인한 뒤 새로고침해 주세요.')
  const receipt = await request<Pick<Message, 'id' | 'status' | 'created_at'>>(
    '/api/rooms/' + room + '/messages/',
    { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf }, body: JSON.stringify({ text }) },
  )
  return { ...receipt, sender: 'user', kind: 'text', text, read_at: null }
}

export function mergeMessages(previous: Message[], incoming: Message[]) {
  const byId = new Map(previous.map(message => [message.id, message]))
  for (const message of incoming) {
    const old = byId.get(message.id)
    byId.set(message.id, old?.read_at && !message.read_at ? old : message)
  }
  return [...byId.values()].sort((a, b) => a.id - b.id)
}
