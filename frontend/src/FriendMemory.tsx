import { useEffect, useId, useState } from 'react'
import { ChevronDown, RefreshCw } from 'lucide-react'
import { csrfToken, request, RequestError } from './chat'
import './memory.css'

type Memory = {
  id: number
  kind: 'schedule' | 'preference' | 'result'
  text: string
  score?: number
  pinned?: boolean
  event_date: string | null
  source: { id: number; text: string }
}
type Snapshot = { enabled: boolean; results_enabled: boolean; revision: number; memories: Memory[] }
type Action = { type: 'edit' | 'delete'; item: Memory } | { type: 'disable' | 'disableResults' }

export default function FriendMemory({ room, name, defaultOpen = false }: { room: number; name: string; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const id = useId()
  return <section className="friend-memory">
    <button type="button" className="memory-heading" aria-expanded={open} aria-controls={open ? id : undefined}
      onClick={() => setOpen(!open)}>
      대화 기억 <ChevronDown size={17} aria-hidden="true"/>
    </button>
    {open && <MemorySettings key={room} id={id} room={room} name={name}/>}
  </section>
}

function MemorySettings({ room, name, id }: { room: number; name: string; id: string }) {
  const url = `/api/rooms/${room}/memories/`
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [action, setAction] = useState<Action | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [stale, setStale] = useState(false)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    request<Snapshot>(url, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setSnapshot(data) })
      .catch(err => { if (!controller.signal.aborted) setError(err.message) })
    return () => controller.abort()
  }, [url, reload])

  function refresh() {
    if (busy || (!snapshot && !error)) return
    setSnapshot(null)
    setAction(null)
    setError('')
    setStale(false)
    setReload(v => v + 1)
  }

  async function change(path: string, method: 'PATCH' | 'DELETE', fields = {}) {
    if (!snapshot || busy || stale) return
    setBusy(true)
    setError('')
    try {
      const next = await request<Snapshot>(url + path, {
        method, headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ ...fields, expected_revision: snapshot.revision }),
      })
      setSnapshot(next)
      setAction(null)
    } catch (err) {
      if (err instanceof RequestError && err.status === 409) setStale(true)
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return <div id={id} className="memory-settings" aria-busy={busy}>
    {error && <div role="alert">{error} <button disabled={busy} onClick={refresh}>다시 불러오기</button></div>}
    {!snapshot && !error && <p role="status">기억을 불러오는 중…</p>}
    {snapshot && <fieldset disabled={busy || stale}>
      {snapshot.enabled ? <>
        <div className="memory-actions"><strong>기억 켜짐</strong><button className="memory-refresh" aria-label="기억 새로고침" disabled={!!action} onClick={refresh}><RefreshCw size={15}/></button><button onClick={() => setAction({ type: 'disable' })}>기억 끄기</button></div>
        {action?.type === 'disable' && <div className="memory-confirm">
          <p>이 친구가 저장한 기억을 모두 지울까요? 대화 내역은 계속 볼 수 있어요.</p>
          <button onClick={() => void change('', 'PATCH', { enabled: false })}>모두 지우고 끄기</button>
          <button onClick={() => setAction(null)}>취소</button>
        </div>}
        <label><input type="checkbox" checked={snapshot.results_enabled} onChange={event => {
          if (event.target.checked) void change('', 'PATCH', { results_enabled: true })
          else setAction({ type: 'disableResults' })
        }}/> 결과도 기억하기</label>
        <p>이 방에서 알려 준 시험 점수는 시간이 지나면 흐릿해져요. ‘꼭 기억’을 켜면 정확히 기억해요.</p>
        {action?.type === 'disableResults' && <div className="memory-confirm">
          <p>이 친구가 기억하는 시험 결과를 모두 지울까요?</p>
          <button onClick={() => void change('', 'PATCH', { results_enabled: false })}>결과를 지우고 끄기</button>
          <button onClick={() => setAction(null)}>취소</button>
        </div>}
        {!snapshot.memories.length && <p>아직 저장한 기억이 없어요.</p>}
        <ul>{snapshot.memories.map(item => <li key={item.id}>
          <small>{item.kind === 'schedule' ? '일정' : item.kind === 'result' ? '시험 결과' : '취향'} {item.event_date}</small>
          <p>{item.text}{item.kind === 'result' && ` · ${item.score}점`}</p>
          <details><summary>내가 보낸 메시지</summary><blockquote>{item.source.text}</blockquote></details>
          {action?.type === 'edit' && action.item.id === item.id ? <form onSubmit={event => {
            event.preventDefault()
            const data = new FormData(event.currentTarget)
            void change(item.id + '/', 'PATCH', item.kind === 'result' ? { score: Number(data.get('score')) } : { text: String(data.get('text')).trim(), event_date: data.get('date') || null })
          }}>
            {item.kind === 'result'
              ? <label>시험 점수<input name="score" type="number" min={0} max={100} step={1} defaultValue={item.score} required/></label>
              : <label>기억 내용<input name="text" defaultValue={item.text} required maxLength={1000}/></label>}
            {item.kind === 'schedule' && <label>날짜<input name="date" type="date" defaultValue={item.event_date || ''}/></label>}
            <button type="submit">수정 저장</button><button type="button" onClick={() => setAction(null)}>취소</button>
          </form> : action?.type === 'delete' && action.item.id === item.id ? <div className="memory-confirm">
            <p>이 기억을 지울까요?</p><button onClick={() => void change(item.id + '/', 'DELETE')}>기억 지우기</button><button onClick={() => setAction(null)}>취소</button>
          </div> : <div className="memory-buttons">
            {item.kind === 'result' && <button aria-pressed={item.pinned} onClick={() => void change(item.id + '/', 'PATCH', { pinned: !item.pinned })}>{item.pinned ? '꼭 기억 해제' : '꼭 기억'}</button>}
            <button onClick={() => setAction({ type: 'edit', item })}>수정</button>
            <button onClick={() => setAction({ type: 'delete', item })}>삭제</button>
          </div>}
        </li>)}</ul>
      </> : <>
        <p>켜면 AI가 앞으로 이 방에 보내는 메시지에서 내 일정과 취향을 찾아 기억해요. {name}과의 대화에만 쓰이고, 여기서 고치거나 지울 수 있어요.</p>
        <button className="memory-enable" onClick={() => void change('', 'PATCH', { enabled: true })}>기억 켜기</button>
      </>}
    </fieldset>}
  </div>
}
