"""Create and dispatch persistent reply plans for the first chat scenario."""

import logging
from http.client import HTTPException
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from chat.live import publish_room_events
from chat.models import Friendship, Message, ReplyPlan
from chat.reply_timing import ReplyTiming, plan_reply
from chat.serialization import iso, message_json
from world.clock import world_now
from world.publication import published_posts
from llm.dialogue import context_for, openai_reply, sample_reply


ACTIVE = (ReplyPlan.Status.PLANNED, ReplyPlan.Status.READY)


@transaction.atomic
def accept_message(room_id, text, source_post_id=None):
    # Both message acceptance and dispatch lock the room before touching its plan.
    room = Friendship.objects.select_for_update().get(pk=room_id)
    now = world_now()
    post = published_posts(room.character_id, now).get(pk=source_post_id) if source_post_id else None
    message = Message.objects.create(
        room=room,
        sender=Message.Sender.USER,
        text=text,
        created_at=now,
        source_post=post,
        source_version=post.version if post else None,
    )
    plan = room.reply_plans.filter(status__in=ACTIVE).first()
    if plan:
        plan.trigger_message = message
        plan.result, plan.model_name, plan.context_versions = [], '', {}
        plan.generation_token = plan.generation_until = None
        plan.save(
            update_fields=[
                'trigger_message',
                'result',
                'model_name',
                'context_versions',
                'generation_token',
                'generation_until',
                'updated_at',
            ]
        )
    else:
        timing = plan_reply(now, room.character.routines.all())
        demo_seconds = settings.WASSUP_REPLY_DEMO_SECONDS
        if settings.DEBUG and demo_seconds > 0:
            duration = timedelta(seconds=demo_seconds)
            read_at = now + duration / 4
            timing = ReplyTiming(read_at, read_at, now + duration)
        ReplyPlan.objects.create(
            room=room,
            trigger_message=message,
            read_at=timing.read_at,
            typing_at=timing.typing_at,
            reply_at=timing.reply_at,
        )
    return message


def dispatch_due(now=None, limit=100):
    now = world_now() if now is None else now
    due = (
        ReplyPlan.objects.filter(
            Q(status=ReplyPlan.Status.PLANNED, read_at__lte=now)
            | Q(status=ReplyPlan.Status.READY, reply_at__lte=now)
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

    plan = room.reply_plans.filter(pk=plan_id, status__in=ACTIVE).first()
    if not plan or plan.read_at > now:
        return

    events = []
    unread = room.messages.filter(
        id__lte=plan.trigger_message_id,
        sender=Message.Sender.USER,
        read_at__isnull=True,
    )
    if plan.reply_at <= now or plan.status == ReplyPlan.Status.PLANNED:
        ids = list(unread.values_list('id', flat=True))
        unread.update(read_at=now)
        events.extend({'type': 'message.read', 'message_id': pk, 'read_at': iso(now)} for pk in ids)

    if plan.reply_at <= now:
        # The plan and its output commit together. A stale worker sees SENT under the same lock.
        versions = plan.context_versions
        current = {
            str(pk): version
            for pk, version in published_posts(room.character_id, now)
            .filter(pk__in=versions)
            .values_list('pk', 'version')
        }
        bubbles = plan.result
        if not bubbles or current != versions:
            bubbles = sample_reply(context_for(plan, now))
            plan.model_name = 'sample' if settings.LLM_BACKEND == 'fake' else 'fallback'
        events.append({'type': 'typing.stop'})
        for text in bubbles:
            reply = Message.objects.create(
                room=room, sender=Message.Sender.CHARACTER, text=text, created_at=now
            )
            events.append({'type': 'message.new', 'message': message_json(reply)})
        plan.result, plan.status = bubbles, ReplyPlan.Status.SENT
        plan.save(update_fields=['result', 'status', 'model_name', 'updated_at'])
    elif plan.status == ReplyPlan.Status.PLANNED:
        plan.status = ReplyPlan.Status.READY
        plan.save(update_fields=['status', 'updated_at'])
        events.append({'type': 'typing.start', 'expires_at': iso(plan.reply_at)})

    if events:
        transaction.on_commit(lambda: publish_room_events(room_id, events))


def prepare_next_reply():
    """Claim one generation independently of the dispatch loop and its deadline."""
    now = world_now()
    with transaction.atomic():
        plan = (
            ReplyPlan.objects.select_for_update(skip_locked=True, of=('self',))
            .select_related('room__character')
            .filter(status__in=ACTIVE, reply_at__gt=now, result=[])
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
        pk=plan.pk, status__in=ACTIVE, generation_token=token, trigger_message_id=plan.trigger_message_id
    ).update(
        result=result,
        model_name=model_name,
        generation_token=None,
        generation_until=None,
        context_versions={str(p['id']): p['version'] for p in context['shared_events']},
    )
