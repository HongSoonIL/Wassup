import { PageTitle } from './PageParts'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ArrowDown, ArrowUp, ChevronLeft, ImageOff, Info, X } from 'lucide-react'
import { getProfile, markRead, messagePhoto, RequestError } from './chat'
import type { Message, Profile, Quote } from './chat'
import useChatRoom from './useChatRoom'
import Publication from './Publication'
import FriendMemory from './FriendMemory'
import RoomSettings from './RoomSettings'
import FriendPortrait from './FriendPortrait'
import FriendsPage from './FriendsPage'
import { InboxList, InboxPlaceholder, useInbox } from './Inbox'
import ProfileBody from './ProfileBody'
import { FriendIdentity, PhotoViewerProvider, PostPhoto } from './SocialMedia'
import UserNav from './UserNav'
import './style.css'

const root = document.getElementById('root')!
const demo = root.dataset.demo === 'true'
const clock = new Intl.DateTimeFormat('ko-KR', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'Asia/Seoul' })
const calendar = new Intl.DateTimeFormat('ko-KR', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short', timeZone: 'Asia/Seoul' })
const day = (message: Message) => calendar.format(new Date(message.created_at))
const hour = (message: Message) => clock.format(new Date(message.created_at))
const atBottom = (el: HTMLElement) => el.scrollHeight - el.clientHeight - el.scrollTop < 24
const overlayOpen = () => Boolean(history.state?.chatProfile || history.state?.wsPhoto)

function quotePhotoKey(messageId: number, quote: Extract<Quote, { status: 'available' }>) {
  return `quote:${messageId}:${quote.id}:${quote.version}`
}

function QuotePreview({ quote, name, messageId }: { quote: Quote; name: string; messageId?: number }) {
  return <div className="chat-quote">
    {quote.status === 'available'
      ? <PostPhoto src={quote.photo_url} alt={quote.photo_alt} className="quote-thumbnail" label={name + '의 인용 사진 확대'}
          viewerKey={messageId === undefined ? undefined : quotePhotoKey(messageId, quote)}/>
      : <span className="quote-unavailable" aria-hidden="true"><ImageOff size={19}/></span>}
    <span className="quote-copy"><strong>{name}의 게시물</strong>
      <span>{quote.status === 'available' ? quote.caption : '공개가 종료된 게시물이에요.'}</span>
    </span>
  </div>
}

function updateQuoteUrl(quote: Quote | null) {
  const url = new URL(location.href)
  if (quote?.status === 'available') url.searchParams.set('source_post', String(quote.id))
  else url.searchParams.delete('source_post')
  history.replaceState(history.state, '', url)
}

function Chat({ room, inbox }: { room: number; inbox: ReturnType<typeof useInbox> }) {
  const [quote, setQuote] = useState<Quote | null>(() => JSON.parse(document.getElementById('initial-quote')?.textContent || 'null'))
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [sendError, setSendError] = useState('')
  const [paging, setPaging] = useState(false)
  const [pageError, setPageError] = useState('')
  const [listOpen, setListOpen] = useState(false)
  const [profileOpen, setProfileOpen] = useState(Boolean(history.state?.chatProfile))
  const [profile, setProfile] = useState<Profile | null>(null)
  const [profileError, setProfileError] = useState('')
  const [profileAttempt, setProfileAttempt] = useState(0)
  const [newBelow, setNewBelow] = useState(false)
  const scroll = useRef<HTMLDivElement>(null)
  const composerInput = useRef<HTMLTextAreaElement>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  const profileTrigger = useRef<HTMLElement | null>(null)
  const closingProfile = useRef(false)
  const profileScroll = useRef<number | null>(null)
  const prepend = useRef<{ node: HTMLElement; top: number } | null>(null)
  const pin = useRef(true)
  const fresh = useRef<number[]>([])
  const [readThrough, setReadThrough] = useState(0)
  const reading = useRef(false)
  const lock = useRef(false)
  const pagingLock = useRef(false)
  const quoteRevision = useRef(0)
  const albumQuote = useRef<Quote | null>(null)

  const onArrival = useCallback((ids: number[], animate: boolean) => {
    const covered = overlayOpen() || profileScroll.current !== null || !scroll.current?.clientHeight
    if (covered) pin.current = false
    if (!pin.current || covered) setNewBelow(true)
    if (animate && !covered && !document.hidden) fresh.current.push(...ids)
  }, [])
  const { friends, updatePreview, updateUnread } = inbox
  const { messages, ready, more, activity, typing, loadError, send, loadOlder } = useChatRoom(room, demo, onArrival, updatePreview)
  const friend = friends?.find(item => item.room_id === room)
  const character = friend?.character
  const characterId = character?.id
  const name = character?.name || '친구'
  const status = activity || character?.status_message
  const latestPhoto = messages.reduce<ReturnType<typeof messagePhoto>>((latest, message) => messagePhoto(message) ?? latest, undefined)
  const resolvePhoto = (key: string) => {
    const messageId = Number(key.split(':')[1])
    const message = messages.find(item => item.id === messageId)
    if (!message) return
    const photo = messagePhoto(message)
    if (photo?.viewerKey === key) return photo
    const quoted = message.source_post
    if (quoted?.status === 'available' && quotePhotoKey(message.id, quoted) === key) {
      return { src: quoted.photo_url, alt: quoted.photo_alt }
    }
  }

  useLayoutEffect(() => {
    const el = scroll.current
    const arrivals = fresh.current.splice(0)
    if (!el || !el.clientHeight || overlayOpen() || profileScroll.current !== null) return
    if (prepend.current) {
      const { node, top } = prepend.current
      el.scrollTop += node.getBoundingClientRect().top - top
      prepend.current = null
    } else if (pin.current) el.scrollTop = el.scrollHeight
    if (!arrivals.length || document.hidden || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    for (const id of arrivals) {
      el.querySelector(`[data-message-id="${id}"] .bubble`)?.animate(
        [{ opacity: 0, transform: 'translateY(5px)' }, { opacity: 1, transform: 'translateY(0)' }],
        { duration: 160, easing: 'ease-out' },
      )
    }
  }, [messages, typing, listOpen])

  useEffect(() => {
    const thread = scroll.current
    if (demo || !thread || listOpen || profileOpen || !ready) return
    const visible = new Set<number>()
    const acknowledge = () => {
      if (document.hidden || !document.hasFocus() || overlayOpen() || reading.current || !visible.size) return
      const through = Math.max(...visible)
      reading.current = true
      markRead(room, through).then(({ unread_count }) => {
        setReadThrough(through)
        updateUnread(room, unread_count)
      }).catch(() => { /* A later observation or room refresh can retry. */ })
        .finally(() => { reading.current = false })
    }
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        const id = Number((entry.target as HTMLElement).dataset.unread)
        if (entry.isIntersecting && entry.intersectionRect.height > 0) visible.add(id)
        else visible.delete(id)
      }
      acknowledge()
    }, { root: thread })
    thread.querySelectorAll('[data-unread]').forEach(el => observer.observe(el))
    window.addEventListener('focus', acknowledge)
    window.addEventListener('popstate', acknowledge)
    document.addEventListener('visibilitychange', acknowledge)
    return () => {
      observer.disconnect()
      window.removeEventListener('focus', acknowledge)
      window.removeEventListener('popstate', acknowledge)
      document.removeEventListener('visibilitychange', acknowledge)
    }
  }, [messages, listOpen, profileOpen, ready, room, readThrough, updateUnread])

  useLayoutEffect(() => {
    const thread = scroll.current
    const compact = window.matchMedia('(max-width:840px), (pointer:coarse) and (max-height:520px)')
    const layoutChanged = () => { if (!compact.matches) setListOpen(false) }
    compact.addEventListener('change', layoutChanged)
    const observer = new ResizeObserver(() => {
      if (thread?.clientHeight && pin.current && !overlayOpen() && profileScroll.current === null) thread.scrollTop = thread.scrollHeight
    })
    if (thread) observer.observe(thread)
    return () => {
      compact.removeEventListener('change', layoutChanged)
      observer.disconnect()
    }
  }, [])

  async function older() {
    if (pagingLock.current || !messages.length) return
    pagingLock.current = true
    setPaging(true)
    setPageError('')
    try {
      await loadOlder(messages[0].id, () => {
        const node = scroll.current?.querySelector<HTMLElement>('[data-message-id]')
        if (node && !overlayOpen()) prepend.current = { node, top: node.getBoundingClientRect().top }
      })
    } catch (error) { setPageError((error as Error).message) }
    finally { pagingLock.current = false; setPaging(false) }
  }

  function clearQuote() {
    quoteRevision.current++
    setQuote(null)
    setSendError('')
    updateQuoteUrl(null)
  }

  async function submit() {
    const text = draft.trim()
    if (!text || lock.current || !ready || listOpen || quote?.status === 'unavailable') return
    lock.current = true
    setBusy(true)
    setSendError('')
    const revision = quoteRevision.current
    try {
      await send(text, quote)
      if (revision === quoteRevision.current && quote) clearQuote()
      setDraft(value => value === draft ? '' : value)
    } catch (error) {
      if (revision === quoteRevision.current && quote && error instanceof RequestError && error.status === 404) {
        setQuote({ status: 'unavailable' })
      }
      setSendError((error as Error).message)
    } finally { lock.current = false; setBusy(false) }
  }

  function openProfile(trigger: HTMLElement) {
    if (history.state?.chatProfile) return
    profileTrigger.current = trigger
    history.pushState({ ...history.state, chatProfile: true }, '')
    setProfileOpen(true)
  }
  function closeProfile() {
    if (closingProfile.current) return
    if (history.state?.chatProfile) {
      closingProfile.current = true
      history.back()
    }
    else setProfileOpen(false)
  }
  function choosePost(post: Profile['posts'][number]) {
    quoteRevision.current++
    albumQuote.current = { status: 'available', id: post.id, version: post.version,
      caption: post.caption, photo_url: post.photo.url, photo_alt: post.photo.alt }
    closeProfile()
  }
  useEffect(() => {
    const restore = () => {
      const open = Boolean(history.state?.chatProfile)
      closingProfile.current = false
      if (!open) setProfile(null)
      if (!open && albumQuote.current) {
        setQuote(albumQuote.current)
        setSendError('')
        updateQuoteUrl(albumQuote.current)
        albumQuote.current = null
      }
      setProfileOpen(open)
      if (!overlayOpen() && scroll.current && profileScroll.current === null) {
        pin.current = atBottom(scroll.current)
        if (pin.current) setNewBelow(false)
      }
    }
    window.addEventListener('popstate', restore)
    return () => window.removeEventListener('popstate', restore)
  }, [])
  useLayoutEffect(() => {
    const thread = scroll.current
    const modal = dialog.current
    if (!thread || !modal) return
    if (profileOpen && !modal.open) {
      profileScroll.current = thread.scrollTop
      modal.showModal()
    } else if (!profileOpen && modal.open) {
      modal.close()
      profileTrigger.current?.focus({ preventScroll: true })
      thread.scrollTop = profileScroll.current ?? thread.scrollTop
      profileScroll.current = null
      pin.current = atBottom(thread)
      if (pin.current) setNewBelow(false)
    }
  }, [profileOpen])
  useEffect(() => {
    if (!profileOpen || demo || !characterId) return
    let active = true
    setProfile(null)
    setProfileError('')
    getProfile(characterId).then(next => { if (active) setProfile(next) })
      .catch(error => { if (active) setProfileError((error as Error).message) })
    return () => { active = false }
  }, [profileOpen, characterId, profileAttempt])
  const displayedProfile: Profile | null = demo && character
    ? { ...character, room_id: room, current_activity: activity, next_cursor: null, posts: [] } : profile

  return <PhotoViewerProvider resolvePhoto={resolvePhoto} loading={!ready && !loadError}><main className={'shell room-selected' + (listOpen ? ' show-list' : '')}>
    <InboxList {...inbox} room={room} demo={demo} onDemoSelect={() => setListOpen(false)}>
      {latestPhoto && <section className="side-photo"><h3>대화 속 사진</h3>
        <PostPhoto {...latestPhoto} key={latestPhoto.viewerKey} className="side-photo-button"/></section>}
    </InboxList>
    <section className="chat-pane" aria-label={name + ' 채팅방'}>
      <header className="chat-head">
        {demo
          ? <button className="back icon-button" aria-label="대화 목록" onClick={() => setListOpen(true)}><ChevronLeft size={23}/></button>
          : <a className="back icon-button" href="/messages/" aria-label="대화 목록"><ChevronLeft size={23}/></a>}
        <button className="chat-person" aria-label={name + ' 프로필'} aria-haspopup="dialog" onClick={e => openProfile(e.currentTarget)}>
          <FriendIdentity person={character} detail={<>{status && <span className="chat-activity">{status}</span>}
            <span className="chat-kind">AI 친구{demo ? ' · 미리보기' : root.dataset.sampleReplies === 'true' ? ' · 샘플 답장' : ''}</span></>}/>
        </button>
        <button className="icon-button info" aria-label="친구 정보" aria-haspopup="dialog" onClick={e => openProfile(e.currentTarget)}><Info size={21}/></button>
      </header>
      {loadError && <p className="notice" role="status">{loadError}</p>}
      <div className="thread" ref={scroll} onScroll={e => {
        if (overlayOpen() || !e.currentTarget.clientHeight) return
        pin.current = atBottom(e.currentTarget)
        if (pin.current) setNewBelow(false)
      }}>
        <div className="thread-inner">
          {more && <button className="older" onClick={older} disabled={paging}>{paging ? '불러오는 중…' : '이전 대화 보기'}</button>}
          {pageError && <p role="alert" className="notice">{pageError}</p>}
          {!messages.length && <p className="empty">{ready ? name + '에게 첫 메시지를 보내 보세요.' : '대화 불러오는 중…'}</p>}
          {messages.map((m, i) => {
            const prev = messages[i-1]
            const newDay = !prev || day(prev) !== day(m)
            const own = m.sender === 'user'
            const start = newDay || prev.sender !== m.sender || hour(prev) !== hour(m)
            const photo = messagePhoto(m)
            const photoBubble = m.kind === 'photo' || Boolean(photo)
            return <div key={m.id} data-message-id={m.id}>
              {newDay && <div className="day"><time dateTime={m.created_at}>{day(m)}</time></div>}
              <div className={'line ' + (own ? 'out' : 'in') + (start ? ' start' : '')}>
                {!own && <div className="face-slot">{start && <button aria-label={name + ' 프로필 열기'} aria-haspopup="dialog" onClick={e => openProfile(e.currentTarget)}><FriendPortrait person={character}/></button>}</div>}
                <div className="message-body">
                  {!own && start && <b className="speaker">{name}</b>}
                  <div className="bubble-row">
                    <div data-unread={!own && !m.read_at && m.id > readThrough ? m.id : undefined} className={'bubble' + (photoBubble ? ' with-photo' : '')}>
                      {m.source_post && <QuotePreview quote={m.source_post} name={name} messageId={m.id}/>}
                      {photo ? <PostPhoto {...photo} key={photo.viewerKey} className="message-photo"/> : photoBubble &&
                        <span className="message-photo message-photo-unavailable"><ImageOff size={20} aria-hidden="true"/>이 사진은 볼 수 없어요.</span>}
                      {m.text && <span className="message-text">{m.text}</span>}
                    </div>
                    <small className="timestamp">{own && !m.read_at && <strong aria-label="읽지 않음">1</strong>}
                      <time dateTime={m.created_at}>{hour(m)}</time></small>
                  </div>
                </div>
              </div>
            </div>
          })}
          {typing && <div className="typing" role="status">
            <span className="typing-dots" aria-hidden="true"><i/><i/><i/></span><span>{name} 입력 중</span>
          </div>}
        </div>
      </div>
      {newBelow && <div className="new-below"><button onClick={() => {
        if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight
        pin.current = true
        setNewBelow(false)
      }}><ArrowDown size={15}/>새 메시지 보기</button></div>}
      <footer className="foot">
        {quote && <div className="quote-draft"><QuotePreview quote={quote} name={name}/>
          <button className="icon-button" aria-label="인용 해제" disabled={busy} onClick={() => {
            clearQuote()
            composerInput.current?.focus({ preventScroll: true })
          }}><X size={18}/></button></div>}
        {sendError && <p role="alert" className="notice">{sendError}</p>}
        <form className="composer" onSubmit={e => { e.preventDefault(); void submit() }}>
          <textarea ref={composerInput} aria-label="메시지" rows={1} maxLength={1000} placeholder="메시지 보내기" value={draft}
            onChange={e => setDraft(e.target.value)} onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && e.nativeEvent.keyCode !== 229) { e.preventDefault(); void submit() }
            }}/>
          <button className="send" aria-label="전송" disabled={!draft.trim() || busy || !ready || quote?.status === 'unavailable'}><ArrowUp size={22}/></button>
        </form>
      </footer>
    </section>
    <dialog className="chat-profile-dialog" aria-label={name + ' 프로필'} ref={dialog}
      onCancel={e => { e.preventDefault(); closeProfile() }}>
      {profileOpen && <>
        <header className="chat-profile-bar"><h2>친구 프로필</h2><button autoFocus className="icon-button" aria-label="닫기" onClick={closeProfile}><X size={21}/></button></header>
        {displayedProfile ? <ProfileBody profile={displayedProfile} onChat={closeProfile} onQuote={choosePost}>
          {!demo && <>
            <RoomSettings room={room} name={name}/>
            <FriendMemory room={room} name={name}/>
          </>}
        </ProfileBody> : profileError
          ? <p className="profile-load" role="alert">{profileError} <button onClick={() => setProfileAttempt(n => n + 1)}>다시 시도</button></p>
          : <p className="profile-load" role="status">프로필을 불러오는 중…</p>}
      </>}
    </dialog>
  </main></PhotoViewerProvider>
}

