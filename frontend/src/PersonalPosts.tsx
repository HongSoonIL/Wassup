import { useId, useRef, useState } from 'react'
import { ChevronDown, LockKeyhole, Trash2, UserRound } from 'lucide-react'
import { EmptyState, SectionTitle } from './PageParts'
import { RequestError } from './chat'
import { PhotoPicker, PhotoPreview, usePhotoDraft } from './PhotoDraft'
import { PostPhoto, useTransientPhoto } from './SocialMedia'
import { createPost, deletePost, getOwnPosts, postKey, usePostTimeline } from './posts'
import type { PersonalPost } from './posts'
import { useDraftRequest } from './useDraftRequest'
import './personal-posts.css'

const stamp = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})

export function PersonalPostComposer({ onCreate }: { onCreate: (post: PersonalPost) => void }) {
  const [caption, setCaption] = useState('')
  const photo = usePhotoDraft()
  const draftRequest = useDraftRequest(caption, photo.selection?.file)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [published, setPublished] = useState(false)
  const lock = useRef(false)
  const description = useId()
  const input = useId()

  async function publish() {
    if (lock.current || photo.error || (!caption.trim() && !photo.selection)) return
    lock.current = true
    setBusy(true)
    setError('')
    setPublished(false)
    try {
      const { post } = await createPost(caption.trim(), draftRequest.key(), photo.selection?.file)
      draftRequest.complete()
      onCreate(post)
      setCaption('')
      photo.clear()
      setPublished(true)
    } catch (e) { setError((e as Error).message) }
    finally { lock.current = false; setBusy(false) }
  }
  function edited() { setError(''); setPublished(false) }
  return <form className="personal-composer" aria-label="내 일상 쓰기" aria-describedby={description}
    onSubmit={event => { event.preventDefault(); void publish() }}>
    <label htmlFor={input}>내 일상 쓰기</label>
    <textarea id={input} rows={2} maxLength={4000} value={caption} disabled={busy}
      placeholder="오늘은 어떤 일이 있었나요?" onChange={event => { setCaption(event.target.value); edited() }}/>
    <PhotoPreview draft={photo} busy={busy} note="답장을 위해 이 사진을 AI 모델에 보낼 수 있어요."/>
    <p className="post-disclosure" id={description}><LockKeyhole size={15} aria-hidden="true"/>
      <span>내 계정에서만 볼 수 있어요. 먼저 연락받기를 켠 AI 친구가 글과 사진을 보고 말을 걸 수 있어요.</span>
    </p>
    {error && <p className="upload-error" role="alert">{error}</p>}
    <div className="personal-controls">
      <PhotoPicker draft={photo} disabled={busy} onSelect={edited}/>
      <span className="post-success" role="status">{published && '일상을 올렸어요.'}</span>
      <button className="people-primary" disabled={busy || !!photo.error || (!caption.trim() && !photo.selection)}>
        {busy ? '올리는 중…' : error ? '다시 올리기' : '올리기'}
      </button>
    </div>
  </form>
}

export function PersonalPostCard({ post, onDelete }: { post: PersonalPost; onDelete: (post: PersonalPost) => void }) {
  const [confirm, setConfirm] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState('')
  const deleteButton = useRef<HTMLButtonElement>(null)
  const key = postKey(post)
  const photo = post.photo
  const photoKey = useTransientPhoto(key, photo ? { src: photo.url, alt: photo.alt } : undefined)
  async function remove() {
    if (deleting) return
    setDeleting(true)
    setError('')
    try {
      await deletePost(post.id)
      onDelete(post)
    } catch (e) {
      if (e instanceof RequestError && e.status === 404) onDelete(post)
      else { setError((e as Error).message); setDeleting(false) }
    }
  }
  return <article id={key} tabIndex={-1} className="feed-post personal-post" aria-label="내 게시물">
    <div className="feed-post-head">
      <a className="feed-author" href="/me/" aria-label="내 프로필">
        <span className="ws-identity"><span className="personal-avatar"><UserRound size={21} aria-hidden="true"/></span>
          <span className="ws-identity-copy"><strong>나</strong><time className="ws-identity-detail" dateTime={post.published_at}>{stamp.format(new Date(post.published_at))}</time></span>
        </span>
      </a>
      <button className="post-remove" aria-label="게시물 삭제" aria-expanded={confirm} disabled={deleting}
        ref={deleteButton} onClick={() => { setConfirm(value => !value); setError('') }}><Trash2 size={17}/></button>
    </div>
    {photo && <PostPhoto src={photo.url} alt={photo.alt} className="feed-post-photo" viewerKey={photoKey}/>}
    {post.caption && <div className="feed-post-foot"><p>{post.caption}</p></div>}
    {confirm && <div className="post-delete-confirm">
      <p>이 게시물을 삭제할까요?</p>
      {error && <p className="upload-error" role="alert">{error}</p>}
      <div>
        <button className="people-secondary" type="button" disabled={deleting} autoFocus onClick={() => {
          setConfirm(false); deleteButton.current?.focus()
        }}>취소</button>
        <button className="post-delete-action" type="button" disabled={deleting} onClick={() => void remove()}>{deleting ? '삭제 중…' : '삭제'}</button>
      </div>
    </div>}
  </article>
}

export function OwnTimeline() {
  const { page, busy, error, load, retry, add, remove } = usePostTimeline(getOwnPosts)
  return <section className="me-records" aria-label="내 일상">
    <SectionTitle>내 일상</SectionTitle>
    <PersonalPostComposer onCreate={add}/>
    {error && <div className="people-alert" role="alert">{error}<button onClick={() => void retry()}>다시 시도</button></div>}
    {page?.posts.length === 0 && <EmptyState>아직 올린 일상이 없어요.</EmptyState>}
    <div className="feed-posts">{page?.posts.map(post => <PersonalPostCard key={postKey(post)} post={post} onDelete={remove}/>)}</div>
    {busy && <p role="status" className="people-note">일상 불러오는 중…</p>}
    {page?.next_cursor && <button className="profile-more" disabled={busy} onClick={() => void load(page.next_cursor!)}>이전 일상 더 보기<ChevronDown size={16}/></button>}
  </section>
}
