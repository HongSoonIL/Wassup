from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import SESSION_KEY
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from chat.models import Friendship
from world.models import Character
from .models import User


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AccountEntryTests(TestCase):
    signup_data = {
        'username': 'new-user',
        'password1': 'Apricot-Window-472!',
        'password2': 'Apricot-Window-472!',
        'birth_date': '1997-08-15',
        'agree': 'on',
    }

    def test_signup_starts_a_session_and_routes_to_first_friend(self):
        csrf = Client(enforce_csrf_checks=True)
        self.assertRedirects(csrf.get('/'), '/login/')
        self.assertContains(csrf.get('/login/'), 'href="/signup/"')
        form = csrf.get('/signup/')
        self.assertTemplateUsed(form, 'accounts/signup.html')
        self.assertEqual(set(form.context['form'].fields), set(self.signup_data))
        self.assertEqual(csrf.post('/signup/', self.signup_data).status_code, 403)
        self.assertFalse(User.objects.exists())

        start = timezone.now()
        response = csrf.post('/signup/', {**self.signup_data, 'csrfmiddlewaretoken': csrf.cookies['csrftoken'].value})
        self.assertRedirects(response, '/', fetch_redirect_response=False)
        user = User.objects.get()
        self.assertEqual(csrf.session[SESSION_KEY], str(user.pk))
        self.assertTrue(user.check_password(self.signup_data['password1']))
        self.assertEqual(user.birth_year, 1997)
        self.assertLessEqual(start, user.agreed_at)
        self.assertLessEqual(user.agreed_at, timezone.now())
        self.assertNotIn('birth_date', [field.name for field in User._meta.fields])
        self.assertFalse(user.friendships.exists())
        self.assertRedirects(csrf.get('/'), '/discover/')
        character = Character.objects.create(name='도현', age=23)
        added = csrf.post(
            '/api/friends/', {'character_id': character.pk}, content_type='application/json',
            HTTP_X_CSRFTOKEN=csrf.cookies['csrftoken'].value,
        )
        self.assertEqual(added.status_code, 201)
        self.assertRedirects(csrf.get('/'), '/home/')
        room_id = added.json()['room_id']
        self.assertEqual(csrf.get(f'/api/characters/{character.pk}/profile/').json()['room_id'], room_id)
        self.assertFalse(csrf.get(f'/api/rooms/{room_id}/memories/').json()['enabled'])

    def test_eighteenth_birthday_starts_at_midnight_in_korea(self):
        birth_date = '2008-11-24'
        for hour, minute, eligible in [(14, 59, False), (15, 0, True)]:
            utc_now = datetime(2026, 11, 23, hour, minute, tzinfo=UTC)
            with self.subTest(now=utc_now), patch('django.utils.timezone.now', return_value=utc_now):
                response = self.client.post('/signup/', {**self.signup_data, 'birth_date': birth_date})
                if eligible:
                    self.assertRedirects(response, '/', fetch_redirect_response=False)
                    self.assertEqual(User.objects.get().birth_year, 2008)
                else:
                    self.assertIn('birth_date', response.context['form'].errors)
                    self.assertFalse(User.objects.exists())

    def test_signup_rejects_invalid_details_without_opening_a_session(self):
        User.objects.create_user('existing')
        for changes, field in [
            ({'username': 'existing'}, 'username'),
            ({'birth_date': '2000-02-31'}, 'birth_date'),
            ({'birth_date': ''}, 'birth_date'),
            ({'birth_date': '2999-01-01'}, 'birth_date'),
            ({'agree': ''}, 'agree'),
            ({'agree': 'off'}, 'agree'),
            ({'agree': '0'}, 'agree'),
            ({'password2': 'a-different-password'}, 'password2'),
            ({'password1': '123', 'password2': '123'}, 'password2'),
        ]:
            with self.subTest(field=field, changes=changes):
                response = self.client.post('/signup/?next=/feed/', {**self.signup_data, **changes})
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context['form'].errors)
                self.assertEqual(response.context['next'], '/feed/')
                self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(User.objects.count(), 1)

    def test_login_and_signup_only_return_to_same_origin(self):
        for index, (target, destination, secure) in enumerate([
            ('/feed/?cursor=abc', '/feed/?cursor=abc', True),
            ('https://testserver/discover/', 'https://testserver/discover/', True),
            ('https://elsewhere.example/', '/', True),
            ('//elsewhere.example/path', '/', False),
            ('https://testserver/feed/', '/', False),
            ('http://testserver/feed/', '/', True),
            ('https://testserver:9999/feed/', '/', True),
        ]):
            username = f'visitor{index}'
            signup = {**self.signup_data, 'username': username, 'next': target}
            credentials = {'username': username, 'password': signup['password1'], 'next': target}
            for url, data in [('/signup/', signup), ('/login/', credentials)]:
                with self.subTest(url=url, target=target):
                    client = Client()
                    form = client.get(url, {'next': target}, secure=secure)
                    self.assertEqual(form.context['next'], '' if destination == '/' else target)
                    response = client.post(url, data, secure=secure)
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response.url, destination)
                    self.assertIn(SESSION_KEY, client.session)

    def test_existing_account_can_log_in_and_route_by_owned_rooms(self):
        user = User.objects.create_user('existing', password='existing-password')
        other = User.objects.create_user('other')
        character = Character.objects.create(name='서윤', age=22)
        Friendship.objects.create(user=other, character=character)
        self.assertIsNone(user.birth_year)
        self.assertIsNone(user.agreed_at)
        self.assertRedirects(
            self.client.post('/login/', {'username': 'existing', 'password': 'existing-password'}),
            '/', fetch_redirect_response=False,
        )
        self.assertRedirects(self.client.get('/'), '/discover/')
        Friendship.objects.create(user=user, character=character)
        self.assertRedirects(self.client.get('/'), '/home/')

    def test_browser_pages_require_login_and_only_show_owned_profiles(self):
        character = Character.objects.create(name='도현', age=23)
        profile = f'/people/{character.pk}/'
        for url in ['/home/', '/discover/', profile]:
            self.assertRedirects(self.client.get(url), '/login/?next=' + url)
        user = User.objects.create_user('first')
        self.client.force_login(user)
        Friendship.objects.create(user=User.objects.create_user('other'), character=character)
        self.assertEqual(self.client.get(profile).status_code, 404)
        Friendship.objects.create(user=user, character=character)
        for url, page in [('/home/', 'home'), ('/discover/', 'discover'), (profile, 'profile')]:
            with self.subTest(page=page):
                response = self.client.get(url)
                self.assertTemplateUsed(response, 'accounts/friends.html')
                self.assertEqual(response.context['page'], page)
                if page == 'profile':
                    self.assertEqual(response.context['character_id'], character.pk)
