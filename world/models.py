from datetime import time

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

from .clock import world_now
from .publication_models import FeedPost, PostDependency, PostPhoto, PostRevision, PublicationWeek, SchoolEvent


class State(models.TextChoices):
    """일과 칸에 붙는 상태. 상태마다 읽기·답장 속도가 다르다 (설계서 3.3절 표)."""

    FREE = 'free', '한가함'
    CLASS = 'class', '수업 중'
    FOCUS = 'focus', '집중'
    MOVING = 'moving', '이동 중'
    LEISURE = 'leisure', '여가'
    SLEEP = 'sleep', '수면'


class Character(models.Model):
    """공개 층: 모든 사용자에게 같은 AI 친구의 프로필."""

    name = models.CharField('이름', max_length=20)
    age = models.PositiveSmallIntegerField('나이')
    major = models.CharField('학과', max_length=50)
    status_message = models.CharField('상태 메시지', max_length=60, blank=True)
    profile_image = models.ImageField('현재 프사', upload_to='characters/profile/', blank=True)
    background_image = models.ImageField('현재 배경', upload_to='characters/background/', blank=True)

    class Meta:
        verbose_name = verbose_name_plural = '캐릭터'
        ordering = ['id']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        previous = None
        if self.pk:
            previous = Character.objects.filter(pk=self.pk).values_list('profile_image', flat=True).first()
        super().save(*args, **kwargs)
        # 프사를 새로 정하면 그 사진을 이력에 남긴다. 지난 프사는 지우지 않는다.
        if self.profile_image and self.profile_image.name != previous:
            self.profile_photos.create(image=self.profile_image.name, set_at=world_now())


class ProfilePhoto(models.Model):
    """공개 층: 캐릭터가 지금까지 쓴 프사. 모든 사용자에게 같은 이력이다 (설계서 2.1절)."""

    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name='profile_photos')
    image = models.ImageField('프사', upload_to='characters/profile/')
    set_at = models.DateTimeField('프사로 바꾼 시각', default=world_now)
    ai_generated = models.BooleanField('AI 생성 사진', default=True)

    class Meta:
        verbose_name = verbose_name_plural = '프사 이력'
        ordering = ['-set_at', '-id']

    def __str__(self):
        return f'{self.character.name} 프사 {self.set_at:%Y-%m-%d}'


class Persona(models.Model):
    """캐릭터 내부 층: 화면에 나오지 않고 대화 재료와 답장 리듬에 쓰인다 (설계서 3.3절)."""

    character = models.OneToOneField(Character, on_delete=models.CASCADE, related_name='persona')
    personality = models.TextField('성격')
    speech_rules = models.TextField('말투 규칙')
    example_dialogues = models.TextField('예시 대화', blank=True)
    taboos = models.TextField('금기', blank=True)

    # 리듬 값. 확률은 관리자가 읽기 쉽게 퍼센트 정수로 둔다.
    reply_speed = models.DecimalField(
        '답장 속도 배율', max_digits=3, decimal_places=2, default=1,
        validators=[MinValueValidator(0.1)],
        help_text='상태별 시간에 곱하는 값. 1보다 크면 느리다.',
    )
    ignore_percent = models.PositiveSmallIntegerField(
        '읽씹 확률(%)', default=0, validators=[MaxValueValidator(100)],
    )
    bubble_min = models.PositiveSmallIntegerField('말풍선 최소 개수', default=1, validators=[MinValueValidator(1)])
    bubble_max = models.PositiveSmallIntegerField('말풍선 최대 개수', default=2, validators=[MinValueValidator(1)])
    hesitation_percent = models.PositiveSmallIntegerField(
        '망설임 확률(%)', default=0, validators=[MaxValueValidator(100)],
    )
    typing_speed = models.PositiveSmallIntegerField(
        '타자 속도(초당 글자)', default=5, validators=[MinValueValidator(1)],
    )

    class Meta:
        verbose_name = verbose_name_plural = '페르소나'
        constraints = [
            models.CheckConstraint(condition=Q(reply_speed__gt=0), name='persona_reply_speed_positive'),
            models.CheckConstraint(condition=Q(ignore_percent__lte=100), name='persona_ignore_percent_lte_100'),
            models.CheckConstraint(condition=Q(hesitation_percent__lte=100), name='persona_hesitation_percent_lte_100'),
            models.CheckConstraint(
                condition=Q(bubble_min__gte=1, bubble_max__gte=F('bubble_min')),
                name='persona_bubble_range_valid',
            ),
            models.CheckConstraint(condition=Q(typing_speed__gte=1), name='persona_typing_speed_positive'),
        ]

    def __str__(self):
        return f'{self.character.name}의 페르소나'

    def clean(self):
        if self.bubble_min and self.bubble_max and self.bubble_max < self.bubble_min:
            raise ValidationError({'bubble_max': '최대 개수는 최소 개수보다 작을 수 없어요.'})


class RoutineTemplate(models.Model):
    """캐릭터 내부 층: 요일별 기본 일과의 한 칸.

    한 칸은 하루 안에서 끝난다. 끝 시각 00:00은 "자정까지"라는 뜻이다.
    자정을 넘는 일과(예: 22:30~01:30 집)는 두 칸으로 나눠 적는다.
    """

    class Weekday(models.IntegerChoices):
        MON = 0, '월'
        TUE = 1, '화'
        WED = 2, '수'
        THU = 3, '목'
        FRI = 4, '금'
        SAT = 5, '토'
        SUN = 6, '일'

    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name='routines')
    weekday = models.PositiveSmallIntegerField('요일', choices=Weekday.choices)  # date.weekday()와 같은 번호
    start_time = models.TimeField('시작')
    end_time = models.TimeField('끝', help_text='00:00은 자정까지라는 뜻이에요.')
    activity = models.CharField('활동', max_length=30)
    state = models.CharField('상태', max_length=10, choices=State.choices)

    class Meta:
        verbose_name = verbose_name_plural = '기본 일과'
        ordering = ['character', 'weekday', 'start_time']
        constraints = [
            models.CheckConstraint(condition=Q(weekday__lte=6), name='routine_weekday_valid'),
            models.CheckConstraint(
                condition=Q(start_time__lt=F('end_time')) | Q(end_time=time(0)),
                name='routine_ends_after_start',
            ),
        ]

    def __str__(self):
        return f'{self.character.name} {self.get_weekday_display()} {self.start_time:%H:%M}~{self.end_time:%H:%M} {self.activity}'

    def clean(self):
        if self.start_time and self.end_time and self.end_time != time(0) and self.end_time <= self.start_time:
            raise ValidationError('끝 시각이 시작 시각보다 늦어야 해요. 자정을 넘으면 두 칸으로 나눠 주세요.')
