import { useEffect, useRef, useState } from 'react'
import {
  ArrowRight, ArrowUpRight, CalendarDays, Check, ChevronDown, ChevronLeft,
  ChevronRight, Clock3, Eye, FastForward, FileText, Image as ImageIcon, Images,
  Layers, List, LockKeyhole, Plus, RotateCcw, X,
} from 'lucide-react'
import { characters, media, portrait, publicPosts, type CharacterId, type OpsProps, type ReviewPost } from './state'
import './ops.css'

const stateNames = { draft: '초안', scheduled: '예약됨', published: '발행됨', failed: '발행 실패' } as const
const gallery = [
  { id: 'cafe-interior', name: '카페 내부' },
  { id: 'cafe-close', name: '카페 마감' },
  { id: 'studio-print', name: '작업실 시안' },
  { id: 'dubu-home', name: '집에 있는 두부' },
]
const dayFormat = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul', month: 'long', day: 'numeric', weekday: 'long',
})

function shiftDate(date: string, days: number) {
  const day = new Date(date + 'T12:00:00Z')
  day.setUTCDate(day.getUTCDate() + days)
  return day.toISOString().slice(0, 10)
}

function StatePill({ state }: { state: ReviewPost['state'] }) {
  return <span className={'op-state op-' + state}><i />{stateNames[state]}</span>
}

function FeedPreview({ item, name }: { item: ReviewPost; name: string }) {
  return <article className="op-feed">
    {item.photoId
      ? <div className="op-feed-photo"><img className="op-post-photo" src={media(item.photoId)} alt={item.photoAlt || '선택한 피드 사진'} /><span className="d-sticker"><Images size={13} />AI 생성 사진</span></div>
      : <div className="op-empty-photo"><ImageIcon size={26} />사진을 선택해 주세요</div>}
    <header><img src={portrait(item.characterId)} alt="" /><div><strong>{name}</strong><time>{item.publishAt.slice(5, 10).replace('-', '.')} · {item.publishAt.slice(11, 16)}</time></div></header>
    <p>{item.caption.trim() || '작성한 게시물이 여기에 표시됩니다.'}</p>
  </article>
}

type EditorProps = {
  item: ReviewPost; name: string; now: string; records: ReviewPost[]
  onSave: OpsProps['onSave']; onPreview: OpsProps['onPreview']
  onTime: OpsProps['onTime']; onFailNext: OpsProps['onFailNext']
}

