"""Create and dispatch persistent reply plans for the first chat scenario."""

import logging
from datetime import timedelta
from http.client import HTTPException
from time import monotonic
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q

from accounts.birthdays import asks_birthday
from chat.live import publish_room_events
from chat.models import Friendship, Message, ReplyBubble, ReplyPlan
from chat.reply_timing import CONVERSATION_WINDOW, PHONE_OPEN_WINDOW, ReplyTiming, followup_timing, plan_reply
from chat.serialization import iso, message_json, readable_photo
from config.llm import model_for
from llm.chat_photos import POST_LINK_KEY, post_link_is_current
from llm import personal_posts
from chat.birthdays import delivered_in_chat
from llm.dialogue import context_for, exam_fallback, fake_reply, openai_reply, sample_reply
from memory.services import enqueue, history_floor, memory_revision, remember_new_result, result_precision_changed
from world.clock import world_now
from world.models import PostPhoto
from world.publication import published_posts


BEFORE_FIRST = (ReplyPlan.Status.PLANNED, ReplyPlan.Status.READ, ReplyPlan.Status.READY)
ACTIVE = (*BEFORE_FIRST, ReplyPlan.Status.SENDING)
GENERATION_LEAD = timedelta(minutes=2)
# Leave time for a superseded request to finish and one new generation to run.
PREFERENCE_RETRY_GRACE = timedelta(seconds=30)


def lock_preference_revision(user_id):
    # Dispatch and generation commit hold the room first. Preference edits only
    # lock the account, and proactive claims skip rooms that are already locked.
    return (get_user_model().objects.select_for_update(no_key=True)
            .values_list('preference_revision', flat=True).get(pk=user_id))


def cancel_remaining(plan):
    plan.bubbles.filter(status__in=[ReplyBubble.Status.QUEUED, ReplyBubble.Status.TYPING]).update(
        status=ReplyBubble.Status.CANCELLED,
    )
    plan.status = ReplyPlan.Status.DISCARDED
    plan.save(update_fields=['status', 'updated_at'])


def accept_message(room_id, text, source_post_id=None, *, photo=None, client_message_id=None):
    message = Message(room_id=room_id, text=text, sender=Message.Sender.USER, user_photo=photo,
                      kind=Message.Kind.PHOTO if photo else Message.Kind.TEXT, client_message_id=client_message_id)
    try:
        return _accept_message(message, source_post_id)
    except Exception:
        # File storage isn't rolled back along with the database.
        if message.user_photo and message.user_photo._committed:
            if not Message.objects.filter(user_photo=message.user_photo.name).exists():
                message.user_photo.delete(save=False)
        raise


@transaction.atomic
def _accept_message(message, source_post_id):
    # Both message acceptance and dispatch lock the room before touching its plan.
    room_id = message.room_id
    room = Friendship.objects.select_for_update().get(pk=room_id)
    if message.client_message_id:
        previous = room.messages.filter(client_message_id=message.client_message_id).first()
        if previous:
            return previous
    now = world_now()
    post = (published_posts(room.character_id, now).select_for_update(no_key=True).get(pk=source_post_id)
            if source_post_id else None)
    message.room, message.created_at = room, now
    message.source_post, message.source_version = post, post.version if post else None
    message.save(force_insert=True)
    remember_new_result(message)
    enqueue(message)
    plan = room.reply_plans.select_for_update().filter(status__in=ACTIVE).first()
    if plan and plan.status == ReplyPlan.Status.SENDING:
        cancel_remaining(plan)
        transaction.on_commit(lambda: publish_room_events(room_id, [{'type': 'typing.stop'}]))
        plan = None
    if plan:
        plan.trigger_message = message
        plan.result, plan.model_name, plan.context_versions = [], '', {}
        plan.attachment = None
        plan.used_memory_ids = []
        plan.generation_token = plan.generation_until = plan.generation_ms = None
        plan.save(
            update_fields=[
                'trigger_message',
                'result',
                'attachment',
                'model_name',
                'context_versions',
                'used_memory_ids',
                'generation_token',
                'generation_until',
                'generation_ms',
                'updated_at',
            ]
        )
    else:
        last_sent = (room.messages.filter(sender=Message.Sender.CHARACTER,
                                          created_at__range=(now - CONVERSATION_WINDOW, now))
                     .order_by('-created_at').values_list('created_at', flat=True).first())
        phone_open = last_sent is not None and now - last_sent <= PHONE_OPEN_WINDOW
        timing = plan_reply(now, room.character.routines.all(),
                            persona=getattr(room.character, 'persona', None),
                            continuing=last_sent is not None, phone_open=phone_open)
        demo_seconds = settings.WASSUP_REPLY_DEMO_SECONDS
        if settings.DEBUG and demo_seconds > 0:
            duration = timedelta(seconds=demo_seconds)
            timing = ReplyTiming(now + duration / 2, now + 3 * duration / 4, now + duration)
        ReplyPlan.objects.create(
            room=room,
            trigger_message=message,
            read_at=timing.read_at,
            typing_at=timing.typing_at,
            reply_at=timing.reply_at,
            preference_revision=room.user.preference_revision,
        )
    return message


