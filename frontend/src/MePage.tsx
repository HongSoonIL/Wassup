import { ArrowLeft, ChevronRight, Image as ImageIcon, Settings, UserPlus, UsersRound } from 'lucide-react'
import { EmptyState, PageTitle, SectionTitle } from './PageParts'

type OwnProfile = {
  section: 'overview' | 'edit' | 'settings'
  username: string
  nickname: string
  bio: string
  errors: Record<string, string[]>
}

export default function MePage({ friendCount }: { friendCount?: number }) {
  const profile: OwnProfile = JSON.parse(document.getElementById('my-profile-data')!.textContent!)
  const name = profile.nickname || profile.username
  const csrf = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] || ''
  const token = <input type="hidden" name="csrfmiddlewaretoken" value={csrf}/>
  const back = <a className="ws-icon-link" href="/me/" aria-label="내 프로필로 돌아가기"><ArrowLeft size={21}/></a>
  if (profile.section === 'edit') return <>
    <PageTitle action={back}>프로필 편집</PageTitle>
    <form method="post" action="/me/?section=edit" className="me-panel me-form">{token}
      <label htmlFor="nickname">닉네임</label>
      <input id="nickname" name="nickname" maxLength={20} defaultValue={profile.nickname} placeholder={profile.username}/>
      {profile.errors.nickname && <p role="alert">{profile.errors.nickname.join(' ')}</p>}
      <label htmlFor="bio">한 줄 소개</label>
      <input id="bio" name="bio" maxLength={80} defaultValue={profile.bio}/>
      {profile.errors.bio && <p role="alert">{profile.errors.bio.join(' ')}</p>}
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
    <section className="me-records"><SectionTitle>내 일상</SectionTitle>
      <EmptyState><ImageIcon size={22} aria-hidden="true"/>아직 올린 일상이 없어요.</EmptyState></section>
  </>
}
