import { useCallback, useEffect, useRef, useState } from 'react'
import { apiResponse, csrfToken, request } from './chat'
import type { Profile } from './chat'

export type PersonalPost = {
  kind: 'personal'
  id: number
  caption: string
  published_at: string
  photo: { url: string; alt: string; ai_generated: false } | null
}
type CharacterPost = Profile['posts'][number] & { kind?: 'character'; name: string; character_id: number }
export type FeedPost = CharacterPost | PersonalPost
type PostPage<T> = { posts: T[]; next_cursor: string | null }

export function postKey(post: FeedPost) {
  return (post.kind === 'personal' ? 'personal-' : 'post-') + post.id
}
const query = (cursor?: string) => cursor ? '?cursor=' + encodeURIComponent(cursor) : ''
export const getFeed = (cursor?: string) => request<PostPage<FeedPost>>('/api/feed/' + query(cursor))
const ownPosts = '/api/me/posts/'
export const getOwnPosts = (cursor?: string) => request<PostPage<PersonalPost>>(ownPosts + query(cursor))

export function createPost(caption: string, key: string, file?: File) {
  const body = new FormData()
  body.set('caption', caption)
  if (file) body.set('photo', file)
  return request<{ post: PersonalPost }>(ownPosts, {
    method: 'POST', headers: { 'X-CSRFToken': csrfToken(), 'Idempotency-Key': key }, body,
  })
}
export const deletePost = (id: number) => apiResponse(`${ownPosts}${id}/`, { method: 'DELETE', headers: { 'X-CSRFToken': csrfToken() } })

export function usePostTimeline<T extends FeedPost>(fetchPage: (cursor?: string) => Promise<PostPage<T>>) {
  const [page, setPage] = useState<PostPage<T> | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const loadedOlder = useRef(false)
  const retryCursor = useRef<string | undefined>(undefined)
  // Keep changes acknowledged while a page is in flight when applying that page.
  const pending = useRef<Map<string, T | null> | null>(null)
  const load = useCallback(async (cursor?: string) => {
    if (pending.current) return
    const changes = new Map<string, T | null>()
    pending.current = changes
    retryCursor.current = cursor
    setBusy(true)
    setError('')
    try {
      const result = await fetchPage(cursor)
      if (pending.current !== changes) return
      loadedOlder.current = !!cursor
      setPage(previous => {
        const incoming = cursor && previous ? [...previous.posts, ...result.posts] : result.posts
        const rows = new Map<string, T>()
        for (const [key, post] of [...changes].reverse()) if (post) rows.set(key, post)
        for (const post of incoming) {
          const key = postKey(post)
          if (!changes.has(key)) rows.set(key, post)
        }
        return { posts: [...rows.values()], next_cursor: result.next_cursor }
      })
    } catch (e) {
      if (pending.current === changes) setError((e as Error).message)
    } finally {
      if (pending.current === changes) { pending.current = null; setBusy(false) }
    }
  }, [fetchPage])

  useEffect(() => {
    void load()
    const restored = (event: PageTransitionEvent) => { if (event.persisted) void load() }
    window.addEventListener('pageshow', restored)
    return () => { pending.current = null; window.removeEventListener('pageshow', restored) }
  }, [load])

  function add(post: T) {
    const key = postKey(post)
    pending.current?.set(key, post)
    setPage(previous => ({
      posts: [post, ...(previous?.posts.filter(item => postKey(item) !== key) ?? [])],
      next_cursor: previous?.next_cursor ?? null,
    }))
  }
  function remove(post: T) {
    const key = postKey(post)
    pending.current?.set(key, null)
    setPage(previous => previous && { ...previous, posts: previous.posts.filter(item => postKey(item) !== key) })
  }
  return { page, load, busy, error, loadedOlder, retry: () => load(retryCursor.current), add, remove }
}
