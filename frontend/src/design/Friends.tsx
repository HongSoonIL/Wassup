import { useState } from 'react'
import { ArrowRight, ArrowUpRight, ChevronRight, Heart, Images, MessageCircle, Plus, X } from 'lucide-react'
import { character, characters, currentActivity, media, portrait, time } from './state'
import type { CharacterId, ReviewPost } from './state'

export function Avatar({ id }: { id: CharacterId }) {
  return <img className="d-avatar" src={portrait(id)} alt="" />
}

export function Photo({ post, onOpen }: { post: ReviewPost; onOpen: (p: ReviewPost) => void }) {
  return <button type="button" className="d-post-image" aria-label={post.photoAlt + ' 크게 보기'} onClick={() => onOpen(post)}>
    <img src={media(post.photoId)} alt={post.photoAlt} />
    <span className="d-sticker"><Images size={13} />AI 생성 사진</span>
  </button>
}

export function PostCard({ post, onProfile, onOpen, liked, onLike }: {
  post: ReviewPost; onProfile: (id: CharacterId) => void; onOpen: (p: ReviewPost) => void
  liked: boolean; onLike: () => void
}) {
  return <article className="d-card d-post">
    <Photo post={post} onOpen={onOpen} />
    <div className="d-post-foot">
      <button type="button" className="d-byline" onClick={() => onProfile(post.characterId)}>
        <Avatar id={post.characterId} /><b>{character(post.characterId).name}</b>
        <time dateTime={post.publishAt}>{post.publishAt.slice(5, 10).replace('-', '.')} · {time(post.publishAt)}</time>
      </button>
      <p>{post.caption}</p>
      <button type="button" className={'d-like ' + (liked ? 'on' : '')} aria-label="좋아요" aria-pressed={liked} onClick={onLike}>
        <Heart size={19} fill={liked ? 'currentColor' : 'none'} />
      </button>
    </div>
  </article>
}

export function FriendRow({ id, now, onProfile, onChat }: {
  id: CharacterId; now: string; onProfile: () => void; onChat: () => void
}) {
  const name = character(id).name
  return <div className="d-friend">
    <button type="button" className="d-friend-person" aria-label={name + ' 프로필'} onClick={onProfile}>
      <Avatar id={id} /><span><b>{name}</b><small><i />{currentActivity(id, now)}</small></span>
    </button>
    <button type="button" className="d-circle" aria-label={name + '에게 말 걸기'} onClick={onChat}><MessageCircle size={16} /></button>
  </div>
}

export function Friends({ mine, onProfile, onAdd }: {
  mine: CharacterId[]; onProfile: (id: CharacterId) => void; onAdd: (id: CharacterId) => void
}) {
  return <div className="d-people">{characters.map(person => {
    const id = person.id as CharacterId
    const added = mine.includes(id)
    return <article key={id} className="d-card">
      <Avatar id={id} /><span className="d-ai">AI 친구</span><h2>{person.name}</h2><p>{person.summary}</p>
      <button type="button" className="d-primary" onClick={() => added ? onProfile(id) : onAdd(id)}>
        {added ? '프로필 보기' : '친구 추가'}{added ? <ChevronRight size={16} /> : <Plus size={16} />}
      </button>
    </article>
  })}</div>
}

export function Profile({ id, now, posts, added, onChat, onAdd, onOpen }: {
  id: CharacterId; now: string; posts: ReviewPost[]; added: boolean; onChat: () => void
  onAdd: () => void; onOpen: (p: ReviewPost) => void
}) {
  const person = character(id)
  return <>
    <section className="d-profile d-card">
      <Avatar id={id} /><div><span className="d-ai">AI 친구</span><h1>{person.name}</h1><p>{person.summary}</p>
      <small className="d-status"><i />{currentActivity(id, now)}</small></div>
      <button type="button" className="d-primary" onClick={added ? onChat : onAdd}>
        {added ? '대화하기' : '친구 추가'}<ArrowUpRight size={16} />
      </button>
    </section>
    <div className="d-label lower"><h2>일상 기록 <sup>{posts.length}</sup></h2></div>
    {posts.length ? <div className="d-album">{posts.map(post => <article className="d-card" key={post.id}>
      <Photo post={post} onOpen={onOpen} /><p><time>{post.publishAt.slice(5, 10).replace('-', '.')} · {time(post.publishAt)}</time>{post.caption}</p>
    </article>)}</div> : <p className="d-empty">아직 공개된 일상 기록이 없어요.</p>}
  </>
}

export function Onboarding({ onJoin }: { onJoin: () => void }) {
  const [adult, setAdult] = useState(false)
  const [consent, setConsent] = useState(false)
  return <section className="d-join d-card">
    <div className="d-face-pair"><Avatar id="dohyun" /><Avatar id="seoyun" /></div>
    <h1>친구의 하루가 궁금할 때</h1><p>도현과 서윤은 각자의 일상을 보내는 AI 친구예요.</p>
    <label><input type="checkbox" checked={adult} onChange={e => setAdult(e.target.checked)} />만 18세 이상이에요.</label>
    <label><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />AI 친구와 나눈 대화를 저장하는 데 동의해요.</label>
    <button className="d-primary" type="button" disabled={!adult || !consent} onClick={onJoin}>친구 찾기<ArrowRight size={16} /></button>
  </section>
}

export function Lightbox({ post, onClose }: { post: ReviewPost; onClose: () => void }) {
  return <dialog ref={node => { if (node && !node.open) node.showModal() }} className="d-lightbox"
    aria-label={character(post.characterId).name + '의 사진'} onCancel={onClose} onClick={e => { if (e.target === e.currentTarget) onClose() }}>
    <div><img src={media(post.photoId)} alt={post.photoAlt} /><button type="button" className="d-circle" aria-label="사진 닫기" onClick={onClose}><X size={18} /></button></div>
    <p><b>{character(post.characterId).name}</b><time>{post.publishAt.slice(5, 10)} · {time(post.publishAt)}</time>{post.caption}</p>
  </dialog>
}
