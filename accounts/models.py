from unicodedata import category

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .private_photos import user_photo_storage


PREFERENCE_TAGS = frozenset({'커피', '음악', '산책', '영화', '음식', '운동'})


def validate_shared_preferences(items):
    if not isinstance(items, list) or len(items) > 5:
        raise ValidationError('취향은 최대 5개까지 보낼 수 있어요.')

    seen_tags = set()
    for item in items:
        if not isinstance(item, dict) or item.keys() != {'tag', 'text'}:
            raise ValidationError('각 취향에 tag와 text를 보내 주세요.')
        tag, text = item['tag'], item['text']
        if not isinstance(tag, str) or not isinstance(text, str):
            raise ValidationError('tag와 text는 문자열로 보내 주세요.')
        if tag and tag not in PREFERENCE_TAGS:
            raise ValidationError('목록에 있는 태그를 선택해 주세요.')
        if tag in seen_tags:
            raise ValidationError('같은 태그는 한 번만 사용할 수 있어요.')
        if tag:
            seen_tags.add(tag)
        if not tag and not text.strip():
            raise ValidationError('태그를 선택하거나 취향을 적어 주세요.')
        if len(text) > 80 or (text and text.splitlines() != [text]):
            raise ValidationError('취향은 한 줄로 80자까지 적을 수 있어요.')
        if '\x00' in text or any(category(char) == 'Cs' for char in text):
            raise ValidationError('취향에 저장할 수 없는 문자가 있어요.')


class User(AbstractUser):
    """wassup 사용자 (설계서 부록 C의 User / Profile).

    설계서는 User와 Profile을 나눠 적었지만, 필드가 몇 개 안 돼서 User 하나에 모았다.
    탈퇴하면 사용자별 층(친구 관계, 메시지, 답장 계획)이 함께 지워진다 (설계서 3.5절).
    """

    nickname = models.CharField('닉네임', max_length=20, blank=True)
    bio = models.CharField('한 줄 소개', max_length=80, blank=True, default='', db_default='')
    shared_preferences = models.JSONField(
        default=list, db_default=[], blank=True, editable=False, validators=[validate_shared_preferences],
    )
    preference_revision = models.PositiveBigIntegerField(default=0, db_default=0, editable=False)
    # 만 18세 이상만 가입한다 (설계서 2.3절). 나이 확인은 가입 API(부록 B)에서 한다.
    birthday_month = models.PositiveSmallIntegerField(null=True, blank=True)
    birthday_day = models.PositiveSmallIntegerField(null=True, blank=True)
    birthday_shared = models.BooleanField(default=False, db_default=False)
    birth_year = models.PositiveSmallIntegerField('생년', null=True, blank=True)
    agreed_at = models.DateTimeField('대화 저장·AI 고지 동의 시각', null=True, blank=True)
    # 선톡은 켠 사용자에게만 보낸다 (설계서 3.8절)
    allow_proactive = models.BooleanField('선톡 허용', default=False)
    usage_time_alert = models.BooleanField('이용 시간 알림', default=True)

    def __str__(self):
        return self.nickname or self.username


class SignupInvite(models.Model):
    digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    redeemed_at = models.DateTimeField(null=True)


class PersonalPost(models.Model):
    owner = models.ForeignKey(User, related_name='personal_posts', on_delete=models.CASCADE)
    client_post_id = models.UUIDField(null=True, editable=False)
    caption = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    photo = models.ImageField(storage=user_photo_storage, upload_to='personal-posts/', blank=True)

    class Meta:
        indexes = [models.Index(fields=['owner', '-created_at', '-id'], name='personal_post_owner_date')]
        constraints = [models.UniqueConstraint(fields=['owner', 'client_post_id'], name='personal_post_owner_client_id')]


@receiver(pre_delete, sender=PersonalPost)
def remove_post_photo(sender, instance, using, **kwargs):
    if instance.photo:
        storage, name = instance.photo.storage, instance.photo.name
        transaction.on_commit(lambda: storage.delete(name), using=using)
