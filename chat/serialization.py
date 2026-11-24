from django.utils import timezone

from world.clock import world_now


def iso(dt):
    return timezone.localtime(dt).isoformat(timespec='seconds') if dt else None


def quote_json(post):
    return {
        'id': post.id,
        'version': post.version,
        'status': 'available',
        'caption': post.caption,
        'photo_url': f'/api/posts/{post.pk}/photo/',
        'photo_alt': post.photo_alt,
    }


def message_json(m):
    quote = None
    if m.source_post_id:
        post = m.source_post
        if (
            post.state == 'published'
            and post.version == m.source_version
            and post.published_at
            and post.published_at <= world_now()
            and post.character_id == m.room.character_id
        ):
            quote = quote_json(post)
        else:
            quote = {'status': 'unavailable'}
    return {
        'id': m.id,
        'sender': m.sender,
        'source_post': quote,
        'kind': m.kind,
        'text': m.text,
        'status': m.status,
        'created_at': iso(m.created_at),
        'read_at': iso(m.read_at),
    }
