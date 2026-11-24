"""Private, single-attempt photo jobs. Editorial writes lock the week before the post."""

import logging
from datetime import timedelta
from time import monotonic

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import OperationalError, transaction
from django.utils import timezone

from llm import photo
from . import publication
from .publication_models import FeedPost, PhotoJob, PostPhoto, PublicationWeek


State = PhotoJob.State
LEASE = timedelta(seconds=photo.REQUEST_TIMEOUT + 60)
INITIAL_LIMIT = 6
EXPIRED = '사진 생성 워커의 응답이 끊겼어요. 중복 요청을 막기 위해 중지했어요. 확인 후 다시 생성해 주세요.'
logger = logging.getLogger(__name__)


def scene_for(post):
    facts = [fact[:600] for fact in post.public_facts[:5] if isinstance(fact, str) and fact.strip()]
    # Older drafts without a scene can be explicitly regenerated from their reviewed text.
    alt = post.photo_alt.strip() or (facts[0] if facts else post.caption.strip())[:200]
    return {
        'character': post.character.name,
        'photo_alt': alt,
        'public_facts': facts,
        'caption': post.caption[:1000],
        'event_at': timezone.localtime(post.event_at).isoformat() if post.event_at else None,
    }


def create_job(post, initial=False):
    return PhotoJob.objects.create(
        post=post,
        initial=initial,
        version=post.version,
        previous_photo_id=post.photo_id,
        scene=scene_for(post),
    )


@transaction.atomic
def enqueue_initial(post):
    # Also called inside the atomic week-creation transaction. The lock covers all six slots.
    week = PublicationWeek.objects.select_for_update().get(pk=post.week_id)
    draft = week.posts.select_related('character').get(pk=post.pk)
    if (
        draft.state != FeedPost.State.DRAFT
        or draft.version != 1
        or draft.photo_id
        or draft.photo_jobs.exists()
    ):
        return
    if PhotoJob.objects.filter(initial=True, post__week=week).count() < INITIAL_LIMIT:
        create_job(draft, initial=True)


def matches(job, post):
    return (
        post.state == FeedPost.State.DRAFT
        and post.version == job.version
        and post.photo_id == job.previous_photo_id
        and scene_for(post) == job.scene
    )


def status_for(post):
    job = post.photo_jobs.order_by('-pk').first()
    if job and job.state in (State.QUEUED, State.GENERATING, State.FAILED) and matches(job, post):
        if job.state == State.GENERATING and job.lease_until <= timezone.now():
            return {'state': 'failed', 'detail': EXPIRED}
        return {'state': job.state, 'detail': job.detail}
    return {'state': 'ready' if post.photo_id else 'idle', 'detail': ''}


@transaction.atomic
def regenerate(week_id, post_id, revision, version):
    week = publication.locked_week(week_id, revision)
    post = week.posts.select_related('character').get(pk=post_id)
    if post.version != version:
        raise publication.ReviewError(409, 'stale_version', '현재 초안 버전을 다시 확인해 주세요.')
    if post.state != FeedPost.State.DRAFT:
        raise publication.ReviewError(409, 'not_draft', '초안에서만 후보 사진을 생성할 수 있어요.')
    if not scene_for(post)['photo_alt']:
        raise publication.ReviewError(422, 'missing_scene', '사진에 보일 장면을 먼저 입력해 주세요.')
    if status_for(post)['state'] in (State.QUEUED, State.GENERATING):
        return
    create_job(post)
    publication.advance(week)


def locked_job(job_id, week_id):
    week = PublicationWeek.objects.select_for_update().get(pk=week_id)
    job = (
        PhotoJob.objects.select_related('post__character')
        .select_for_update(of=('self', 'post'))
        .get(pk=job_id)
    )
    return week, job


def current(job):
    return matches(job, job.post) and not job.post.photo_jobs.filter(pk__gt=job.pk).exists()


