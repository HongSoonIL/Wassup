import { useState } from 'react'
import { ArrowLeft, Brain, ChevronRight, Settings, UserPlus, UsersRound } from 'lucide-react'
import FriendMemory from './FriendMemory'
import { csrfToken } from './chat'
import { PageTitle, SectionTitle } from './PageParts'
import { OwnTimeline } from './PersonalPosts'
import { PreferenceEditor, PreferenceSummary } from './ProfilePreferences'
import type { PreferenceSnapshot } from './ProfilePreferences'
import type { useFriendsOverview } from './UserNav'

type OwnProfile = {
  section: 'overview' | 'edit' | 'settings' | 'preferences' | 'memories'
  username: string
  nickname: string
  birthday_month: number | string | null
  birthday_day: number | string | null
  birthday_shared: boolean
  bio: string
  errors: Record<string, string[]>
  preferences?: PreferenceSnapshot
}

type FriendsOverview = ReturnType<typeof useFriendsOverview>

function MemoriesPicker({ data, error, retry }: FriendsOverview) {
  const [room, setRoom] = useState('')
  const friend = data?.friends.find(friend => String(friend.room_id) === room)
  return <section className="me-panel me-memories" aria-label="친구별 대화 기억">
    {error && <div role="alert">{error} <button type="button" className="people-secondary" onClick={retry}>다시 불러오기</button></div>}
    {!data && !error && <p role="status">친구를 불러오는 중…</p>}
    {data?.friends.length === 0 && <p>친구를 추가하면 여기서 친구별 대화 기억을 관리할 수 있어요.</p>}
    {!!data?.friends.length && <div className="me-form me-memory-picker">
      <label htmlFor="memory-friend">친구 선택</label>
      <select id="memory-friend" value={friend ? room : ''} onChange={event => setRoom(event.currentTarget.value)}>
        <option value="">친구를 선택해 주세요</option>
        {data.friends.map(friend => <option key={friend.room_id} value={friend.room_id}>{friend.character.name}</option>)}
      </select>
    </div>}
    {friend && <FriendMemory key={friend.room_id} room={friend.room_id} name={friend.character.name} defaultOpen/>}
  </section>
}

export default function MePage({ overview }: { overview: FriendsOverview }) {
  const [profile] = useState<OwnProfile>(() => JSON.parse(document.getElementById('my-profile-data')!.textContent!))
  const [month, setMonth] = useState(String(profile.birthday_month ?? ''))
  const [day, setDay] = useState(String(profile.birthday_day ?? ''))
  const daysInMonth = (value: string) => value ? new Date(2000, Number(value), 0).getDate() : 31
  const name = profile.nickname || profile.username
  const friendCount = overview.data?.friends.length
  const token = <input type="hidden" name="csrfmiddlewaretoken" value={csrfToken()}/>
  const back = <a className="ws-icon-link" href="/me/" aria-label="내 프로필로 돌아가기"><ArrowLeft size={21}/></a>
  if (profile.section === 'preferences') return <>
    <PageTitle action={back}>내 취향</PageTitle>
    <PreferenceEditor initial={profile.preferences}/>
  </>
  if (profile.section === 'memories') return <>
    <PageTitle action={back}>친구별 기억</PageTitle>
    <MemoriesPicker {...overview}/>
  </>
  if (profile.section === 'edit') return <>
    <PageTitle action={back}>프로필 편집</PageTitle>
    <form method="post" action="/me/?section=edit" className="me-panel me-form">{token}
      <label htmlFor="nickname">닉네임</label>
      <input id="nickname" name="nickname" maxLength={20} defaultValue={profile.nickname} placeholder={profile.username}/>
      {profile.errors.nickname && <p role="alert">{profile.errors.nickname.join(' ')}</p>}
      <label htmlFor="bio">한 줄 소개</label>
      <input id="bio" name="bio" maxLength={80} defaultValue={profile.bio}/>
      {profile.errors.bio && <p role="alert">{profile.errors.bio.join(' ')}</p>}
      <fieldset className="birthday-fields"><legend>생일</legend>
        <div><select name="birthday_month" aria-label="생일 월" value={month} onChange={event => {
          const next = event.target.value
          setMonth(next)
          if (!next || Number(day) > daysInMonth(next)) setDay('')
        }}>
          <option value="">월 선택</option>{Array.from({ length: 12 }, (_, i) =>
            <option key={i} value={i + 1}>{i + 1}월</option>)}
        </select>
        <select name="birthday_day" aria-label="생일 일" value={day} disabled={!month} onChange={event => setDay(event.target.value)}>
          <option value="">일 선택</option>{Array.from({ length: daysInMonth(month) }, (_, i) =>
            <option key={i} value={i + 1}>{i + 1}일</option>)}
        </select></div>
        {['birthday_month', 'birthday_day'].map(key => profile.errors[key] && <p key={key} role="alert">{profile.errors[key].join(' ')}</p>)}
        <label><input type="checkbox" name="birthday_shared" disabled={!month || !day} defaultChecked={profile.birthday_shared}/>친구에게 생일 공개</label>
        <small>공개하면 새로 추가한 친구도 생일을 알아요. ‘먼저 연락받기’를 켠 친구는 생일에 축하를 보낼 수 있어요.</small>
      </fieldset>
      <div className="me-actions"><button className="people-primary" type="submit">저장</button>
        <a className="people-secondary" href="/me/">취소</a></div>
    </form>
  </>
  if (profile.section === 'settings') return <>
    <PageTitle action={back}>계정 설정</PageTitle>
    <section className="me-panel"><SectionTitle>로그인 계정</SectionTitle><p>{profile.username}</p>
      <form method="post" action="/logout/">{token}<button className="people-secondary">로그아웃</button></form>
    </section>
  </>
  return <>
    <PageTitle action={<a href="/me/?section=settings" className="ws-icon-link" aria-label="계정 설정"><Settings size={21}/></a>}>내 프로필</PageTitle>
    <section className="me-panel" aria-label="내 소개">
      <div className="me-identity"><span className="me-avatar" aria-hidden="true">{Array.from(name)[0]}</span>
        <div><SectionTitle>{name}</SectionTitle>{profile.bio && <p>{profile.bio}</p>}</div></div>
      <div className="me-actions"><a className="people-secondary" href="/me/?section=edit">프로필 편집</a>
        <a className="people-secondary" href="/discover/"><UserPlus size={17}/>친구 찾기</a></div>
      <a href="/home/" className="me-friends"><UsersRound size={18}/>내 친구
        {friendCount !== undefined && <span>{friendCount}명</span>}<ChevronRight size={18}/></a>
    </section>
    <PreferenceSummary items={profile.preferences?.items ?? []}/>
    <section className="me-panel me-preferences-panel">
      <a href="/me/?section=memories" className="me-friends me-memory-link">
        <Brain size={18} aria-hidden="true"/>친구별 기억<ChevronRight size={18} aria-hidden="true"/>
      </a>
    </section>
    <OwnTimeline/>
  </>
}
