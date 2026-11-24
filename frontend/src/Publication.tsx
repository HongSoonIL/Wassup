import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { MouseEvent } from 'react'
import { ArrowUpRight, CalendarDays, Check, ChevronDown, HeartHandshake, Image as ImageIcon, MessageCircle, RefreshCw, X } from 'lucide-react'
import './publication.css'
import { getProfile, returnToLogin } from './chat'
import type { Friend, Profile } from './chat'
import { discover, getFeed, PeopleHeader, PersonProfile } from './FriendsPage'
import type { FeedPage } from './FriendsPage'
import { FriendIdentity, PostPhoto } from './SocialMedia'
import { isPlainClick } from './ProfileBody'

type PhotoState = 'idle' | 'queued' | 'generating' | 'ready' | 'failed'
type Candidate = {
  post_id: number
  character_id: number
  name: string
  state: string
  version: number
  caption: string
  public_facts: string[]
  photo_id: string | null
  photo_alt: string
  photo_job: { state: PhotoState; detail: string }
  event_at: string | null
  publish_at: string
  calendar_labels: string[]
  operator_note: string
  last_error: string
  issues: string[]
}
type Week = {
  id: number
  week_start: string
  revision: number
  generation: string
  detail: string
  posts: Candidate[]
  photos: { key: string; character_id: number; alt: string }[]
}
type Save = (path: string, method: string, fields: object) => Promise<boolean>
type Editor = { post: Candidate; revision: number }
const photoStates: Record<PhotoState, string> = {
  idle: '생성 전',
  queued: '생성 대기 중',
  generating: '생성 중',
  ready: '준비됨',
  failed: '생성 실패',
}
const photoInProgress = (post: Candidate) =>
  post.photo_job.state === 'queued' || post.photo_job.state === 'generating'
const states: Record<string, string> = {
  draft: '검토 중',
  scheduled: '예약됨',
  published: '발행됨',
  failed: '발행 실패',
  skipped: '제외됨',
}
const issueLabels: Record<string, string> = {
  missing_photo: '사진 선택 필요',
  invalid_photo: '사진 설명 확인',
  missing_content: '본문과 공개 사실 확인',
  past_time: '미래 시각 선택',
  outside_week: '선택한 주의 시각으로 변경',
  calendar_conflict: '학사 일정 변경',
  schedule_conflict: '수면 또는 수업 시간과 겹침',
  event_order: '사건 이후로 발행 시각 변경',
  duplicate: '중복된 소식',
}
const stamp = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul',
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})
const reason = (err: unknown) => (err instanceof Error ? err.message : '연결을 확인해 주세요.')

class RequestError extends Error {
  constructor(readonly status: number, message: string) {
    super(message)
  }
}

async function request<T>(path: string, method = 'GET', fields?: object): Promise<T> {
  const token = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] || ''
  const response = await fetch(path, {
    method,
    cache: 'no-store',
    headers: fields ? { 'Content-Type': 'application/json', 'X-CSRFToken': token } : undefined,
    body: fields ? JSON.stringify(fields) : undefined,
  })
  if (response.status === 401) returnToLogin()
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = data?.detail || '요청을 처리할 수 없어요. 다시 로그인하거나 새로고침해 주세요.'
    const issues = data?.code === 'invalid_timing' ? data.posts?.[0]?.issues : null
    throw new RequestError(response.status, issues ? detail + ' ' + issues.map((key: string) => issueLabels[key] || key).join(' · ') : detail)
  }
  return data
}

type OpenProfile = { characterId: number; postId: number; authorTop: number }
function profileRoute(): OpenProfile | null {
  return history.state?.wsFeedProfile || null
}
function linkedPost() {
  const match = /^#post-(\d+)$/.exec(location.hash)
  return match ? Number(match[1]) : null
}

