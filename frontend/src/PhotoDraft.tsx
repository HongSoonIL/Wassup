import { useEffect, useRef, useState } from 'react'
import { ImagePlus, LoaderCircle, X } from 'lucide-react'
import { PostPhoto, useTransientPhoto } from './SocialMedia'
import './photo-draft.css'

const accepted = 'image/jpeg,image/png,image/webp'

export function usePhotoDraft() {
  const [selection, setSelection] = useState<{ file: File; url: string } | null>(null)
  const [error, setError] = useState('')
  useEffect(() => () => {
    if (selection) URL.revokeObjectURL(selection.url)
  }, [selection])

  function select(file: File) {
    if (file.size > 10 * 1024 * 1024) {
      setError('사진은 10MB까지 올릴 수 있어요.')
      return
    }
    if (file.type && !accepted.split(',').includes(file.type)) {
      setError('JPG, PNG, WebP 사진을 선택해 주세요.')
      return
    }
    setError('')
    setSelection({ file, url: URL.createObjectURL(file) })
  }
  function clear() {
    setSelection(null)
    setError('')
  }
  return { selection, error, select, clear, dismissError: () => setError('') }
}

type PhotoDraft = ReturnType<typeof usePhotoDraft>

export function PhotoPicker({ draft, disabled, compact = false, onSelect }: {
  draft: PhotoDraft; disabled: boolean; compact?: boolean; onSelect?: () => void
}) {
  const input = useRef<HTMLInputElement>(null)
  const label = draft.selection ? '사진 변경' : '사진 첨부'
  return <>
    <input type="file" hidden ref={input} accept={accepted} disabled={disabled} onChange={event => {
      const file = event.currentTarget.files?.[0]
      event.currentTarget.value = ''
      if (file) { draft.select(file); onSelect?.() }
    }}/>
    <button type="button" className={'photo-picker' + (compact ? ' compact' : '')} disabled={disabled}
      aria-label={label} title={label} onClick={() => input.current?.click()}>
      <ImagePlus size={20} aria-hidden="true"/>{!compact && label}
    </button>
  </>
}

function DraftThumbnail({ url }: { url: string }) {
  const alt = '첨부할 사진'
  const key = useTransientPhoto('draft', { src: url, alt })
  return <PostPhoto src={url} alt={alt} className="draft-thumbnail" viewerKey={key}/>
}

export function PhotoPreview({ draft, busy, note }: { draft: PhotoDraft; busy: boolean; note?: string }) {
  const { selection, error } = draft
  return <>
    {selection && <div className="upload-preview">
      <DraftThumbnail key={selection.url} url={selection.url}/>
      <div className="upload-copy">
        <span className="upload-filename">{selection.file.name}</span>
        {busy ? <span role="status" className="upload-status"><LoaderCircle size={14} aria-hidden="true"/>사진 올리는 중…</span>
          : note && <small>{note}</small>}
      </div>
      <button type="button" className="upload-remove" aria-label="첨부 사진 제거" onClick={draft.clear} disabled={busy}><X size={18}/></button>
    </div>}
    {error && <p role="alert" className="upload-error">{error} <button type="button" onClick={draft.dismissError}>확인</button></p>}
  </>
}
