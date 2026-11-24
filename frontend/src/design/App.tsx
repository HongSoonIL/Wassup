import { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ArrowLeft, ArrowRight, ArrowUpRight, Clock3, House, Images, Plus, SlidersHorizontal, UsersRound } from 'lucide-react'
import Chat, { createConversationState } from './Chat'
import { FriendRow, Friends, Lightbox, Onboarding, PostCard, Profile } from './Friends'
import Ops from './Ops'
import { character, initialPosts, previewAt, publicPosts, time } from './state'
import type { CharacterId, ReviewPost } from './state'
import './design.css'

type Page = 'home' | 'profile' | 'feed' | 'chat' | 'ops' | 'join' | 'friends'
type Account = 'a' | 'b'
const days = new Intl.DateTimeFormat('ko-KR', { weekday: 'long', timeZone: 'Asia/Seoul' })
const reviewTimes = [
  '2026-11-26T18:40:00+09:00', previewAt, '2026-11-26T21:39:00+09:00',
  '2026-11-26T21:40:00+09:00', '2026-11-26T22:05:00+09:00', '2026-11-27T09:00:00+09:00',
]
const menu = [
  { page: 'home', label: '홈', icon: House },
  { page: 'feed', label: '피드', icon: Images },
  { page: 'friends', label: '친구', icon: UsersRound },
] as const

function readRoute(): { page: Page; id: CharacterId } {
  const [page, who] = location.hash.slice(1).split('/')
  const id = who === 'seoyun' ? who : 'dohyun'
  switch (page) {
    case 'home': case 'friends': case 'feed': case 'ops': case 'join': case 'profile': case 'chat':
      return { page, id }
    default:
      return { page: 'home', id }
  }
}