function FeedProfileDialog({ route, load, openPost }: {
  route: OpenProfile
  load: (id: number) => Promise<Profile>
  openPost: (id: number) => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const closing = useRef(false)
  const leavingForPost = useRef(false)
  useLayoutEffect(() => {
    const el = dialog.current!
    const author = document.getElementById('feed-author-'+route.postId)
    el.showModal()
    return () => {
      el.close()
      // The browser can focus a URL fragment after popstate has already run.
      requestAnimationFrame(() => {
        if (leavingForPost.current || profileRoute() || !author?.isConnected) return
        author.focus({ preventScroll: true })
        window.scrollBy(0, author.getBoundingClientRect().top - route.authorTop)
      })
    }
  }, [route.postId, route.authorTop])
  function close() {
    if (closing.current) return
    closing.current = true
    history.back()
  }
  function followPost(event: MouseEvent<HTMLDialogElement>) {
    if (event.target === event.currentTarget) { close(); return }
    if (!isPlainClick(event) || !(event.target instanceof Element)) return
    const link = event.target.closest<HTMLAnchorElement>('a[href^="/feed/#post-"]')
    if (!link) return
    event.preventDefault()
    leavingForPost.current = true
    openPost(Number(link.hash.slice(6)))
  }
  return <dialog className="feed-profile-dialog" aria-label="친구 프로필" ref={dialog}
    onCancel={event => { event.preventDefault(); close() }} onClick={followPost}>
    <div className="feed-profile-panel">
      <div className="feed-profile-bar"><b>친구 프로필</b>
        <button type="button" className="feed-profile-close" aria-label="프로필 닫기" onClick={close} autoFocus><X size={19}/></button>
      </div>
      <PersonProfile key={route.characterId} characterId={route.characterId} load={load}/>
    </div>
  </dialog>
}

function Feed() {
  const [page, setPage] = useState<FeedPage | null>(null)
  const [people, setPeople] = useState<Map<number, Friend['character']>>(new Map())
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [route, setRoute] = useState(profileRoute)
  const savedPosition = useRef<OpenProfile | null>(history.state?.wsFeedReturn || null)
  const [target, setTarget] = useState(() => savedPosition.current?.postId || linkedPost())
  const [unavailable, setUnavailable] = useState(false)
  const profiles = useRef(new Map<number, Promise<Profile>>())
  const sequence = useRef(0)
  const loadedOlder = useRef(false)
  const loading = useRef(false)
  const retryCursor = useRef<string | undefined>(undefined)

  const loadProfile = useCallback((id: number) => {
    const cached = profiles.current.get(id)
    if (cached) return cached
    const pending = getProfile(id).catch(error => { profiles.current.delete(id); throw error })
    profiles.current.set(id, pending)
    return pending
  }, [])

  const load = useCallback(async (cursor?: string) => {
    if (loading.current) return
    loading.current = true
    const current = ++sequence.current
    setBusy(true)
    setError('')
    retryCursor.current = cursor
    try {
      const data = await getFeed(cursor)
      if (sequence.current === current) {
        loadedOlder.current = !!cursor
        if (!cursor) profiles.current.clear()
        setPage(prev => cursor && prev ? { ...data, posts: [...prev.posts, ...data.posts] } : data)
      }
    } catch (e) {
      if (sequence.current === current) setError(reason(e))
    } finally {
      if (sequence.current === current) { setBusy(false); loading.current = false }
    }
  }, [])

  useEffect(() => {
    void load()
    let active = true
    discover().then(data => {
      if (active) setPeople(new Map(data.characters.map(person => [person.id, person])))
    }).catch(() => { /* The feed still has the public author name. */ })
    const refresh = () => {
      if (!document.hidden && !loadedOlder.current && !profileRoute() && !history.state?.wsPhoto && window.scrollY < 8) void load()
    }
    const restore = () => setRoute(profileRoute())
    const hashChanged = (event: HashChangeEvent) => {
      if (new URL(event.oldURL).pathname === '/feed/' && location.pathname === '/feed/') {
        setUnavailable(false)
        setTarget(linkedPost())
      }
    }
    window.addEventListener('popstate', restore)
    window.addEventListener('hashchange', hashChanged)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      active = false
      sequence.current++
      loading.current = false
      window.removeEventListener('popstate', restore)
      window.removeEventListener('hashchange', hashChanged)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [load])

  useEffect(() => {
    if (!target || !page || route || busy || error) return
    const article = document.getElementById('post-'+target)
    if (article) {
      const position = savedPosition.current
      const author = position && document.getElementById('feed-author-'+position.postId)
      if (position && author && position.postId === target) {
        author.focus({ preventScroll: true })
        window.scrollBy(0, author.getBoundingClientRect().top - position.authorTop)
      } else {
        article.scrollIntoView({ block: 'start' })
        article.focus({ preventScroll: true })
      }
      savedPosition.current = null
      setTarget(null)
    } else if (page.next_cursor) {
      void load(page.next_cursor)
    } else {
      setUnavailable(true)
      setTarget(null)
    }
  }, [target, page, route, busy, error, load])

  function openProfile(event: MouseEvent<HTMLAnchorElement>, characterId: number, postId: number) {
    if (!isPlainClick(event)) return
    event.preventDefault()
    const next = { characterId, postId, authorTop: event.currentTarget.getBoundingClientRect().top }
    history.replaceState({ ...history.state, wsFeedReturn: next }, '')
    history.pushState({ ...history.state, wsFeedProfile: next }, '', event.currentTarget.href)
    setRoute(next)
  }
  function openPost(id: number) {
    history.pushState(null, '', '/feed/#post-'+id)
    setRoute(null)
    setUnavailable(false)
    setTarget(id)
  }

  return <>
    <div className="people-heading"><h1>친구 피드</h1><button className="feed-refresh" type="button" aria-label="피드 새로고침" disabled={busy} onClick={() => void load()}><RefreshCw size={17}/></button></div>
    {error && <div role="alert" className="people-alert">{error}<button onClick={() => void load(retryCursor.current)}>다시 시도</button></div>}
    {unavailable && <p role="status" className="people-note">이 게시물을 볼 수 없어요.</p>}
    {page?.posts.length === 0 && <div className="people-empty"><HeartHandshake size={28}/><p>친구가 올린 소식이 여기에 보여요.</p><a href="/discover/">친구 만나기 <ArrowUpRight size={16}/></a></div>}
    <div className="feed-posts">{page?.posts.map(post => {
      const person = people.get(post.character_id) || { name: post.name, profile_image: null }
      return <article key={post.id} className="feed-post" id={'post-'+post.id} tabIndex={-1} aria-label={post.name+'의 소식'}>
        <div className="feed-post-head">
          <a className="feed-author" id={'feed-author-'+post.id} href={`/people/${post.character_id}/`}
            aria-label={post.name+' 프로필'} aria-describedby={'feed-time-'+post.id} aria-haspopup="dialog"
            onClick={event => openProfile(event, post.character_id, post.id)}>
            <FriendIdentity person={person} detail={<time id={'feed-time-'+post.id} dateTime={post.published_at}>{stamp.format(new Date(post.published_at))}</time>}/>
          </a>
          {post.photo.ai_generated && <small className="feed-ai">AI 생성 사진</small>}
        </div>
        <PostPhoto src={post.photo.url} alt={post.photo.alt} className="feed-post-photo"/>
        <div className="feed-post-foot">
          <p>{post.caption}</p>
          <a className="feed-quote" href={`/chat/from-post/${post.id}/`}><MessageCircle size={15}/>{post.name}에게 말 걸기<ArrowUpRight size={15}/></a>
        </div>
      </article>
    })}</div>
    {busy && <p className="people-note" role="status">소식 불러오는 중…</p>}
    {page?.next_cursor && <button className="profile-more" disabled={busy} onClick={() => void load(page.next_cursor!)}>이전 소식 더 보기<ChevronDown size={16}/></button>}
    {route && <FeedProfileDialog key={route.postId} route={route} load={loadProfile} openPost={openPost}/>}
  </>
}

function CandidateEditor({
  editor,
  current,
  week,
  url,
  busy,
  save,
  close,
  reload,
}: {
  editor: Editor
  current: Candidate
  week: Week
  url: string
  busy: boolean
  save: Save
  close: () => void
  reload: () => void
}) {
  const post = editor.post
  const [photo, setPhoto] = useState(post.photo_id || '')
  const [alt, setAlt] = useState(post.photo_alt)
  const stale =
    editor.revision !== week.revision ||
    post.version !== current.version ||
    post.state !== current.state
  async function submit(form: HTMLFormElement) {
    const fields = new FormData(form)
    function changedTime(name: 'event_at' | 'publish_at') {
      const value = fields.get(name)
      if (!value) return {}
      const local = value + '+09:00'
      const initial = post[name]
      return initial && Date.parse(local) === Date.parse(initial) ? {} : { [name]: local }
    }
    const saved = await save(url, 'PATCH', {
      expected_revision: editor.revision,
      version: post.version,
      caption: fields.get('caption'),
      public_facts: String(fields.get('public_facts'))
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean),
      operator_note: fields.get('operator_note'),
      photo_id: photo || null,
      photo_alt: alt,
      ...changedTime('event_at'),
      ...changedTime('publish_at'),
    })
    if (saved) close()
  }
  return (
    <form
      className="pub-editor"
      onSubmit={(e) => {
        e.preventDefault()
        void submit(e.currentTarget)
      }}
    >
      <label>
        피드 본문
        <textarea name="caption" required maxLength={4000} defaultValue={post.caption} />
      </label>
      <label>
        장면 · 공개 사실 (한 줄에 한 문장, 최대 5개)
        <textarea name="public_facts" required defaultValue={post.public_facts.join('\n')} />
      </label>
      <label>
        캐릭터의 사진
        <select
          value={photo}
          onChange={(e) => {
            const next = week.photos.find((p) => p.key === e.target.value)
            setPhoto(next?.key || '')
            setAlt(next?.alt || '')
          }}
        >
          <option value="">사진 없음</option>
          {week.photos
            .filter((p) => p.character_id === post.character_id)
            .map((p) => (
              <option key={p.key} value={p.key}>
                {p.alt}
              </option>
            ))}
        </select>
      </label>
      {photo && (
        <img
          className="pub-thumb"
          src={'/api/ops/photos/' + encodeURIComponent(photo) + '/'}
          alt={alt}
        />
      )}
      <label>
        사진 대체 텍스트
        <input value={alt} maxLength={200} onChange={(e) => setAlt(e.target.value)} />
      </label>
      <label>
        사건 시각 · 한국 시간
        <input
          name="event_at"
          type="datetime-local"
          step="1"
          required={!!post.event_at}
          defaultValue={post.event_at?.slice(0, 19) || ''}
        />
      </label>
      <label>
        예약 시각 · 한국 시간
        <input
          name="publish_at"
          type="datetime-local"
          step="1"
          required
          defaultValue={post.publish_at.slice(0, 19)}
        />
      </label>
      <label>
        운영 메모
        <textarea name="operator_note" maxLength={4000} defaultValue={post.operator_note} />
      </label>
      {photoInProgress(current) && <p>수정한 내용으로 사진을 만들려면 저장 후 다시 생성해 주세요.</p>}
      {stale && (
        <div className="pub-stale" role="status">
          <p>편집 중에 주간 계획이 바뀌었어요. 최신 내용을 확인해 주세요.</p>
          {current.state !== 'published' && (
            <button type="button" disabled={busy} onClick={reload}>
              최신 내용으로 다시 열기
            </button>
          )}
        </div>
      )}
      <div className="pub-actions">
        <button className="accent" disabled={busy || stale}>
          초안으로 저장
        </button>
        <button type="button" disabled={busy} onClick={close}>
          취소
        </button>
      </div>
    </form>
  )
}

function CandidateCard({
  post,
  week,
  busy,
  save,
  editor,
  onEdit,
}: {
  post: Candidate
  week: Week
  busy: boolean
  save: Save
  editor: Editor | null
  onEdit: (editor: Editor | null) => void
}) {
  const url = '/api/ops/weeks/' + week.id + '/posts/' + post.post_id + '/'
  const edit = () => onEdit({ post, revision: week.revision })
  const job = post.photo_job
  return (
    <article className="pub-card pub-candidate">
      {!editor &&
        (post.photo_id ? (
          <a
            className="pub-preview"
            href={'/api/ops/photos/' + encodeURIComponent(post.photo_id) + '/'}
            target="_blank"
            rel="noreferrer"
          >
            <img
              src={'/api/ops/photos/' + encodeURIComponent(post.photo_id) + '/'}
              alt={post.photo_alt || post.name + ' 사진 미리보기'}
            />
          </a>
        ) : (
          <div className="pub-preview pub-placeholder">
            <ImageIcon size={24} aria-hidden="true" />
            사진 미리보기 없음
          </div>
        ))}
      <div className="pub-info">
        <div className="pub-by">
          <strong>{post.name}</strong>
          <span className={'pub-state ' + post.state}>{states[post.state]}</span>
        </div>
        <div className={'pub-photo-status ' + job.state} role="status">
          <span>사진 · {photoStates[job.state]}</span>
          {job.detail && <p>{job.detail}</p>}
        </div>
        {editor ? (
          <CandidateEditor
            key={editor.revision + '-' + editor.post.version + '-' + editor.post.state}
            editor={editor}
            current={post}
            week={week}
            url={url}
            busy={busy}
            save={save}
            close={() => onEdit(null)}
            reload={edit}
          />
        ) : (
          <>
            <p className="pub-copy">{post.caption}</p>
            <dl className="pub-details">
              <dt>사건 시각</dt>
              <dd>
                {post.event_at ? (
                  <time dateTime={post.event_at}>{stamp.format(new Date(post.event_at))} KST</time>
                ) : (
                  '미기록'
                )}
              </dd>
              <dt>발행 예정</dt>
              <dd>
                <time dateTime={post.publish_at}>{stamp.format(new Date(post.publish_at))} KST</time>
              </dd>
              {post.calendar_labels.length > 0 && (
                <>
                  <dt>관련 일정</dt>
                  <dd>{post.calendar_labels.join(' · ')}</dd>
                </>
              )}
              <dt>장면 · 공개 사실</dt>
              <dd>
                <ul>
                  {post.public_facts.map((fact, i) => (
                    <li key={i}>{fact}</li>
                  ))}
                </ul>
              </dd>
              {post.photo_alt && (
                <>
                  <dt>대체 텍스트</dt>
                  <dd>{post.photo_alt}</dd>
                </>
              )}
              {post.operator_note && (
                <>
                  <dt>운영 메모</dt>
                  <dd>{post.operator_note}</dd>
                </>
              )}
            </dl>
            {post.issues.length > 0 && (
              <p className="pub-issue">{post.issues.map((x) => issueLabels[x] || x).join(' · ')}</p>
            )}
            {post.last_error && <p className="pub-issue">{post.last_error}</p>}
            <div className="pub-actions">
              {post.state !== 'published' && (
                <button disabled={busy} onClick={edit}>
                  수정
                </button>
              )}
              {post.state === 'draft' && (
                <button
                  disabled={busy || photoInProgress(post)}
                  onClick={() =>
                    void save(url + 'regenerate-photo/', 'POST', { version: post.version })
                  }
                >
                  <RefreshCw size={14} />
                  {job.state === 'failed'
                    ? '사진 생성 재시도'
                    : post.photo_id
                      ? '사진 다시 생성'
                      : '사진 생성'}
                </button>
              )}
              {['draft', 'scheduled', 'failed'].includes(post.state) && (
                <button
                  disabled={busy}
                  onClick={() => void save(url, 'PATCH', { version: post.version, state: 'skipped' })}
                >
                  이번 주 제외
                </button>
              )}
              {post.state === 'skipped' && (
                <button
                  disabled={busy}
                  onClick={() => void save(url, 'PATCH', { version: post.version, state: 'draft' })}
                >
                  검토에 다시 포함
                </button>
              )}
              {post.state === 'failed' && (
                <button
                  disabled={busy}
                  onClick={() => void save(url + 'retry/', 'POST', { version: post.version })}
                >
                  발행 재시도
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </article>
  )
}

function Studio() {
  const [date, setDate] = useState(() => document.getElementById('root')?.dataset.weekStart || '')
  const [week, setWeek] = useState<Week | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [editor, setEditor] = useState<Editor | null>(null)
  const [pollError, setPollError] = useState('')
  const drafts = week?.posts.filter((p) => p.state === 'draft') || []
  const blocked = drafts.filter(
    (p) => p.issues.length > 0 || photoInProgress(p) || p.photo_job.state === 'failed',
  )
  async function openWeek(retryFailed = false) {
    setBusy(true)
    setEditor(null)
    setError('')
    setPollError('')
    setWeek(null)
    try {
      const created = await request<{ id: number }>('/api/ops/weeks/generate/', 'POST', {
        week_start: date,
        retry_failed: retryFailed,
      })
      setWeek(await request<Week>('/api/ops/weeks/' + created.id + '/'))
      const url = new URL(window.location.href)
      url.searchParams.set('week_start', date)
      window.history.replaceState(window.history.state, '', url)
    } catch (e) {
      setError(reason(e))
    } finally {
      setBusy(false)
    }
  }
  useEffect(() => {
    const pending = week?.generation === 'pending'
    if (!week || busy) return
    const generatingPhotos = week.posts.some(photoInProgress)
    if (!pending && !generatingPhotos && !week.posts.some((p) => p.state === 'scheduled')) return
    const delay = pending || generatingPhotos ? 1500 : 5000
    let closed = false
    const path = '/api/ops/weeks/' + week.id + '/'
    const refresh = async () => {
      try {
        const next = await request<Week>(path)
        if (!closed) {
          setPollError('')
          setWeek(next)
        }
      } catch (e) {
        if (!closed) {
          setPollError(reason(e))
          timer = window.setTimeout(refresh, delay)
        }
      }
    }
    let timer = window.setTimeout(refresh, delay)
    return () => {
      closed = true
      clearTimeout(timer)
    }
  }, [week, busy])
  async function save(path: string, method: string, fields: object) {
    if (!week) return false
    setBusy(true)
    setError('')
    try {
      await request(path, method, { expected_revision: week.revision, ...fields })
      setWeek(await request<Week>('/api/ops/weeks/' + week.id + '/'))
      setPollError('')
      return true
    } catch (e) {
      setError(reason(e))
      if (e instanceof RequestError && (e.status === 409 || e.status === 422)) {
        try {
          setWeek(await request<Week>('/api/ops/weeks/' + week.id + '/'))
        } catch (refreshError) {
          setPollError(reason(refreshError))
        }
      }
      return false
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <div className="pub-title">
        <h1>주간 발행</h1>
        <a href="/feed/">친구 피드 ↗</a>
      </div>
      <form
        className="pub-toolbar"
        onSubmit={(e) => {
          e.preventDefault()
          void openWeek()
        }}
      >
        <label>
          <CalendarDays size={15} />
          시작일 · 월요일
          <input
            type="date"
            required
            value={date}
            onChange={(e) => {
              setDate(e.target.value)
              setWeek(null)
              setError('')
              setPollError('')
            }}
            disabled={busy || editor !== null}
          />
        </label>
        <button disabled={busy || editor !== null} className="accent">
          주간 계획 열기
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      {pollError && <p role="alert">{pollError}</p>}
      {week?.generation === 'pending' && <p role="status">주간 후보를 준비하고 있어요.</p>}
      {week?.generation === 'failed' && (
        <>
          <p role="alert">{week.detail}</p>
          <button disabled={busy} onClick={() => void openWeek(true)}>
            후보 생성 다시 시도
          </button>
        </>
      )}
      {week?.generation === 'ready' && (
        <>
          <div className="pub-title pub-sub">
            <h2>발행 후보 {week.posts.length}</h2>
            <button
              disabled={busy || editor !== null || drafts.length === 0 || blocked.length > 0}
              className="accent"
              onClick={() => void save('/api/ops/weeks/' + week.id + '/approve/', 'POST', {})}
            >
              <Check size={16} />
              주간 묶음 승인
            </button>
          </div>
          {week.posts.length === 0 && <p className="pub-empty">이번 주에는 검토할 후보가 없어요.</p>}
          {blocked.length > 0 && (
            <p className="pub-review-summary" role="status">
              후보 {blocked.length}건의 사진 또는 편집 내용을 확인한 뒤 승인할 수 있어요.
            </p>
          )}
          <div className="pub-grid">
            {week.posts.map((p) => (
              <CandidateCard
                key={p.post_id}
                post={p}
                week={week}
                busy={busy || (editor !== null && editor.post.post_id !== p.post_id)}
                save={save}
                editor={editor?.post.post_id === p.post_id ? editor : null}
                onEdit={setEditor}
              />
            ))}
          </div>
        </>
      )}
    </>
  )
}

export default function Publication({ studio, canPublish }: { studio: boolean; canPublish: boolean }) {
  if (!studio) return <div className="people"><PeopleHeader page="feed" canPublish={canPublish}/><main className="people-main feed-main"><Feed/></main></div>
  const csrf = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] || ''
  return (
    <div className="pub">
      <header>
        <a href="/" aria-label="wassup 홈">
          wassup<span>.</span>
        </a>
        <nav>
          <a href="/home/">친구 홈</a>
          <form method="post" action="/logout/">
            <input type="hidden" name="csrfmiddlewaretoken" value={csrf} />
            <button>로그아웃</button>
          </form>
        </nav>
      </header>
      <main><Studio /></main>
    </div>
  )
}
