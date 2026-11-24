import { useCallback, useEffect, useState } from 'react'
import { ArrowRight, ArrowUpRight, ChevronRight, MessageCircle, Plus, UsersRound } from 'lucide-react'
import { getFriends, getProfile, request } from './chat'
import type { Friend, Profile } from './chat'
import FriendPortrait from './FriendPortrait'
import './people.css'

type Person = Friend['character'] & { room_id: number | null }
const discover = () => request<{ characters: Person[] }>('/api/characters/')
const date = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', month: 'short', day: 'numeric' })
const day = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: 'numeric', day: 'numeric' })
const clock = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })

function usePageData<T>(load: () => Promise<T>, refreshWhileOpen = false) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let version = 0
    async function refresh() {
      if (document.hidden) return
      const current = ++version
      try {
        const next = await load()
        if (current === version) { setData(next); setError('') }
      } catch (e) {
        if (current === version) setError((e as Error).message)
      }
    }
    void refresh()
    const restored = (event: PageTransitionEvent) => { if (event.persisted) void refresh() }
    window.addEventListener('pageshow', restored)
    document.addEventListener('visibilitychange', refresh)
    const timer = refreshWhileOpen ? window.setInterval(refresh, 15000) : undefined
    return () => {
      version++
      window.removeEventListener('pageshow', restored)
      document.removeEventListener('visibilitychange', refresh)
      window.clearInterval(timer)
    }
  }, [load, refreshWhileOpen, attempt])
  const notice = error ? <div role="alert" className="people-alert">{error} <button onClick={() => setAttempt(n => n + 1)}>다시 시도</button></div>
    : !data ? <p role="status" className="people-note">불러오는 중…</p> : null
  return { data, notice }
}

function Discover() {
  const { data, notice } = usePageData(discover)
  const [adding, setAdding] = useState<number | null>(null)
  const [error, setError] = useState('')
  async function add(person: Person) {
    setAdding(person.id)
    setError('')
    try {
      const csrf = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] || ''
      await request('/api/friends/', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
        body: JSON.stringify({ character_id: person.id }) })
      window.location.assign(`/people/${person.id}/`)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setAdding(null)
    }
  }
  return <>
    <h1>친구 찾기</h1>
    {notice}{error && <p role="alert" className="people-alert">{error}</p>}
    {data && !data.characters.length && <p className="people-empty">지금은 추가할 친구가 없어요.</p>}
    <div className="discovery-grid">{data?.characters.map(person => <article className="discovery-card" key={person.id}>
      <FriendPortrait person={person} className="discovery-photo"/>
      <div className="discovery-foot">
        <div className="person-title"><h2>{person.name}</h2><span>AI 친구</span></div>
        <p>{person.status_message}</p>
        {person.room_id
          ? <a className="people-secondary" href={`/people/${person.id}/`}>프로필 보기 <ChevronRight size={16}/></a>
          : <button className="people-primary" disabled={adding !== null} onClick={() => void add(person)}>
            {adding === person.id ? '추가 중…' : '친구 추가'} <Plus size={16}/></button>}
      </div>
    </article>)}</div>
  </>
}

function Home() {
  const { data, notice } = usePageData(getFriends, true)
  return <>
    <div className="people-heading"><h1>내 친구</h1><a className="people-secondary" href="/discover/"><Plus size={16}/> 친구 찾기</a></div>
    {notice}
    {!!data && !data.friends.length && <div className="people-empty"><UsersRound/><p>첫 친구를 추가해 보세요.</p><a href="/discover/">친구 찾기 <ArrowRight size={15}/></a></div>}
    {!!data?.friends.length && <ul className="conversation-list">{data.friends.map(friend => {
      const last = friend.last_message
      const sent = last ? new Date(last.created_at) : null
      const stamp = sent && (day.format(sent) === day.format(new Date()) ? clock : date).format(sent)
      return <li key={friend.room_id}>
        <a href={`/people/${friend.character.id}/`} aria-label={friend.character.name+' 프로필'}><FriendPortrait person={friend.character}/></a>
        <a className="conversation-link" href={`/chat/${friend.room_id}/`}>
          <span className="conversation-copy"><b>{friend.character.name}</b><small>{last?.text || (last?.kind === 'photo' ? '사진' : '첫 메시지 보내기')}</small></span>
          <span className="conversation-meta">{last && <time dateTime={last.created_at}>{stamp}</time>}{friend.unread_count > 0 && <strong aria-label={'안 읽은 메시지 '+friend.unread_count+'개'}>{friend.unread_count}</strong>}</span>
          <ChevronRight size={17}/>
        </a>
      </li>
    })}</ul>}
  </>
}

function PersonProfile({ characterId }: { characterId: number }) {
  const load = useCallback(() => getProfile(characterId), [characterId])
  const { data, notice } = usePageData(load)
  const [more, setMore] = useState<Profile | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { setMore(null); setError('') }, [data])
  const profile = more || data
  async function older() {
    if (!profile?.next_cursor) return
    setBusy(true)
    try {
      const next = await getProfile(characterId, profile.next_cursor)
      setMore({ ...next, posts: [...profile.posts, ...next.posts] })
      setError('')
    } catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  return <>{notice}{profile && <>
    <section className="people-profile">
      <FriendPortrait person={profile} className="profile-face"/>
      <div><div className="person-title"><h1>{profile.name}</h1><span>AI 친구</span></div>
        <p>{profile.status_message}</p>{profile.current_activity && <small className="people-activity">{profile.current_activity}</small>}
      </div>
      <a className="people-primary" href={`/chat/${profile.room_id}/`}><MessageCircle size={17}/> 대화하기</a>
    </section>
    <h2 className="people-subhead">일상 기록</h2>
    {!profile.posts.length && <p className="people-empty">아직 올라온 소식이 없어요.</p>}
    <div className="people-album">{profile.posts.map(post => <a key={post.id} href={`/chat/from-post/${post.id}/`} aria-label={post.caption+' · 이 사진으로 말 걸기'}>
      <img src={post.photo.url} alt={post.photo.alt} loading="lazy"/>
      <div><small>{date.format(new Date(post.published_at))}{post.photo.ai_generated && ' · AI 생성 사진'}</small>
        <p>{post.caption}</p><span>이 사진으로 말 걸기 <ArrowUpRight size={15}/></span></div>
    </a>)}</div>
    {error && <p role="alert">{error}</p>}{profile.next_cursor && <button className="people-secondary" disabled={busy} onClick={() => void older()}>{busy ? '불러오는 중…' : '이전 소식 더 보기'}</button>}
  </>}</>
}

export default function FriendsPage({ page, characterId }: { page: string; characterId: number }) {
  return <div className="people"><header className="people-header"><div>
    <a className="people-logo" href="/" aria-label="wassup 홈">wassup<span>.</span></a>
    <nav aria-label="서비스 메뉴"><a href="/home/" aria-current={page === 'home' ? 'page' : undefined}>친구 홈</a><a href="/feed/">친구 피드</a>
      <form method="post" action="/logout/"><input type="hidden" name="csrfmiddlewaretoken" value={document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] || ''}/><button>로그아웃</button></form>
    </nav>
  </div></header>
  <main className="people-main">{page === 'discover' ? <Discover/> : page === 'profile' ? <PersonProfile characterId={characterId}/> : <Home/>}</main></div>
}
