from django.core.exceptions import SuspiciousFileOperation
from django.utils import timezone

from world.clock import world_now


def iso(dt, *, timespec='seconds'):
    return timezone.localtime(dt).isoformat(timespec=timespec) if dt else None


def public_character_json(character):
    return {
        'id': character.pk,
        'name': character.name,
        'status_message': character.status_message,
        'profile_image': character.profile_image.url if character.profile_image else None,
    }


def quote_json(post):
    return {
        'id': post.id,
        'version': post.version,
        'status': 'available',
        'caption': post.caption,
        'photo_url': f'/api/posts/{post.pk}/photo/',
        'photo_alt': post.photo_alt,
    }


def readable_photo(photo):
    if not photo or not photo.image:
        return False
    try:
        with photo.image.storage.open(photo.image.name, 'rb') as stream:
            return bool(stream.read(1))
    except (OSError, SuspiciousFileOperation, ValueError):
        return False


def attachment_json(message, now):
    if message.sender == 'user' and message.kind == 'photo':
        field = message.user_photo
        if not field:
            return {'status': 'unavailable'}
        try:
            with field.storage.open(field.name, 'rb') as stream:
                if not stream.read(1):
                    return {'status': 'unavailable'}
        except (OSError, SuspiciousFileOperation, ValueError):
            return {'status': 'unavailable'}
        return {
            'status': 'available',
            'photo_url': f'/api/rooms/{message.room_id}/messages/{message.pk}/photo/',
            'photo_alt': '대화에 보낸 사진',
        }
    if message.attachment_version is None and message.attachment_post_id is None:
        return None
    post = message.attachment_post
    if (
        message.sender == 'character'
        and message.kind == 'photo'
        and post
        and post.state == 'published'
        and post.version == message.attachment_version
        and post.published_at
        and post.published_at <= now
        and post.character_id == message.room.character_id
        and post.photo_id
        and post.photo.character_id == post.character_id
        and readable_photo(post.photo)
    ):
        return {**quote_json(post), 'photo_url': f'/api/posts/{post.pk}/photo/?version={post.version}'}
    return {'status': 'unavailable'}


def message_json(m, *, now=None):
    now = world_now() if now is None else now
    quote = None
    if m.source_post_id:
        post = m.source_post
        if (
            post.state == 'published'
            and post.version == m.source_version
            and post.published_at
            and post.published_at <= now
            and post.character_id == m.room.character_id
        ):
            quote = quote_json(post)
        else:
            quote = {'status': 'unavailable'}
    elif m.source_personal_post_id or m.source_version is not None:
        from accounts.models import PersonalPost

        post = PersonalPost.objects.filter(pk=m.source_personal_post_id, owner_id=m.room.user_id).first()
        quote = {'kind': 'personal', 'status': 'unavailable'}
        if post:
            quote = {
                'kind': 'personal', 'id': post.pk, 'version': 1, 'status': 'available', 'caption': post.caption,
                'photo_url': f'/api/me/posts/{post.pk}/photo/' if post.photo else None,
                'photo_alt': '내 게시물 사진' if post.photo else '',
            }
    data = {
        'id': m.id,
        'sender': m.sender,
        'source_post': quote,
        'kind': m.kind,
        'text': m.text,
        'status': m.status,
        'created_at': iso(m.created_at),
        'read_at': iso(m.read_at),
    }
    attachment = attachment_json(m, now)
    if attachment is not None:
        data['attachment'] = attachment
    return data