function PostEditor({ item, name, now, records, onSave, onPreview, onTime, onFailNext }: EditorProps) {
  const [draft, setDraft] = useState(item)
  const [picking, setPicking] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const [feedback, setFeedback] = useState('')
  const [reviewing, setReviewing] = useState(false)
  const reviewDialog = useRef<HTMLDialogElement>(null)
  const reviewTrigger = useRef<HTMLButtonElement>(null)
  const editable = item.state === 'draft'
  const originals = characters.find(person => person.id === item.characterId)!.posts
  const visible = publicPosts(records, now)
  const availablePhotos = originals
    .filter(post => post.id === item.id || visible.some(p => p.id === post.id && p.photoId === post.photoId))
    .map(post => post.photoId)

  useEffect(() => {
    if (reviewing && !reviewDialog.current?.open) reviewDialog.current?.showModal()
  }, [reviewing])

  function update(changes: Partial<ReviewPost>) {
    setDraft(previous => ({ ...previous, ...changes }))
    setFeedback('')
    setErrors([])
  }

  function startReview() {
    const problems: string[] = []
    if (!draft.diary.trim()) problems.push('연결할 하루 일기를 입력해 주세요.')
    if (!draft.caption.trim()) problems.push('피드 본문을 입력해 주세요.')
    if (!draft.photoId) problems.push('공개할 사진을 선택해 주세요.')
    if (draft.photoId && !draft.photoAlt.trim()) problems.push('사진 설명을 입력해 주세요.')
    if (!draft.publishAt.slice(11, 16) || !(Date.parse(draft.publishAt) > Date.parse(now))) problems.push('발행 시간을 검토 시계 이후로 설정해 주세요.')
    const source = originals.find(post => post.photoId === draft.photoId)
    if (source && Date.parse(draft.publishAt) < Date.parse(source.publishAt)) {
      problems.push('이 사진을 공개할 수 있는 첫 시각은 ' + source.publishAt.slice(5, 10) + ' ' + source.publishAt.slice(11, 16) + '입니다.')
    }
    setErrors(problems)
    if (!problems.length) setReviewing(true)
  }

  function closeReview() {
    reviewDialog.current?.close()
    setReviewing(false)
    reviewTrigger.current?.focus()
  }

  const titleId = 'op-editor-title'
  return <div className="op-workgrid">
    <section className="op-card op-editor" aria-labelledby={titleId}>
      <div className="op-card-heading"><h3 id={titleId}><FileText size={17} />발행 편집</h3><StatePill state={item.state} /></div>
      <div className="op-fields">
        <label className="op-label" htmlFor="op-diary"><span>하루 일기</span><small><LockKeyhole size={12} />운영자 전용</small></label>
        <textarea id="op-diary" rows={2} value={draft.diary} readOnly={!editable} onChange={event => update({ diary: event.target.value })} placeholder="이 게시물에 연결할 그날의 사건을 기록하세요." />
        <label className="op-label" htmlFor="op-caption">피드 본문</label>
        <textarea id="op-caption" rows={2} value={draft.caption} readOnly={!editable} onChange={event => update({ caption: event.target.value })} placeholder="친구에게 보여 줄 글을 작성하세요." />
        <div className="op-label"><span>피드 사진</span></div>
        <div className="op-asset">
          {draft.photoId ? <img src={media(draft.photoId)} alt="" /> : <span className="op-no-asset"><ImageIcon size={18} /></span>}
          <span>{gallery.find(photo => photo.id === draft.photoId)?.name || (draft.photoId ? '선택한 사진' : '선택한 사진 없음')}</span>
          {editable && <button className="op-text-btn" type="button" aria-expanded={picking} onClick={() => setPicking(!picking)}>{draft.photoId ? '사진 변경' : '사진 선택'}<ChevronDown size={14} /></button>}
        </div>
        {picking && <div className="op-gallery" aria-label="피드 사진 선택">
          {gallery.filter(photo => availablePhotos.includes(photo.id)).map(photo => <button type="button" key={photo.id} aria-pressed={draft.photoId === photo.id} onClick={() => {
            update({ photoId: photo.id, photoAlt: draft.photoId === photo.id ? draft.photoAlt : '' })
            setPicking(false)
          }}><img src={media(photo.id)} alt="" /><span>{photo.name}</span></button>)}
          {draft.photoId && <button className="op-remove" type="button" onClick={() => { update({ photoId: '', photoAlt: '' }); setPicking(false) }}>선택 해제</button>}
        </div>}
        {draft.photoId && <><label className="op-label" htmlFor="op-alt">사진 설명</label>
          <input id="op-alt" value={draft.photoAlt} readOnly={!editable} onChange={event => update({ photoAlt: event.target.value })} placeholder="사진 속 장면을 적어 주세요." /></>}
        <label className="op-label" htmlFor="op-time"><span>발행 시간</span><small>한국 시간</small></label>
        <input id="op-time" type="time" value={draft.publishAt.slice(11, 16)} readOnly={!editable} onChange={event => {
          const time = event.target.value
          update({ publishAt: draft.publishAt.slice(0, 10) + (time ? 'T' + time + ':00+09:00' : '') })
        }} />
        {!!errors.length && <div className="op-errors" role="alert"><strong>예약 전에 확인해 주세요</strong><ul>{errors.map(error => <li key={error}>{error}</li>)}</ul></div>}
      </div>
      <div className="op-editor-actions">
        {editable && <>
          <button className="op-secondary" type="button" onClick={() => {
            if (!draft.publishAt.slice(11, 16)) { setErrors(['초안을 저장하려면 발행 시간을 입력해 주세요.']); return }
            onSave(draft); setFeedback('초안을 저장했습니다.')
          }}>초안 저장</button>
          <button className="op-primary" type="button" ref={reviewTrigger} onClick={startReview}>발행 검토<ArrowRight size={15} /></button>
        </>}
        {item.state === 'scheduled' && <button className="op-secondary" type="button" onClick={() => onSave({ ...item, state: 'draft' })}><RotateCcw size={15} />예약 취소하고 수정</button>}
        {item.state === 'failed' && <div className="op-failure" role="status"><strong>발행 실패 · {item.publishAt.slice(11, 16)} KST</strong>
          <p>사진 전송 중 일시적인 오류가 발생했습니다. 저장된 게시물로 다시 시도할 수 있습니다.</p>
          {Date.parse(now) >= Date.parse(item.publishAt)
            ? <button type="button" className="op-secondary" onClick={() => onSave({ ...item, state: 'published' })}><RotateCcw size={14} />다시 시도</button>
            : <button type="button" className="op-secondary" onClick={() => onTime(item.publishAt)}>예약 시각으로 이동</button>}
        </div>}
        {item.state === 'published' && <button className="op-primary" type="button" onClick={() => onPreview('feed')}>사용자 피드 열기<ArrowUpRight size={15} /></button>}
        <span role="status">{feedback}</span>
      </div>
    </section>
    <aside className="op-preview" aria-label="사용자 공개 범위">
      <div className="op-preview-heading"><h3><Eye size={17} />피드 미리보기</h3></div>
      <FeedPreview item={draft} name={name} />
      <div className="op-release-info"><Clock3 size={16} /><span>{{
        draft: '승인 후 설정한 시각에 피드에 공개됩니다.',
        scheduled: draft.publishAt.slice(11, 16) + '에 피드에 공개될 예정입니다.',
        published: '사용자 피드에 공개된 게시물입니다.',
        failed: '발행에 실패했습니다. 사용자 피드에서 이전 기록을 유지합니다.',
      }[item.state]}</span></div>
      {item.state === 'scheduled' && <div className="op-simulation"><span><Clock3 size={13} />시안 검토 시계</span><div>
        <button type="button" className="op-secondary" onClick={() => onTime(item.publishAt)}><FastForward size={13} />예약 시각으로 이동</button>
        {onFailNext && <button type="button" className="op-text-btn" onClick={() => { onFailNext(item.id); onTime(item.publishAt) }}>발행 실패 재현</button>}
      </div></div>}
    </aside>
    {reviewing && <dialog ref={reviewDialog} className="op-dialog" aria-labelledby="op-review-title" onCancel={closeReview}>
      <div className="op-dialog-head"><h3 id="op-review-title">예약 발행 검토</h3><button aria-label="검토 닫기" className="op-icon" type="button" onClick={closeReview}><X size={18} /></button></div>
      <FeedPreview item={draft} name={name} />
      <p><Clock3 size={15} />{dayFormat.format(new Date(draft.publishAt))} {draft.publishAt.slice(11, 16)} KST</p>
      <div className="op-dialog-actions"><button className="op-secondary" type="button" onClick={closeReview}>수정하기</button>
        <button type="button" className="op-primary" onClick={() => {
          closeReview()
          if (!(Date.parse(draft.publishAt) > Date.parse(now))) { setErrors(['발행 시간을 검토 시계 이후로 설정해 주세요.']); return }
          onSave({ ...draft, state: 'scheduled' })
        }}><Check size={15} />승인하고 예약</button></div>
    </dialog>}
  </div>
}

