"""Birthday greetings share an account lock with other outbound messages.

Reserve before inference, then recheck the room, profile and calendar before
delivery. The annual ledger follows the character even if a room is replaced.
"""
from datetime import timedelta
from uuid import uuid4

from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q, Subquery
from django.db.models.functions import Greatest

from accounts.birthdays import announces_birthday, celebrates, directed_birthday_wish, observed_days
from accounts.models import User
from llm.proactive import birthday_greeting
from world.models import State
from .live import publish_room_events
from .models import BirthdayGreeting, Friendship, Message, ProactiveDelivery, ReplyPlan
from .reply_timing import CONVERSATION_WINDOW
from .serialization import message_json


def blocked(user_id, now):
    return BirthdayGreeting.objects.filter(Q(sent_at__gt=now - timedelta(hours=1)) |
                                          Q(lease_until__gt=now), user_id=user_id)


def eligible(day, now, *, own_claim=None):
    from . import proactive
    birthdays = Q()
    for month, date in observed_days(day):
        birthdays |= Q(user__birthday_month=month, user__birthday_day=date)
    annual = BirthdayGreeting.objects.filter(user_id=OuterRef('user_id'), character_id=OuterRef('character_id'))
    slot = proactive._slots(now).filter(character_id=OuterRef('character_id'))
    recent = blocked(OuterRef('user_id'), now)
    if own_claim is not None:
        recent = recent.exclude(pk=own_claim)
    pending = ReplyPlan.objects.filter(room_id=OuterRef('pk'), status__in=proactive.ACTIVE)
    talking = Message.objects.filter(room_id=OuterRef('pk'), created_at__gt=now - CONVERSATION_WINDOW)
    ordinary = ProactiveDelivery.objects.filter(room_id=OuterRef('pk')).order_by('-sent_at')
    last_birthday = annual.filter(sent_at__isnull=False).order_by('-sent_at')
    return (Friendship.objects.filter(
        birthdays, ~Exists(recent), ~Exists(annual.filter(year=day.year, sent_at__isnull=False)),
        ~Exists(pending), ~Exists(talking), user__birthday_shared=True, proactive_enabled=True,
    ).alias(slot_state=Subquery(slot.values('state')[:1]), last_contact=Greatest(
        Subquery(ordinary.values('sent_at')[:1]), Subquery(last_birthday.values('sent_at')[:1]),
    )).filter(slot_state=State.FREE).order_by(F('last_contact').asc(nulls_first=True), 'pk'))


def defer_ordinary(user_id, day, now):
    """Caller holds the user lock. A due birthday or a greeting in the last hour takes priority."""
    return blocked(user_id, now).exists() or eligible(day, now).filter(user_id=user_id).exists()


@transaction.atomic
def _claim(room_id, user_id, day):
    from . import proactive
    user = User.objects.select_for_update(no_key=True, skip_locked=True).filter(pk=user_id).first()
    if not user:
        return None
    room = Friendship.objects.select_for_update(skip_locked=True).filter(pk=room_id).first()
    now = proactive.world_now()
    if not room or proactive._day_in_window(now) != day or not eligible(day, now).filter(pk=room_id).exists():
        return None
    claim, _ = BirthdayGreeting.objects.get_or_create(user=user, character_id=room.character_id, year=day.year)
    claim.token, claim.lease_until = uuid4(), now + proactive.LEASE
    claim.save(update_fields=['token', 'lease_until'])
    return claim, user.preference_revision, room.proactive_revision, proactive._inbound_through(room)


@transaction.atomic
def _finish(room_id, day, snapshot, text):
    from . import proactive
    claim, profile_revision, room_revision, inbound = snapshot
    room = Friendship.objects.select_for_update().filter(pk=room_id).first()
    user = User.objects.select_for_update(no_key=True).filter(pk=claim.user_id).first()
    live = BirthdayGreeting.objects.filter(pk=claim.pk, token=claim.token, sent_at__isnull=True)
    now = proactive.world_now()
    if not user or not live.exists():
        return None
    if (not room or claim.lease_until <= now or proactive._day_in_window(now) != day
            or user.preference_revision != profile_revision or room.proactive_revision != room_revision
            or proactive._inbound_through(room) != inbound or not eligible(day, now, own_claim=claim.pk).filter(pk=room_id).exists()):
        live.update(token=None, lease_until=None)
        return None
    message = Message.objects.create(room=room, sender=Message.Sender.CHARACTER, text=text, created_at=now)
    live.update(token=None, lease_until=None, sent_at=now)
    event = {'type': 'message.new', 'message': message_json(message, now=now)}
    transaction.on_commit(lambda: publish_room_events(room_id, [event]), robust=True)
    return message


def prepare(day, now):
    for room in eligible(day, now).select_related('character__persona')[:32]:
        snapshot = _claim(room.pk, room.user_id, day)
        if snapshot:
            persona = getattr(room.character, 'persona', None)
            return _finish(room.pk, day, snapshot, birthday_greeting(room.character.name, persona.speech_rules if persona else ''))
    return None


def delivered_in_chat(plan, text, now):
    from .proactive import KST
    if not celebrates(text):
        return
    local_day = now.astimezone(KST).date()
    user = User.objects.select_for_update(no_key=True).get(pk=plan.room.user_id)
    shared_today = user.birthday_shared and (user.birthday_month, user.birthday_day) in observed_days(local_day)
    trigger = plan.trigger_message
    declared_today = announces_birthday(trigger.text) and trigger.created_at.astimezone(KST).date() == local_day
    if user.birthday_shared and not shared_today:
        return
    if not declared_today and not (shared_today and directed_birthday_wish(trigger.text, text)):
        return
    row, _ = BirthdayGreeting.objects.get_or_create(user=user, character_id=plan.room.character_id, year=local_day.year)
    if row.sent_at is None:
        row.sent_at, row.token, row.lease_until = now, None, None
        row.save(update_fields=['sent_at', 'token', 'lease_until'])
