"""Lease yesterday's follow-ups and recheck them before creating a message."""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q, Subquery
from django.utils import timezone

from chat.live import publish_room_events
from chat.models import Friendship, Message, ProactiveClaim, ProactiveDelivery, ReplyPlan
from chat.replies import ACTIVE
from chat.serialization import message_json
from llm import proactive as provider
from memory.models import Memory, RoomMemoryState
from memory.services import room_memories
from world.clock import active_routines, world_now
from world.models import State


logger = logging.getLogger(__name__)
KST = ZoneInfo('Asia/Seoul')
CANDIDATE_LIMIT = 32
HISTORY_LIMIT = 24
HISTORY_CHAR_LIMIT = 12_000
LEASE = timedelta(seconds=60)
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Followup:
    claim_id: int
    room_id: int
    token: UUID
    day: date
    consent_revision: int
    memory_revision: int
    slot_id: int
    context: dict


def _day_in_window(now):
    local = timezone.localtime(now, KST)
    return local.date() if 10 <= local.hour < 21 else None


def _slots(now):
    with timezone.override(KST):
        return active_routines(now).order_by('-start_time', 'pk')


def _active_claims(user_id, day, now):
    return ProactiveClaim.objects.filter(
        schedule__source__room__user_id=user_id, day=day,
        status=ProactiveClaim.Status.PENDING, token__isnull=False, available_at__gt=now,
    )


def _candidates(day, now):
    room = 'source__room__'
    pending = ReplyPlan.objects.filter(room_id=OuterRef('source__room_id'), status__in=ACTIVE)
    deliveries = ProactiveDelivery.objects.filter(user_id=OuterRef(room + 'user_id'))
    last = ProactiveDelivery.objects.filter(room_id=OuterRef('source__room_id')).order_by('-sent_at')
    current_slot = _slots(now).filter(character_id=OuterRef(room + 'character_id'))
    return list(
        Memory.objects.filter(
            kind=Memory.Kind.SCHEDULE, event_date=day - timedelta(days=1),
            source__sender=Message.Sender.USER, source__kind=Message.Kind.TEXT,
            source__room__proactive_enabled=True, source__room__memory_state__enabled=True,
            source_id__gt=F(room + 'memory_state__history_floor'),
        ).filter(
            ~Exists(pending), ~Exists(deliveries.filter(day=day)),
            ~Exists(_active_claims(OuterRef(room + 'user_id'), day, now)),
            Q(proactive_claim__isnull=True)
            | Q(proactive_claim__status=ProactiveClaim.Status.PENDING, proactive_claim__available_at__lte=now),
        ).alias(
            slot_state=Subquery(current_slot.values('state')[:1]),
            last_proactive=Subquery(last.values('sent_at')[:1]),
        ).filter(slot_state=State.FREE)
        .order_by(F('last_proactive').asc(nulls_first=True), 'source__room_id', 'pk')
        .values_list('pk', 'source__room_id', 'source__room__user_id')[:CANDIDATE_LIMIT]
    )


def _inbound_through(room):
    return room.messages.filter(sender=Message.Sender.USER).order_by('-pk').values_list('pk', flat=True).first() or 0


def _eligible_schedule(room, state, schedule_id, day):
    if not room.proactive_enabled or state is None or not state.enabled:
        return None
    return room_memories(room.pk).filter(
        pk=schedule_id, kind=Memory.Kind.SCHEDULE, event_date=day - timedelta(days=1),
        source__kind=Message.Kind.TEXT, source_id__gt=state.history_floor,
    ).select_related('source').first()


def _context(room, slot, schedule, floor, now):
    event_start = datetime.combine(schedule.event_date, time.min, tzinfo=KST)
    # Start at the disclosure or the start of the event day, whichever includes
    # more context. If we cannot see the full relevant interval, decline safely.
    recent = list(room.messages.filter(
        Q(pk__gte=schedule.source_id) | Q(created_at__gte=event_start),
        pk__gt=floor, created_at__lte=now,
    ).exclude(text='').order_by('pk').values('sender', 'text')[:HISTORY_LIMIT + 1])
    if len(recent) > HISTORY_LIMIT or sum(len(turn['text']) for turn in recent) > HISTORY_CHAR_LIMIT:
        return None
    person = room.character
    persona = getattr(person, 'persona', None)
    return {
        'name': person.name,
        'persona': {
            'personality': persona.personality, 'speech_rules': persona.speech_rules,
            'example_dialogues': persona.example_dialogues, 'taboos': persona.taboos,
        } if persona else '',
        'activity': slot.activity,
        'schedule': schedule.text,
        'event_date': schedule.event_date.isoformat(),
        'recent': recent,
    }


def _release(claim, *, status=ProactiveClaim.Status.PENDING, at=None):
    claim.status, claim.token, claim.available_at = status, None, at
    claim.save(update_fields=['status', 'token', 'available_at'])


