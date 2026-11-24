from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import SignupInvite, User


@override_settings(INVITE_ONLY=True, PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class InviteSignupTests(TestCase):
    def setUp(self):
        output = StringIO()
        call_command('create_invite', stdout=output)
        self.code = output.getvalue().strip()
        self.signup = {
            'username': 'member', 'password1': 'Quartz-token-874', 'password2': 'Quartz-token-874',
            'agree': 'on', 'birth_date': '1999-02-11', 'invite_code': self.code,
        }

    def test_invalid_signup_preserves_code_and_valid_signup_consumes_it_once(self):
        self.assertContains(self.client.get('/signup/'), '초대 코드')
        for changes in [{'invite_code': ''}, {'invite_code': 'unknown'}, {'password2': 'mismatch'}]:
            self.assertEqual(self.client.post('/signup/', {**self.signup, **changes}).status_code, 200)
        self.assertFalse(User.objects.exists())
        self.assertIsNone(SignupInvite.objects.get().redeemed_at)
        self.assertEqual(self.client.post('/signup/', self.signup).status_code, 302)
        self.client.logout()
        retry = self.client.post('/signup/', {**self.signup, 'username': 'other'})
        self.assertContains(retry, '사용할 수 없는 초대 코드')
        self.assertEqual(User.objects.count(), 1)

    def test_expired_code(self):
        SignupInvite.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertContains(self.client.post('/signup/', self.signup), '사용할 수 없는 초대 코드')
        self.assertFalse(User.objects.exists())
