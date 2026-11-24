import fixture from '../../../docs/design/day-samples.json'
import dohyun from '../assets/dohyun.webp'
import seoyun from './assets/seoyun.webp'

export type CharacterId = 'dohyun' | 'seoyun'
export type ReviewPost = {
  id: string
  characterId: CharacterId
  caption: string
  photoId: string
  photoAlt: string
  publishAt: string
  releasedAt?: string
  state: 'draft' | 'scheduled' | 'published' | 'failed'
  diary: string
}

export type OpsProps = {
  characterId: CharacterId
  onCharacter: (id: CharacterId) => void
  now: string
  onTime: (iso: string) => void
  records: ReviewPost[]
  onSave: (record: ReviewPost) => void
  onPreview: (target?: 'feed') => void
  onFailNext?: (id: string) => void
}

export const characters = fixture.characters
export const previewAt = fixture.previewAt
export const character = (id: CharacterId) => characters.find(c => c.id === id)!
export const portrait = (id: CharacterId) => id === 'dohyun' ? dohyun : seoyun
const assets = import.meta.glob<string>('./assets/*.webp', { eager: true, import: 'default' })
export const media = (photoId: string) => assets['./assets/' + photoId + '.webp']
const clock = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit' })
export const time = (iso: string) => clock.format(new Date(iso))

export function currentActivity(id: CharacterId, now: string) {
  const instant = Date.parse(now)
  return character(id).schedule.find(s => Date.parse(s.start) <= instant && instant < Date.parse(s.end))?.label
    ?? '등록된 일과 없음'
}

export function publicPosts(records: ReviewPost[], now: string) {
  return records
    .filter(p => p.state === 'published' && Date.parse(p.releasedAt ?? p.publishAt) <= Date.parse(now))
    .sort((a, b) => Date.parse(b.publishAt) - Date.parse(a.publishAt))
}

export const initialPosts: ReviewPost[] = characters.flatMap(c => c.posts.map(post => ({
  ...post,
  characterId: c.id as CharacterId,
  state: post.state as ReviewPost['state'],
  diary: c.diary.find(entry => entry.sourcePostId === post.id)?.text ?? '',
})))
