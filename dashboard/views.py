import json
from datetime import date, timedelta
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from chat.models import Friendship
from chat.serialization import iso
from chat.views import error, json_response, login_required_json
from world.clock import world_now
from world.models import FeedPost, PostPhoto, PublicationWeek
from world.publication import ReviewError, approve, edit, issues, retry


def operator(view):
    @wraps(view)
    @login_required_json
    def wrapped(request, *args, **kwargs):
        if not request.user.has_perm('world.publish_week'):
            return error(403, '발행 관리 권한이 필요해요.')
        try:
            return view(request, *args, **kwargs)
        except ReviewError as exc:
            return json_response(exc.body, exc.status)
        except (FeedPost.DoesNotExist, PublicationWeek.DoesNotExist):
            return error(404, '주간 계획 또는 게시물을 찾을 수 없어요.')

    return wrapped


def body(request):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeError):
        data = None
    if not isinstance(data, dict):
        raise ReviewError(400, 'invalid_json', 'JSON 객체를 보내 주세요.')
    return data


def integer(data, field):
    value = data.get(field)
    if type(value) is not int or value < 1:
        raise ReviewError(400, 'invalid_integer', f'{field} 값은 양의 정수여야 해요.')
    return value


@require_POST
@operator
def generate(request):
    data = body(request)
    try:
        start = date.fromisoformat(data.get('week_start'))
        if start.weekday() != 0 or start > date.max - timedelta(days=7):
            raise ValueError
    except (ValueError, TypeError):
        return error(400, '시작일은 월요일이어야 해요.')
    with transaction.atomic():
        week, _ = PublicationWeek.objects.get_or_create(start=start)
        week = PublicationWeek.objects.select_for_update().get(pk=week.pk)
        if week.generation == 'failed':
            week.generation, week.generation_error = 'pending', ''
            week.save(update_fields=['generation', 'generation_error'])
    return json_response(
        {'id': week.id, 'generation': week.generation}, 200 if week.generation == 'ready' else 202
    )


def staff_post(post, now):
    return {
        'post_id': post.pk,
        'version': post.version,
        'character_id': post.character_id,
        'name': post.character.name,
        'calendar_ids': [e.pk for e in post.events.all()],
        'state': post.state,
        'public_facts': post.public_facts,
        'operator_note': post.operator_note,
        'caption': post.caption,
        'photo_id': post.photo_id,
        'photo_alt': post.photo_alt,
        'publish_at': iso(post.publish_at),
        'published_at': iso(post.published_at),
        'last_error': post.last_error,
        'issues': issues(post, now) if post.state == FeedPost.State.DRAFT else [],
    }


@require_GET
@never_cache
@operator
@transaction.atomic
def week_detail(request, week_id):
    week = PublicationWeek.objects.select_for_update().get(pk=week_id)
    posts = (
        week.posts.select_related('week', 'character', 'photo')
        .prefetch_related('events')
        .order_by('publish_at', 'pk')
    )
    now = world_now()
    return json_response(
        {
            'id': week.pk,
            'week_start': week.start.isoformat(),
            'generation': week.generation,
            'detail': week.generation_error,
            'revision': week.revision,
            'posts': [staff_post(p, now) for p in posts],
            'photos': list(PostPhoto.objects.order_by('pk').values('key', 'character_id', 'alt')),
        }
    )


@require_http_methods(['PATCH'])
@operator
def patch_post(request, week_id, post_id):
    data = body(request)
    revision = integer(data, 'expected_revision')
    data.pop('expected_revision')
    if not data or data.keys() - {
        'state',
        'caption',
        'public_facts',
        'photo_id',
        'photo_alt',
        'operator_note',
        'publish_at',
    }:
        return error(400, '수정할 항목을 확인해 주세요.')
    for key in ('caption', 'operator_note', 'photo_alt'):
        if key in data and (
            not isinstance(data[key], str) or len(data[key]) > (200 if key == 'photo_alt' else 4000)
        ):
            return error(400, '글자 수와 문구를 확인해 주세요.')
    if 'state' in data and data['state'] not in ('draft', 'skipped'):
        return error(400, '편집 시 초안 또는 제외만 선택할 수 있어요.')
    if 'public_facts' in data:
        facts = data['public_facts']
        if not isinstance(facts, list) or any(
            not isinstance(fact, str) or not fact.strip() for fact in facts
        ):
            return error(400, '공개 사실을 문장 목록으로 입력해 주세요.')
    if 'publish_at' in data:
        try:
            value = parse_datetime(data['publish_at'])
        except (ValueError, TypeError):
            value = None
        if value is None or timezone.is_naive(value):
            return error(400, '시간대를 포함한 발행 시각을 입력해 주세요.')
        data['publish_at'] = value
    if data.get('photo_id') is not None:
        character_id = FeedPost.objects.values_list('character_id', flat=True).get(
            pk=post_id, week_id=week_id
        )
        if (
            not isinstance(data['photo_id'], str)
            or not PostPhoto.objects.filter(pk=data['photo_id'], character_id=character_id).exists()
        ):
            return error(400, '이 캐릭터의 사진을 골라 주세요.')
    return json_response(edit(week_id, post_id, revision, data))


