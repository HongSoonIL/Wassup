export type Quote = { status: 'available'; id: number; version: number; caption: string; photo_url: string; photo_alt: string } | { status: 'unavailable' }
export type Profile = {
  id: number; room_id: number; name: string; profile_image: string | null
  status_message: string; current_activity: string | null; next_cursor: string | null
  posts: {id: number; version: number; caption: string; published_at: string; photo: {url:string; alt:string; ai_generated:boolean}}[]
}

export type Message = {
  id: number
  text: string
  sender: 'user' | 'character'
  kind: 'text' | 'photo'
  status: 'sent' | 'read'
  created_at: string
  read_at: string | null
  source_post?: Quote | null
  photo?: string
}
export type Friend = {
  room_id: number
  character: { id: number; name: string; profile_image: string | null; status_message: string }
  last_message: Message | null
  unread_count: number
}
export type History = { messages: Message[]; has_more: boolean; typing_until?: string | null; current_activity?: string | null }

export function returnToLogin() {
  const next = window.location.pathname + window.location.search
  window.location.replace('/login/?next=' + encodeURIComponent(next))
}

export class RequestError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { cache: 'no-store', ...init })
  if (res.status === 401) returnToLogin()
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new RequestError(res.status, body?.detail || '연결을 확인하고 다시 시도해 주세요.')
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

export function getProfile(characterId: number, cursor?: string) {
  return request<Profile>('/api/characters/' + characterId + '/profile/' + (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''))
}

export function markRead(room: number, through: number) {
  const csrf = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] || ''
  return request<{ unread_count: number }>(`/api/rooms/${room}/read/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
    body: JSON.stringify({ through_id: through }),
  })
}

export async function sendMessage(room: number, text: string, quote: Quote | null = null): Promise<Message> {
  const csrf = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1]
  if (!csrf) throw new Error('세션을 확인한 뒤 새로고침해 주세요.')
  const receipt = await request<Pick<Message, 'id' | 'status' | 'created_at'>>(
    '/api/rooms/' + room + '/messages/',
    { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf }, body: JSON.stringify(quote?.status === 'available' ? { text, source_post_id: quote.id } : { text }) },
  )
  return { ...receipt, sender: 'user', kind: 'text', text, source_post: quote, read_at: null }
}

export function mergeMessages(previous: Message[], incoming: Message[]) {
  const byId = new Map(previous.map(message => [message.id, message]))
  for (const message of incoming) {
    const old = byId.get(message.id)
    byId.set(message.id, old?.read_at && !message.read_at ? { ...message, status: old.status, read_at: old.read_at } : message)
  }
  return [...byId.values()].sort((a, b) => a.id - b.id)
}
