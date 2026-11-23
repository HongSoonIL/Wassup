import type { Friend, Message } from './chat'
import portrait from './assets/dohyun.webp'
import cafe from './assets/cafe.webp'

const date = '2026-11-19T'
function entry(id: number, time: string, sender: Message['sender'], text: string, photo?: string): Message {
  const at = date + time + ':00+09:00'
  return { id, sender, text, photo, kind: photo ? 'photo' : 'text', created_at: at, read_at: at, status: 'read' }
}
export const sampleMessages = [
  entry(1, '18:40', 'user', '도현아 오늘도 카페야?'),
  entry(2, '20:10', 'character', 'ㅇㅇ 지금 잠깐 쉬는 중 ㅋㅋ'),
  entry(3, '20:11', 'character', '오늘 카페 분위기 괜찮지', cafe),
  entry(4, '20:12', 'user', '오 분위기 좋다 ㅋㅋ 오늘 몇 시에 끝나?'),
  entry(5, '20:13', 'character', '열 시! 마감하고 연락할게'),
]
export const sampleFriend: Friend = {
  room_id: 1, character: { id: 1, name: '도현', status_message: '카페 알바 중', profile_image: portrait },
  last_message: sampleMessages.at(-1)!, unread_count: 0,
}
