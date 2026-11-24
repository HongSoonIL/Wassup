import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ImageOff, X } from 'lucide-react'
import FriendPortrait from './FriendPortrait'
import './social.css'

type Person = { name: string; profile_image?: string | null }
type Photo = { src: string; alt: string }
type PhotoTarget = Photo | { key: string }
type OpenPhoto = (photo: PhotoTarget, trigger: HTMLButtonElement) => void
const PhotoContext = createContext<OpenPhoto | null>(null)

export function FriendIdentity({ person, detail, className = '' }: {
  person?: Person; detail?: ReactNode; className?: string
}) {
  return <span className={'ws-identity ' + className}>
    <FriendPortrait person={person && { ...person, profile_image: person.profile_image ?? null }} />
    <span className="ws-identity-copy">
      <strong>{person?.name ?? '친구'}</strong>
      {detail && <span className="ws-identity-detail">{detail}</span>}
    </span>
  </span>
}

function historyPhoto(): PhotoTarget | null {
  const photo = history.state?.wsPhoto
  if (typeof photo?.key === 'string') return { key: photo.key }
  return photo && typeof photo.src === 'string' && typeof photo.alt === 'string' ? photo : null
}

export function PhotoViewerProvider({ children, resolvePhoto, loading = false }: {
  children: ReactNode; resolvePhoto?: (key: string) => Photo | undefined; loading?: boolean
}) {
  const [target, setTarget] = useState(historyPhoto)
  const photo = target && ('key' in target ? resolvePhoto?.(target.key) : target)
  const [failedSrc, setFailedSrc] = useState<string | null>(null)
  const viewer = useRef<HTMLDialogElement>(null)
  const trigger = useRef<HTMLButtonElement | null>(null)
  const description = useId()
  const openPhoto = useCallback<OpenPhoto>((next, button) => {
    trigger.current = button
    setFailedSrc(null)
    history.pushState({ ...history.state, wsPhoto: next }, '')
    setTarget(next)
  }, [])

  useEffect(() => {
    const restore = () => {
      setFailedSrc(null)
      setTarget(historyPhoto())
    }
    window.addEventListener('popstate', restore)
    return () => window.removeEventListener('popstate', restore)
  }, [])

  useEffect(() => {
    const dialog = viewer.current
    if (!dialog) return
    if (target && !dialog.open) dialog.showModal()
    if (!target && dialog.open) {
      dialog.close()
      if (trigger.current?.isConnected) trigger.current.focus({ preventScroll: true })
    }
  }, [target])

  const close = () => {
    if (historyPhoto()) history.back()
    else setTarget(null)
  }

  return <PhotoContext.Provider value={openPhoto}>
    {children}
    <dialog className="ws-viewer" ref={viewer} aria-label="사진 원본" aria-describedby={description}
      onCancel={event => { event.preventDefault(); close() }}
      onClick={event => { if (event.target === viewer.current) close() }}>
      {target && <>
        <button type="button" className="ws-viewer-close" aria-label="사진 닫기" autoFocus onClick={close}><X size={22}/></button>
        {!photo ? <div className="ws-photo-error" role="status">
            {!loading && <ImageOff aria-hidden="true"/>}<p>{loading ? '사진 불러오는 중…' : '이 사진은 볼 수 없어요.'}</p>
          </div> : failedSrc === photo.src
          ? <div className="ws-photo-error"><ImageOff aria-hidden="true"/><p>사진을 불러올 수 없어요.</p>
              <button type="button" onClick={() => setFailedSrc(null)}>다시 시도</button></div>
          : <img className="ws-viewer-image" src={photo.src} alt={photo.alt} onError={() => setFailedSrc(photo.src)}/>}
        <span id={description} className="ws-sr">닫으면 보고 있던 위치로 돌아갑니다.</span>
      </>}
    </dialog>
  </PhotoContext.Provider>
}

export function PostPhoto({ src, alt, className = '', label, viewerKey }: Photo & {
  className?: string; label?: string; viewerKey?: string
}) {
  const open = useContext(PhotoContext)
  const [failedSrc, setFailedSrc] = useState<string | null>(null)
  const failed = failedSrc === src
  return <button className={'ws-post-photo ' + className} type="button" aria-haspopup={failed ? undefined : 'dialog'}
    aria-label={failed ? '사진 다시 불러오기' : label || (alt ? alt + ' · 사진 확대' : '사진 확대')}
    onClick={event => {
      if (failed) setFailedSrc(null)
      // Chat photos are resolved from current messages when revisiting a browser history entry.
      else open?.(viewerKey ? { key: viewerKey } : { src, alt }, event.currentTarget)
    }}>
    {failed ? <span className="ws-photo-retry" title="사진 다시 불러오기"><ImageOff size={20} aria-hidden="true"/></span>
      : <img src={src} alt={alt} loading="lazy" decoding="async" onError={() => setFailedSrc(src)}/>}
  </button>
}
