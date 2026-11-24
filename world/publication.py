"""Weekly editorial review and durable publication jobs."""

from datetime import datetime, time, timedelta

from PIL import Image

from django.db import transaction
from django.utils import timezone

from .clock import world_now
from .models import Character, FeedPost, PostPhoto, PostRevision, PublicationWeek, SchoolEvent

State = FeedPost.State


class ReviewError(Exception):
    def __init__(self, status, code, detail, **extra):
        self.status = status
        self.body = {'code': code, 'detail': detail, **extra}


def locked_week(week_id, revision):
    week = PublicationWeek.objects.select_for_update().get(pk=week_id)
    if week.revision != revision:
        raise ReviewError(409, 'week_changed', '주간 계획이 바뀌었어요.', current_revision=week.revision)
    if week.generation != 'ready':
        raise ReviewError(409, 'week_pending', '주간 후보 생성을 먼저 완료해 주세요.')
    return week


def advance(week):
    week.revision += 1
    week.save(update_fields=['revision'])


def snapshot(post):
    PostRevision.objects.create(
        post=post,
        version=post.version,
        snapshot={
            'caption': post.caption,
            'public_facts': post.public_facts,
            'photo_id': post.photo_id,
            'photo_alt': post.photo_alt,
            'publish_at': post.publish_at.isoformat(),
            'operator_note': post.operator_note,
            'character_id': post.character_id,
            'calendar': [
                {
                    'id': e.pk,
                    'revision': e.revision,
                    'label': e.label,
                    'start': e.start_date.isoformat(),
                    'end': e.end_date_exclusive.isoformat(),
                }
                for e in post.events.order_by('pk')
            ],
        },
    )


def revise(post, state):
    post.version += 1
    post.state = state
    post.approved_version = post.approved_by_id = post.approved_at = None
    post.last_error = ''
    post.save()
    snapshot(post)


def invalidate_event(event):
    active = FeedPost.objects.filter(events=event).exclude(state__in=[State.PUBLISHED, State.SKIPPED])
    for week in (
        PublicationWeek.objects.filter(pk__in=active.values('week_id')).order_by('pk').select_for_update()
    ):
        affected = list(active.filter(week=week))
        for post in affected:
            revise(post, State.DRAFT)
        if affected:
            advance(week)


def issues(post, now):
    result = []
    if not post.photo_id:
        result.append('missing_photo')
    elif post.photo.character_id != post.character_id or not post.photo_alt.strip():
        result.append('invalid_photo')
    if not post.caption.strip() or not post.public_facts:
        result.append('missing_content')
    if post.publish_at <= now:
        result.append('past_time')
    local = timezone.localtime(post.publish_at)
    day = local.date()
    if not post.week.start <= day < post.week.start + timedelta(days=7):
        result.append('outside_week')
    for event in post.events.all():
        first = event.start_date - timedelta(days=1 if event.kind == SchoolEvent.Kind.SPORTS else 0)
        if not first <= day < event.end_date_exclusive:
            result.append('calendar_conflict')
            break
    for routine in post.character.routines.filter(weekday=day.weekday(), state='sleep'):
        if routine.start_time <= local.time() and (
            routine.end_time == time.min or local.time() < routine.end_time
        ):
            result.append('schedule_conflict')
            break
    if (
        FeedPost.objects.filter(week=post.week, character=post.character, caption=post.caption)
        .exclude(pk=post.pk)
        .exclude(state=State.SKIPPED)
        .exists()
    ):
        result.append('duplicate')
    return result


@transaction.atomic
def edit(week_id, post_id, revision, changes):
    week = locked_week(week_id, revision)
    post = week.posts.get(pk=post_id)
    if post.state == State.PUBLISHED:
        raise ReviewError(409, 'published', '이미 발행한 게시물이에요.')
    for field, value in changes.items():
        setattr(post, field, value)
    revise(post, changes.get('state', State.DRAFT))
    advance(week)
    return {'revision': week.revision, 'post_id': post.pk, 'version': post.version, 'state': post.state}


@transaction.atomic
def approve(week_id, revision, user):
    week = locked_week(week_id, revision)
    drafts = list(week.posts.filter(state=State.DRAFT).select_related('week', 'photo', 'character'))
    if not drafts:
        raise ReviewError(409, 'no_drafts', '예약할 초안이 없어요.')
    now = world_now()
    problems = []
    for post in drafts:
        found = issues(post, now)
        if found:
            problems.append({'post_id': post.pk, 'issues': found})
    if problems:
        raise ReviewError(422, 'review_required', '표시된 후보를 수정하거나 제외해 주세요.', posts=problems)
    for post in drafts:
        post.state = State.SCHEDULED
        post.approved_version = post.version
        post.approved_by = user
        post.approved_at = now
    FeedPost.objects.bulk_update(drafts, ['state', 'approved_version', 'approved_by', 'approved_at'])
    advance(week)
    return {'revision': week.revision, 'scheduled': [{'post_id': p.pk, 'version': p.version} for p in drafts]}


