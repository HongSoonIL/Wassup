"""Room memory and extraction jobs share the Friendship lock with chat acceptance and delivery."""

from datetime import date

from django.contrib.postgres.expressions import ArraySubquery
from django.db import transaction
from django.db.models import OuterRef
from django.db.models.functions import JSONObject
from django.utils import timezone

from chat.models import Friendship, Message
from .models import Memory, MemoryJob, RoomMemoryState


def room_memories(room_id):
    return Memory.objects.filter(source__room_id=room_id, source__sender=Message.Sender.USER)


def _snapshot(room_id, *, include_source):
    # Read revision and facts in one PostgreSQL statement. A reader holding a reply-plan
    # lock never has to acquire the room lock, and cannot pair an old revision with new facts.
    fields = dict(id='id', kind='kind', text='text', event_date='event_date')
    if include_source:
        fields['source'] = JSONObject(id='source_id', text='source__text')
    facts = room_memories(OuterRef('room_id')).order_by('id').values(fact=JSONObject(**fields))
    state = RoomMemoryState.objects.filter(pk=room_id).values(
        'enabled', 'revision', memories=ArraySubquery(facts)
    ).first()
    if state is None:
        return {'enabled': False, 'revision': 0, 'memories': []}
    if not state['enabled']:
        state['memories'] = []
    return state


def room_snapshot(room_id):
    return _snapshot(room_id, include_source=True)


def memory_snapshot(room_id):
    state = _snapshot(room_id, include_source=False)
    return state['revision'], state['memories']


def history_floor(room_id):
    return RoomMemoryState.objects.filter(pk=room_id).values_list('history_floor', flat=True).first() or 0


def memory_revision(room_id):
    return RoomMemoryState.objects.filter(pk=room_id).values_list('revision', flat=True).first() or 0


def state_for_locked_room(room_id):
    return RoomMemoryState.objects.filter(pk=room_id).first() or RoomMemoryState(room_id=room_id)


def _fence_history(state):
    # The room lock also serializes new user messages and character bubbles. Keeping just an
    # ID fences derived character turns and extraction retries without copying deleted text.
    latest = Message.objects.filter(room_id=state.room_id).order_by('-pk').values_list('pk', flat=True).first()
    state.history_floor = max(state.history_floor, latest or 0)
    MemoryJob.objects.filter(message__room_id=state.room_id, message_id__lte=state.history_floor).delete()


def set_enabled(state, enabled):
    """Change consent under the caller's room lock."""
    if state.enabled == enabled:
        return
    if not enabled:
        _fence_history(state)
        room_memories(state.room_id).delete()
    state.enabled = enabled
    state.revision += 1
    state.save()


def edit_memory(state, memory, changes):
    """Apply an owner edit or deletion under the caller's room lock."""
    if changes is None:
        memory.delete()
    else:
        for field, value in changes.items():
            setattr(memory, field, value)
        memory.save(update_fields=changes.keys())
    _fence_history(state)
    state.revision += 1
    state.save(update_fields=['history_floor', 'revision'])


@transaction.atomic
def enqueue(message):
    """Enqueue only newly accepted user text, in the transaction that inserted it."""
    if message.sender != Message.Sender.USER or message.kind != Message.Kind.TEXT:
        return
    Friendship.objects.select_for_update().only('pk').get(pk=message.room_id)
    if RoomMemoryState.objects.filter(pk=message.room_id, enabled=True, history_floor__lt=message.pk).exists():
        MemoryJob.objects.get_or_create(message=message)


def clean_text(value):
    if not isinstance(value, str):
        raise ValueError('기억 내용을 입력해 주세요.')
    text = value.strip()
    maximum = Memory._meta.get_field('text').max_length
    if not text or len(text) > maximum:
        raise ValueError(f'기억 내용은 1자에서 {maximum}자까지 입력해 주세요.')
    if '\x00' in text or any(0xD800 <= ord(char) <= 0xDFFF for char in text):
        raise ValueError('기억 내용에 사용할 수 없는 문자가 있어요.')
    return text


@transaction.atomic
def finish_job(job_id, token, candidates):
    """Apply a live claim once. A worker returning after a room change or re-claim does nothing."""
    if token is None:
        return False
    claims = MemoryJob.objects.filter(pk=job_id, token=token)
    room_id = claims.values_list('message__room_id', flat=True).first()
    if room_id is None or not Friendship.objects.select_for_update().filter(pk=room_id).exists():
        return False
    job = claims.filter(message__room_id=room_id).select_for_update(of=('self',)).select_related('message').first()
    if job is None or (job.lease_until is not None and job.lease_until <= timezone.now()):
        return False

    state = state_for_locked_room(room_id)
    if (not state.enabled or job.message_id <= state.history_floor or job.message.sender != Message.Sender.USER
            or job.message.kind != Message.Kind.TEXT):
        job.delete()
        return False

    changed = False
    existing = room_memories(room_id)
    for candidate in candidates:
        kind, event_date = candidate['kind'], candidate['event_date']
        if kind not in Memory.Kind.values:
            raise ValueError('기억의 종류가 올바르지 않아요.')
        if event_date is not None and (type(event_date) is not date or kind == Memory.Kind.PREFERENCE):
            raise ValueError('기억의 날짜가 올바르지 않아요.')
        fields = dict(kind=kind, text=clean_text(candidate['text']), event_date=event_date)
        # Extraction only inserts. A later message cannot overwrite an owner-edited fact or its source.
        if not existing.filter(**fields).exists():
            Memory.objects.create(source_id=job.message_id, **fields)
            changed = True
    if changed:
        state.revision += 1
        state.save(update_fields=['revision'])
    job.delete()
    return changed
