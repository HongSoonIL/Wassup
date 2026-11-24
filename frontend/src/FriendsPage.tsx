import { useEffect, useState } from 'react'
import { ArrowRight, ArrowUpRight, ChevronRight, House, Images, LogOut, MessageCircle, Plus, UsersRound } from 'lucide-react'
import { getFriends, getProfile, request } from './chat'
import type { Friend, Profile } from './chat'
import FriendPortrait from './FriendPortrait'
import { FriendIdentity } from './SocialMedia'
import ProfileBody from './ProfileBody'
import './people.css'

type Person = Friend['character'] & { room_id: number | null }
export const discover = () => request<{ characters: Person[] }>('/api/characters/')
export type FeedPost = Profile['posts'][number] & { name: string; character_id: number }
export type FeedPage = { posts: FeedPost[]; next_cursor: string | null }
export const getFeed = (cursor?: string) => request<FeedPage>('/api/feed/' + (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''))
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

function HomeFriend({ friend }: { friend: Friend & { current_activity?: string | null } }) {
  const person = friend.character
  const last = friend.last_message
  const sent = last && new Date(last.created_at)
  const stamp = sent && (day.format(sent) === day.format(new Date()) ? clock : date).format(sent)
  const activity = friend.current_activity
  return <li className="home-friend">
    <a href={`/people/${person.id}/`} className="home-person" aria-label={person.name+' 프로필'} aria-describedby={'activity-'+person.id}>
      <FriendIdentity person={person} detail={<span id={'activity-'+person.id}>{activity
        ? <span className="home-activity"><i aria-hidden="true"/><span className="sr-only">현재 활동 </span>{activity}</span>
        : person.status_message || 'AI 친구'}</span>}/>
    </a>
    <a className="home-chat" href={`/chat/${friend.room_id}/`} aria-label={person.name+'에게 말 걸기'}><MessageCircle size={18}/></a>
    <a className="home-last" href={`/chat/${friend.room_id}/`}>
      <span>{last?.text || (last?.kind === 'photo' ? '사진' : '첫 메시지 보내기')}</span>
      {last && <time dateTime={last.created_at}>{stamp}</time>}
      {friend.unread_count > 0 && <strong aria-label={'안 읽은 메시지 '+friend.unread_count+'개'}>{friend.unread_count}</strong>}
    </a>
  </li>
}

function RecentPost() {
  const { data, notice } = usePageData(getFeed)
  const post = data?.posts[0]
  return <section className="home-latest" aria-label="최근 소식">
    <div className="home-section-title"><h2>최근 소식</h2><a href="/feed/">피드 전체 보기 <ArrowRight size={15}/></a></div>
    {notice}
    {data && (post ? <a className="home-latest-card" href={`/feed/#post-${post.id}`}>
      <img src={post.photo.url} alt={post.photo.alt}/>
      <div><span className="home-latest-by"><b>{post.name}</b><time dateTime={post.published_at}>{date.format(new Date(post.published_at))} · {clock.format(new Date(post.published_at))}</time>
        {post.photo.ai_generated && <small>AI 생성 사진</small>}</span>
        <p>{post.caption}</p></div>
    </a> : <p className="people-empty">아직 올라온 소식이 없어요.</p>)}
  </section>
}

function Home() {
  const { data, notice } = usePageData(getFriends, true)
  return <>
    <div className="people-heading"><h1>내 친구</h1><a className="people-secondary" href="/discover/"><Plus size={16}/>친구 찾기</a></div>
    {notice}
    {data && (!data.friends.length
      ? <div className="people-empty"><UsersRound/><p>첫 친구를 추가해 보세요.</p><a href="/discover/">친구 찾기 <ArrowRight size={15}/></a></div>
      : <div className="home-columns"><ul className="home-friends" aria-label="내 친구">{data.friends.map(friend => <HomeFriend key={friend.room_id} friend={friend}/>)}</ul><RecentPost/></div>)}
  </>
}

export function PersonProfile({ characterId, load = getProfile }: { characterId: number; load?: (id: number) => Promise<Profile> }) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    setProfile(null)
    setError('')
    load(characterId).then(result => { if (active) setProfile(result) })
      .catch(e => { if (active) setError((e as Error).message) })
    return () => { active = false }
  }, [characterId, load, attempt])
  if (error) return <div role="alert" className="people-alert">{error}<button onClick={() => setAttempt(n => n + 1)}>다시 시도</button></div>
  if (!profile) return <p role="status" className="people-note">프로필 불러오는 중…</p>
  return <ProfileBody profile={profile}/>
}

export function PeopleHeader({ page, canPublish }: { page: string; canPublish?: boolean }) {
  return <header className="people-header"><div>
    <a className="people-logo" href="/" aria-label="wassup 홈">wassup<span>.</span></a>
    <nav aria-label="서비스 메뉴">
      <a href="/home/" aria-label="친구 홈" aria-current={page === 'home' ? 'page' : undefined}><House size={16}/><span><span className="people-nav-prefix">친구 </span>홈</span></a>
      <a href="/feed/" aria-label="친구 피드" aria-current={page === 'feed' ? 'page' : undefined}><Images size={16}/><span><span className="people-nav-prefix">친구 </span>피드</span></a>
      {canPublish && <a href="/studio/" className="people-studio">주간 발행<ArrowUpRight size={13}/></a>}
      <form method="post" action="/logout/"><input type="hidden" name="csrfmiddlewaretoken" value={document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] || ''}/>
        <button aria-label="로그아웃" title="로그아웃"><LogOut size={17}/></button></form>
    </nav>
  </div></header>
}

export default function FriendsPage({ page, characterId, canPublish }: { page: string; characterId: number; canPublish: boolean }) {
  useEffect(() => {
    if (page !== 'profile') return
    // A reload on a feed-opened profile serves this standalone Django entry.
    // Back can still be a same-document traversal to the original feed URL.
    const restore = () => {
      if (location.pathname !== `/people/${characterId}/`) location.reload()
    }
    window.addEventListener('popstate', restore)
    return () => window.removeEventListener('popstate', restore)
  }, [page, characterId])
  return <div className="people"><PeopleHeader page={page} canPublish={canPublish}/>
    <main className={'people-main people-'+page}>{page === 'discover' ? <Discover/> : page === 'profile'
      ? <PersonProfile key={characterId} characterId={characterId}/> : <Home/>}</main>
  </div>
}