def dispatch_due(now=None, limit=100):
    cutoff = world_now() if now is None else now
    due_bubble = ReplyBubble.objects.filter(plan_id=OuterRef('pk')).filter(
        Q(status=ReplyBubble.Status.QUEUED, typing_at__lte=cutoff)
        | Q(status=ReplyBubble.Status.TYPING, deliver_at__lte=cutoff)
    )
    stale_preference = Q(preference_revision__lt=F('room__user__preference_revision'))
    waiting_for_retry = (
        Q(status=ReplyPlan.Status.READ, reply_at__lte=cutoff, preference_retry_until__gt=cutoff)
        & (Q(result=[]) | stale_preference)
    )
    due = (
        ReplyPlan.objects.filter(
            Q(status=ReplyPlan.Status.PLANNED, read_at__lte=cutoff)
            | (Q(status=ReplyPlan.Status.READ, typing_at__lte=cutoff) & (Q(reply_at__lte=cutoff) | ~stale_preference))
            | (Q(status=ReplyPlan.Status.READY) & (Q(reply_at__lte=cutoff) | stale_preference))
            | (Q(status=ReplyPlan.Status.SENDING) & (Exists(due_bubble) | stale_preference))
        )
        .exclude(waiting_for_retry)
        .order_by('reply_at')
        .values_list('id', 'room_id')[:limit]
    )
    for plan_id, room_id in list(due):
        _dispatch_plan(plan_id, room_id, now)


@transaction.atomic
def _dispatch_plan(plan_id, room_id, now):
    room = Friendship.objects.select_for_update().filter(pk=room_id).first()
    if room is None:
        return

    plan = room.reply_plans.select_for_update().filter(pk=plan_id, status__in=ACTIVE).first()
    now = world_now() if now is None else now
    if not plan or plan.read_at > now:
        return

    events = []
    if plan.status == ReplyPlan.Status.SENDING:
        dispatch_followup(plan, now, events)
    else:
        dispatch_first(plan, now, events)
    if events:
        transaction.on_commit(lambda: publish_room_events(room_id, events))


def lock_current_posts(plan, versions, now):
    if not versions:
        return {}
    # Keep a concurrent edit or withdrawal on the same side of commit as our decision.
    if personal_posts.KEY in versions and not (
        personal_posts.quoted_posts(plan.room).filter(pk=versions[personal_posts.KEY])
        .select_for_update(no_key=True, of=('self',)).exists()
    ):
        return None
    editions = {key: value for key, value in versions.items() if key not in (POST_LINK_KEY, personal_posts.KEY)}
    rows = (published_posts(plan.room.character_id, now).filter(pk__in=editions).select_related('photo')
            .order_by('pk').select_for_update(no_key=True, of=('self',)))
    posts = {str(post.pk): post for post in rows}
    if {key: post.version for key, post in posts.items()} != editions:
        return None
    if POST_LINK_KEY in versions and not post_link_is_current(versions, posts, now):
        return None
    return posts