function App() {
  const [page, setPage] = useState<Page>(() => readRoute().page)
  const [id, setId] = useState<CharacterId>(() => readRoute().id)
  const [account, setAccount] = useState<Account>('a')
  const [now, setNow] = useState(previewAt)
  const [records, setRecords] = useState(initialPosts)
  const [joined, setJoined] = useState({ a: true, b: false })
  const [friends, setFriends] = useState<Record<Account, CharacterId[]>>({ a: ['dohyun', 'seoyun'], b: [] })
  const [likes, setLikes] = useState<Record<string, boolean>>({})
  const [conversation, setConversation] = useState(createConversationState)
  const [settings, setSettings] = useState(false)
  const [lightbox, setLightbox] = useState<ReviewPost | null>(null)
  const pendingFailures = useRef(new Set<string>())
  const mine = friends[account]
  const open = publicPosts(records, now)
  const timeline = open.filter(p => mine.includes(p.characterId))
  const today = timeline.filter(p => p.publishAt.slice(0, 10) === now.slice(0, 10))

  useEffect(() => {
    function restoreRoute() {
      const route = readRoute()
      let next = route.page
      if (next !== 'ops' && !joined[account]) next = 'join'
      if (next === 'chat' && !mine.includes(route.id)) next = 'profile'
      if (next === 'join' && joined[account]) next = 'friends'
      setPage(next)
      setId(route.id)
      setSettings(false)
      setLightbox(null)
      window.scrollTo(0, 0)
    }
    window.addEventListener('hashchange', restoreRoute)
    return () => window.removeEventListener('hashchange', restoreRoute)
  }, [account, joined, mine])

  function go(next: Page, who = id) {
    setId(who)
    setPage(next)
    setLightbox(null)
    setSettings(false)
    const target = next === 'chat' || next === 'profile' ? next + '/' + who : next
    const hash = target === 'home' ? '' : '#' + target
    if (hash !== location.hash) history.pushState(null, '', location.pathname + location.search + hash)
    window.scrollTo(0, 0)
  }

  function changeTime(instant: string) {
    if (!Number.isFinite(Date.parse(instant))) return
    const failures = new Set(pendingFailures.current)
    setRecords(old => old.map(post => {
      if (post.state !== 'scheduled' || Date.parse(post.publishAt) > Date.parse(instant)) return post
      return { ...post, state: failures.has(post.id) ? 'failed' : 'published' }
    }))
    for (const post of records) {
      if (Date.parse(post.publishAt) <= Date.parse(instant)) pendingFailures.current.delete(post.id)
    }
    setNow(instant)
    setLightbox(null)
  }

  function save(record: ReviewPost) {
    const saved = record.state === 'published' ? { ...record, releasedAt: now } : record
    setRecords(old => [...old.filter(p => p.id !== record.id), saved])
  }

  function addFriend(who: CharacterId) {
    setFriends(old => ({ ...old, [account]: [...new Set([...old[account], who])] }))
  }

  function switchAccount(next: Account) {
    setAccount(next)
    go(joined[next] ? 'home' : 'join')
  }

  function postCard(post: ReviewPost) {
    const key = account + '/' + post.id
    return <PostCard key={post.id} post={post} onProfile={who => go('profile', who)} onOpen={setLightbox}
      liked={!!likes[key]} onLike={() => setLikes(old => ({ ...old, [key]: !old[key] }))} />
  }

  const start = () => go(joined[account] ? 'home' : 'join')
  if (page === 'ops') {
    return <div className="d-app"><Ops characterId={id} onCharacter={setId} now={now} onTime={changeTime}
      records={records} onSave={save} onFailNext={key => pendingFailures.current.add(key)} onPreview={target => go(joined[account] ? target ?? 'home' : 'join')} /></div>
  }

  return <div className="d-app">
    <header className="d-head"><div className="d-head-inner">
      <button type="button" className="d-logo" aria-label="wassup 홈" onClick={start}>wassup<span>.</span></button>
      {joined[account] && <nav aria-label="주 메뉴">{menu.map(item =>
        <button type="button" key={item.page} aria-current={page === item.page ? 'page' : undefined}
          onClick={() => go(item.page)}><item.icon size={17} />{item.label}</button>
      )}</nav>}
      <button type="button" className="d-control" onClick={() => setSettings(!settings)} aria-expanded={settings}>
        <SlidersHorizontal size={15} /><span>시안 설정</span>
      </button>
    </div></header>
    {settings && <section className="d-controls" aria-label="시안 설정">
      <label>체험 계정<select value={account} onChange={e => switchAccount(e.target.value as Account)}>
        <option value="a">A · 기존 사용자</option><option value="b">B · 신규 사용자</option>
      </select></label>
      <label>시안 시각<select value={now} onChange={e => changeTime(e.target.value)}>
        {[...new Set([...reviewTimes, now])].sort().map(v => <option value={v} key={v}>{v.slice(5, 10)} · {time(v)}</option>)}
      </select></label>
      <button type="button" className="d-pill" onClick={() => go('ops')}>운영 대시보드<ArrowUpRight size={15} /></button>
    </section>}
    <main className="d-content">
      {['home', 'feed', 'friends'].includes(page) && <header className="d-page-title">
        <div className="d-date"><span className="d-dot" /><time dateTime={now}>{now.slice(0, 10).replaceAll('-', '.')} · {days.format(new Date(now))}</time></div>
        <h1>{page === 'home' ? '친구와 새 소식' : page === 'feed' ? '친구 피드' : '친구 목록'}</h1>
      </header>}
      {page === 'home' && <div className="d-grid">
        <section className="d-feature"><div className="d-label"><h2>최근 소식</h2><button type="button" onClick={() => go('feed')}>피드 전체<ArrowRight size={14} /></button></div>
          {timeline.length ? postCard(timeline[0]) : <div className="d-empty">친구가 올린 소식이 여기에 보여요.</div>}
        </section>
        <aside className="d-right">
          <section className="d-friends-panel"><div className="d-label"><h2>내 친구 <sup>{mine.length}</sup></h2>
            <button type="button" aria-label="친구 찾기" onClick={() => go('friends')}><Plus size={17} /></button></div>
            <div className="d-card d-list">{mine.map(who => <FriendRow key={who} id={who} now={now}
              onProfile={() => go('profile', who)} onChat={() => go('chat', who)} />)}
              {!mine.length && <button type="button" className="d-pill" onClick={() => go('friends')}>첫 친구 추가</button>}
            </div>
          </section>
          <section className="d-archive"><div className="d-label"><h2>오늘의 기록</h2><Clock3 size={16} /></div>
            <div className="d-history">{today.map(post => <button type="button" key={post.id} onClick={() => setLightbox(post)}>
              <time>{time(post.publishAt)}</time><span><b>{character(post.characterId).name}</b>{post.caption}</span>
            </button>)}{!today.length && <p>오늘 공개된 기록이 없어요.</p>}</div>
          </section>
        </aside>
      </div>}
      {page === 'feed' && <div className="d-feed">{timeline.map(postCard)}
        {!timeline.length && <p className="d-empty">친구를 추가하면 피드를 볼 수 있어요.</p>}</div>}
      {page === 'friends' && <Friends mine={mine} onProfile={who => go('profile', who)} onAdd={addFriend} />}
      {page === 'join' && <Onboarding onJoin={() => { setJoined(old => ({ ...old, [account]: true })); go('friends') }} />}
      {page === 'profile' && <>
        <button className="d-back" type="button" onClick={() => go('home')}><ArrowLeft size={16} />홈</button>
        <Profile id={id} now={now} posts={open.filter(p => p.characterId === id)} added={mine.includes(id)}
          onChat={() => go('chat')} onAdd={() => addFriend(id)} onOpen={setLightbox} />
      </>}
      {page === 'chat' && <Chat key={account + '/' + id} id={id} account={account} now={now} records={records}
        state={conversation} onChange={setConversation} onBack={() => go('profile')} />}
    </main>
    {lightbox && <Lightbox post={lightbox} onClose={() => setLightbox(null)} />}
  </div>
}

createRoot(document.getElementById('design')!).render(<App />)
