import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ArrowUp, BookOpen, Check, ChevronDown, Clock3, Trash2, X } from 'lucide-react'
import fixture from '../../../docs/design/day-samples.json'
import { character, currentActivity, media, portrait, publicPosts, time } from './state'
import type { CharacterId, ReviewPost } from './state'
import './chat.css'

type Account = 'a' | 'b'
type Timing = 'routine' | 'deadline' | 'immediate'
type Reply = { text: string; source?: string; photo?: boolean }
type Prompt = { id: string; at: string; text: string; source?: string }
type Memory = { id: string; at: string; fact: string }
type Room = {
  prompts: Prompt[]
  memories: Memory[]
  remember: boolean
  proactive: boolean
  timing: Timing
  sentReplies: Record<string, Reply>
}
export type ConversationState = Record<Account, Record<CharacterId, Room>>

const emptyRoom = (): Room => ({
  prompts: [], memories: [], remember: false, proactive: false, timing: 'routine', sentReplies: {},
})
export function createConversationState(): ConversationState {
  const state = {
    a: { dohyun: emptyRoom(), seoyun: emptyRoom() },
    b: { dohyun: emptyRoom(), seoyun: emptyRoom() },
  }
  for (const entry of fixture.accounts) {
    const account = entry.id === 'account-a' ? 'a' : 'b'
    const relation = entry.relationship
    const id = relation.characterId as CharacterId
    const room = state[account][id]
    room.remember = relation.memoryConsent
    room.proactive = relation.proactiveConsent
    room.memories = relation.memories.flatMap(memory => {
      const at = character(id).conversation.find(m => m.id === memory.sourceMessageId)?.at
      return at ? [{ id: memory.id, at, fact: memory.fact }] : []
    })
  }
  return state
}

const windowSample = fixture.characters.find(c => c.id === 'dohyun')!.replyWindow!
const breakTime = Date.parse(windowSample.breakAt)
const maxDelay = Date.parse(windowSample.deadlineAt) - Date.parse(windowSample.firstMessageAt)
const shift = character('dohyun').schedule.find(slot => slot.label === '카페 알바 중')!
const parse = Date.parse
const iso = (ms: number) => new Date(ms).toISOString()
const dayInSeoul = (ms: number) => new Date(ms + 9 * 3600_000).toISOString().slice(0, 10)
const dateLabel = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', month: 'long', day: 'numeric', weekday: 'long' })

type Preset = Prompt & { replies: (Reply & { at: string })[] }
function samplePrompts(id: CharacterId, account: Account, timing: Timing): Preset[] {
  if (account !== 'a') return []
  const prompts: Preset[] = []
  for (const m of character(id).conversation) {
    const source = 'sourcePostId' in m ? m.sourcePostId : undefined
    if (m.sender === 'user') {
      // The follow-up assumes the first answer arrived during the coffee break.
      if (id === 'dohyun' && timing === 'deadline' && prompts.length) break
      prompts.push({ id: m.id, at: m.at, text: m.text, source, replies: [] })
    } else {
      const prompt = prompts.at(-1)!
      prompt.replies.push({ at: m.at, text: m.text, source: source || prompt.source })
    }
  }
  return prompts
}

function replyTime(id: CharacterId, at: number, timing: Timing, preset?: Preset) {
  if (timing === 'immediate') return at
  if (id === 'dohyun' && at >= parse(shift.start) && at < parse(shift.end)) {
    if (timing === 'deadline') return Math.min(at + maxDelay, parse(shift.end))
    if (at < breakTime) return Math.min(breakTime, at + maxDelay)
    if (at >= breakTime + 10 * 60_000) return Math.min(parse(shift.end), at + maxDelay)
  }
  return preset?.replies[0] ? parse(preset.replies[0].at) : at
}

function requestedMemory(text: string) {
  const match = /기억해\s*(?:줘|주세요|둬|둬요)/.exec(text)
  if (!match) return ''
  const before = text.slice(0, match.index).trim().replace(/[.,!?。·:]+$/, '').trim()
  const after = text.slice(match.index + match[0].length).trim().replace(/^[.,!?。·:]+/, '').trim()
  return (before || after).trim().slice(0, 160)
}

