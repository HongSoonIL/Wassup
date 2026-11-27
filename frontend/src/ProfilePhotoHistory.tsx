import { useEffect, useId, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, ImageOff, X } from 'lucide-react'
import type { ProfilePhoto } from './chat'
import './profile-photos.css'

const changedOn = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: 'long', day: 'numeric' })

// 프로필 사진 위에 겹쳐 놓는 버튼. 누르면 지난 프사를 최신순으로 넘겨 본다 (설계서 2.3절).
export default function ProfilePhotoHistory({ name, photos }: { name: string; photos: ProfilePhoto[] }) {
  const [index, setIndex] = useState<number | null>(null)
  const [failed, setFailed] = useState<number[]>([])
  const dialog = useRef<HTMLDialogElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const caption = useId()
  const photo = index === null ? null : photos[index]
  const past = photos.filter(p => !p.current).length

  useEffect(() => {
    const element = dialog.current
    if (!element) return
    if (index !== null && !element.open) element.showModal()
    if (index === null && element.open) {
      element.close()
      trigger.current?.focus({ preventScroll: true })
    }
  }, [index])

  if (!photos.length) return null
  const move = (step: number) => setIndex(i => i === null ? i : Math.min(photos.length - 1, Math.max(0, i + step)))

  return <>
    <button ref={trigger} type="button" className="profile-photo-history" aria-haspopup="dialog"
      aria-label={`${name} 프사 보기, ${photos.length}장`} onClick={() => setIndex(0)}>
      {past > 0 && <span className="profile-photo-count" aria-hidden="true">지난 프사 {past}</span>}
    </button>
    <dialog ref={dialog} className="ws-viewer profile-photo-viewer" aria-label={`${name}의 프사`} aria-describedby={caption}
      onCancel={event => { event.preventDefault(); setIndex(null) }}
      onClick={event => { if (event.target === dialog.current) setIndex(null) }}
      onKeyDown={event => {
        if (event.key === 'ArrowLeft') move(-1)
        if (event.key === 'ArrowRight') move(1)
      }}>
      {photo && index !== null && <>
        <button type="button" className="ws-viewer-close" aria-label="프사 닫기" autoFocus onClick={() => setIndex(null)}><X size={22}/></button>
        {failed.includes(photo.id)
          ? <div className="ws-photo-error"><ImageOff aria-hidden="true"/><p>사진을 불러올 수 없어요.</p></div>
          : <img className="ws-viewer-image" src={photo.url} alt={`${name}의 ${photo.current ? '현재' : '지난'} 프사`}
              onError={() => setFailed(ids => [...ids, photo.id])}/>}
        <button type="button" className="profile-photo-step prev" aria-label="더 최근 프사" disabled={index === 0}
          onClick={() => move(-1)}><ChevronLeft size={26}/></button>
        <button type="button" className="profile-photo-step next" aria-label="더 지난 프사" disabled={index === photos.length - 1}
          onClick={() => move(1)}><ChevronRight size={26}/></button>
        <p id={caption} className="profile-photo-caption" aria-live="polite">
          <strong>{photo.current ? '현재 프사' : changedOn.format(new Date(photo.set_at)) + ' 프사'}</strong>
          <span>{index + 1} / {photos.length}</span>
          {photo.ai_generated && <small>AI 생성 사진</small>}
        </p>
      </>}
    </dialog>
  </>
}