def reply_context_versions(context):
    versions = {str(post['id']): post['version'] for post in context['shared_events']}
    if personal_posts.KEY in context:
        versions[personal_posts.KEY] = context[personal_posts.KEY]['id']
    continuity = context.get('post_continuity')
    if continuity:
        previous = continuity['previous']
        versions[str(previous['id'])] = previous['version']
        versions[POST_LINK_KEY] = [continuity['post_id'], previous['id']]
    return versions


def validate_reply_sources(plan, now):
    selection = plan.attachment
    versions = plan.context_versions.copy()
    if selection is not None:
        key = str(selection['post_id'])
        version = selection['version']
        if versions.setdefault(key, version) != version:
            return False, None
    posts = lock_current_posts(plan, versions, now)
    if posts is None or selection is None:
        return posts is not None, None
    post = posts[key]
    # Lock the asset after the post, matching the publication edit path.
    asset = PostPhoto.objects.select_for_update(no_key=True).filter(
        pk=post.photo_id, character_id=post.character_id,
    ).first()
    if not readable_photo(asset):
        return False, None
    post.photo = asset
    return True, post


def sample_at_deadline(plan, now):
    context = context_for(plan, now)
    versions = reply_context_versions(context)
    if plan.result or lock_current_posts(plan, versions, now) is None:
        # A prepared sequence lost its source, or this sample's snapshot raced an edit.
        context.update(shared_events=[], quoted_post_id=None, photo_reference_id=None)
        context.pop('post_continuity', None)
        context.pop(personal_posts.KEY, None)
        versions = {}
    plan.context_versions = versions
    plan.memory_revision = context['memory_revision']
    plan.preference_revision = context['preference_revision']
    exam = exam_fallback(context)
    plan.used_memory_ids = exam['used_memory_ids'] if exam else []
    plan.result = exam['bubbles'] if exam else sample_reply(context)
    plan.attachment = None
    plan.model_name = 'sample' if settings.LLM_BACKEND == 'fake' else 'fallback'


def deliver_bubble(plan, bubble, now, events, post):
    photo = post if bubble.attachment_post_id else None
    reply = Message.objects.create(room_id=plan.room_id, sender=Message.Sender.CHARACTER,
                                   text=bubble.text, created_at=now,
                                   birthday_profile_answer=asks_birthday(plan.trigger_message.text),
                                   kind=Message.Kind.PHOTO if photo else Message.Kind.TEXT,
                                   attachment_post=photo, attachment_version=bubble.attachment_version)
    delivered_in_chat(plan, reply.text, now)
    bubble.message, bubble.status = reply, ReplyBubble.Status.SENT
    bubble.save(update_fields=['message', 'status'])
    events.extend([{'type': 'typing.stop'}, {'type': 'message.new', 'message': message_json(reply, now=now)}])
    following = plan.bubbles.filter(position=bubble.position + 1).first()
    if following:
        persona = getattr(plan.room.character, 'persona', None)
        following.typing_at, following.deliver_at = followup_timing(now, following.text, persona)
        following.save(update_fields=['typing_at', 'deliver_at'])
        plan.status = ReplyPlan.Status.SENDING
    else:
        plan.status = ReplyPlan.Status.SENT
    plan.save(update_fields=['status', 'updated_at'])


def dispatch_followup(plan, now, events):
    preference_revision = lock_preference_revision(plan.room.user_id)
    # An edit, deletion or opt-out fences at least this trigger. A newer
    # extraction only adds facts and can safely leave an underway reply intact.
    if (plan.preference_revision != preference_revision
            or result_precision_changed(plan.room_id, plan.reply_at, now)
            or (plan.memory_revision != memory_revision(plan.room_id)
                and history_floor(plan.room_id) >= plan.trigger_message_id)):
        cancel_remaining(plan)
        events.append({'type': 'typing.stop'})
        return
    bubble = plan.bubbles.filter(status__in=[ReplyBubble.Status.QUEUED, ReplyBubble.Status.TYPING]).first()
    current, post = validate_reply_sources(plan, now)
    if not current:
        cancel_remaining(plan)
        events.append({'type': 'typing.stop'})
    elif bubble.status == ReplyBubble.Status.QUEUED and now >= bubble.typing_at:
        # If the worker was asleep during the pause, start a full typing interval now.
        bubble.deliver_at = now + (bubble.deliver_at - bubble.typing_at)
        bubble.typing_at, bubble.status = now, ReplyBubble.Status.TYPING
        bubble.save(update_fields=['typing_at', 'deliver_at', 'status'])
        events.append({'type': 'typing.start', 'expires_at': iso(bubble.deliver_at, timespec='milliseconds')})
    elif bubble.status == ReplyBubble.Status.TYPING and now >= bubble.deliver_at:
        deliver_bubble(plan, bubble, now, events, post)


