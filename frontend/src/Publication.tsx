import { useEffect, useRef, useState } from 'react'
import { CalendarDays, Check, HeartHandshake, RefreshCw } from 'lucide-react'
import './publication.css'

type PublicPost = {
  id: number
  name: string
  caption: string
  published_at: string
  photo: { url: string; alt: string; ai_generated: boolean }
}
type FeedPage = { posts: PublicPost[]; next_cursor: string | null }
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
  publish_at: string
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
  schedule_conflict: '수면 시간과 겹침',
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

async function request<T>(path: string, method = 'GET', fields?: object): Promise<T> {
  const token = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] || ''
  const response = await fetch(path, {
    method,
    cache: 'no-store',
    headers: fields ? { 'Content-Type': 'application/json', 'X-CSRFToken': token } : undefined,
    body: fields ? JSON.stringify(fields) : undefined,
  })
  const data = await response.json().catch(() => null)
  if (!response.ok)
    throw new Error(data?.detail || '요청을 처리할 수 없어요. 다시 로그인하거나 새로고침해 주세요.')
  return data
}

function Feed() {
  const [page, setPage] = useState<FeedPage | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const sequence = useRef(0)
  async function load(cursor?: string) {
    const current = ++sequence.current
    setBusy(true)
    setError('')
    try {
      const data = await request<FeedPage>(
        '/api/feed/' + (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''),
      )
      if (sequence.current === current)
        setPage((prev) => (cursor && prev ? { ...data, posts: [...prev.posts, ...data.posts] } : data))
    } catch (e) {
      if (sequence.current === current) setError(reason(e))
    } finally {
      if (sequence.current === current) setBusy(false)
    }
  }
  useEffect(() => {
    const refresh = () => {
      if (!document.hidden) void load()
    }
    refresh()
    document.addEventListener('visibilitychange', refresh)
    return () => {
      sequence.current++
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [])
  return (
    <>
      <div className="pub-title">
        <h1>친구 피드</h1>
        <button aria-label="피드 새로고침" disabled={busy} onClick={() => void load()}>
          <RefreshCw size={17} />
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      {page?.posts.length === 0 && (
        <div className="pub-empty">
          <HeartHandshake size={28} />
          <p>친구가 올린 소식이 여기에 보여요.</p>
        </div>
      )}
      <div className="pub-grid">
        {page?.posts.map((p) => (
          <article key={p.id} className="pub-card">
            <a className="pub-photo-link" href={`/chat/from-post/${p.id}/`} aria-label={`${p.name}에게 이 사진으로 말 걸기`}>
              <img src={p.photo.url} alt={p.photo.alt} />
            </a>
            <div className="pub-info">
              <strong>{p.name}</strong>
              <time>{stamp.format(new Date(p.published_at))}</time>
              <p>{p.caption}</p>
              <a className="pub-talk" href={`/chat/from-post/${p.id}/`}>{p.name}에게 말 걸기 ↗</a>
              {p.photo.ai_generated && <small>AI 생성 사진</small>}
            </div>
          </article>
        ))}
      </div>
      {busy && <p role="status">소식 불러오는 중…</p>}
      {page?.next_cursor && (
        <button disabled={busy} onClick={() => void load(page.next_cursor!)}>
          이전 소식 더 보기
        </button>
      )}
    </>
  )
}

function CandidateCard({
  post,
  week,
  busy,
  save,
  editing,
  onEdit,
}: {
  post: Candidate
  week: Week
  busy: boolean
  save: Save
  editing: boolean
  onEdit: (active: boolean) => void
}) {
  const [caption, setCaption] = useState(post.caption)
  const [facts, setFacts] = useState(post.public_facts.join('\n'))
  const [photo, setPhoto] = useState(post.photo_id || '')
  const [alt, setAlt] = useState(post.photo_alt)
  const url = '/api/ops/weeks/' + week.id + '/posts/' + post.post_id + '/'
  async function submit(form: HTMLFormElement) {
    if (
      await save(url, 'PATCH', {
        caption,
        public_facts: facts
          .split('\n')
          .map((x) => x.trim())
          .filter(Boolean),
        photo_id: photo || null,
        photo_alt: alt,
        publish_at: new FormData(form).get('publish_at') + ':00+09:00',
      })
    )
      onEdit(false)
  }
  return (
    <article className="pub-card pub-candidate">
      <div className="pub-info">
        <div className="pub-by">
          <strong>{post.name}</strong>
          <span className={'pub-state ' + post.state}>{states[post.state]}</span>
        </div>
        <time>{stamp.format(new Date(post.publish_at))} KST</time>
        {editing ? (
          <form
            onSubmit={(e) => {
              e.preventDefault()
              void submit(e.currentTarget)
            }}
          >
            <label>
              피드 본문
              <textarea value={caption} onChange={(e) => setCaption(e.target.value)} />
            </label>
            <label>
              대화에서 쓸 공개 사실
              <textarea value={facts} onChange={(e) => setFacts(e.target.value)} />
            </label>
            <label>
              사진
              <select
                value={photo}
                onChange={(e) => {
                  setPhoto(e.target.value)
                  setAlt(week.photos.find((p) => p.key === e.target.value)?.alt || '')
                }}
              >
                <option value="">사진 선택</option>
                {week.photos
                  .filter((p) => p.character_id === post.character_id)
                  .map((p) => (
                    <option key={p.key} value={p.key}>
                      {p.alt}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              사진 설명
              <input value={alt} maxLength={200} onChange={(e) => setAlt(e.target.value)} />
            </label>
            <label>
              예약 시각 · 한국 시간
              <input
                name="publish_at"
                type="datetime-local"
                required
                defaultValue={post.publish_at.slice(0, 16)}
              />
            </label>
            <button className="accent" disabled={busy}>
              초안 저장
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setCaption(post.caption)
                setFacts(post.public_facts.join('\n'))
                setPhoto(post.photo_id || '')
                setAlt(post.photo_alt)
                onEdit(false)
              }}
            >
              취소
            </button>
          </form>
        ) : (
          <>
            <p>{post.caption}</p>
            <ul>
              {post.public_facts.map((fact, i) => (
                <li key={i}>{fact}</li>
              ))}
            </ul>
            {post.photo_id && (
              <img
                className="pub-thumb"
                src={'/api/ops/photos/' + post.photo_id + '/'}
                alt={post.photo_alt}
              />
            )}
            {post.issues.length > 0 && (
              <p className="pub-issue">{post.issues.map((x) => issueLabels[x] || x).join(' · ')}</p>
            )}
            {post.last_error && <p className="pub-issue">{post.last_error}</p>}
            <div className="pub-actions">
              {post.state !== 'published' && (
                <button disabled={busy} onClick={() => onEdit(true)}>
                  수정
                </button>
              )}
              {['draft', 'scheduled', 'failed'].includes(post.state) && (
                <button disabled={busy} onClick={() => void save(url, 'PATCH', { state: 'skipped' })}>
                  이번 주 제외
                </button>
              )}
              {post.state === 'failed' && (
                <button
                  disabled={busy}
                  onClick={() => void save(url + 'retry/', 'POST', { version: post.version })}
                >
                  다시 시도
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </article>
  )
}

function monday() {
  const d = new Date(Date.now() + 9 * 3600000)
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7))
  return d.toISOString().slice(0, 10)
}
function Studio() {
  const [date, setDate] = useState(monday)
  const [week, setWeek] = useState<Week | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [editor, setEditor] = useState<number | null>(null)
  const [waiting, setWaiting] = useState(false)
  async function openWeek() {
    setBusy(true)
    setEditor(null)
    setError('')
    setWeek(null)
    try {
      const created = await request<{ id: number }>('/api/ops/weeks/generate/', 'POST', {
        week_start: date,
      })
      setWeek(await request<Week>('/api/ops/weeks/' + created.id + '/'))
    } catch (e) {
      setError(reason(e))
    } finally {
      setBusy(false)
    }
  }
  useEffect(() => {
    const pending = week?.generation === 'pending'
    setWaiting(pending)
    if (
      !week ||
      busy ||
      editor !== null ||
      (!pending && !week.posts.some((p) => p.state === 'scheduled'))
    )
      return
    let closed = false
    const timer = window.setTimeout(
      async () => {
        try {
          const next = await request<Week>('/api/ops/weeks/' + week.id + '/')
          if (!closed) setWeek(next)
        } catch (e) {
          if (!closed) {
            setError(reason(e))
            setWaiting(false)
          }
        }
      },
      pending ? 1200 : 5000,
    )
    return () => {
      closed = true
      clearTimeout(timer)
    }
  }, [week, busy, editor])
  async function save(path: string, method: string, fields: object) {
    if (!week) return false
    setBusy(true)
    setError('')
    try {
      await request(path, method, { ...fields, expected_revision: week.revision })
      setWeek(await request<Week>('/api/ops/weeks/' + week.id + '/'))
      return true
    } catch (e) {
      setError(reason(e))
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
            }}
            disabled={busy || editor !== null}
          />
        </label>
        <button disabled={busy || editor !== null} className="accent">
          주간 계획 열기
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      {waiting && <p role="status">주간 후보를 준비하고 있어요.</p>}
      {week?.generation === 'failed' && <p role="alert">{week.detail}</p>}
      {week?.generation === 'ready' && (
        <>
          <div className="pub-title pub-sub">
            <h2>발행 후보 {week.posts.length}</h2>
            <button
              disabled={busy || editor !== null || !week.posts.some((p) => p.state === 'draft')}
              className="accent"
              onClick={() => void save('/api/ops/weeks/' + week.id + '/approve/', 'POST', {})}
            >
              <Check size={16} />
              주간 묶음 승인
            </button>
          </div>
          <div className="pub-grid">
            {week.posts.map((p) => (
              <CandidateCard
                key={p.post_id + '-' + p.version + '-' + p.state}
                post={p}
                week={week}
                busy={busy || (editor !== null && editor !== p.post_id)}
                save={save}
                editing={editor === p.post_id}
                onEdit={(active) => setEditor(active ? p.post_id : null)}
              />
            ))}
          </div>
        </>
      )}
    </>
  )
}

export default function Publication({ studio, canPublish }: { studio: boolean; canPublish: boolean }) {
  const csrf = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] || ''
  return (
    <div className="pub">
      <header>
        <a href="/feed/">
          wassup<span>.</span>
        </a>
        <nav>
          {canPublish && !studio && <a href="/studio/">주간 발행</a>}
          <form method="post" action="/logout/">
            <input type="hidden" name="csrfmiddlewaretoken" value={csrf} />
            <button>로그아웃</button>
          </form>
        </nav>
      </header>
      <main>{studio ? <Studio /> : <Feed />}</main>
    </div>
  )
}