@transaction.atomic
def _claim(schedule_id, room_id, user_id, day):
    # Serialize competing claims across an account's rooms without waiting on
    # a busy room or interfering with the ordinary reply worker's room lock.
    if not get_user_model().objects.select_for_update(no_key=True, skip_locked=True).filter(pk=user_id).exists():
        return None
    room = Friendship.objects.select_for_update(skip_locked=True).filter(pk=room_id, user_id=user_id).first()
    now = world_now()
    if room is None or _day_in_window(now) != day:
        return None
    if (ProactiveDelivery.objects.filter(user_id=user_id, day=day).exists()
            or _active_claims(user_id, day, now).exists()
            or room.reply_plans.filter(status__in=ACTIVE).exists()):
        return None
    state = RoomMemoryState.objects.filter(room_id=room.pk).first()
    schedule = _eligible_schedule(room, state, schedule_id, day)
    slot = _slots(now).filter(character_id=room.character_id).first()
    if schedule is None or slot is None or slot.state != State.FREE:
        return None

    inbound = _inbound_through(room)
    claim, created = ProactiveClaim.objects.get_or_create(
        schedule=schedule, defaults={'day': day, 'inbound_through': inbound},
    )
    if not created and (claim.status != ProactiveClaim.Status.PENDING or claim.day != day
                        or (claim.available_at and claim.available_at > now)):
        return None
    if inbound > claim.inbound_through or claim.attempts >= MAX_ATTEMPTS:
        _release(claim, status=ProactiveClaim.Status.DECLINED)
        return None
    context = _context(room, slot, schedule, state.history_floor, now)
    if context is None:
        _release(claim, status=ProactiveClaim.Status.DECLINED)
        return None

    claim.token, claim.available_at = uuid4(), now + LEASE
    claim.attempts += 1
    claim.save(update_fields=['token', 'available_at', 'attempts'])
    return Followup(claim.pk, room.pk, claim.token, day, room.proactive_revision, state.revision, slot.pk, context)


@transaction.atomic
def _finish(followup, text, *, failed=False):
    room = Friendship.objects.select_for_update().filter(pk=followup.room_id).first()
    if room is None:
        return None
    claim = ProactiveClaim.objects.filter(pk=followup.claim_id, token=followup.token).first()
    if claim is None:
        return None
    now = world_now()
    if _inbound_through(room) > claim.inbound_through:
        _release(claim, status=ProactiveClaim.Status.DECLINED)
        return None
    if now >= claim.available_at:
        return None

    state = RoomMemoryState.objects.filter(room_id=room.pk).first()
    schedule = _eligible_schedule(room, state, claim.schedule_id, followup.day)
    if (schedule is None or _day_in_window(now) != followup.day
            or room.proactive_revision != followup.consent_revision or state.revision != followup.memory_revision
            or schedule.text != followup.context['schedule']):
        _release(claim, at=now)
        return None
    slot = _slots(now).filter(character_id=room.character_id).first()
    if (slot is None or slot.state != State.FREE or slot.pk != followup.slot_id
            or slot.activity != followup.context['activity'] or room.reply_plans.filter(status__in=ACTIVE).exists()):
        _release(claim, at=now)
        return None
    if failed and claim.attempts < MAX_ATTEMPTS:
        _release(claim, at=now + timedelta(minutes=2 ** (claim.attempts - 1)))
        return None
    if not text:
        _release(claim, status=ProactiveClaim.Status.DECLINED)
        return None

    # The user/day unique constraint also covers concurrent attempts in other
    # rooms and survives friendship deletion. Both records commit together.
    _, created = ProactiveDelivery.objects.get_or_create(
        user_id=room.user_id, day=followup.day, defaults={'room': room, 'sent_at': now},
    )
    if not created:
        _release(claim, status=ProactiveClaim.Status.DECLINED)
        return None
    message = Message.objects.create(room=room, sender=Message.Sender.CHARACTER, text=text, created_at=now)
    _release(claim, status=ProactiveClaim.Status.SENT)
    event = {'type': 'message.new', 'message': message_json(message)}
    transaction.on_commit(lambda: publish_room_events(room.pk, [event]), robust=True)
    return message


def prepare_next_proactive():
    """Process one eligible follow-up, without holding database locks during generation."""
    now = world_now()
    day = _day_in_window(now)
    if day is None:
        return None
    for schedule_id, room_id, user_id in _candidates(day, now):
        followup = _claim(schedule_id, room_id, user_id, day)
        if followup is None:
            continue
        try:
            text = provider.generate_followup(followup.context)
        except (provider.FollowupError, OSError) as exc:
            logger.warning('Proactive generation failed for claim %s (%s)', followup.claim_id, type(exc).__name__)
            return _finish(followup, None, failed=True)
        if text and any(0xD800 <= ord(char) <= 0xDFFF for char in text):
            text = None
        return _finish(followup, text)
    return None