@transaction.atomic
def retry(week_id, post_id, revision, version):
    week = locked_week(week_id, revision)
    post = week.posts.get(pk=post_id)
    if post.version != version or post.approved_version != version:
        raise ReviewError(409, 'stale_version', '현재 버전을 다시 검토해 주세요.')
    if post.state == State.FAILED:
        post.state, post.last_error = State.SCHEDULED, ''
        post.attempt += 1
        post.save(update_fields=['state', 'last_error', 'attempt'])
    return {'post_id': post.pk, 'version': version, 'state': post.state}


def generate_week(week_id):
    # Lock calendar rows before the week, also used by calendar editing.
    with transaction.atomic():
        start = PublicationWeek.objects.values_list('start', flat=True).get(pk=week_id)
        end = start + timedelta(days=7)
        events = list(
            SchoolEvent.objects.select_for_update()
            .filter(start_date__lte=end, end_date_exclusive__gt=start)
            .order_by('pk')
        )
        week = PublicationWeek.objects.select_for_update().get(pk=week_id)
        if week.generation != 'pending':
            return
        people = {c.name: c for c in Character.objects.filter(name__in=['도현', '서윤'])}
        if people.keys() != {'도현', '서윤'}:
            week.generation, week.generation_error = 'failed', 'seed_world로 두 캐릭터를 먼저 등록해 주세요.'
            week.save()
            return

        def candidate(person, day, hour, minute, key, text, facts, event=None):
            if not start <= day < end:
                return
            photo = PostPhoto.objects.filter(pk=key, character=people[person]).first() if key else None
            post = week.posts.create(
                character=people[person],
                caption=text,
                public_facts=facts,
                photo=photo,
                photo_alt=photo.alt if photo else '',
                publish_at=timezone.make_aware(datetime.combine(day, time(hour, minute))),
            )
            if event:
                post.events.add(event)
            snapshot(post)

        for event in events:
            if event.kind == SchoolEvent.Kind.SPORTS:
                candidate(
                    '도현',
                    event.start_date - timedelta(days=1),
                    17,
                    50,
                    'cafe-interior',
                    '내일 체육대회 몇 시에 모이냐… 커피부터 사야겠다',
                    ['체육대회 전날 카페에 들렀다.'],
                    event,
                )
            elif event.start_date < end:
                day = min(
                    max(start + timedelta(days=2), event.start_date),
                    event.end_date_exclusive - timedelta(days=1),
                )
                if event.kind == SchoolEvent.Kind.EXAMS:
                    candidate(
                        '서윤',
                        day,
                        16,
                        30,
                        'studio-print',
                        '둘 다 뽑아 보길 잘했다. 오늘은 주황 🍊',
                        ['군청색과 주황색으로 포스터를 한 장씩 인쇄했다.', '최종 색으로 주황을 골랐다.'],
                        event,
                    )
                    if day + timedelta(days=1) < event.end_date_exclusive:
                        candidate(
                            '서윤',
                            day + timedelta(days=1),
                            18,
                            0,
                            None,
                            '책상 치우다가 작년 스케치북 발견',
                            ['책상을 정리하다가 스케치북을 찾았다.'],
                            event,
                        )
                else:
                    candidate(
                        '도현',
                        day,
                        14,
                        0,
                        'cafe-interior',
                        '방학인데 또 카페 왔음',
                        ['방학 중 카페에 들렀다.'],
                        event,
                    )
        if not week.posts.exists():
            candidate(
                '도현',
                start + timedelta(days=2),
                17,
                55,
                'cafe-interior',
                '출근 전에 한 잔만',
                ['카페에서 일하기 전에 커피를 마셨다.'],
            )
        week.generation, week.revision = 'ready', 1
        week.save(update_fields=['generation', 'revision'])


def dispatch_publications(now=None, limit=100):
    instant = now or world_now()
    due = (
        FeedPost.objects.filter(state=State.SCHEDULED, publish_at__lte=instant)
        .select_related('photo')
        .order_by('publish_at', 'pk')
    )
    for candidate in list(due[:limit]):
        try:
            with candidate.photo.image.open('rb') as stream, Image.open(stream) as image:
                image.load()
                available = True
        except (OSError, ValueError, Image.DecompressionBombError, SyntaxError):
            available = False
        with transaction.atomic():
            PublicationWeek.objects.select_for_update().get(pk=candidate.week_id)
            post = FeedPost.objects.get(pk=candidate.pk)
            if (
                post.state != State.SCHEDULED
                or post.version != candidate.version
                or post.attempt != candidate.attempt
                or post.approved_version != post.version
            ):
                continue
            post.state = State.PUBLISHED if available else State.FAILED
            post.published_at = (instant if now is not None else world_now()) if available else None
            post.last_error = (
                '' if available else '사진을 읽을 수 없어요. 자산을 복구한 뒤 다시 시도해 주세요.'
            )
            post.save(update_fields=['state', 'published_at', 'last_error'])
