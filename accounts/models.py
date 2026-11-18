from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """wassup 사용자.

    AUTH_USER_MODEL은 첫 migrate 전에 정해야 해서 빈 껍데기로 먼저 만들어 둔다.
    닉네임, 생년월일, 동의 시각, 선톡 허용 필드는 0주차 회의에서 확정한 뒤 추가한다.
    """
