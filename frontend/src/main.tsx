import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ArrowUp, ChevronLeft, Coffee, Info, MessageCircle, X, ZoomIn } from 'lucide-react'
import { getFriends, getMessages, getProfile, mergeMessages, sendMessage } from './chat'
import type { Friend, Message, Profile, Quote } from './chat'
import { subscribeToRoom } from './live'
import { sampleFriend, sampleMessages } from './preview'
import Publication from './Publication'
import seoyunPortrait from './design/assets/seoyun.webp'
import dohyunPortrait from './assets/dohyun.webp'
import './style.css'

const root = document.getElementById('root')!
const demo = root.dataset.demo === 'true'
const room = Number(root.dataset.roomId || 1)
const clock = new Intl.DateTimeFormat('ko-KR', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'Asia/Seoul' })
const calendar = new Intl.DateTimeFormat('ko-KR', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short', timeZone: 'Asia/Seoul' })
const day = (message: Message) => calendar.format(new Date(message.created_at))
const hour = (message: Message) => clock.format(new Date(message.created_at))

const portraits: Record<string, string> = { 서윤: seoyunPortrait, 도현: dohyunPortrait }

function Avatar({ friend }: { friend?: Friend }) {
  const source = friend && (friend.character.profile_image || portraits[friend.character.name])
  return source
    ? <img className="avatar" src={source} alt="" />
    : <span className="avatar initials" aria-hidden="true">{friend?.character.name[0] || '·'}</span>
}

function Brand() {
  return <span className="brand" aria-label="wassup">wassup<span className="brand-dot">.</span></span>
}

function QuoteCard({ quote }: { quote: Quote }) {
  if (quote.status === 'unavailable') return <div className="chat-quote">공개가 종료된 게시물이에요.</div>
  return <div className="chat-quote"><img src={quote.photo_url} alt={quote.photo_alt}/><span><small>인용한 게시물</small>{quote.caption}</span></div>
}

function Chat() {
  const [quote, setQuote] = useState<Quote | null>(() => JSON.parse(document.getElementById('initial-quote')?.textContent || 'null'))
  const [profile, setProfile] = useState<Profile | null>(null)
  const [profileError, setProfileError] = useState('')

  const [friends, setFriends] = useState<Friend[]>(demo ? [sampleFriend] : [])
  const [messages, setMessages] = useState<Message[]>(demo ? sampleMessages : [])
  const [ready, setReady] = useState(demo)
  const [more, setMore] = useState(false)
  const [paging, setPaging] = useState(false)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [sendError, setSendError] = useState('')
  const [typing, setTyping] = useState(false)
  const [listOpen, setListOpen] = useState(false)
  const [modal, setModal] = useState<'profile' | 'photo' | null>(null)
  const [photo, setPhoto] = useState('')
  const scroll = useRef<HTMLDivElement>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  const pin = useRef(true)
  const profileScroll = useRef<number | null>(null)
  const [newBelow, setNewBelow] = useState(false)
  const prepend = useRef<number | null>(null)
  const timers = useRef<number[]>([])
  const lock = useRef(false)
  const demoId = useRef(10)
  const friend = friends.find(item => item.room_id === room)
  const name = friend?.character.name || '친구'
  const status = friend?.character.status_message
  const photos = messages.filter(m => m.photo)
  const latestPhoto = photos.at(-1)?.photo

  useEffect(() => {
    if (demo) return () => timers.current.forEach(clearTimeout)
    let active = true
    let syncing = false
    let first = true
    async function sync() {
      if (syncing || document.hidden) return
      syncing = true
      try {
        const [history, list] = await Promise.all([getMessages(room), getFriends()])
        if (!active) return
        setFriends(list.friends)
        setMessages(old => mergeMessages(old, history.messages))
        if (first) setMore(history.has_more)
        first = false
        setReady(true)
        setLoadError('')
      } catch (e) {
        if (active) setLoadError((e as Error).message)
      } finally {
        syncing = false
        if (active && needsSync) {
          needsSync = false
          void sync()
        }
      }
    }
    let typingTimer = 0
    let needsSync = false
    function stopTyping() {
      clearTimeout(typingTimer)
      setTyping(false)
    }
    function resync() {
      if (syncing) needsSync = true
      else void sync()
    }

    const disconnect = subscribeToRoom(room, event => {
      switch (event.type) {
        case 'message.read':
          setMessages(old => old.map(message => message.id === event.message_id
            ? { ...message, status: 'read', read_at: event.read_at }
            : message))
          resync()
          break
        case 'message.new':
          stopTyping()
          if (!pin.current || profileScroll.current !== null) setNewBelow(true)
          setMessages(old => mergeMessages(old, [event.message]))
          break
        case 'typing.start': {
          stopTyping()
          const expiresIn = event.expires_at ? Date.parse(event.expires_at) - Date.now() : 8000
          const duration = Number.isFinite(expiresIn) ? Math.min(expiresIn, 30_000) : 8000
          if (duration > 0) {
            setTyping(true)
            typingTimer = window.setTimeout(stopTyping, duration)
          }
          break
        }
        case 'typing.stop':
          stopTyping()
          break
      }
    }, resync, stopTyping)
    void sync()
    // REST recovers messages or read state missed during a disconnect or a suspended tab.
    const tick = window.setInterval(sync, 3000)
    document.addEventListener('visibilitychange', sync)
    window.addEventListener('online', sync)
    return () => {
      active = false
      clearInterval(tick)
      clearTimeout(typingTimer)
      disconnect()
      document.removeEventListener('visibilitychange', sync)
      window.removeEventListener('online', sync)
    }
  }, [])

  useLayoutEffect(() => {
    const el = scroll.current
    if (!el || profileScroll.current !== null) return
    if (prepend.current !== null) {
      el.scrollTop += el.scrollHeight - prepend.current
      prepend.current = null
    } else if (pin.current) el.scrollTop = el.scrollHeight
  }, [messages, typing, listOpen])

  useEffect(() => {
    const viewport = window.visualViewport
    if (!viewport) return
    const resize = () => {
      if (viewport.scale !== 1) return
      document.documentElement.style.setProperty('--chat-height', viewport.height + 'px')
      if (profileScroll.current === null && pin.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight
    }
    resize()
    viewport.addEventListener('resize', resize)
    return () => viewport.removeEventListener('resize', resize)
  }, [])

  useEffect(() => {
    if (modal) dialog.current?.showModal()
    else dialog.current?.close()
  }, [modal])

  async function older() {
    if (paging || !messages.length) return
    setPaging(true)
    try {
      const page = await getMessages(room, messages[0].id)
      prepend.current = scroll.current?.scrollHeight ?? null
      setMore(page.has_more)
      setMessages(old => mergeMessages(old, page.messages))
    } catch (e) { setLoadError((e as Error).message) }
    finally { setPaging(false) }
  }

  function localSend(text: string): Message {
    const id = demoId.current++
    const created_at = new Date().toISOString()
    const sent: Message = { id, text, created_at, sender: 'user', status: 'sent', kind: 'text', read_at: null }
    timers.current.forEach(clearTimeout)
    setTyping(false)
    timers.current = [
      window.setTimeout(() => {
        setMessages(old => old.map(m => m.sender === 'user' && !m.read_at ? { ...m, status: 'read', read_at: new Date().toISOString() } : m))
        setTyping(true)
      }, 1000),
      window.setTimeout(() => {
        const reply: Message = { ...sent, id: demoId.current++, created_at: new Date().toISOString(), sender: 'character', text: '지금 잠깐 쉬는 중 ㅋㅋ', status: 'read' }
        setMessages(old => [...old, reply])
        setTyping(false)
      }, 2600),
    ]
    return sent
  }

  async function submit() {
    const text = draft.trim()
    if (!text || lock.current || !ready || listOpen || quote?.status==='unavailable') return
    lock.current = true
    setBusy(true)
    setSendError('')
    try {
      const sent = demo ? localSend(text) : await sendMessage(room, text, quote)
      clearQuote()
      pin.current = true
      setMessages(old => mergeMessages(old, [sent]))
      setDraft(value => value.trim() === text ? '' : value)
    } catch (e) { setSendError((e as Error).message) }
    finally { lock.current = false; setBusy(false) }
  }

  function clearQuote() {
    setQuote(null)
    const url = new URL(location.href)
    url.searchParams.delete('source_post')
    history.replaceState(history.state, '', url)
  }

  useLayoutEffect(() => {
    const thread = scroll.current
    if (!thread) return
    if (modal === 'profile') {
      profileScroll.current = thread.scrollTop
    } else if (profileScroll.current !== null) {
      thread.scrollTop = profileScroll.current
      profileScroll.current = null
      pin.current = thread.scrollHeight - thread.clientHeight - thread.scrollTop < 64
      if (pin.current) setNewBelow(false)
    }
  }, [modal])

  function openProfile() {
    history.pushState({...history.state, chatProfile: true}, '')
    setModal('profile')
  }
  function closeModal() {
    if (modal === 'profile' && history.state?.chatProfile) history.back()
    else setModal(null)
  }
  useEffect(() => {
    const restore = () => setModal(history.state?.chatProfile ? 'profile' : null)
    restore()
    window.addEventListener('popstate', restore)
    return () => window.removeEventListener('popstate', restore)
  }, [])
  useEffect(() => {
    if (modal !== 'profile' || demo || !friend) return
    let active = true
    setProfileError('')
    getProfile(friend.character.id)
      .then(next => { if (active) setProfile(next) })
      .catch(e => { if (active) setProfileError((e as Error).message) })
    return () => { active = false }
  }, [modal, friend?.character.id])

  const showPhoto = (url: string) => { setPhoto(url); setModal('photo') }
  const rows = friends.map(f => {
    const selected = f.room_id === room
    const last = selected ? messages.at(-1) || f.last_message : f.last_message
    return <a key={f.room_id} className={'friend-row ' + (selected ? 'selected' : '')}
      href={demo ? '#' : '/chat/' + f.room_id + '/'} aria-current={selected ? 'page' : undefined}
      onClick={e => { if (selected) { e.preventDefault(); setListOpen(false) } }}>
      <Avatar friend={f}/><span className="friend-text"><b>{f.character.name}</b><small>{last?.text || (last?.kind === 'photo' ? '사진' : '첫 메시지를 보내 보세요')}</small></span>
      <span className="row-meta">{last && hour(last)}{f.unread_count > 0 && <em>{f.unread_count}</em>}</span>
    </a>
  })

  return <main className={'shell ' + (listOpen ? 'show-list' : '')}>
    <aside className="side" aria-label="대화 목록">
      <Brand/>
      <div className="side-heading"><h2>대화</h2><span>{friends.length}</span>{!demo && <a href="/feed/">친구 피드 ↗</a>}</div>
      <nav>{rows}</nav>
      {latestPhoto && <section className="side-photo"><h3>대화 속 사진</h3><button onClick={() => showPhoto(latestPhoto)}><img src={latestPhoto} alt="대화에서 받은 사진"/><ZoomIn size={17}/></button></section>}
      {demo && <small className="preview-note">샘플 대화 미리보기</small>}
    </aside>
    <section className="chat-pane" aria-label={name + ' 채팅방'}>
      <header className="chat-head">
        <button className="back icon-button" aria-label="대화 목록" onClick={() => setListOpen(true)}><ChevronLeft/></button>
        <button className="chat-person" aria-label={name+' 프로필'} onClick={openProfile}><Avatar friend={friend}/><span className="head-title"><b>{name}</b><span>AI 친구</span>{(demo || root.dataset.sampleReplies === 'true') && <small className="demo-tag">{demo ? '미리보기' : '샘플 답장'}</small>}</span></button>
        <button className="icon-button info" onClick={openProfile} aria-label="친구 정보"><Info size={21}/></button>
      </header>
      {status && <div className="status-strip">{status.includes('카페') ? <Coffee size={15}/> : <MessageCircle size={15}/>}<span>{status}</span><i/></div>}
      {loadError && <p className="notice" role="status">{loadError}</p>}
      <div className="thread" ref={scroll} onScroll={e => { const el = e.currentTarget; pin.current = el.scrollHeight - el.clientHeight - el.scrollTop < 64; if (pin.current) setNewBelow(false) }}>
        <div className="thread-inner">
          {more && <button className="older" onClick={older} disabled={paging}>{paging ? '불러오는 중…' : '이전 대화 보기'}</button>}
          {!messages.length && <p className="empty">{ready ? name + '에게 첫 메시지를 보내 보세요.' : '대화 불러오는 중…'}</p>}
          {messages.map((m,i) => {
            const prev = messages[i-1]
            const newDay = !prev || day(prev) !== day(m)
            const own = m.sender === 'user'
            const start = newDay || prev.sender !== m.sender || hour(prev) !== hour(m)
            return <div key={m.id}>
              {newDay && <div className="day"><span>{day(m)}</span></div>}
              <div className={'line ' + (own ? 'out' : 'in') + (start ? ' start' : '')}>
                {!own && <div className="face-slot">{start && <button aria-label={name+' 프로필 열기'} onClick={openProfile}><Avatar friend={friend}/></button>}</div>}
                <div className="message-body">
                  {!own && start && <b className="speaker">{name}</b>}
                  <div className="bubble-row">
                    <div className={'bubble' + (m.photo ? ' with-photo' : '')}>
                      {m.source_post && <QuoteCard quote={m.source_post}/>}
                      {m.photo && <button className="photo" onClick={() => showPhoto(m.photo!)}><img src={m.photo} alt="채팅 사진 확대"/></button>}
                      {(m.text || m.kind === 'photo') && <span>{m.text || '사진'}</span>}
                    </div>
                    <small className="timestamp">{own && !m.read_at && <strong aria-label="읽지 않음">1</strong>}{hour(m)}</small>
                  </div>
                </div>
              </div>
            </div>
          })}
          {typing && <div className="typing" role="status" aria-label={name + ' 입력 중'}><i/><i/><i/></div>}
        </div>
      </div>
      {newBelow && <button className="older" onClick={() => { if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; setNewBelow(false) }}>새 메시지 보기 ↓</button>}
      <footer className="foot">
        {quote && <div className="quote-draft"><QuoteCard quote={quote}/><button aria-label="인용 해제" disabled={busy} onClick={clearQuote}><X size={17}/></button></div>}
        {sendError && <p role="alert" className="notice">{sendError}</p>}
        <form className="composer" onSubmit={e => { e.preventDefault(); void submit() }}>
          <textarea aria-label="메시지" rows={1} maxLength={1000} placeholder="메시지 보내기" value={draft}
            onChange={e => setDraft(e.target.value)} onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && e.nativeEvent.keyCode !== 229) { e.preventDefault(); void submit() }
            }}/>
          <button className="send" aria-label="전송" disabled={!draft.trim() || busy || !ready || quote?.status==='unavailable'}><ArrowUp size={22}/></button>
        </form>
      </footer>
    </section>
    <dialog aria-label={modal === 'photo' ? '사진 보기' : '친구 정보'} ref={dialog} onCancel={e => {e.preventDefault();closeModal()}}>
      <button className="close icon-button" aria-label="닫기" onClick={closeModal}><X size={18}/></button>
      {modal === 'photo' ? <><img className="large-photo" src={photo} alt="대화에서 받은 사진"/><small>AI 생성 사진</small></>
        : <><Avatar friend={friend}/><h2>{name}</h2><small>AI 친구</small>{status && <p>{status}</p>}{profile?.current_activity && <p>{profile.current_activity}</p>}
          {profileError && <p role="alert">{profileError}</p>}
          {profile && <div className="profile-album">{profile.posts.map(p=><figure key={p.id}><img src={p.photo.url} alt={p.photo.alt}/><figcaption>{p.caption}</figcaption></figure>)}</div>}</>}
    </dialog>
  </main>
}
createRoot(root).render(root.dataset.publication ? <Publication studio={root.dataset.publication === 'studio'} canPublish={root.dataset.canPublish === 'true'}/> : <Chat/>)
