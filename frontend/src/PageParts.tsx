import type { MouseEventHandler, ReactNode } from 'react'
import { MessageCircle } from 'lucide-react'
import './page-parts.css'

export function PageTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return <header className="ws-page-title"><h1>{children}</h1>{action}</header>
}

export function SectionTitle({ children, id }: { children: ReactNode; id?: string }) {
  return <h2 className="ws-section-title" id={id}>{children}</h2>
}

export function EmptyState({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return <div className="ws-empty"><p>{children}</p>{action}</div>
}

export function ReplyToPost({ id, name, onClick }: {
  id: number; name: string; onClick?: MouseEventHandler<HTMLAnchorElement>
}) {
  return <a className="feed-quote" href={`/chat/from-post/${id}/`} onClick={onClick}
    aria-label={name+'에게 이 게시물로 답장'}><MessageCircle size={17} aria-hidden="true"/>답장</a>
}
