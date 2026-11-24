"""Keyset pagination shared by the mixed feed and the two kinds of profile."""

from django.core import signing
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from chat.serialization import iso, readable_photo
from world.post_links import current_previous
from world.publication import ReviewError


PAGE_SIZE = 30


def public_previous(post, now):
    previous = current_previous(post, now)
    if previous is None or not readable_photo(previous.photo):
        return None
    return {
        'id': previous.pk,
        'version': previous.version,
        'caption': previous.caption,
        'published_at': iso(previous.published_at),
        'photo': {
            'url': f'/api/posts/{previous.pk}/photo/?version={previous.version}',
            'alt': previous.photo_alt,
        },
    }


def character_post_json(post, now):
    return {
        'kind': 'character',
        'id': post.pk,
        'version': post.version,
        'character_id': post.character_id,
        'name': post.character.name,
        'published_at': iso(post.published_at),
        'caption': post.caption,
        'previous_post': public_previous(post, now),
        'photo': {
            'url': f'/api/posts/{post.pk}/photo/',
            'alt': post.photo_alt,
            'ai_generated': post.photo.ai_generated,
        },
    }


def personal_post_json(post):
    return {
        'kind': 'personal',
        'id': post.pk,
        'caption': post.caption,
        'published_at': iso(post.created_at),
        'photo': {
            'url': reverse('my-post-photo', kwargs={'post_id': post.pk}),
            'alt': '내가 올린 사진',
            'ai_generated': False,
        } if post.photo else None,
    }


def decode_cursor(token):
    if not token:
        return None
    try:
        data = signing.loads(token, salt='feed-page')
        if not isinstance(data, list) or len(data) not in (2, 3):
            raise ValueError
        # Earlier feed and character-profile cursors held only [timestamp, id].
        stamp, pk = data[:2]
        kind = data[2] if len(data) == 3 else 'character'
        at = parse_datetime(stamp)
        if (at is None or timezone.is_naive(at) or type(pk) is not int or not 0 < pk < 2**63
                or kind not in ('character', 'personal')):
            raise ValueError
        return at, pk, kind
    except (signing.BadSignature, ValueError, TypeError, OverflowError):
        raise ReviewError(400, 'invalid_cursor', '페이지 위치를 확인해 주세요.') from None


def page_rows(queryset, date_field, kind, cursor):
    if cursor:
        at, pk, last_kind = cursor
        # All three keys sort descending. Include the equal id if this source sorts after the anchor.
        id_lookup = 'pk__lte' if kind < last_kind else 'pk__lt'
        queryset = queryset.filter(
            Q(**{date_field + '__lt': at}) | Q(**{date_field: at, id_lookup: pk})
        )
    rows = queryset.order_by('-' + date_field, '-pk')[:PAGE_SIZE + 1]
    return [(getattr(post, date_field), post.pk, kind, post) for post in rows]


def post_page(token, *, now=None, characters=None, personal=None):
    cursor = decode_cursor(token)
    rows = []
    if characters is not None:
        characters = characters.select_related('previous_post__photo')
        rows.extend(page_rows(characters, 'published_at', 'character', cursor))
    if personal is not None:
        rows.extend(page_rows(personal, 'created_at', 'personal', cursor))
    rows.sort(key=lambda row: row[:3], reverse=True)
    next_cursor = None
    if len(rows) > PAGE_SIZE:
        rows = rows[:PAGE_SIZE]
        at, pk, kind, _ = rows[-1]
        next_cursor = signing.dumps([at.isoformat(), pk, kind], salt='feed-page')
    return {
        'posts': [personal_post_json(post) if kind == 'personal' else character_post_json(post, now)
                  for _, _, kind, post in rows],
        'next_cursor': next_cursor,
    }