function respond(id: CharacterId, text: string, at: number, room: Room, records: ReviewPost[]): Reply {
  const casual = id === 'dohyun'
  if (requestedMemory(text)) {
    if (!room.remember) return { text: '대화 설정에서 기억 저장을 켜면 기억해 둘 수 있어.' }
    return { text: casual ? 'ㅇㅇ 기억해 둘게' : '응응 다음에도 꼭 기억할게!' }
  }
  const memories = room.remember ? room.memories.filter(m => parse(m.at) <= at) : []
  const saved = memories.at(-1)
  if (/(기억|내 (?:시험|발표|일정)|내가 (?:좋아|말했)|통계.*(?:언제|기억))/.test(text)) {
    if (!saved) return { text: casual ? '우리 대화에서 따로 기억해 둔 건 아직 없어.' : '아직 따로 기억해 둔 얘기는 없어. 알려 줄래?' }
    if (saved.id === 'a-d-exam') return { text: '11월 27일에 통계 시험이라고 했잖아. 기억함 ㅋㅋ' }
    return { text: casual ? '“' + saved.fact + '”라고 했지. 기억함' : '기억하지! “' + saved.fact + '”라고 했어.' }
  }

  const yesterday = /어제/.test(text)
  const date = dayInSeoul(yesterday ? at - 86400_000 : at)
  const available = publicPosts(records, iso(at)).filter(p => p.characterId === id && dayInSeoul(parse(p.publishAt)) === date)
  const topics: [RegExp, RegExp][] = [
    [/마감|피처/, /마감|피처|영업 끝/], [/두부|고양이/, /두부|고양이/],
    [/포스터|시안/, /포스터|시안|군청|주황/], [/창가/, /창가/],
  ]
  const topic = topics.find(([query]) => query.test(text))?.[1]
  const post = topic ? available.find(p => topic.test(p.caption + ' ' + p.photoAlt)) : available[0]
  if (/사진|보여|뭐\s*(했|해)|소식|일상|올렸|어제|마감|두부|포스터|시안|창가/.test(text)) {
    if (!post) return { text: casual ? '그 얘기로 올린 건 아직 없음 ㅋㅋ' : '거기에 대해 올린 소식은 아직 없어!' }
    const examFollowup = yesterday && memories.some(m => m.id === 'a-d-exam') && dayInSeoul(at) === '2026-11-27'
    return {
      text: post.caption + (examFollowup ? ' 너 오늘 통계는 어땠냐' : ''),
      source: post.id, photo: !!post.photoId && /사진|보여/.test(text),
    }
  }
  return { text: casual ? 'ㅇㅇ 듣고 있음. 무슨 일인데?' : '응응 듣고 있어! 더 얘기해 줘.' }
}

type DisplayTurn = { id: string; at: number; sender: 'user' | 'character'; text: string; source?: string; photo?: boolean; unread?: boolean }
type Batch = { first: Prompt; inputs: Prompt[]; due: number; preset?: Preset }
function project(id: CharacterId, account: Account, now: number, records: ReviewPost[], room: Room) {
  const presets = samplePrompts(id, account, room.timing)
  const all: Prompt[] = [...presets, ...room.prompts].filter(p => parse(p.at) <= now).sort((a, b) => parse(a.at) - parse(b.at))
  const batches: Batch[] = []
  for (const p of all) {
    const last = batches.at(-1)
    if (last && parse(p.at) < last.due) last.inputs.push(p)
    else {
      const preset = presets.find(m => m.id === p.id)
      batches.push({ first: p, inputs: [p], due: replyTime(id, parse(p.at), room.timing, preset), preset })
    }
  }
  const turns: DisplayTurn[] = []
  const newReplies: Record<string, Reply> = {}
  for (const batch of batches) {
    for (const p of batch.inputs) turns.push({ ...p, at: parse(p.at), sender: 'user', unread: batch.due > now })
    if (batch.due > now) continue
    const latest = batch.inputs.at(-1)!
    if (batch.preset && latest.id === batch.preset.id) {
      const firstAt = parse(batch.preset.replies[0].at)
      batch.preset.replies.forEach((r, i) => {
        const at = batch.due + parse(r.at) - firstAt
        if (at <= now) turns.push({ ...r, at, id: latest.id + '-r' + i, sender: 'character' })
      })
    } else {
      const key = latest.id + '@' + batch.due
      const reply = room.sentReplies[key] || respond(id, latest.text, batch.due, room, records)
      if (!room.sentReplies[key]) newReplies[key] = reply
      turns.push({ ...reply, id: key, sender: 'character', at: batch.due })
    }
  }
  turns.sort((a, b) => a.at - b.at)
  const pending = batches.find(b => b.due > now)
  return { turns, pending, newReplies }
}

