from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """wassup 사용자 (설계서 부록 C의 User / Profile).

    설계서는 User와 Profile을 나눠 적었지만, 필드가 몇 개 안 돼서 User 하나에 모았다.
    탈퇴하면 사용자별 층(친구 관계, 메시지, 답장 계획)이 함께 지워진다 (설계서 3.5절).
    """

    nickname = models.CharField('닉네임', max_length=20, blank=True)
    bio = models.CharField('한 줄 소개', max_length=80, blank=True, default='', db_default='')
    # 만 18세 이상만 가입한다 (설계서 2.3절). 나이 확인은 가입 API(부록 B)에서 한다.
    birth_year = models.PositiveSmallIntegerField('생년', null=True, blank=True)
    agreed_at = models.DateTimeField('대화 저장·AI 고지 동의 시각', null=True, blank=True)
    # 선톡은 켠 사용자에게만 보낸다 (설계서 3.8절)
    allow_proactive = models.BooleanField('선톡 허용', default=False)
    usage_time_alert = models.BooleanField('이용 시간 알림', default=True)

    def __str__(self):
        return self.nickname or self.username
