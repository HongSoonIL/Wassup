import { useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { ChevronRight, Plus } from 'lucide-react'
import { csrfToken, request, RequestError } from './chat'
import { SectionTitle } from './PageParts'
import './profile-preferences.css'

const tags = ['커피', '음악', '산책', '영화', '음식', '운동']
const maxItems = 5
const maxText = 80
const url = '/api/me/preferences/'
const visibility = '저장한 취향은 모든 AI 친구가 나와 대화할 때 참고해요.'

type Preference = { tag: string; text: string }
export type PreferenceSnapshot = { items: Preference[]; revision: number }
type Draft = { type: 'edit'; index: number | null; item: Preference } | { type: 'delete'; index: number }

function preferenceLabel(item: Preference) {
  return [item.tag, item.text].filter(Boolean).join(' · ')
}

function PreferenceList({ items, actions }: { items: Preference[]; actions?: (item: Preference, index: number) => ReactNode }) {
  if (items.length === 0) return <p className="me-preferences-note">아직 저장한 취향이 없어요.</p>
  return <ul className="me-preferences-list">{items.map((item, index) => <li key={item.tag || index}>
    <div className="me-preference-value">
      {item.tag && <span className="me-preference-tag">{item.tag}</span>}
      {item.text && <p>{item.text}</p>}
    </div>
    {actions?.(item, index)}
  </li>)}</ul>
}

export function PreferenceSummary({ items }: { items: Preference[] }) {
  const [current, setCurrent] = useState(items)
  useEffect(() => {
    const controller = new AbortController()
    function restore(event: PageTransitionEvent) {
      if (!event.persisted) return
      request<PreferenceSnapshot>(url, { signal: controller.signal })
        .then(data => { if (!controller.signal.aborted) setCurrent(data.items) })
        .catch(() => {})
    }
    window.addEventListener('pageshow', restore)
    return () => {
      controller.abort()
      window.removeEventListener('pageshow', restore)
    }
  }, [])
  return <section className="me-panel me-preferences-panel" aria-labelledby="me-preferences-summary">
    <SectionTitle id="me-preferences-summary">
      <a className="me-preferences-link" href="/me/?section=preferences">내 취향<ChevronRight size={18} aria-hidden="true"/></a>
    </SectionTitle>
    <PreferenceList items={current}/>
  </section>
}

function sameItem(first: Preference, second: Preference) {
  return first.tag === second.tag && first.text === second.text
}

function recoverDraft(draft: Draft | null, previous: Preference[], latest: Preference[]): Draft | null {
  if (!draft || draft.index === null || draft.index < 0) return draft
  if (previous.length === latest.length && previous.every((item, index) => sameItem(item, latest[index]))) return draft
  const original = previous[draft.index]
  const matches = latest.flatMap((item, index) => sameItem(item, original) ? [index] : [])
  if (matches.length === 1 && previous.filter(item => sameItem(item, original)).length === 1) {
    return { ...draft, index: matches[0] }
  }
  // A text-only item has no stable ID. Never apply a stale action to an ambiguous match.
  return draft.type === 'edit' ? { ...draft, index: -1 } : null
}

export function PreferenceEditor({ initial }: { initial?: PreferenceSnapshot }) {
  const [snapshot, setSnapshot] = useState<PreferenceSnapshot | null>(initial ?? null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(!initial)
  const [needsRefresh, setNeedsRefresh] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const heading = useRef<HTMLDivElement>(null)
  const draftOpen = useRef(false)
  const items = snapshot?.items ?? []

  useEffect(() => {
    if (initial) return
    const controller = new AbortController()
    request<PreferenceSnapshot>(url, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setSnapshot(data) })
      .catch(err => { if (!controller.signal.aborted) setError(err.message) })
      .finally(() => { if (!controller.signal.aborted) setBusy(false) })
    return () => controller.abort()
  }, [initial])

  useEffect(() => {
    if (draftOpen.current && !draft) heading.current?.focus()
    draftOpen.current = !!draft
  }, [draft])

  async function loadLatest(afterConflict: boolean) {
    setError('')
    setStatus('')
    try {
      const latest = await request<PreferenceSnapshot>(url)
      const nextDraft = recoverDraft(draft, items, latest.items)
      setSnapshot(latest)
      setDraft(nextDraft)
      setNeedsRefresh(false)
      if (afterConflict) {
        setStatus(draft && !nextDraft
          ? '다른 곳에서 이 항목이 바뀌었어요. 최신 목록에서 항목을 다시 선택해 주세요.'
          : '다른 곳에서 취향이 바뀌었어요. 최신 목록을 확인한 뒤 다시 시도해 주세요.')
      }
    } catch (err) {
      setError(afterConflict ? '취향이 바뀌었어요. 최신 목록을 다시 불러와 주세요.' : (err as Error).message)
    }
  }

  async function retry() {
    if (busy) return
    setBusy(true)
    await loadLatest(needsRefresh)
    setBusy(false)
  }

  function choose(next: Draft | null) {
    setDraft(next)
    setError('')
    setStatus('')
  }

  function chooseTag(tag: string) {
    if (draft?.type !== 'edit') return
    const existing = tag ? items.findIndex(item => item.tag === tag) : -1
    choose(existing >= 0 && existing !== draft.index
      ? { type: 'edit', index: existing, item: items[existing] }
      : { ...draft, item: { ...draft.item, tag } })
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!snapshot || !draft || busy || needsRefresh || draft.index === -1) return
    const next = [...items]
    if (draft.type === 'delete') {
      next.splice(draft.index, 1)
    } else {
      const item = { ...draft.item, text: draft.item.text.trim() }
      if (!item.tag && !item.text) return setError('태그를 선택하거나 한 줄 취향을 입력해 주세요.')
      if (Array.from(item.text).length > maxText || /[\r\n\v\f\u001c-\u001e\u0085\u2028\u2029]/.test(item.text)) {
        return setError('취향은 한 줄로 80자까지 입력할 수 있어요.')
      }
      if (item.tag && items.some((saved, index) => index !== draft.index && saved.tag === item.tag)) {
        return setError('이미 저장한 태그예요. 목록에서 해당 항목을 선택해 주세요.')
      }
      if (draft.index === null) {
        if (items.length >= maxItems) return setError('취향은 5개까지 저장할 수 있어요.')
        next.push(item)
      } else {
        next[draft.index] = item
      }
    }
    setBusy(true)
    setError('')
    setStatus('')
    try {
      setSnapshot(await request<PreferenceSnapshot>(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ items: next, expected_revision: snapshot.revision }),
      }))
      setDraft(null)
      setStatus(draft.type === 'delete' ? '취향을 삭제했어요.' : '취향을 저장했어요.')
    } catch (err) {
      if (err instanceof RequestError && err.status === 409) {
        setNeedsRefresh(true)
        await loadLatest(true)
      } else {
        setError((err as Error).message)
      }
    } finally {
      setBusy(false)
    }
  }

  const blocked = busy || needsRefresh
  const duplicate = draft?.type === 'edit' && draft.item.tag &&
    items.some((item, index) => index !== draft.index && item.tag === draft.item.tag)
  return <section className="me-panel me-preferences-editor" aria-labelledby="me-preferences-title">
    <div className="me-preferences-heading" ref={heading} tabIndex={-1}>
      <SectionTitle id="me-preferences-title">저장한 취향</SectionTitle>
      {snapshot && <p className="me-preferences-note">최대 {maxItems}개 · {items.length}개 저장됨</p>}
    </div>
    {draft?.type !== 'edit' && <p className="me-preferences-note">{visibility}</p>}
    {error && <div className="me-preferences-error" role="alert">{error}
      {(needsRefresh || !snapshot) && <button className="people-secondary" type="button" disabled={busy}
        onClick={() => void retry()}>다시 불러오기</button>}
    </div>}
    <p className="me-preferences-note me-preferences-status" role="status">{busy ? '취향을 처리하는 중…' : status}</p>
    {snapshot && <>
      <PreferenceList items={items} actions={(item, index) => <div className="me-actions">
        <button className="people-secondary" type="button" disabled={blocked}
          aria-label={preferenceLabel(item) + ' 수정'} onClick={() => choose({ type: 'edit', index, item })}>수정</button>
        <button className="people-secondary" type="button" disabled={blocked}
          aria-label={preferenceLabel(item) + ' 삭제'} onClick={() => choose({ type: 'delete', index })}>삭제</button>
      </div>}/>
      {draft === null && items.length < maxItems && <div className="me-actions">
        <button className="people-primary" type="button" disabled={blocked}
          onClick={() => choose({ type: 'edit', index: null, item: { tag: '', text: '' } })}>
          <Plus size={17} aria-hidden="true"/>취향 추가
        </button>
      </div>}
      {draft && <form className="me-preferences-form" onSubmit={event => void save(event)}>
        <fieldset className="me-form" disabled={blocked}>
          <legend>{draft.type === 'delete' ? '취향 삭제' : draft.index === null ? '취향 추가' : '취향 수정'}</legend>
          {draft.type === 'delete' ? <p>“{preferenceLabel(items[draft.index])}” 항목을 삭제할까요?</p> : <>
            <label htmlFor="preference-tag">태그</label>
            <select id="preference-tag" autoFocus value={draft.item.tag} onChange={event => chooseTag(event.currentTarget.value)}>
              <option value="">태그 없음</option>
              {tags.map(tag => <option key={tag} value={tag}>
                {tag}{items.some(item => item.tag === tag) ? ' (저장됨)' : ''}
              </option>)}
            </select>
            <label htmlFor="preference-text">한 줄 취향</label>
            <input id="preference-text" type="text" value={draft.item.text} aria-describedby="preference-text-limit"
              onChange={event => setDraft({ ...draft, item: { ...draft.item, text: event.currentTarget.value } })}/>
            <p id="preference-text-limit" className="me-preferences-note">태그와 한 줄 취향 중 하나만 입력해도 돼요. 한 줄 취향은 최대 {maxText}자예요.</p>
            {draft.index === -1
              ? <p className="me-preferences-error">다른 곳에서 이 항목이 바뀌었어요. 최신 목록에서 항목을 다시 선택해 주세요.</p>
              : duplicate && <p className="me-preferences-error">이미 저장한 태그예요. 목록에서 해당 항목을 선택해 주세요.</p>}
            {draft.index === null && items.length >= maxItems && <p className="me-preferences-error">취향은 {maxItems}개까지 저장할 수 있어요.</p>}
            <p id="preference-visibility" className="me-preferences-note">{visibility}</p>
          </>}
          <div className="me-actions">
            <button type="submit" className={draft.type === 'delete' ? 'people-secondary me-preferences-error' : 'people-primary'}
              aria-describedby={draft.type === 'edit' ? 'preference-visibility' : undefined}
              disabled={draft.index === -1 || !!duplicate || (draft.index === null && items.length >= maxItems)}>
              {draft.type === 'delete' ? '삭제' : '저장'}
            </button>
            <button type="button" className="people-secondary" autoFocus={draft.type === 'delete'} onClick={() => choose(null)}>취소</button>
          </div>
        </fieldset>
      </form>}
    </>}
  </section>
}
