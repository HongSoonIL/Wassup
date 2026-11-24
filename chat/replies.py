"""Create and dispatch persistent reply plans for the first chat scenario."""

import logging
from http.client import HTTPException
from datetime import timedelta
from time import monotonic
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, Q

from chat.live import publish_room_events
from chat.models import Friendship, Message, ReplyBubble, ReplyPlan
from chat.reply_timing import CONVERSATION_WINDOW, PHONE_OPEN_WINDOW, ReplyTiming, followup_timing, plan_reply
from chat.serialization import iso, message_json
from world.clock import world_now
from world.publication import published_posts
from llm.dialogue import context_for, openai_reply, sample_reply


BEFORE_FIRST = (ReplyPlan.Status.PLANNED, ReplyPlan.Status.READ, ReplyPlan.Status.READY)
ACTIVE = (*BEFORE_FIRST, ReplyPlan.Status.SENDING)
GENERATION_LEAD = timedelta(minutes=2)


def cancel_remaining(plan):
    plan.bubbles.filter(status__in=[ReplyBubble.Status.QUEUED, ReplyBubble.Status.TYPING]).update(
        status=ReplyBubble.Status.CANCELLED,
    )
    plan.status = ReplyPlan.Status.DISCARDED
    plan.save(update_fields=['status', 'updated_at'])


@transaction.atomic
def accept_message(room_id, text, source_post_id=None):
    # Both message acceptance and dispatch lock the room before touching its plan.
    room = Friendship.objects.select_for_update().get(pk=room_id)
    now = world_now()
    post = (published_posts(room.character_id, now).select_for_update(no_key=True).get(pk=source_post_id)
            if source_post_id else None)
    message = Message.objects.create(
        room=room,
        sender=Message.Sender.USER,
        text=text,
        created_at=now,
        source_post=post,
        source_version=post.version if post else None,
    )
    plan = room.reply_plans.select_for_update().filter(status__in=ACTIVE).first()
    phone_open = False
    if plan and plan.status == ReplyPlan.Status.SENDING:
        phone_open = plan.bubbles.filter(status=ReplyBubble.Status.SENT,
                                         message__created_at__range=(now - PHONE_OPEN_WINDOW, now)).exists()
        cancel_remaining(plan)
        transaction.on_commit(lambda: publish_room_events(room_id, [{'type': 'typing.stop'}]))
        plan = None
    if plan:
        plan.trigger_message = message
        plan.result, plan.model_name, plan.context_versions = [], '', {}
        plan.generation_token = plan.generation_until = plan.generation_ms = None
        plan.save(
            update_fields=[
                'trigger_message',
                'result',
                'model_name',
                'context_versions',
                'generation_token',
                'generation_until',
                'generation_ms',
                'updated_at',
            ]
        )
    else:
        continuing = room.messages.filter(sender=Message.Sender.CHARACTER,
                                          created_at__gte=now - CONVERSATION_WINDOW).exists()
        timing = plan_reply(now, room.character.routines.all(),
                            persona=getattr(room.character, 'persona', None), continuing=continuing, phone_open=phone_open)
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
        )
    return message


