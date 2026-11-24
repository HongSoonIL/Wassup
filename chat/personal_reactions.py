"""Lease one owner's post, generate outside locks, then recheck the selected room."""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import DateTimeField, Exists, ExpressionWrapper, F, OuterRef, Q, Subquery

from accounts.models import PersonalPost
from chat import birthdays, proactive
from chat.live import publish_room_events
from chat.models import Friendship, Message, PersonalPostReaction, ProactiveClaim, ProactiveDelivery, ReplyPlan
from chat.replies import ACTIVE
from chat.serialization import message_json
from llm import proactive as provider, user_photos
from world.models import State


logger = logging.getLogger(__name__)
Status = ProactiveClaim.Status


@dataclass(frozen=True)
class Reaction:
    post: PersonalPost
    token: UUID
    day: date
    friends: list[dict]
    # room id -> (consent revision, free slot id, newest incoming message id)
    room_snapshots: dict


def _due_posts(now):
    # A stable 15–44 minute delay persists across worker restarts.
    minutes = ((F('pk') % 30) * 17) % 30
    due = F('created_at') + timedelta(minutes=15) + minutes * timedelta(minutes=1)
    return PersonalPost.objects.alias(due_at=ExpressionWrapper(due, output_field=DateTimeField())).filter(due_at__lte=now)


def _rooms(user_id, now):
    slot = proactive._slots(now).filter(character_id=OuterRef('character_id'))
    pending = ReplyPlan.objects.filter(room_id=OuterRef('pk'), status__in=ACTIVE)
    return (Friendship.objects.filter(~Exists(pending), user_id=user_id, proactive_enabled=True)
            .annotate(slot_id=Subquery(slot.values('pk')[:1]))
            .alias(slot_state=Subquery(slot.values('state')[:1])).filter(slot_state=State.FREE))


def _candidates(day, now):
    owner = OuterRef('owner_id')
    return list(_due_posts(now).filter(
        Exists(_rooms(owner, now)), ~Exists(birthdays.blocked(owner, now)),
        ~Exists(ProactiveDelivery.objects.filter(user_id=owner, day=day)),
        ~Exists(proactive._active_claims(owner, day, now)),
        ~Exists(proactive._active_reactions(owner, now)),
        Q(reaction__isnull=True) | Q(reaction__status=Status.PENDING, reaction__available_at__lte=now),
    ).order_by('created_at', 'pk').values_list('pk', 'owner_id')[:proactive.CANDIDATE_LIMIT])


@transaction.atomic
def _claim(post_id, owner_id, day):
    now = proactive.world_now()
    if proactive._day_in_window(now) != day:
        return None
    if not get_user_model().objects.select_for_update(no_key=True, skip_locked=True).filter(pk=owner_id).exists():
        return None
    if (birthdays.defer_ordinary(owner_id, day, now) or ProactiveDelivery.objects.filter(user_id=owner_id, day=day).exists()
            or proactive._active_claims(owner_id, day, now).exists()
            or proactive._active_reactions(owner_id, now).exists()):
        return None
    post = _due_posts(now).select_for_update(skip_locked=True).filter(pk=post_id, owner_id=owner_id).first()
    if post is None:
        return None
    inbound = Message.objects.filter(room_id=OuterRef('pk'), sender=Message.Sender.USER).order_by('-pk')
    rooms = (_rooms(owner_id, now).annotate(inbound=Subquery(inbound.values('pk')[:1]))
             .select_related('character__persona').order_by('pk'))
    friends, snapshots = [], {}
    for room in rooms:
        person = room.character
        persona = getattr(person, 'persona', None)
        friends.append({
            'room_id': room.pk, 'name': person.name, 'major': person.major, 'status_message': person.status_message,
            'personality': persona.personality if persona else '', 'voice': persona.speech_rules if persona else '',
        })
        snapshots[room.pk] = room.proactive_revision, room.slot_id, room.inbound or 0
    if not friends:
        return None
    claim, _ = PersonalPostReaction.objects.get_or_create(post=post)
    if claim.status != Status.PENDING or (claim.available_at and claim.available_at > now):
        return None
    if claim.attempts >= proactive.MAX_ATTEMPTS:
        proactive._release(claim, status=Status.DECLINED)
        return None
    claim.token, claim.available_at = uuid4(), now + proactive.LEASE
    claim.attempts += 1
    claim.save(update_fields=['token', 'available_at', 'attempts'])
    return Reaction(post, claim.token, day, friends, snapshots)


