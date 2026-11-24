from django.db import models


class RoomMemoryState(models.Model):
    """Created on first consent. Existing rooms start disabled, at revision and floor zero."""

    room = models.OneToOneField('chat.Friendship', primary_key=True, on_delete=models.CASCADE,
                                related_name='memory_state')
    enabled = models.BooleanField(default=False)
    revision = models.PositiveBigIntegerField(default=0)
    history_floor = models.PositiveBigIntegerField(default=0)


class Memory(models.Model):
    class Kind(models.TextChoices):
        SCHEDULE = 'schedule', '일정'
        PREFERENCE = 'preference', '취향'

    # The source determines the room, so a fact cannot be assigned to a different friendship.
    source = models.ForeignKey('chat.Message', on_delete=models.CASCADE, related_name='memories')
    kind = models.CharField(max_length=10, choices=Kind.choices)
    text = models.CharField(max_length=1000)
    event_date = models.DateField(null=True, blank=True)


class MemoryJob(models.Model):
    message = models.OneToOneField('chat.Message', on_delete=models.CASCADE, related_name='memory_job')
    token = models.UUIDField(null=True, editable=False)
    lease_until = models.DateTimeField(null=True, editable=False)
    attempts = models.PositiveIntegerField(default=0)
