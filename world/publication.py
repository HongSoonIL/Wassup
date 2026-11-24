"""Weekly editorial review and durable publication jobs."""

import logging
from datetime import timedelta
from uuid import uuid4

from PIL import Image

from django.db import OperationalError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from llm import weekly

from .clock import world_now
from .models import Character, FeedPost, PostPhoto, PostRevision, PublicationWeek
from .weekly import (
    calendar_entry,
    calendar_for,
    candidates_for,
    content_keys,
    contexts_for,
    routines_for,
    timing_issues,
)

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
            'event_at': post.event_at.isoformat() if post.event_at else None,
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
    result.extend(
        timing_issues(
            post.week.start,
            post.event_at,
            post.publish_at,
            now,
            routines_for(post.character, post.week.start),
            [calendar_entry(e) for e in post.events.all()],
        )
    )
    keys = content_keys(post.caption, post.public_facts)
    others = (
        post.week.posts.filter(character_id=post.character_id)
        .exclude(pk=post.pk)
        .exclude(state=State.SKIPPED)
    )
    for other in others:
        if (
            other.publish_at == post.publish_at
            or (post.event_at and other.event_at == post.event_at)
            or keys & content_keys(other.caption, other.public_facts)
        ):
            result.append('duplicate')
            break
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


LEASE = timedelta(seconds=2 * weekly.REQUEST_TIMEOUT + 40)
MAX_ATTEMPTS = 2  # At most one automatic recovery for calendar changes or an abandoned claim.


def available_weeks():
    return (
        PublicationWeek.objects.filter(generation='pending')
        .filter(Q(generation_token=None) | Q(lease_until__lte=timezone.now()))
        .order_by('pk')
    )


@transaction.atomic
def claim_week(week_id):
    week = PublicationWeek.objects.select_for_update().filter(pk=week_id).first()
    now = timezone.now()
    if week is None or week.generation != 'pending' or (week.lease_until and week.lease_until > now):
        return None
    if week.posts.exists():
        # Preserve all existing operator work, including a legacy week with inconsistent status.
        week.generation = 'ready'
        week.revision = max(week.revision, 1)
        week.generation_token = week.lease_until = None
        week.save()
        return None
    if week.generation_attempts >= MAX_ATTEMPTS:
        week.generation, week.generation_error = (
            'failed',
            '생성 작업이 중단됐어요. 워커 상태를 확인한 뒤 다시 시도해 주세요.',
        )
        week.generation_token = week.lease_until = None
        week.save()
        return None
    week.generation_token, week.lease_until = uuid4(), now + LEASE
    week.generation_attempts += 1
    week.save(update_fields=['generation_token', 'lease_until', 'generation_attempts'])
    return week


def current_claim(week):
    return PublicationWeek.objects.filter(
        pk=week.pk,
        generation='pending',
        generation_token=week.generation_token,
        lease_until__gt=timezone.now(),
    )


def calendar_changed(week):
    current_claim(week).update(
        generation='pending' if week.generation_attempts < MAX_ATTEMPTS else 'failed',
        generation_token=None,
        lease_until=None,
        generation_error='학사 일정이 바뀌었어요. 최신 일정으로 다시 생성해 주세요.',
    )


@transaction.atomic
def store_week(week, calendar, contexts, rows):
    # PostgreSQL SHARE blocks all calendar writes (including previously absent rows).
    # Acquire it before the week lock to match SchoolEvent.save's invalidation order.
    with connection.cursor() as cursor:
        cursor.execute('LOCK TABLE world_schoolevent IN SHARE MODE')
    live = current_claim(week).select_for_update().first()
    if live is None:
        return
    if calendar_for(week.start) != calendar:
        calendar_changed(week)
        return
    selected_keys = {r['photo_id'] for r in rows if r['photo_id']}
    expected = {
        (asset['key'], context['character']['id'], asset['alt'])
        for context in contexts
        for asset in context['assets']
        if asset['key'] in selected_keys
    }
    owned = set(
        PostPhoto.objects.select_for_update()
        .filter(pk__in=selected_keys)
        .order_by('pk')
        .values_list('pk', 'character_id', 'alt')
    )
    if owned != expected:
        raise weekly.WeeklyError('사용 가능 사진이 바뀌었어요. 캐릭터의 사진을 확인하고 다시 시도해 주세요.')
    people = Character.objects.filter(pk__in=[c['character']['id'] for c in contexts]).prefetch_related(
        'routines'
    )
    current_routines = {p.pk: routines_for(p, week.start) for p in people}
    if any(current_routines.get(c['character']['id']) != c['routines'] for c in contexts):
        raise weekly.WeeklyError('캐릭터 일과가 바뀌었어요. 새 시간표로 다시 생성해 주세요.')
    now = world_now()
    if any(row['publish_at'] <= now for row in rows):
        raise weekly.WeeklyError('생성 중 예약 시간이 지났어요. 현재 시각으로 다시 생성해 주세요.')
    for row in rows:
        ids = row.pop('calendar_ids')
        post = live.posts.create(**row)
        post.events.add(*ids)
        snapshot(post)
    live.generation, live.generation_error = 'ready', ''
    live.revision += 1
    live.generation_token = live.lease_until = None
    live.save()


def generate_week(week_id):
    week = claim_week(week_id)
    if week is None:
        return
    try:
        now = world_now()
        if week.start + timedelta(days=7) <= timezone.localdate(now):
            raise weekly.WeeklyError(
                '선택한 주의 발행 기간이 지났어요. 현재 또는 다음 주 계획을 열어 주세요.'
            )
        calendar = calendar_for(week.start)
        rows = []
        contexts = contexts_for(week.start, calendar, now)
        for context in contexts:
            if not current_claim(week).exists():
                return
            if calendar_for(week.start) != calendar:
                calendar_changed(week)
                return
            output = weekly.propose(context)  # Network I/O runs outside all database transactions.
            rows.extend(candidates_for(context, output, world_now()))
        store_week(week, calendar, contexts, rows)
        return
    except weekly.WeeklyError as exc:
        detail = str(exc)
    except OperationalError:
        raise  # A durable lease bounds recovery when the database is unavailable.
    except Exception:
        logging.getLogger(__name__).exception('Weekly generation failed for week %s', week_id)
        detail = '후보를 저장하지 못했어요. 워커 기록을 확인한 뒤 다시 시도해 주세요.'
    current_claim(week).update(
        generation='failed', generation_error=detail, generation_token=None, lease_until=None
    )


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


def published_posts(character_id, now):
    return FeedPost.objects.filter(character_id=character_id, state=State.PUBLISHED, published_at__lte=now)