@transaction.atomic
def _finish(reaction, decision, description='', *, failed=False):
    post = PersonalPost.objects.select_for_update().filter(pk=reaction.post.pk, owner_id=reaction.post.owner_id).first()
    if post is None:
        return None
    claim = PersonalPostReaction.objects.filter(post=post, token=reaction.token, status=Status.PENDING).first()
    if claim is None:
        return None
    room = None
    if decision and decision[0] in reaction.room_snapshots:
        room = Friendship.objects.select_for_update().filter(pk=decision[0], user_id=post.owner_id).first()
    now = proactive.world_now()
    if now >= claim.available_at:
        return None
    if (proactive._day_in_window(now) != reaction.day or post.caption != reaction.post.caption
            or post.photo.name != reaction.post.photo.name):
        proactive._release(claim, at=now)
        return None
    if failed and claim.attempts < proactive.MAX_ATTEMPTS:
        proactive._release(claim, at=now + timedelta(minutes=2 ** (claim.attempts - 1)))
        return None
    if not decision or decision[0] not in reaction.room_snapshots:
        proactive._release(claim, status=Status.DECLINED)
        return None
    revision, slot_id, inbound = reaction.room_snapshots[decision[0]]
    if room is not None and proactive._inbound_through(room) != inbound:
        proactive._release(claim, status=Status.DECLINED)
        return None
    if (room is None or room.proactive_revision != revision
            or not _rooms(post.owner_id, now).filter(pk=room.pk, slot_id=slot_id).exists()):
        proactive._release(claim, at=now)
        return None
    get_user_model().objects.select_for_update(no_key=True).get(pk=post.owner_id)
    if birthdays.defer_ordinary(post.owner_id, reaction.day, now):
        proactive._release(claim, at=now + timedelta(minutes=1))
        return None
    _, created = ProactiveDelivery.objects.get_or_create(
        user_id=post.owner_id, day=reaction.day, defaults={'room': room, 'sent_at': now},
    )
    if not created:
        proactive._release(claim, at=now)
        return None
    message = Message.objects.create(room=room, sender=Message.Sender.CHARACTER, text=decision[1],
                                     source_personal_post=post, source_version=1, created_at=now)
    claim.photo_description = description
    claim.status, claim.token, claim.available_at = Status.SENT, None, None
    claim.save(update_fields=['photo_description', 'status', 'token', 'available_at'])
    event = {'type': 'message.new', 'message': message_json(message, now=now)}
    transaction.on_commit(lambda: publish_room_events(room.pk, [event]), robust=True)
    return message


def prepare_post_reaction(day, now):
    for post_id, owner_id in _candidates(day, now):
        reaction = _claim(post_id, owner_id, day)
        if reaction is None:
            continue
        post = reaction.post
        try:
            description = user_photos.describe_photo(post.photo) if post.photo else ''
            if not description and not post.caption.strip() and post.photo:
                return _finish(reaction, None, failed=True)
            decision = provider.generate_post_reaction({
                'caption': post.caption, 'photo_description': description,
            }, reaction.friends)
        except (provider.FollowupError, OSError) as exc:
            logger.warning('Post reaction generation failed for post %s (%s)', post_id, type(exc).__name__)
            return _finish(reaction, None, failed=True)
        return _finish(reaction, decision, description)
    return None
