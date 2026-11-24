import { Images, LogOut, MessageCircle, UsersRound } from 'lucide-react'
import { getFriends } from './chat'
import type { Friend } from './chat'
import { usePageData } from './usePageData'
import './user-nav.css'

const destinations = [
  { page: 'feed', href: '/feed/', label: '피드', Icon: Images },
  { page: 'messages', href: '/messages/', label: '메시지', Icon: MessageCircle },
  { page: 'home', href: '/home/', label: '친구', Icon: UsersRound },
] as const

export type OverviewFriend = Friend & { current_activity?: string | null }

export function useFriendsOverview(enabled = true) {
  const result = usePageData<{ friends: OverviewFriend[] }>(getFriends, { pollMs: 5000, enabled })
  const unreadCount = result.data?.friends.reduce((total, friend) => total + friend.unread_count, 0) ?? 0
  return { ...result, unreadCount }
}

// Put UserNav first inside .user-layout, followed by the page content as its sibling.
// Viewport-sized pages can add .user-layout--viewport and subtract --user-nav-bottom
// from their mobile content height. The content then supplies its own mobile header.
export function UserNav({ page, unreadCount }: { page: string; unreadCount?: number }) {
  const overview = useFriendsOverview(unreadCount === undefined)
  const unread = unreadCount ?? overview.unreadCount
  const active = page === 'profile' || page === 'discover' ? 'home' : page
  const csrf = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] || ''

  return <header className="user-nav">
    <a className="people-logo" href="/" aria-label="wassup 홈">wassup<span>.</span></a>
    <nav className="user-nav-links" aria-label="서비스 메뉴">
      {destinations.map(({ page, href, label, Icon }) => <a key={page} href={href}
        aria-current={active === page ? 'page' : undefined}>
        <span className="user-nav-icon">
          <Icon size={21} aria-hidden="true"/>
          {page === 'messages' && unread > 0 && <span className="user-nav-badge">
            <span aria-hidden="true">{unread > 99 ? '99+' : unread}</span>
            <span className="ws-sr">안 읽은 메시지 {unread}개</span>
          </span>}
        </span>
        <span>{label}</span>
      </a>)}
    </nav>
    <form className="user-nav-session" method="post" action="/logout/">
      <input type="hidden" name="csrfmiddlewaretoken" value={csrf}/>
      <button aria-label="로그아웃" title="로그아웃"><LogOut size={19} aria-hidden="true"/><span>로그아웃</span></button>
    </form>
  </header>
}

export default UserNav
