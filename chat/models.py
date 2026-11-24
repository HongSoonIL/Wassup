from django.conf import settings
from django.db import models
from django.db.models import F, Q

from world.clock import world_now
from world.models import Character


class Friendship(models.Model):
    """사용자별 층: 사용자와 캐릭터의 친구 관계. 이것 하나가 채팅방 하나다 (room id = friendship id).

    설계서 부록 C의 "마지막 메시지, 안 읽은 수"는 필드로 저장하지 않고 조회할 때 Message에서 계산한다.
    메시지가 생길 때마다 두 값을 맞춰 고치는 일을 없애려는 것이다.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='friendships')
    # 대화가 남아 있는 캐릭터를 실수로 지우지 않도록 막는다
    character = models.ForeignKey(Character, on_delete=models.PROTECT, related_name='friendships')
    created_at = models.DateTimeField('친구 맺은 날', default=world_now)
    proactive_enabled = models.BooleanField(default=False, db_default=False)
    proactive_revision = models.PositiveBigIntegerField(default=0, db_default=0)

    class Meta:
        verbose_name = verbose_name_plural = '친구 관계(채팅방)'
        constraints = [
            models.UniqueConstraint(fields=['user', 'character'], name='friendship_unique_user_character'),
        ]

    def __str__(self):
        return f'{self.user} ↔ {self.character}'


class Message(models.Model):
    """사용자별 층: 채팅방의 말풍선 하나."""

    class Sender(models.TextChoices):
        USER = 'user', '사용자'
        CHARACTER = 'character', '캐릭터'

    class Kind(models.TextChoices):
        TEXT = 'text', '글'
        PHOTO = 'photo', '사진'

    room = models.ForeignKey(Friendship, on_delete=models.CASCADE, related_name='messages')
    sender = models.CharField('보낸 쪽', max_length=10, choices=Sender.choices)
    kind = models.CharField('종류', max_length=10, choices=Kind.choices, default=Kind.TEXT)
    text = models.TextField('내용', blank=True)
    source_post = models.ForeignKey('world.FeedPost', null=True, blank=True, on_delete=models.PROTECT)
    source_version = models.PositiveIntegerField(null=True, blank=True)
    attachment_post = models.ForeignKey(
        'world.FeedPost', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    attachment_version = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField('보낸 시각', default=world_now)
    # 사용자 메시지: 캐릭터가 읽은 시각 (숫자 1이 사라짐) / 캐릭터 메시지: 사용자가 읽은 시각
    read_at = models.DateTimeField('읽은 시각', null=True, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = '메시지'
        ordering = ['id']
        indexes = [
            models.Index(fields=['room', '-id'], name='message_room_recent'),
        ]

    def __str__(self):
        return f'[{self.get_sender_display()}] {self.text[:30]}'

    @property
    def status(self):
        """API에 내보내는 상태. 읽은 시각에서 바로 나오므로 따로 저장하지 않는다."""
        return 'read' if self.read_at else 'sent'


class ReplyPlan(models.Model):
    """사용자별 층: 답장 계획. "언제 읽고, 언제 입력하고, 언제 답할지"와 미리 만든 답장을 담는다 (설계서 3.4절).

    read_at, typing_at, reply_at은 첫 말풍선의 일정이다. 후속 발송은 ReplyBubble에서 이어간다.
    """

    class Status(models.TextChoices):
        PLANNED = 'planned', '계획됨'      # 시각만 정해짐, 답장 생성 전
        GENERATING = 'generating', '생성 중'
        READ = 'read', '읽음'
        READY = 'ready', '준비됨'          # 출력 검사를 통과한 답장이 있음
        SENDING = 'sending', '후속 말풍선 발송 중'
        SENT = 'sent', '보냄'
        FAILED = 'failed', '실패'
        DISCARDED = 'discarded', '버림'    # 답장 전에 새 메시지가 와서 다시 만듦

    room = models.ForeignKey(Friendship, on_delete=models.CASCADE, related_name='reply_plans')
    trigger_message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name='reply_plans', verbose_name='계기 메시지',
    )
    read_at = models.DateTimeField('읽을 시각')
    typing_at = models.DateTimeField('입력 시작 시각')
    reply_at = models.DateTimeField('답장 시각')
    result = models.JSONField('생성 결과', default=list, blank=True, help_text='말풍선 문장 목록')
    # The server pins the validated selection before the timed bubbles exist.
    attachment = models.JSONField(null=True, blank=True, editable=False)
    status = models.CharField('상태', max_length=12, choices=Status.choices, default=Status.PLANNED)
    generation_token = models.UUIDField(null=True, editable=False)
    generation_until = models.DateTimeField(null=True, editable=False)
    generation_ms = models.PositiveIntegerField(null=True, editable=False)
    context_versions = models.JSONField(default=dict, blank=True)
    memory_revision = models.PositiveBigIntegerField(default=0, editable=False)
    used_memory_ids = models.JSONField(default=list, blank=True, editable=False)
    model_name = models.CharField('사용 모델', max_length=50, blank=True)
    attempts = models.PositiveSmallIntegerField('시도 수', default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = verbose_name_plural = '답장 계획'
        ordering = ['reply_at']
        indexes = [
            # 디스패처가 "시각이 된 계획"을 답장 시각 순으로 찾을 때 쓴다
            models.Index(fields=['status', 'reply_at'], name='replyplan_due'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(read_at__lte=F('typing_at'), typing_at__lte=F('reply_at')),
                name='replyplan_times_in_order',
            ),
        ]

    def __str__(self):
        return f'{self.room} 답장 {self.reply_at:%m-%d %H:%M} ({self.get_status_display()})'


class ReplyBubble(models.Model):
    """한 답장 안의 순서와 발송 기록. 다음 말풍선만 실제 앞선 발송 시각을 기준으로 예약한다."""

    class Status(models.TextChoices):
        QUEUED = 'queued', '대기'
        TYPING = 'typing', '입력 중'
        SENT = 'sent', '보냄'
        CANCELLED = 'cancelled', '취소'

    plan = models.ForeignKey(ReplyPlan, on_delete=models.CASCADE, related_name='bubbles')
    position = models.PositiveSmallIntegerField()
    text = models.TextField()
    attachment_post = models.ForeignKey(
        'world.FeedPost', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    attachment_version = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    typing_at = models.DateTimeField(null=True)
    deliver_at = models.DateTimeField(null=True)
    message = models.OneToOneField(Message, null=True, on_delete=models.CASCADE, related_name='reply_bubble')

    class Meta:
        ordering = ['position']
        constraints = [
            models.UniqueConstraint(fields=['plan', 'position'], name='replybubble_plan_position'),
        ]
        indexes = [
            models.Index(fields=['status', 'typing_at'], name='replybubble_typing_due'),
            models.Index(fields=['status', 'deliver_at'], name='replybubble_delivery_due'),
        ]


class ProactiveClaim(models.Model):
    """A schedule's generation lease, retry state and final decision."""

    class Status(models.TextChoices):
        PENDING = 'pending'
        DECLINED = 'declined'
        SENT = 'sent'

    schedule = models.OneToOneField('memory.Memory', on_delete=models.CASCADE, related_name='proactive_claim')
    day = models.DateField()
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING)
    token = models.UUIDField(null=True, editable=False)
    # Lease expiration while claimed, or the earliest retry when released.
    available_at = models.DateTimeField(null=True)
    inbound_through = models.PositiveBigIntegerField()
    attempts = models.PositiveSmallIntegerField(default=0)


class ProactiveDelivery(models.Model):
    """The account's daily cap and the room's last turn in the rotation."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    room = models.ForeignKey(Friendship, null=True, on_delete=models.SET_NULL, db_index=False)
    day = models.DateField()
    sent_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'day'], name='proactive_user_day')]
        indexes = [models.Index(fields=['room', '-sent_at'], name='proactive_last_delivery')]
