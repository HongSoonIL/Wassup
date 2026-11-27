type QuoteContent = { status: 'available'; id: number; version: number; caption: string; photo_alt: string }
export type Quote = (QuoteContent & ({ kind?: 'character'; photo_url: string } | { kind: 'personal'; photo_url: string | null }))
  | { status: 'unavailable'; kind?: 'personal' | 'character' }
export type PostSummary = {
  id: number
  version: number
  caption: string
  published_at: string
  photo: { url: string; alt: string }
}
export type ProfilePhoto = { id: number; url: string; set_at: string; ai_generated: boolean; current: boolean }
export type Profile = {
  id: number; room_id: number; name: string; profile_image: string | null
  profile_photos?: ProfilePhoto[]
  status_message: string; current_activity: string | null; next_cursor: string | null
  posts: (PostSummary & { photo: { ai_generated: boolean }; previous_post?: PostSummary | null })[]
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
  attachment?: { status: 'available'; id?: number; version?: number; photo_url: string; photo_alt: string } | { status: 'unavailable' } | null
  photo?: string
}

export function messagePhoto(message: Message) {
  const uploadKey = `upload:${message.id}`
  const attachment = message.attachment
  if (message.kind === 'photo' && attachment?.status === 'available' && attachment.photo_url) {
    return { src: attachment.photo_url, alt: attachment.photo_alt,
      viewerKey: message.sender === 'user' ? uploadKey : `attachment:${message.id}:${attachment.id}:${attachment.version}` }
  }
  if (attachment === undefined && message.photo) {
    return { src: message.photo, alt: message.text || (message.sender === 'user' ? '내가 보낸 사진' : '대화에서 받은 사진'),
      viewerKey: message.sender === 'user' ? uploadKey : `preview:${message.id}` }
  }
}

export function messagePreview(message: Message | null | undefined, empty: string) {
  if (!message) return empty
  const photo = messagePhoto(message)
  if (message.kind !== 'photo' && !photo) return message.text || empty
  const label = photo ? '사진' : '볼 수 없는 사진'
  return message.text ? `${label} · ${message.text}` : label
}
export type Friend = {
  room_id: number
  character: { id: number; name: string; profile_image: string | null; status_message: string }
  last_message: Message | null
  unread_count: number
}
export type History = { messages: Message[]; has_more: boolean; typing_until?: string | null; current_activity?: string | null }
export type FriendSettings = { proactive_enabled: boolean }

export function returnToLogin() {
  const next = window.location.pathname + window.location.search
  window.location.replace('/login/?next=' + encodeURIComponent(next))
}

export class RequestError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

export async function apiResponse(url: string, init?: RequestInit) {
  let res: Response
  try { res = await fetch(url, { cache: 'no-store', ...init }) }
  catch (error) {
    if (error instanceof TypeError) throw new Error('연결을 확인하고 다시 시도해 주세요.')
    throw error
  }
  if (res.status === 401) returnToLogin()
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new RequestError(res.status, body?.detail || '연결을 확인하고 다시 시도해 주세요.')
  }
  return res
}

export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  return (await apiResponse(url, init)).json()
}

export function csrfToken() {
  return document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] || ''
}

function friendUrl(room: number) {
  return `/api/friends/${room}/`
}

export function getFriendSettings(room: number) {
  return request<FriendSettings>(friendUrl(room))
}

export function updateFriendSettings(room: number, settings: FriendSettings) {
  return request<FriendSettings>(friendUrl(room), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
    body: JSON.stringify(settings),
  })
}

export async function deleteFriend(room: number) {
  await apiResponse(friendUrl(room), {
    method: 'DELETE',
    headers: { 'X-CSRFToken': csrfToken() },
  })
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

export function getPreviousPost(id: number, version: number, signal: AbortSignal) {
  return request<{ previous_post: PostSummary | null }>(`/api/posts/${id}/previous/?version=${version}`, { signal })
}

export function markRead(room: number, through: number) {
  const csrf = csrfToken()
  return request<{ unread_count: number }>(`/api/rooms/${room}/read/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
    body: JSON.stringify({ through_id: through }),
  })
}

export async function sendMessage(room: number, text: string, quote: Quote | null, key: string, photo?: File): Promise<Message> {
  const csrf = csrfToken()
  if (!csrf) throw new Error('세션을 확인한 뒤 새로고침해 주세요.')
  const source = quote?.status === 'available' && quote.kind !== 'personal' ? quote.id : undefined
  let body: FormData | string
  const headers: Record<string, string> = { 'X-CSRFToken': csrf, 'Idempotency-Key': key }
  if (photo) {
    body = new FormData()
    body.set('text', text)
    body.set('photo', photo)
    if (source !== undefined) body.set('source_post_id', String(source))
  } else {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify({ text, source_post_id: source })
  }
  const receipt = await request<Pick<Message, 'id' | 'status' | 'created_at'> & Partial<Message>>(
    `/api/rooms/${room}/messages/`, { method: 'POST', headers, body })
  const attachment = photo ? { status: 'available' as const, photo_url: `/api/rooms/${room}/messages/${receipt.id}/photo/`, photo_alt: '대화에 보낸 사진' } : undefined
  return { sender: 'user', kind: photo ? 'photo' : 'text', text, source_post: quote, read_at: null, attachment, ...receipt }
}

export function mergeMessages(previous: Message[], incoming: Message[]) {
  const byId = new Map(previous.map(message => [message.id, message]))
  for (const message of incoming) {
    const old = byId.get(message.id)
    byId.set(message.id, old?.read_at && !message.read_at ? { ...message, status: old.status, read_at: old.read_at } : message)
  }
  return [...byId.values()].sort((a, b) => a.id - b.id)
}