def dispatch_first(plan, now, events):
    unread = plan.room.messages.filter(
        id__lte=plan.trigger_message_id,
        sender=Message.Sender.USER,
        read_at__isnull=True,
    )
    ids = list(unread.values_list('id', flat=True))
    if ids:
        unread.update(read_at=now)
        events.extend({'type': 'message.read', 'message_id': pk, 'read_at': iso(now)} for pk in ids)

    if plan.reply_at <= now:
        preference_current = plan.preference_revision == lock_preference_revision(plan.room.user_id)
        if not preference_current:
            plan.preference_retry_until = plan.reply_at + PREFERENCE_RETRY_GRACE
        if (plan.preference_retry_until and now < plan.preference_retry_until
                and (not preference_current or not plan.result)):
            # Keep the original delivery as the anchor even if preferences
            # change again. Stop the expired first typing phase while we retry.
            if plan.status == ReplyPlan.Status.READY:
                events.append({'type': 'typing.stop'})
            plan.status = ReplyPlan.Status.READ
            plan.save(update_fields=['status', 'preference_retry_until', 'updated_at'])
            return
        current, post = (
            validate_reply_sources(plan, now)
            if (plan.result and preference_current and plan.memory_revision == memory_revision(plan.room_id)
                and not result_precision_changed(plan.room_id, plan.reply_at, now))
            else (False, None)
        )
        if not current:
            sample_at_deadline(plan, now)
        plan.generation_token = plan.generation_until = None
        plan.preference_retry_until = None
        plan.save(update_fields=['result', 'attachment', 'context_versions', 'model_name', 'memory_revision',
                                 'preference_revision', 'preference_retry_until', 'used_memory_ids',
                                 'generation_token', 'generation_until'])
        bubbles = [ReplyBubble(plan=plan, position=i, text=text) for i, text in enumerate(plan.result)]
        if post:
            selected = bubbles[plan.attachment['bubble_index']]
            selected.attachment_post, selected.attachment_version = post, post.version
        bubbles[0].typing_at, bubbles[0].deliver_at = plan.typing_at, plan.reply_at
        ReplyBubble.objects.bulk_create(bubbles)
        deliver_bubble(plan, bubbles[0], now, events, post)
    elif plan.typing_at <= now:
        preference_current = plan.preference_revision == lock_preference_revision(plan.room.user_id)
        status = ReplyPlan.Status.READY if preference_current else ReplyPlan.Status.READ
        if plan.status != status:
            if preference_current:
                events.append({'type': 'typing.start', 'expires_at': iso(plan.reply_at, timespec='milliseconds')})
            elif plan.status == ReplyPlan.Status.READY:
                events.append({'type': 'typing.stop'})
            plan.status = status
            plan.save(update_fields=['status', 'updated_at'])
    elif plan.status == ReplyPlan.Status.PLANNED:
        plan.status = ReplyPlan.Status.READ
        plan.save(update_fields=['status', 'updated_at'])


def selected_attachment(result, context):
    selection = result.get('attachment')
    if selection is None:
        return None
    if not isinstance(selection, dict) or set(selection) != {'post_id', 'bubble_index'}:
        raise ValueError('Invalid photo selection')
    post_id, index = selection['post_id'], selection['bubble_index']
    if type(post_id) is not int or type(index) is not int or not 0 <= index < len(result['bubbles']):
        raise ValueError('Invalid photo selection')
    for candidate in context.get('photo_candidates', []):
        if candidate['id'] == post_id:
            return {**selection, 'version': candidate['version']}
    raise ValueError('Photo selection is not in the reply context')