function Messaging({ room }: { room?: number }) {
  const inbox = useInbox(demo)
  useLayoutEffect(() => {
    const viewport = window.visualViewport
    if (!viewport) return
    const style = document.documentElement.style
    const resize = () => {
      if (viewport.scale !== 1) return
      style.setProperty('--chat-height', viewport.height + 'px')
      style.setProperty('--chat-top', viewport.offsetTop + 'px')
    }
    resize()
    viewport.addEventListener('resize', resize)
    viewport.addEventListener('scroll', resize)
    return () => {
      viewport.removeEventListener('resize', resize)
      viewport.removeEventListener('scroll', resize)
      style.removeProperty('--chat-height')
      style.removeProperty('--chat-top')
    }
  }, [])
  const unreadCount = inbox.friends?.reduce((total, friend) => total + friend.unread_count, 0) ?? 0
  return <div className={room === undefined ? 'people user-layout inbox-page' : 'messaging' + (demo ? '' : ' user-layout user-layout--viewport')}>
    {!demo && <UserNav page="messages" unreadCount={unreadCount}/>}
    {room === undefined ? <main className="inbox-main"><PageTitle>메시지</PageTitle><div className="shell"><InboxList {...inbox}/><InboxPlaceholder hasConversations={Boolean(inbox.friends?.length)}/></div></main>
      : <Chat room={room} inbox={inbox}/>}
  </div>
}

const page = root.dataset.friendsPage
const publication = root.dataset.publication
createRoot(root).render(page === 'messages' ? <Messaging/> : page || publication
  ? <PhotoViewerProvider>{page
    ? <FriendsPage page={page} characterId={Number(root.dataset.characterId)}/>
    : <Publication studio={publication === 'studio'}/>
  }</PhotoViewerProvider>
  : <Messaging room={Number(root.dataset.roomId || 1)}/>)
