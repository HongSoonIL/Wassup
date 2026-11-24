import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { getPreviousPost, RequestError } from './chat'
import type { PostSummary } from './chat'
import { PostPhoto, useTransientPhoto } from './SocialMedia'
import './previous-post.css'

type Preview = { status: 'closed' | 'loading' | 'unavailable' | 'error' } | { status: 'ready'; post: PostSummary }
type ReadingPosition = { scroller: HTMLDialogElement | null; offset: number }

const published = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})

function readingOffset(button: HTMLButtonElement, scroller: HTMLDialogElement | null) {
  return button.getBoundingClientRect().top - (scroller?.getBoundingClientRect().top ?? 0)
}

export default function PreviousPost({ id, version }: Pick<PostSummary, 'id' | 'version'>) {
  const [preview, setPreview] = useState<Preview>({ status: 'closed' })
  const expanded = preview.status !== 'closed'
  const toggle = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const readingPosition = useRef<ReadingPosition | null>(null)
  const pending = useRef<AbortController | null>(null)
  const panelId = useId()
  const post = preview.status === 'ready' ? preview.post : null
  const viewerKey = useTransientPhoto(`${id}:${version}:${post?.id}:${post?.version}`,
    post ? { src: post.photo.url, alt: post.photo.alt } : undefined)
  const message = preview.status === 'loading' ? '이전 소식 불러오는 중…'
    : preview.status === 'unavailable' ? '이전 소식을 볼 수 없어요.'
    : preview.status === 'error' ? '이전 소식을 불러오지 못했어요.' : ''

  const refresh = useCallback(async () => {
    pending.current?.abort()
    const request = new AbortController()
    pending.current = request
    if (panel.current?.contains(document.activeElement)) toggle.current?.focus({ preventScroll: true })
    setPreview({ status: 'loading' })
    try {
      const { previous_post: next } = await getPreviousPost(id, version, request.signal)
      if (!request.signal.aborted) setPreview(next ? { status: 'ready', post: next } : { status: 'unavailable' })
    } catch (error) {
      if (!request.signal.aborted) {
        setPreview({ status: error instanceof RequestError && error.status === 404 ? 'unavailable' : 'error' })
      }
    }
  }, [id, version])

  useLayoutEffect(() => () => pending.current?.abort(), [])

  useEffect(() => {
    if (!expanded) return
    const revalidate = () => { if (!document.hidden) void refresh() }
    const restored = (event: PageTransitionEvent) => { if (event.persisted) revalidate() }
    window.addEventListener('focus', revalidate)
    window.addEventListener('pageshow', restored)
    document.addEventListener('visibilitychange', revalidate)
    return () => {
      window.removeEventListener('focus', revalidate)
      window.removeEventListener('pageshow', restored)
      document.removeEventListener('visibilitychange', revalidate)
    }
  }, [expanded, refresh])

  useLayoutEffect(() => {
    if (expanded || !readingPosition.current || !toggle.current) return
    const { scroller, offset } = readingPosition.current
    readingPosition.current = null
    toggle.current.focus({ preventScroll: true })
    const delta = readingOffset(toggle.current, scroller) - offset
    if (scroller) scroller.scrollBy(0, delta)
    else window.scrollBy(0, delta)
  }, [expanded])

  function open() {
    if (!toggle.current) return
    const scroller = toggle.current.closest<HTMLDialogElement>('dialog[open]')
    readingPosition.current = { scroller, offset: readingOffset(toggle.current, scroller) }
    void refresh()
  }

  function close() {
    pending.current?.abort()
    setPreview({ status: 'closed' })
  }

  return <div className="previous-post">
    <button className="previous-post-toggle" type="button" ref={toggle} aria-expanded={expanded} aria-controls={panelId}
      onClick={expanded ? close : open}>
      {expanded ? '이전 소식 접기' : '이전 소식 보기'}
      {expanded ? <ChevronUp size={15} aria-hidden="true"/> : <ChevronDown size={15} aria-hidden="true"/>}
    </button>
    <div className="previous-post-panel" id={panelId} ref={panel} hidden={!expanded}>
      <p className={message ? 'previous-post-status' : 'ws-sr'} role="status">
        {message || (post ? '이전 소식을 불러왔어요.' : '')}
      </p>
      {preview.status === 'error' && <button className="previous-post-inline" type="button" onClick={() => void refresh()}>다시 시도</button>}
      {post && <>
        <PostPhoto className="previous-post-photo" src={post.photo.url} alt={post.photo.alt} viewerKey={viewerKey}/>
        <div className="previous-post-copy">
          <time dateTime={post.published_at}>{published.format(new Date(post.published_at))}</time>
          <p>{post.caption}</p>
          <button className="previous-post-inline" type="button" onClick={close}>접고 돌아가기<ChevronUp size={14} aria-hidden="true"/></button>
        </div>
      </>}
    </div>
  </div>
}