@transaction.atomic
def claim(job_id, week_id):
    _, job = locked_job(job_id, week_id)
    if job.state != State.QUEUED:
        return None
    if not current(job):
        job.state = State.DISCARDED
        job.save(update_fields=['state'])
        return None
    # Commit this marker before any provider I/O. An ambiguous attempt can ONLY be retried by an operator.
    job.started_at = timezone.now()
    job.lease_until = job.started_at + LEASE
    job.model = 'fake' if settings.LLM_BACKEND == 'fake' else settings.WASSUP_IMAGE_MODEL
    job.state = State.GENERATING
    job.save(update_fields=['started_at', 'lease_until', 'state', 'model'])
    return job


def filename(job):
    return f'photos/generated/{job.file_token.hex}.webp'


def store_photo(job, image):
    key = 'generated-' + job.file_token.hex
    asset = PostPhoto(
        key=key, character_id=job.post.character_id, alt=job.scene['photo_alt'], ai_generated=True
    )
    storage = asset.image.storage
    name = filename(job)
    if storage.exists(name):
        raise OSError('Generated photo filename already exists')
    saved = storage.save(name, ContentFile(image))
    if saved != name:
        storage.delete(saved)
        raise OSError('Private storage renamed an exclusive generated photo')
    asset.image.name = name
    asset.save(force_insert=True)
    return asset


@transaction.atomic
def finish(job_id, week_id, elapsed_ms, usage, *, image=None, detail=''):
    week, job = locked_job(job_id, week_id)
    # Keep billed usage even when an explicit retry or intervening edit won the race.
    job.elapsed_ms, job.usage = elapsed_ms, usage
    if job.state == State.GENERATING:
        if job.lease_until <= timezone.now():
            job.state, job.detail, job.cleanup_pending = State.FAILED, EXPIRED, True
        elif not current(job):
            job.state, job.cleanup_pending = State.DISCARDED, True
        elif detail:
            job.state, job.detail, job.cleanup_pending = State.FAILED, detail[:240], True
        else:
            job.result = store_photo(job, image)
            post = job.post
            post.photo, post.photo_alt = job.result, job.result.alt
            publication.revise(post, FeedPost.State.DRAFT)
            publication.advance(week)
            job.state = State.READY
        job.lease_until = None
    job.save()


def recover_expired():
    PhotoJob.objects.filter(state=State.GENERATING, lease_until__lte=timezone.now()).update(
        state=State.FAILED,
        detail=EXPIRED,
        lease_until=None,
        cleanup_pending=True,
    )


def clean_orphans():
    # The UUID path is durable before storage is touched. A crash between save and commit
    # leaves an expired job, which is cleaned even after a full process restart.
    storage = PostPhoto._meta.get_field('image').storage
    for pk in PhotoJob.objects.filter(cleanup_pending=True).values_list('pk', flat=True).order_by('pk')[:100]:
        with transaction.atomic():
            job = PhotoJob.objects.select_for_update().filter(pk=pk, cleanup_pending=True).first()
            if job is None:
                continue
            name = filename(job)
            try:
                if not PostPhoto.objects.filter(image=name).exists() and storage.exists(name):
                    storage.delete(name)
            except OSError:
                logger.warning('Could not remove private file for photo job %s', pk)
                continue
            job.cleanup_pending = False
            job.save(update_fields=['cleanup_pending'])


def process(job):
    started = monotonic()
    usage = {}
    try:
        result = photo.generate(job.scene, job.model)
        usage = result.usage
        finish(job.pk, job.post.week_id, round((monotonic() - started) * 1000), usage, image=result.image)
        return
    except photo.PhotoError as exc:
        detail, usage = str(exc), exc.usage
        logger.warning('Photo job %s (%s) failed: %s', job.pk, job.model, detail)
    except OperationalError:
        raise  # Recovery will reconcile the committed claim before another operator attempt.
    except Exception:
        logger.exception('Could not complete photo job %s', job.pk)
        detail = '후보 사진을 저장하지 못했어요. 저장소와 워커 기록을 확인한 뒤 다시 생성해 주세요.'
    finish(job.pk, job.post.week_id, round((monotonic() - started) * 1000), usage, detail=detail)


def drain():
    recover_expired()
    clean_orphans()
    queued = PhotoJob.objects.filter(state=State.QUEUED).order_by('pk').values_list('pk', 'post__week_id')
    for pk, week_id in list(queued[:INITIAL_LIMIT]):
        job = claim(pk, week_id)
        if job:
            process(job)
    clean_orphans()
