import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ArrowRight, MessageCircle } from 'lucide-react'
import { getFriends, messagePreview } from './chat'
import type { Friend, Message } from './chat'
import { sampleFriend } from './preview'
import { FriendIdentity } from './SocialMedia'

const timezone = 'Asia/Seoul'
const day = new Intl.DateTimeFormat('ko-KR', { timeZone: timezone, dateStyle: 'short' })
const date = new Intl.DateTimeFormat('ko-KR', { timeZone: timezone, month: 'short', day: 'numeric' })
const clock = new Intl.DateTimeFormat('ko-KR', { timeZone: timezone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })

export function useInbox(demo: boolean) {
  const [friends, setFriends] = useState<Friend[] | null>(demo ? [sampleFriend] : null)
  const [error, setError] = useState('')
  const refresh = useRef<() => void>(() => {})

  useEffect(() => {
    if (demo) return
    let active = true
    let loading = false
    let queued = false
    let revision = 0

    async function load() {
      if (!active || document.hidden) return
      if (loading) { queued = true; return }
      loading = true
      const requested = revision
      try {
        const { friends } = await getFriends()
        if (active && requested === revision) {
          setFriends(friends)
          setError('')
        }
      } catch (error) {
        if (active && requested === revision) setError((error as Error).message)
      } finally {
        loading = false
        if (queued) {
          queued = false
          void load()
        }
      }
    }

    // Discard a list response requested before a local message or read update.
    refresh.current = () => {
      revision++
      void load()
    }
    void load()
    const timer = window.setInterval(() => { if (!loading) void load() }, 3000)
    const restored = (event: PageTransitionEvent) => { if (event.persisted) void load() }
    const resume = () => { void load() }
    window.addEventListener('pageshow', restored)
    window.addEventListener('online', resume)
    document.addEventListener('visibilitychange', resume)
    return () => {
      active = false
      clearInterval(timer)
      window.removeEventListener('pageshow', restored)
      window.removeEventListener('online', resume)
      document.removeEventListener('visibilitychange', resume)
    }
  }, [demo])

  const updatePreview = useCallback((room: number, message: Message) => {
    setFriends(current => current?.map(friend => friend.room_id === room && message.id > (friend.last_message?.id ?? 0)
      ? { ...friend, last_message: message, unread_count: friend.unread_count + Number(message.sender === 'character' && !message.read_at) }
      : friend).sort((a, b) => (b.last_message?.id ?? 0) - (a.last_message?.id ?? 0)) ?? null)
    refresh.current()
  }, [])

  const updateUnread = useCallback((room: number, count: number) => {
    setFriends(current => current?.map(friend => friend.room_id === room ? { ...friend, unread_count: count } : friend) ?? null)
    refresh.current()
  }, [])

  return { friends, error, retry: () => refresh.current(), updatePreview, updateUnread }
}

export function InboxList({ friends, error, retry, room, demo = false, onDemoSelect, children }: {
  friends: Friend[] | null
  error: string
  retry: () => void
  room?: number
  demo?: boolean
  onDemoSelect?: () => void
  children?: ReactNode
}) {
  const today = day.format(new Date())
  return <aside className="side" aria-label="대화 목록">
    {demo && <span className="brand" aria-label="wassup">wassup<span className="brand-dot">.</span></span>}
    <div className="side-heading">{room === undefined ? <h1>메시지</h1> : <h2>대화</h2>}</div>
    {error && <p className="inbox-notice" role="status">{error} <button onClick={retry}>다시 시도</button></p>}
    {friends === null && !error && <p className="inbox-notice" role="status">대화 목록을 불러오는 중…</p>}
    {friends?.length === 0 && <div className="inbox-empty">
      <p>아직 대화할 친구가 없어요.</p><a href="/discover/">친구 찾기 <ArrowRight size={16} aria-hidden="true"/></a>
    </div>}
    {Boolean(friends?.length) && <nav aria-label="채팅방">{friends?.map(friend => {
      const selected = room === friend.room_id
      const last = friend.last_message
      const preview = messagePreview(last, '첫 메시지를 보내 보세요')
      const sent = last && new Date(last.created_at)
      return <a key={friend.room_id} className={'friend-row' + (selected ? ' selected' : '')}
        href={demo ? '#' : `/chat/${friend.room_id}/`} aria-current={selected ? 'page' : undefined}
        onClick={event => {
          if (demo) {
            event.preventDefault()
            onDemoSelect?.()
          } else if (selected && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
            event.preventDefault()
          }
        }}>
        <FriendIdentity person={friend.character} detail={last?.sender === 'user' ? '나 · ' + preview : preview}/>
        <span className="row-meta">
          {sent && <time dateTime={last.created_at}>{(day.format(sent) === today ? clock : date).format(sent)}</time>}
          {friend.unread_count > 0 && <span className="room-unread">
            <span aria-hidden="true">{friend.unread_count > 99 ? '99+' : friend.unread_count}</span>
            <span className="ws-sr">안 읽은 메시지 {friend.unread_count}개</span>
          </span>}
        </span>
      </a>
    })}</nav>}
    {children}
    {demo && <small className="preview-note">샘플 대화 미리보기</small>}
  </aside>
}

export function InboxPlaceholder({ hasConversations }: { hasConversations: boolean }) {
  return <div className="inbox-placeholder">{hasConversations && <>
    <MessageCircle size={30} aria-hidden="true"/>
    <p>대화 목록에서 친구를 선택해 주세요.</p>
  </>}</div>
}