export default function Ops({ characterId, onCharacter, now, onTime, records, onSave, onPreview, onFailNext }: OpsProps) {
  const character = characters.find(entry => entry.id === characterId)!
  const [view, setView] = useState<'day' | 'queue'>('day')
  const [date, setDate] = useState(now.slice(0, 10))
  const [selected, setSelected] = useState('')
  const [clockOpen, setClockOpen] = useState(false)
  const [scheduleOpen, setScheduleOpen] = useState(false)
  const personRecords = records.filter(record => record.characterId === characterId).sort((a, b) => a.publishAt.localeCompare(b.publishAt))
  const dayRecords = personRecords.filter(record => record.publishAt.slice(0, 10) === date)
  const active = dayRecords.find(record => record.id === selected) ?? dayRecords.find(record => record.state === 'draft') ?? dayRecords.at(-1)
  const sessions = character.schedule.filter(session => session.start.slice(0, 10) === date)
  const currentSlot = sessions.find(slot => Date.parse(slot.start) <= Date.parse(now) && Date.parse(now) < Date.parse(slot.end))
  const upcoming = records.filter(record => record.state === 'scheduled' && Date.parse(record.publishAt) > Date.parse(now)).sort((a, b) => a.publishAt.localeCompare(b.publishAt))[0]
  function selectCharacter(id: CharacterId) { onCharacter(id); setSelected('') }
  function pickDate(value: string) { if (value) { setDate(value); setSelected('') } }
  function newDraft() {
    const id = crypto.randomUUID()
    onSave({ id, characterId, caption: '', photoId: '', photoAlt: '', publishAt: date + 'T23:00:00+09:00', state: 'draft', diary: '' })
    setSelected(id); setView('day')
  }
  return <div className="op-shell">
    <aside className="op-side">
      <div className="op-brand" aria-label="wassup 스튜디오"><span>wassup<i>.</i></span><small>스튜디오</small></div>
      <nav className="op-nav" aria-label="스튜디오 메뉴">
        <button type="button" className={view === 'day' ? 'op-active' : ''} aria-current={view === 'day' ? 'page' : undefined} onClick={() => setView('day')}><CalendarDays size={17} />하루 편집실</button>
        <button type="button" className={view === 'queue' ? 'op-active' : ''} aria-current={view === 'queue' ? 'page' : undefined} onClick={() => setView('queue')}><Layers size={17} />발행 목록</button>
      </nav>
      <section className="op-people"><h2>캐릭터</h2><div>{characters.map(person =>
        <button type="button" key={person.id} aria-pressed={characterId === person.id} onClick={() => selectCharacter(person.id as CharacterId)}>
          <img src={portrait(person.id as CharacterId)} alt="" /><span>{person.name}</span>{characterId === person.id && <i />}
        </button>)}</div></section>
      <div className="op-clock">
        <button type="button" className="op-clock-button" aria-expanded={clockOpen} onClick={() => setClockOpen(!clockOpen)}>
          <span><Clock3 size={13} />검토 시계</span><strong>{now.slice(11, 16)} <small>KST</small></strong><ChevronDown size={13} />
        </button>
        {clockOpen && <div className="op-clock-panel"><label htmlFor="op-clock-input">시안 기준 시각</label>
          <input id="op-clock-input" type="datetime-local" value={now.slice(0, 16)} onChange={event => { if (event.target.value) onTime(event.target.value + ':00+09:00') }} />
          <button className="op-clock-jump" type="button" disabled={!upcoming} onClick={() => { if (upcoming) onTime(upcoming.publishAt) }}><FastForward size={13} />다음 예약 시각으로</button>
        </div>}
      </div>
    </aside>
    <main className="op-main">
      <div className="op-topbar"><div><span>스튜디오</span><ChevronRight size={13} /><strong>{view === 'day' ? '하루 편집실' : '발행 목록'}</strong></div><button type="button" className="op-text-btn" onClick={() => onPreview()}>사용자 앱 열기<ArrowUpRight size={15} /></button></div>
      <div className="op-content">
        <header className="op-title"><h1>{view === 'day' ? '하루 편집실' : '발행 목록'}</h1>
          {view === 'day' && <div className="op-date-controls"><button type="button" className="op-icon" aria-label="이전 날짜" onClick={() => pickDate(shiftDate(date, -1))}><ChevronLeft size={16} /></button>
            <label className="op-date"><CalendarDays size={15} /><span>{dayFormat.format(new Date(date + 'T12:00:00+09:00'))}</span><input aria-label="편집 날짜" type="date" value={date} onChange={event => pickDate(event.target.value)} /></label>
            <button type="button" className="op-icon" aria-label="다음 날짜" onClick={() => pickDate(shiftDate(date, 1))}><ChevronRight size={16} /></button></div>}
        </header>
        {view === 'day' ? <>
          <section className="op-timetable" aria-label={character.name + '의 시간표'}>
            <div className="op-timetable-head"><div className="op-profile"><img src={portrait(characterId)} alt="" /><div><h2>{character.name}의 하루</h2><p>{character.summary}</p></div></div>
            <button type="button" className="op-schedule-count" aria-expanded={scheduleOpen} onClick={() => setScheduleOpen(!scheduleOpen)}><List size={14} />일정 {sessions.length}개<ChevronDown size={12} /></button></div>
            {!!sessions.length ? <>
              <div className="op-track" aria-hidden="true">{sessions.map(slot => <i key={slot.start} className={slot === currentSlot ? 'op-live' : undefined} style={{ flexGrow: Date.parse(slot.end) - Date.parse(slot.start) }} />)}</div>
              <div className="op-track-labels" aria-hidden="true">{['00:00','06:00','12:00','18:00','24:00'].map(time => <span key={time}>{time}</span>)}</div>
              {currentSlot && <p className="op-current-label"><i />{currentSlot.label}<span>{currentSlot.start.slice(11,16)} – {currentSlot.end.slice(11,16)}</span></p>}
            </> : <p className="op-empty">이 날짜에 등록한 일과가 없습니다.</p>}
            {scheduleOpen && !!sessions.length && <ol className="op-slots">{sessions.map(slot => {
              const current = Date.parse(slot.start) <= Date.parse(now) && Date.parse(now) < Date.parse(slot.end)
              return <li key={slot.start + slot.label} className={current ? 'op-current' : ''}><time>{slot.start.slice(11, 16)} – {slot.end.slice(11, 16)}</time><strong>{slot.label}</strong>{current && <small>진행 중</small>}</li>
            })}</ol>}
          </section>
          <div className="op-record-bar"><h2>게시물 <span>{dayRecords.length}</span></h2><div>
            {!!dayRecords.length && <select aria-label="편집 게시물" value={active?.id} onChange={event => setSelected(event.target.value)}>
              {dayRecords.map(record => <option key={record.id} value={record.id}>{record.publishAt.slice(11, 16)} · {stateNames[record.state]} · {record.caption.slice(0, 25) || '새 게시물'}</option>)}
            </select>}
            <button type="button" className="op-secondary" onClick={newDraft}><Plus size={14} />새 초안</button>
          </div></div>
          {active ? <PostEditor key={active.id + active.state} item={active} now={now} records={records} name={character.name} onSave={onSave} onPreview={onPreview} onTime={onTime} onFailNext={onFailNext} />
            : <div className="op-empty-card"><FileText size={24} /><strong>이 날짜에 작성한 게시물이 없습니다.</strong><button className="op-secondary" type="button" onClick={newDraft}><Plus size={15} />첫 초안 작성</button></div>}
        </> : <section className="op-card op-queue"><div className="op-card-heading"><h3><img src={portrait(characterId)} alt="" />{character.name}의 게시물</h3><span>{personRecords.length}건</span></div>
          {personRecords.length ? <ul>{[...personRecords].reverse().map(record => <li key={record.id}>
            <button type="button" onClick={() => { setDate(record.publishAt.slice(0, 10)); setSelected(record.id); setView('day') }}>
              {record.photoId ? <img src={media(record.photoId)} alt="" /> : <span className="op-no-asset"><ImageIcon size={17} /></span>}
              <span className="op-queue-text"><strong>{record.caption || '새 게시물'}</strong><time>{record.publishAt.slice(0, 10)} · {record.publishAt.slice(11, 16)}</time></span>
              <StatePill state={record.state} /><ChevronRight size={15} />
            </button>
          </li>)}</ul> : <div className="op-empty-card">등록한 게시물이 없습니다.</div>}
        </section>}
      </div>
    </main>
  </div>
}