def prepare_next_reply():
    """Return whether a generation was claimed, independently of its delivery deadline."""
    now = world_now()
    with transaction.atomic():
        # A revision can supersede a prepared result or an outstanding lease.
        stale_preference = Q(preference_revision__lt=F('room__user__preference_revision'))
        needs_reply = (
            Q(result=[]) & (Q(generation_until__isnull=True) | Q(generation_until__lte=now))
            | Q(room__memory_state__revision__gt=F('memory_revision'))
            | stale_preference
        )
        in_time = (Q(reply_at__gt=now) | Q(preference_retry_until__gt=now)
                   | (stale_preference & Q(reply_at__gt=now - PREFERENCE_RETRY_GRACE)))
        pending = ReplyPlan.objects.filter(needs_reply, in_time, status__in=BEFORE_FIRST,
                                           reply_at__lte=now + GENERATION_LEAD)
        candidate = (pending.select_related('room').select_for_update(skip_locked=True, of=('room',))
                     .order_by('reply_at', 'id').first())
        if candidate is None:
            return False
        # Re-read plan eligibility after obtaining the common room lock.
        plan = pending.filter(pk=candidate.pk).first()
        if plan is None:
            return False
        photo_fields = []
        context = context_for(plan, now, photo_fields=photo_fields)
        if plan.preference_revision != context['preference_revision']:
            plan.preference_retry_until = plan.reply_at + PREFERENCE_RETRY_GRACE
        plan.preference_revision = context['preference_revision']
        token = uuid4()
        plan.generation_token, plan.generation_until = token, now + timedelta(seconds=30)
        plan.attempts += 1
        plan.result, plan.used_memory_ids, plan.context_versions = [], [], {}
        plan.attachment = None
        plan.memory_revision = context['memory_revision']
        plan.generation_ms, plan.model_name = None, ''
        plan.save(update_fields=['generation_token', 'generation_until', 'attempts', 'result', 'attachment',
                                 'used_memory_ids', 'context_versions', 'memory_revision', 'preference_revision',
                                 'preference_retry_until', 'generation_ms', 'model_name'])
    started = monotonic()
    attachment = None
    try:
        if not context['recent_turns']:
            result, model_name = {'bubbles': sample_reply(context), 'used_memory_ids': []}, 'fallback'
        elif asks_birthday(context['recent_turns'][-1]['text']):
            result, model_name = {'bubbles': sample_reply(context), 'used_memory_ids': []}, 'profile'
        elif settings.LLM_BACKEND == 'fake':
            result, model_name = fake_reply(context), 'sample'
        else:
            result = openai_reply(context, photo_fields=photo_fields) if photo_fields else openai_reply(context)
            model_name = model_for('chat')[: ReplyPlan._meta.get_field('model_name').max_length]
        attachment = selected_attachment(result, context)
    except (HTTPException, OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        logging.getLogger(__name__).warning(
            'Dialogue generation failed for reply plan %s (%s, HTTP %s)',
            plan.pk,
            type(exc).__name__,
            getattr(exc, 'code', None),
        )
        result = exam_fallback(context) or {'bubbles': sample_reply(context), 'used_memory_ids': []}
        model_name = 'fallback'
    with transaction.atomic():
        room = Friendship.objects.select_for_update().filter(pk=plan.room_id).first()
        if room is None:
            return True
        claimed = ReplyPlan.objects.filter(pk=plan.pk, status__in=BEFORE_FIRST, generation_token=token,
                                           trigger_message_id=plan.trigger_message_id)
        if not claimed.exists():
            return True
        preference_revision = lock_preference_revision(room.user_id)
        deadline = plan.preference_retry_until or plan.reply_at
        if (preference_revision != plan.preference_revision or memory_revision(plan.room_id) != plan.memory_revision
                or world_now() >= min(deadline, plan.generation_until)):
            # Nothing from the old snapshot reaches durable storage. A fresh
            # generation may claim immediately while its delivery window is open.
            claimed.update(generation_token=None, generation_until=None)
            return True
        claimed.update(
            result=result['bubbles'],
            attachment=attachment,
            used_memory_ids=result['used_memory_ids'],
            generation_ms=round((monotonic() - started) * 1000),
            model_name=model_name,
            generation_token=None,
            generation_until=None,
            context_versions=reply_context_versions(context),
        )
    return True
