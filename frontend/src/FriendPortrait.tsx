import { useState } from 'react'
import seoyun from './design/assets/seoyun.webp'
import dohyun from './assets/dohyun.webp'

const portraits: Record<string, string> = { 서윤: seoyun, 도현: dohyun }

export default function FriendPortrait({ person, className = 'avatar' }: {
  person?: { name: string; profile_image: string | null }; className?: string
}) {
  const [broken, setBroken] = useState<string[]>([])
  const preferred = person?.profile_image
  const src = [preferred, portraits[person?.name || '']].find(source => source && !broken.includes(source))
  return src
    ? <img className={className} src={src} onError={() => setBroken(previous => [...previous, src])} alt="" />
    : <span className={className + ' initials'} aria-hidden="true">{person?.name[0] || '·'}</span>
}
