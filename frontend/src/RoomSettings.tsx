import { useEffect, useId, useRef, useState } from 'react'
import { Settings2 } from 'lucide-react'
import { deleteFriend, getFriendSettings, RequestError, updateFriendSettings } from './chat'
import './room-settings.css'

function reportError(error: unknown, show: (message: string) => void) {
  if (error instanceof RequestError && error.status === 404) {
    window.location.replace('/home/')
  } else {
    show((error as Error).message)
  }
}

export default function RoomSettings({ room, name }: { room: number; name: string }) {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState('')
  const [deleteError, setDeleteError] = useState('')
  const confirmation = useRef<HTMLDialogElement>(null)
  const removeButton = useRef<HTMLButtonElement>(null)
  const titleId = useId()
  const descriptionId = useId()
  const deleteTitleId = useId()
  const deleteDescriptionId = useId()

  useEffect(() => {
    let active = true
    setError('')
    getFriendSettings(room)
      .then(settings => { if (active) setEnabled(settings.proactive_enabled) })
      .catch(error => { if (active) reportError(error, setError) })
    return () => { active = false }
  }, [room, attempt])

  async function toggle(checked: boolean) {
    setSaving(true)
    setError('')
    try {
      const settings = await updateFriendSettings(room, { proactive_enabled: checked })
      setEnabled(settings.proactive_enabled)
    } catch (error) {
      reportError(error, setError)
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    setDeleting(true)
    setDeleteError('')
    try {
      await deleteFriend(room)
      window.location.replace('/home/')
    } catch (error) {
      reportError(error, setDeleteError)
      setDeleting(false)
    }
  }

  return <section className="room-settings" aria-labelledby={titleId}>
    <h2 id={titleId}><Settings2 size={18} aria-hidden="true"/>대화 설정</h2>
    {enabled === null && !error && <p role="status">설정을 불러오는 중…</p>}
    {enabled !== null && <label className="room-contact">
      <span><strong>먼저 연락받기</strong>
        <small id={descriptionId}>일상·일정에 관한 선톡은 모든 친구를 합쳐 하루 한 번이에요. 공개한 생일에는 이 친구도 따로 축하 톡을 보낼 수 있어요.</small>
      </span>
      <input type="checkbox" checked={enabled} aria-label="먼저 연락받기" aria-describedby={descriptionId}
        disabled={saving} onChange={event => void toggle(event.target.checked)}/>
    </label>}
    {saving && <p role="status">설정을 저장하는 중…</p>}
    {error && <p role="alert" className="room-settings-error">{error} {enabled === null &&
      <button type="button" onClick={() => setAttempt(value => value + 1)}>다시 시도</button>}
    </p>}
    <button ref={removeButton} type="button" className="room-delete" disabled={saving} onClick={() => {
      setDeleteError('')
      confirmation.current?.showModal()
    }}>친구 삭제</button>
    <dialog ref={confirmation} className="room-delete-dialog" aria-labelledby={deleteTitleId}
      aria-describedby={deleteDescriptionId} onCancel={event => {
        event.stopPropagation()
        if (deleting) event.preventDefault()
      }} onClose={() => removeButton.current?.focus()}>
      <h2 id={deleteTitleId}>친구 목록에서 {name} 삭제</h2>
      <p id={deleteDescriptionId}>이 친구와의 대화 내역, 저장된 기억, 대화 설정, 발송 대기 중인 메시지가 영구적으로 삭제돼요.
        다시 친구로 추가하면 새 대화가 시작돼요.</p>
      {deleteError && <p className="room-settings-error" role="alert">{deleteError}</p>}
      <div className="room-delete-actions">
        <button autoFocus type="button" aria-disabled={deleting} onClick={() => {
          if (!deleting) confirmation.current?.close()
        }}>취소</button>
        <button className="room-delete-confirm" type="button" aria-disabled={deleting} onClick={() => {
          if (!deleting) void remove()
        }}>{deleting ? '삭제 중…' : '친구 삭제'}</button>
      </div>
    </dialog>
  </section>
}