def dispatch_due(now=None, limit=100):
    cutoff = world_now() if now is None else now
    due_bubble = ReplyBubble.objects.filter(plan_id=OuterRef('pk')).filter(
        Q(status=ReplyBubble.Status.QUEUED, typing_at__lte=cutoff)
        | Q(status=ReplyBubble.Status.TYPING, deliver_at__lte=cutoff)
    )
    due = (
        ReplyPlan.objects.filter(
            Q(status=ReplyPlan.Status.PLANNED, read_at__lte=cutoff)
            | Q(status=ReplyPlan.Status.READ, typing_at__lte=cutoff)
            | Q(status=ReplyPlan.Status.READY, reply_at__lte=cutoff)
            | (Q(status=ReplyPlan.Status.SENDING) & Exists(due_bubble))
        )
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


def versions_current(plan, versions, now):
    if not versions:
        return True
    # Keep a concurrent edit or withdrawal on the same side of commit as our decision.
    rows = (published_posts(plan.room.character_id, now).filter(pk__in=versions)
            .order_by('pk').select_for_update(no_key=True).values_list('pk', 'version'))
    return {str(pk): version for pk, version in rows} == versions


def sample_at_deadline(plan, now):
    context = context_for(plan, now)
    versions = {str(p['id']): p['version'] for p in context['shared_events']}
    if plan.result or not versions_current(plan, versions, now):
        # A prepared sequence lost its source, or this sample's snapshot raced an edit.
        context.update(shared_events=[], quoted_post_id=None)
        versions = {}
    plan.context_versions = versions
    plan.result = sample_reply(context)
    plan.model_name = 'sample' if settings.LLM_BACKEND == 'fake' else 'fallback'


def deliver_bubble(plan, bubble, now, events):
    reply = Message.objects.create(room_id=plan.room_id, sender=Message.Sender.CHARACTER,
                                   text=bubble.text, created_at=now)
    bubble.message, bubble.status = reply, ReplyBubble.Status.SENT
    bubble.save(update_fields=['message', 'status'])
    events.extend([{'type': 'typing.stop'}, {'type': 'message.new', 'message': message_json(reply)}])
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
    bubble = plan.bubbles.filter(status__in=[ReplyBubble.Status.QUEUED, ReplyBubble.Status.TYPING]).first()
    if bubble.status == ReplyBubble.Status.QUEUED and now >= bubble.typing_at:
        # If the worker was asleep during the pause, start a full typing interval now.
        bubble.deliver_at = now + (bubble.deliver_at - bubble.typing_at)
        bubble.typing_at, bubble.status = now, ReplyBubble.Status.TYPING
        bubble.save(update_fields=['typing_at', 'deliver_at', 'status'])
        events.append({'type': 'typing.start', 'expires_at': iso(bubble.deliver_at, timespec='milliseconds')})
    elif bubble.status == ReplyBubble.Status.TYPING and now >= bubble.deliver_at:
        if versions_current(plan, plan.context_versions, now):
            deliver_bubble(plan, bubble, now, events)
        else:
            cancel_remaining(plan)
            events.append({'type': 'typing.stop'})


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
        if not plan.result or not versions_current(plan, plan.context_versions, now):
            sample_at_deadline(plan, now)
        plan.generation_token = plan.generation_until = None
        plan.save(update_fields=['result', 'context_versions', 'model_name', 'generation_token', 'generation_until'])
        bubbles = [ReplyBubble(plan=plan, position=i, text=text) for i, text in enumerate(plan.result)]
        bubbles[0].typing_at, bubbles[0].deliver_at = plan.typing_at, plan.reply_at
        ReplyBubble.objects.bulk_create(bubbles)
        deliver_bubble(plan, bubbles[0], now, events)
    elif plan.typing_at <= now and plan.status != ReplyPlan.Status.READY:
        plan.status = ReplyPlan.Status.READY
        plan.save(update_fields=['status', 'updated_at'])
        events.append({'type': 'typing.start', 'expires_at': iso(plan.reply_at, timespec='milliseconds')})
    elif plan.status == ReplyPlan.Status.PLANNED:
        plan.status = ReplyPlan.Status.READ
        plan.save(update_fields=['status', 'updated_at'])


def prepare_next_reply():
    """Claim one generation independently of the dispatch loop and its deadline."""
    now = world_now()
    with transaction.atomic():
        plan = (
            ReplyPlan.objects.select_for_update(skip_locked=True, of=('self',))
            .select_related('room__character')
            .filter(status__in=BEFORE_FIRST, reply_at__gt=now, reply_at__lte=now + GENERATION_LEAD, result=[])
            .filter(Q(generation_until__isnull=True) | Q(generation_until__lte=now))
            .order_by('reply_at', 'id')
            .first()
        )
        if not plan:
            return
        token = uuid4()
        plan.generation_token, plan.generation_until = token, now + timedelta(seconds=30)
        plan.attempts += 1
        plan.save(update_fields=['generation_token', 'generation_until', 'attempts'])
        context = context_for(plan, now)
    started = monotonic()
    try:
        result = openai_reply(context)
        model_name = settings.LLM_MODEL[: ReplyPlan._meta.get_field('model_name').max_length]
    except (HTTPException, OSError, ValueError, KeyError, TypeError) as exc:
        logging.getLogger(__name__).warning(
            'Dialogue generation failed for reply plan %s (%s, HTTP %s)',
            plan.pk,
            type(exc).__name__,
            getattr(exc, 'code', None),
        )
        result, model_name = sample_reply(context), 'fallback'
    ReplyPlan.objects.filter(
        pk=plan.pk, status__in=BEFORE_FIRST, generation_token=token, trigger_message_id=plan.trigger_message_id
    ).update(
        result=result,
        generation_ms=round((monotonic() - started) * 1000),
        model_name=model_name,
        generation_token=None,
        generation_until=None,
        context_versions={str(p['id']): p['version'] for p in context['shared_events']},
    )
