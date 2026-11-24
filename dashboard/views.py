import json
from datetime import date, timedelta
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db import transaction
from django.db.models import F, Q
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
from world import photo_jobs
from world.clock import routine_at, world_now
from world.models import FeedPost, PostPhoto, PublicationWeek
from world.publication import ReviewError, approve, edit, issues, retry
from world.weekly import bounded_text, due_week_start, normalized


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
    retry_failed = data.get('retry_failed', True)
    if not isinstance(retry_failed, bool):
        return error(400, '다시 생성할지 확인해 주세요.')
    try:
        start = date.fromisoformat(data.get('week_start'))
        if start.weekday() != 0 or start > date.max - timedelta(days=7):
            raise ValueError
    except (ValueError, TypeError):
        return error(400, '시작일은 월요일이어야 해요.')
    with transaction.atomic():
        week, _ = PublicationWeek.objects.get_or_create(start=start)
        week = PublicationWeek.objects.select_for_update().get(pk=week.pk)
        if week.generation == 'failed' and retry_failed:
            week.generation, week.generation_error = 'pending', ''
            week.generation_token = week.lease_until = None
            week.generation_attempts = 0
            week.save(
                update_fields=[
                    'generation',
                    'generation_error',
                    'generation_token',
                    'lease_until',
                    'generation_attempts',
                ]
            )
    return json_response(
        {'id': week.id, 'generation': week.generation}, 202 if week.generation == 'pending' else 200
    )


def staff_post(post, now):
    return {
        'post_id': post.pk,
        'version': post.version,
        'character_id': post.character_id,
        'name': post.character.name,
        'calendar_ids': [e.pk for e in post.events.all()],
        'calendar_labels': [e.label for e in post.events.all()],
        'state': post.state,
        'public_facts': post.public_facts,
        'operator_note': post.operator_note,
        'caption': post.caption,
        'photo_id': post.photo_id,
        'photo_alt': post.photo_alt,
        'photo_job': photo_jobs.status_for(post),
        'event_at': iso(post.event_at),
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
    # Older studio clients fence edits with the week revision, which advances on every content change.
    version = integer(data, 'version') if 'version' in data else None
    data.pop('expected_revision')
    data.pop('version', None)
    if not data or data.keys() - {
        'state',
        'caption',
        'public_facts',
        'photo_id',
        'photo_alt',
        'operator_note',
        'event_at',
        'publish_at',
    }:
        return error(400, '수정할 항목을 확인해 주세요.')
    for key, limit in [('caption', 4000), ('operator_note', 4000), ('photo_alt', 200)]:
        if key in data and not bounded_text(data[key], limit, allow_empty=key != 'caption'):
            return error(400, '글자 수와 문구를 확인해 주세요.')
    if 'state' in data and data['state'] not in ('draft', 'skipped'):
        return error(400, '편집 시 초안 또는 제외만 선택할 수 있어요.')
    if 'public_facts' in data:
        facts = data['public_facts']
        if (
            not isinstance(facts, list)
            or not 1 <= len(facts) <= 5
            or not all(bounded_text(fact, 500) for fact in facts)
            or len({normalized(fact) for fact in facts}) != len(facts)
        ):
            return error(400, '서로 다른 공개 사실을 1~5개 입력해 주세요. 한 줄에 최대 500자예요.')
        data['public_facts'] = [fact.strip() for fact in facts]
    for field in ('publish_at', 'event_at'):
        if field not in data:
            continue
        try:
            value = parse_datetime(data[field])
            if value is None or timezone.is_naive(value):
                raise ValueError
            data[field] = timezone.localtime(value)
        except (ValueError, TypeError, OverflowError):
            return error(400, '시간대를 포함한 사건·발행 시각을 입력해 주세요.')
    if data.get('photo_id') is not None and not bounded_text(data['photo_id'], 50):
        return error(400, '이 캐릭터의 사진을 골라 주세요.')
    return json_response(edit(week_id, post_id, revision, data, version=version))


@require_POST
@operator
@transaction.atomic
def regenerate_photo(request, week_id, post_id):
    data = body(request)
    if data.keys() != {'expected_revision', 'version'}:
        return error(400, 'expected_revision과 version을 보내 주세요.')
    photo_jobs.regenerate(week_id, post_id, integer(data, 'expected_revision'), integer(data, 'version'))
    post = FeedPost.objects.select_related('week').get(pk=post_id, week_id=week_id)
    return json_response({
        'revision': post.week.revision,
        'post_id': post.pk,
        'version': post.version,
        'state': post.state,
        'photo_job': photo_jobs.status_for(post),
    }, 202)


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
    slot = routine_at(char, now)
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
    posts = visible(request.user).filter(photo__character_id=F('character_id'))
    versions = request.GET.getlist('version')
    if versions:
        version = versions[0]
        if len(versions) != 1 or not (
            version.isascii() and version.isdecimal() and len(version) <= 10 and 0 < int(version) < 2**31
        ):
            raise Http404
        posts = posts.filter(version=int(version))
    return image_response(get_object_or_404(posts, pk=post_id).photo)


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
    start = due_week_start(world_now())
    requested = request.GET.get('week_start', '')
    try:
        selected = date.fromisoformat(requested)
    except ValueError:
        selected = None
    if (
        selected and selected.isoformat() == requested
        and selected.weekday() == 0 and selected <= date.max - timedelta(days=7)
    ):
        start = selected
    return render(request, 'dashboard/publication.html', {'page': 'studio', 'week_start': start.isoformat()})


@require_GET
@login_required(login_url='/login/')
@ensure_csrf_cookie
def feed_page(request):
    return render(request, 'dashboard/publication.html', {'page': 'feed'})
