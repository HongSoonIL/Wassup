import { ReplyToPost, SectionTitle } from './PageParts'
import { useRef, useState } from 'react'
import type { MouseEvent, ReactNode } from 'react'
import { ArrowUpRight, ChevronDown, Images, MessageCircle, Settings2 } from 'lucide-react'
import { getProfile } from './chat'
import type { Profile } from './chat'
import { FriendIdentity, PostPhoto } from './SocialMedia'
import PreviousPost from './PreviousPost'
import ProfilePhotoHistory from './ProfilePhotoHistory'
import './profile.css'

type Post = Profile['posts'][number]
type ProfileProps = {
  profile: Profile
  onChat?: () => void
  onQuote?: (post: Post) => void
  children?: ReactNode
}
const published = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})

export function isPlainClick(event: MouseEvent) {
  return event.button === 0 && !event.defaultPrevented && !event.metaKey && !event.ctrlKey && !event.altKey && !event.shiftKey
}

function Album({ profile, onQuote }: Pick<ProfileProps, 'profile' | 'onQuote'>) {
  const [extra, setExtra] = useState<{ posts: Post[]; next_cursor: string | null } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const waiting = useRef(false)
  const cursor = extra ? extra.next_cursor : profile.next_cursor
  const posts = extra ? [...profile.posts, ...extra.posts] : profile.posts

  async function loadOlder() {
    if (!cursor || waiting.current) return
    waiting.current = true
    setBusy(true)
    setError('')
    try {
      const next = await getProfile(profile.id, cursor)
      setExtra(previous => ({ posts: [...(previous?.posts || []), ...next.posts], next_cursor: next.next_cursor }))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      waiting.current = false
      setBusy(false)
    }
  }

  return <section className="profile-records" aria-label="일상 기록">
    <div className="profile-section-title"><Images size={20} aria-hidden="true"/><SectionTitle>일상 기록</SectionTitle></div>
    {!posts.length && <p className="profile-empty">아직 올라온 소식이 없어요.</p>}
    <div className="profile-album-grid">{posts.map(post => (
      <article className="profile-post" key={post.id}>
        <PostPhoto src={post.photo.url} alt={post.photo.alt} className="profile-post-photo"/>
        <div className="profile-post-text">
          <div className="profile-post-meta"><time dateTime={post.published_at}>{published.format(new Date(post.published_at))}</time>
            {post.photo.ai_generated && <small>AI 생성 사진</small>}</div>
          <p>{post.caption}</p>
          <div className="profile-post-actions">
            <ReplyToPost id={post.id} name={profile.name} onClick={event => {
              if (onQuote && isPlainClick(event)) { event.preventDefault(); onQuote(post) }
            }}/>
            <a href={`/feed/#post-${post.id}`}>피드에서 보기 <ArrowUpRight size={13}/></a>
          </div>
        </div>
        {post.previous_post && <PreviousPost key={post.version} id={post.id} version={post.version}/>}
      </article>
    ))}</div>
    {error && <p className="profile-error" role="alert">{error}</p>}
    {cursor && <button type="button" className="profile-more" disabled={busy} onClick={() => void loadOlder()}>
      {busy ? '불러오는 중…' : '이전 소식 더 보기'}<ChevronDown size={16}/>
    </button>}
  </section>
}

export default function ProfileBody({ profile, onChat, onQuote, children }: ProfileProps) {
  return <div className="ws-profile">
    <section className="profile-hero" aria-label={profile.name+' 프로필'}>
      <h1 className="profile-name" aria-label={profile.name}>
        <FriendIdentity person={profile} className="profile-identity" detail={<span className="profile-kind">AI 친구</span>}/>
      </h1>
      <ProfilePhotoHistory name={profile.name} photos={profile.profile_photos ?? []}/>
      <div className="profile-intro">
        {profile.status_message && <p>{profile.status_message}</p>}
        {profile.current_activity && <span className="profile-activity"><i aria-hidden="true"/><span className="sr-only">현재 활동 </span>{profile.current_activity}</span>}
      </div>
      <a className="profile-chat" href={`/chat/${profile.room_id}/`} onClick={event => {
        if (onChat && isPlainClick(event)) { event.preventDefault(); onChat() }
      }}><MessageCircle size={17}/>대화하기<ArrowUpRight size={16}/></a>
    </section>
    {children && <details className="profile-settings">
      <summary><Settings2 size={16} aria-hidden="true"/>대화 설정과 기억<ChevronDown size={16} aria-hidden="true"/></summary>
      {children}
    </details>}
    <Album key={profile.id} profile={profile} onQuote={onQuote}/>
  </div>
}
