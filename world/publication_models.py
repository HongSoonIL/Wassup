from uuid import uuid4

from django.conf import settings
from django.core.files.storage import storages
from django.db import models, transaction


def private_storage():
    return storages['post_photos']


class SchoolEvent(models.Model):
    class Kind(models.TextChoices):
        EXAMS = 'exams', '시험 기간'
        SPORTS = 'sports', '체육대회'
        BREAK = 'break', '방학'

    label = models.CharField(max_length=80)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    start_date = models.DateField()
    end_date_exclusive = models.DateField()
    revision = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date_exclusive__gt=models.F('start_date')),
                name='school_event_date_order',
            )
        ]

    @transaction.atomic
    def save(self, *args, **kwargs):
        if self._state.adding:
            return super().save(*args, **kwargs)
        previous = type(self).objects.select_for_update(no_key=True).get(pk=self.pk)
        fields = ('label', 'kind', 'start_date', 'end_date_exclusive')
        changed = any(
            getattr(previous, f) != getattr(self, f)
            for f in fields
            if kwargs.get('update_fields') is None or f in kwargs['update_fields']
        )
        self.revision = previous.revision + int(changed)
        if changed and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'revision'}
        super().save(*args, **kwargs)
        if changed:
            from .publication import invalidate_event

            invalidate_event(self)

    def __str__(self):
        return self.label


class PostPhoto(models.Model):
    key = models.SlugField(primary_key=True)
    character = models.ForeignKey('world.Character', on_delete=models.PROTECT)
    image = models.ImageField(storage=private_storage, upload_to='photos/')
    alt = models.CharField(max_length=200)
    ai_generated = models.BooleanField(default=True)

    def __str__(self):
        return self.key


class PublicationWeek(models.Model):
    start = models.DateField(unique=True)
    revision = models.PositiveIntegerField(default=0)
    generation = models.CharField(max_length=8, default='pending')
    generation_error = models.CharField(max_length=240, blank=True)
    generation_token = models.UUIDField(null=True, editable=False)
    lease_until = models.DateTimeField(null=True, editable=False)
    generation_attempts = models.PositiveSmallIntegerField(default=0, editable=False)

    class Meta:
        permissions = [('publish_week', '주간 발행을 관리할 수 있음')]


class FeedPost(models.Model):
    class State(models.TextChoices):
        DRAFT = 'draft'
        SCHEDULED = 'scheduled'
        FAILED = 'failed'
        PUBLISHED = 'published'
        SKIPPED = 'skipped'

    week = models.ForeignKey(PublicationWeek, related_name='posts', on_delete=models.PROTECT)
    character = models.ForeignKey('world.Character', on_delete=models.PROTECT)
    events = models.ManyToManyField(SchoolEvent, through='PostDependency')
    caption = models.TextField()
    public_facts = models.JSONField(default=list)
    operator_note = models.TextField(blank=True)
    photo = models.ForeignKey(PostPhoto, null=True, on_delete=models.PROTECT)
    photo_alt = models.CharField(max_length=200, blank=True)
    # Earlier reviewed posts have no recorded event time.
    event_at = models.DateTimeField(null=True, blank=True)
    publish_at = models.DateTimeField()
    version = models.PositiveIntegerField(default=1)
    state = models.CharField(max_length=12, choices=State.choices, default=State.DRAFT)
    attempt = models.PositiveIntegerField(default=1)
    approved_version = models.PositiveIntegerField(null=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    approved_at = models.DateTimeField(null=True)
    published_at = models.DateTimeField(null=True)
    last_error = models.CharField(max_length=240, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['state', 'publish_at']),
            models.Index(fields=['character', '-published_at']),
        ]


class PostDependency(models.Model):
    post = models.ForeignKey(FeedPost, on_delete=models.CASCADE)
    event = models.ForeignKey(SchoolEvent, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['post', 'event'], name='post_calendar_unique')]


class PostRevision(models.Model):
    post = models.ForeignKey(FeedPost, related_name='versions', on_delete=models.CASCADE)
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['post', 'version'], name='post_content_version')]


class PhotoJob(models.Model):
    class State(models.TextChoices):
        QUEUED = 'queued'
        GENERATING = 'generating'
        READY = 'ready'
        FAILED = 'failed'
        DISCARDED = 'discarded'

    post = models.ForeignKey(FeedPost, related_name='photo_jobs', on_delete=models.PROTECT)
    version = models.PositiveIntegerField()
    initial = models.BooleanField(default=False)
    scene = models.JSONField()
    previous_photo_id = models.CharField(max_length=50, null=True)
    state = models.CharField(max_length=12, choices=State.choices, default=State.QUEUED)
    detail = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    lease_until = models.DateTimeField(null=True)
    model = models.CharField(max_length=120, blank=True)
    elapsed_ms = models.PositiveBigIntegerField(null=True)
    usage = models.JSONField(default=dict)
    file_token = models.UUIDField(default=uuid4, unique=True, editable=False)
    cleanup_pending = models.BooleanField(default=False)
    result = models.OneToOneField(
        PostPhoto, related_name='generation_job', on_delete=models.PROTECT, null=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['post'], condition=models.Q(initial=True), name='photo_initial_once_per_post'
            )
        ]
        indexes = [models.Index(fields=['state', 'lease_until'])]