type ChatProps = {
  id: CharacterId; account: Account; now: string; records: ReviewPost[]
  state: ConversationState; onChange: (state: ConversationState) => void; onBack: () => void
}
export default function Chat({ id, account, now, records, state, onChange, onBack }: ChatProps) {
  const room = state[account][id]
  const person = character(id)
  const [draft, setDraft] = useState('')
  const [preferences, setPreferences] = useState(false)
  const [viewPhoto, setViewPhoto] = useState<string | null>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  const settingsDialog = useRef<HTMLDialogElement>(null)
  const photoDialog = useRef<HTMLDialogElement>(null)
  const log = useRef<HTMLDivElement>(null)
  const at = parse(now)
  const openPosts = publicPosts(records, now).filter(p => p.characterId === id)
  const photo = openPosts.find(p => p.id === viewPhoto)
  const { turns, pending, newReplies } = useMemo(() => project(id, account, at, records, room), [id, account, at, records, room])
  const memories = room.memories.filter(m => parse(m.at) <= at)
  function update(patch: Partial<Room>) {
    onChange({ ...state, [account]: { ...state[account], [id]: { ...room, ...patch } } })
  }
  useEffect(() => {
    if (Object.keys(newReplies).length) {
      onChange({ ...state, [account]: { ...state[account], [id]: { ...room, sentReplies: { ...room.sentReplies, ...newReplies } } } })
    }
  }, [account, id, newReplies, onChange, room, state])
  useEffect(() => {
    setDraft(''); setPreferences(false); setViewPhoto(null)
  }, [account, id])
  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight
  }, [turns.length, now, account, id])
  useEffect(() => {
    if (preferences && !settingsDialog.current?.open) settingsDialog.current?.showModal()
  }, [preferences])
  useEffect(() => {
    if (photo && !photoDialog.current?.open) photoDialog.current?.showModal()
  }, [photo])
  function submit(value: string) {
    const text = value.trim().slice(0, 1000)
    if (!text) return
    const prompt = { id: 'sent-' + (room.prompts.length + 1), at: now, text }
    const fact = room.remember ? requestedMemory(text) : ''
    const saved = fact && !room.memories.some(m => m.fact === fact) ? [...room.memories, { id: prompt.id, at: now, fact }] : room.memories
    update({ prompts: [...room.prompts, prompt], memories: saved })
    setDraft('')
  }
  const nextDay = dayInSeoul(at) > fixture.date
  const suggestions = [nextDay ? '어제 뭐 했어?' : '오늘 올린 사진 보여줘', '나에 대해 뭘 기억해?']
  return <section className="cr-room" aria-label={person.name + ' 채팅방'}>
    <header className="cr-header">
      <button className="cr-icon" onClick={onBack} aria-label="프로필로 돌아가기"><ArrowLeft size={19}/></button>
      <img className="cr-portrait" src={portrait(id)} alt=""/>
      <div className="cr-heading"><h1>{person.name}</h1><p><i/>{currentActivity(id, now)} <span>· AI 친구</span></p></div>
      <button ref={closeButton} className="cr-manage" aria-label="대화 설정 및 기억 관리" onClick={() => setPreferences(true)}><BookOpen size={17}/><span>대화 설정</span></button>
    </header>
    <div className="cr-log" ref={log} role="log" aria-label="대화 기록" aria-live="polite">
      {!turns.length && <div className="cr-empty"><img src={portrait(id)} alt=""/><b>{person.name}에게 첫 인사를 건네 보세요</b><p>{person.summary}</p></div>}
      {turns.map((turn, idx) => {
        const changedDate = !idx || dayInSeoul(turn.at) !== dayInSeoul(turns[idx - 1].at)
        const post = openPosts.find(p => p.id === turn.source)
        const removed = !!turn.source && !post
        return <div className="cr-entry" key={turn.id}>
          {changedDate && <div className="cr-date"><time>{dateLabel.format(turn.at)}</time></div>}
          <div className={'cr-line cr-' + turn.sender}>
            {turn.sender === 'character' && <img className="cr-mini" src={portrait(id)} alt=""/>}
            <div className="cr-bubble">{removed ? '공개가 종료된 게시물이에요.' : <>
              {turn.photo && post?.photoId && <button className="cr-photo" onClick={() => setViewPhoto(post.id)} aria-label={post.photoAlt + ' 크게 보기'}><img src={media(post.photoId)} alt={post.photoAlt}/></button>}
              <span>{turn.text}</span>
            </>}</div>
            <div className="cr-receipt">{turn.unread && <b aria-label="아직 읽지 않음">1</b>}<time>{time(iso(turn.at))}</time></div>
          </div>
        </div>
      })}
      {pending && <p className="cr-pending" role="status"><Clock3 size={14}/> {person.name}이 메시지를 확인하면 답장해요. <span>첫 전송 {time(pending.first.at)} · 늦어도 {time(iso(parse(pending.first.at) + maxDelay))}</span></p>}
    </div>
    <div className="cr-bottom">
      <div className="cr-suggestions" aria-label="대화 예시">{suggestions.map(s => <button key={s} onClick={() => submit(s)}>{s}</button>)}</div>
      <form className="cr-compose" onSubmit={e => { e.preventDefault(); submit(draft) }}>
        <label htmlFor="cr-message" className="cr-sr">메시지</label>
        <input id="cr-message" placeholder={person.name + '에게 메시지 보내기'} value={draft} onChange={e => setDraft(e.target.value)} maxLength={1000} autoComplete="off"/>
        <button disabled={!draft.trim()} aria-label="메시지 전송"><ArrowUp size={19}/></button>
      </form>
    </div>
    {preferences && <dialog className="cr-dialog" ref={settingsDialog} aria-labelledby="cr-settings" onClose={() => { setPreferences(false); closeButton.current?.focus() }}>
      <header><div><small>{person.name}과 나</small><h2 id="cr-settings">대화 설정</h2></div><button className="cr-icon" aria-label="닫기" onClick={() => settingsDialog.current?.close()}><X size={18}/></button></header>
      <label className="cr-option"><span><b>대화 기억 저장</b><small>“기억해 줘”라고 한 내용을 저장해요. 끄면 이 친구가 저장한 기억을 지워요.</small></span><input type="checkbox" checked={room.remember} onChange={e => update({ remember: e.target.checked, memories: e.target.checked ? room.memories : [] })}/></label>
      <div className="cr-memories"><h3>기억한 내용</h3>{memories.length ? memories.map(m => <div key={m.id}><span>{m.fact}</span><button className="cr-icon" aria-label={m.fact + ' 기억 삭제'} onClick={() => update({ memories: room.memories.filter(item => item.id !== m.id) })}><Trash2 size={15}/></button></div>) : <p>저장된 기억이 없어요.</p>}</div>
      <label className="cr-option"><span><b>먼저 연락 받기</b><small>이 친구가 먼저 말을 걸 수 있어요.</small></span><input type="checkbox" checked={room.proactive} onChange={e => update({ proactive: e.target.checked })}/></label>
      {id === 'dohyun' && <label className="cr-timing"><span><Clock3 size={14}/> 시안의 답장 조건</span><div><select value={room.timing} onChange={e => update({ timing: e.target.value as Timing })}>
        <option value="routine">일정대로 · 20:10 휴식</option><option value="deadline">휴식을 놓치면 · 3시간 상한</option><option value="immediate">즉시 답장</option>
      </select><ChevronDown size={15}/></div></label>}
      <button className="cr-done" onClick={() => settingsDialog.current?.close()}><Check size={15}/> 완료</button>
    </dialog>}
    {photo && <dialog className="cr-dialog cr-zoom" ref={photoDialog} aria-label="대화 사진" onClose={() => setViewPhoto(null)}>
      <img src={media(photo.photoId)} alt={photo.photoAlt}/><p>{photo.caption}</p><button className="cr-done" onClick={() => photoDialog.current?.close()}>닫기</button>
    </dialog>}
  </section>
}
