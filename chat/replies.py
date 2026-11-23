"""Create and dispatch persistent reply plans for the first chat scenario."""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from chat.live import publish_room_events
from chat.models import Friendship, Message, ReplyPlan
from chat.reply_timing import ReplyTiming, plan_reply
from chat.serialization import iso, message_json
from world.clock import world_now


ACTIVE = (ReplyPlan.Status.PLANNED, ReplyPlan.Status.READY)


@transaction.atomic
def accept_message(room_id, text):
    # Both message acceptance and dispatch lock the room before touching its plan.
    room = Friendship.objects.select_for_update().get(pk=room_id)
    now = world_now()
    message = Message.objects.create(room=room, sender=Message.Sender.USER, text=text, created_at=now)
    plan = room.reply_plans.filter(status__in=ACTIVE).first()
    if plan:
        plan.trigger_message = message
        plan.save(update_fields=['trigger_message', 'updated_at'])
    else:
        timing = plan_reply(now, room.character.routines.all())
        demo_seconds = settings.WASSUP_REPLY_DEMO_SECONDS
        if settings.DEBUG and demo_seconds > 0:
            duration = timedelta(seconds=demo_seconds)
            read_at = now + duration / 4
            timing = ReplyTiming(read_at, read_at, now + duration)
        ReplyPlan.objects.create(
            room=room, trigger_message=message,
            read_at=timing.read_at, typing_at=timing.typing_at, reply_at=timing.reply_at,
        )
    return message


def dispatch_due(now=None, limit=100):
    now = world_now() if now is None else now
    due = ReplyPlan.objects.filter(
        Q(status=ReplyPlan.Status.PLANNED, read_at__lte=now)
        | Q(status=ReplyPlan.Status.READY, reply_at__lte=now)
    ).order_by('reply_at').values_list('id', 'room_id')[:limit]
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
        id__lte=plan.trigger_message_id, sender=Message.Sender.USER, read_at__isnull=True,
    )
    if plan.reply_at <= now or plan.status == ReplyPlan.Status.PLANNED:
        ids = list(unread.values_list('id', flat=True))
        unread.update(read_at=now)
        events.extend({'type': 'message.read', 'message_id': pk, 'read_at': iso(now)} for pk in ids)

    if plan.reply_at <= now:
        # The plan and its output commit together. A stale worker sees SENT under the same lock.
        text = '지금 잠깐 쉬는 중 ㅋㅋ'
        reply = Message.objects.create(room=room, sender=Message.Sender.CHARACTER, text=text, created_at=now)
        plan.result = [text]
        plan.status = ReplyPlan.Status.SENT
        plan.save(update_fields=['result', 'status', 'updated_at'])
        events.extend([{'type': 'typing.stop'}, {'type': 'message.new', 'message': message_json(reply)}])
    elif plan.status == ReplyPlan.Status.PLANNED:
        plan.status = ReplyPlan.Status.READY
        plan.save(update_fields=['status', 'updated_at'])
        events.append({'type': 'typing.start', 'expires_at': iso(plan.reply_at)})

    if events:
        transaction.on_commit(lambda: publish_room_events(room_id, events))
