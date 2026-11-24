import secrets
from datetime import timedelta
from hashlib import sha256

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import SignupInvite


class Command(BaseCommand):
    help = '가입 시 한 번 쓸 수 있는 7일 유효 초대 코드를 발급한다.'

    def handle(self, *args, **options):
        code = secrets.token_urlsafe(18)
        SignupInvite.objects.create(
            digest=sha256(code.encode()).hexdigest(), expires_at=timezone.now() + timedelta(days=7),
        )
        self.stdout.write(code)