@require_POST
@operator
def approve_week(request, week_id):
    return json_response(approve(week_id, integer(body(request), 'expected_revision'), request.user))


@require_POST
@operator
def retry_post(request, week_id, post_id):
    data = body(request)
    result = retry(week_id, post_id, integer(data, 'expected_revision'), integer(data, 'version'))
    return json_response(result, 200 if result['state'] == 'published' else 202)


def visible(user):
    friends = Friendship.objects.filter(user=user).values('character_id')
    return FeedPost.objects.filter(
        character_id__in=friends, state='published', published_at__lte=world_now()
    ).select_related('character', 'photo')


def page(request, posts):
    cursor = request.GET.get('cursor')
    if cursor:
        try:
            at, pk = signing.loads(cursor, salt='feed-page')
        except signing.BadSignature:
            raise ReviewError(400, 'invalid_cursor', '페이지 위치를 확인해 주세요.')
        posts = posts.filter(Q(published_at__lt=at) | Q(published_at=at, pk__lt=pk))
    rows = list(posts.order_by('-published_at', '-pk')[:31])
    more = len(rows) > 30
    rows = rows[:30]
    return {
        'posts': [
            {
                'id': p.pk,
                'version': p.version,
                'character_id': p.character_id,
                'name': p.character.name,
                'published_at': iso(p.published_at),
                'caption': p.caption,
                'photo': {
                    'url': f'/api/posts/{p.pk}/photo/',
                    'alt': p.photo_alt,
                    'ai_generated': p.photo.ai_generated,
                },
            }
            for p in rows
        ],
        'next_cursor': signing.dumps([rows[-1].published_at.isoformat(), rows[-1].pk], salt='feed-page')
        if more
        else None,
    }


@require_GET
@never_cache
@login_required_json
def feed(request):
    try:
        return json_response(page(request, visible(request.user)))
    except ReviewError as exc:
        return json_response(exc.body, exc.status)


@require_GET
@never_cache
@login_required_json
def profile(request, character_id):
    room = get_object_or_404(
        Friendship.objects.select_related('character'), user=request.user, character_id=character_id
    )
    char = room.character
    now = timezone.localtime(world_now())
    slot = (
        char.routines.filter(weekday=now.weekday(), start_time__lte=now.time())
        .filter(Q(end_time__gt=now.time()) | Q(end_time='00:00'))
        .order_by('-start_time')
        .first()
    )
    try:
        result = page(request, visible(request.user).filter(character=char))
    except ReviewError as exc:
        return json_response(exc.body, exc.status)
    return json_response(
        {
            **result,
            'id': char.pk,
            'name': char.name,
            'room_id': room.pk,
            'is_ai': True,
            'profile_image': char.profile_image.url if char.profile_image else None,
            'status_message': char.status_message,
            'current_activity': slot.activity if slot else None,
        }
    )


def image_response(photo):
    try:
        stream = photo.image.open('rb')
    except (ValueError, OSError):
        raise Http404
    return FileResponse(stream, filename=photo.image.name.rsplit('/', 1)[-1])


@require_GET
@never_cache
@login_required_json
def photo(request, post_id):
    return image_response(get_object_or_404(visible(request.user), pk=post_id).photo)


@require_GET
@never_cache
@operator
def preview(request, key):
    return image_response(get_object_or_404(PostPhoto, pk=key))


@require_GET
@login_required(login_url='/login/')
@operator
@ensure_csrf_cookie
def studio_page(request):
    return render(request, 'dashboard/publication.html', {'page': 'studio'})


@require_GET
@login_required(login_url='/login/')
@ensure_csrf_cookie
def feed_page(request):
    return render(request, 'dashboard/publication.html', {'page': 'feed'})
