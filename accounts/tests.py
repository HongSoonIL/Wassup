from datetime import UTC, datetime
from unittest.mock import patch
from urllib.parse import quote

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

    def test_signup_starts_a_session_at_the_feed_and_can_add_a_friend(self):
        csrf = Client(enforce_csrf_checks=True)
        self.assertRedirects(csrf.get('/'), '/feed/', fetch_redirect_response=False)
        self.assertRedirects(csrf.get('/feed/'), '/login/?next=/feed/')
        self.assertContains(csrf.get('/login/'), 'href="/signup/"')
        form = csrf.get('/signup/')
        self.assertTemplateUsed(form, 'accounts/signup.html')
        self.assertEqual(set(form.context['form'].fields), set(self.signup_data))
        self.assertEqual(csrf.post('/signup/', self.signup_data).status_code, 403)
        self.assertFalse(User.objects.exists())

        start = timezone.now()
        response = csrf.post('/signup/', {**self.signup_data, 'csrfmiddlewaretoken': csrf.cookies['csrftoken'].value})
        self.assertRedirects(response, '/feed/')
        user = User.objects.get()
        self.assertEqual(csrf.session[SESSION_KEY], str(user.pk))
        self.assertTrue(user.check_password(self.signup_data['password1']))
        self.assertEqual(user.birth_year, 1997)
        self.assertLessEqual(start, user.agreed_at)
        self.assertLessEqual(user.agreed_at, timezone.now())
        self.assertNotIn('birth_date', [field.name for field in User._meta.fields])
        self.assertFalse(user.friendships.exists())
        self.assertEqual(csrf.get('/api/feed/').json()['posts'], [])
        self.assertContains(csrf.get('/discover/'), 'data-friends-page="discover"')
        character = Character.objects.create(name='도현', age=23)
        added = csrf.post(
            '/api/friends/', {'character_id': character.pk}, content_type='application/json',
            HTTP_X_CSRFTOKEN=csrf.cookies['csrftoken'].value,
        )
        self.assertEqual(added.status_code, 201)
        self.assertContains(csrf.get('/home/'), 'data-friends-page="home"')
        room_id = added.json()['room_id']
        self.assertContains(csrf.get(f'/chat/{room_id}/'), f'data-room-id="{room_id}"')
        self.assertEqual(csrf.get(f'/api/characters/{character.pk}/profile/').json()['room_id'], room_id)
        self.assertFalse(csrf.get(f'/api/rooms/{room_id}/memories/').json()['enabled'])

    def test_eighteenth_birthday_starts_at_midnight_in_korea(self):
        birth_date = '2008-11-24'
        for hour, minute, eligible in [(14, 59, False), (15, 0, True)]:
            utc_now = datetime(2026, 11, 23, hour, minute, tzinfo=UTC)
            with self.subTest(now=utc_now), patch('django.utils.timezone.now', return_value=utc_now):
                response = self.client.post('/signup/', {**self.signup_data, 'birth_date': birth_date})
                if eligible:
                    self.assertRedirects(response, '/feed/', fetch_redirect_response=False)
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
            ('/messages/?room=42&filter=unread', '/messages/?room=42&filter=unread', True),
            ('https://testserver/discover/', 'https://testserver/discover/', True),
            ('https://elsewhere.example/', '/feed/', True),
            ('//elsewhere.example/path', '/feed/', False),
            ('https://testserver/messages/', '/feed/', False),
            ('http://testserver/messages/', '/feed/', True),
            ('https://testserver:9999/messages/', '/feed/', True),
        ]):
            username = f'visitor{index}'
            signup = {**self.signup_data, 'username': username, 'next': target}
            credentials = {'username': username, 'password': signup['password1'], 'next': target}
            for url, data in [('/signup/', signup), ('/login/', credentials)]:
                with self.subTest(url=url, target=target):
                    client = Client()
                    form = client.get(url, {'next': target}, secure=secure)
                    switch = '/signup/' if url == '/login/' else '/login/'
                    if destination == target:
                        self.assertEqual(form.context['next'], target)
                        self.assertContains(form, f'href="{switch}?next={quote(target)}"')
                    else:
                        self.assertEqual(form.context['next'], '')
                        self.assertContains(form, f'href="{switch}"')
                    response = client.post(url, data, secure=secure)
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response.url, destination)
                    self.assertIn(SESSION_KEY, client.session)

    def test_existing_account_defaults_to_feed_regardless_of_friendships(self):
        user = User.objects.create_user('existing', password='existing-password')
        other = User.objects.create_user('other')
        character = Character.objects.create(name='서윤', age=22)
        Friendship.objects.create(user=other, character=character)
        self.assertIsNone(user.birth_year)
        self.assertIsNone(user.agreed_at)
        self.assertRedirects(
            self.client.post('/login/', {'username': 'existing', 'password': 'existing-password'}),
            '/feed/',
        )
        self.assertRedirects(self.client.get('/'), '/feed/')
        Friendship.objects.create(user=user, character=character)
        self.assertRedirects(self.client.get('/'), '/feed/')

    def test_browser_pages_require_login_and_only_show_owned_profiles(self):
        character = Character.objects.create(name='도현', age=23)
        profile = f'/people/{character.pk}/'
        for url in ['/home/', '/messages/', '/me/', '/discover/', profile]:
            self.assertRedirects(self.client.get(url), '/login/?next=' + url)
        user = User.objects.create_user('first')
        self.client.force_login(user)
        Friendship.objects.create(user=User.objects.create_user('other'), character=character)
        self.assertEqual(self.client.get(profile).status_code, 404)
        Friendship.objects.create(user=user, character=character)
        for url, page, title in [
            ('/home/', 'home', '친구 홈'),
            ('/messages/', 'messages', '메시지'),
            ('/me/', 'me', '내 프로필'),
            ('/discover/', 'discover', '친구 찾기'),
            (profile, 'profile', '친구 프로필'),
        ]:
            with self.subTest(page=page):
                response = self.client.get(url)
                self.assertTemplateUsed(response, 'accounts/friends.html')
                self.assertContains(response, f'data-friends-page="{page}"')
                self.assertContains(response, f'<title>{title} · wassup</title>')
                self.assertNotContains(response, 'data-can-publish')
                if page == 'profile':
                    self.assertEqual(response.context['character_id'], character.pk)

    def test_profile_edit_is_session_scoped_and_preserves_other_account_fields(self):
        user = User.objects.create_user('owner')
        other = User.objects.create_user('someone', nickname='다른 사람')
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        response = client.get('/me/')
        self.assertEqual(response.context['my_profile']['nickname'], '')
        self.assertNotContains(response, other.nickname)
        payload = {'nickname': '민지', 'bio': '토요일엔 산책', 'id': other.pk, 'is_staff': '1'}
        self.assertEqual(client.post('/me/', payload).status_code, 403)
        payload['csrfmiddlewaretoken'] = client.cookies['csrftoken'].value
        self.assertRedirects(client.post('/me/', payload), '/me/')
        user.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((user.nickname, user.bio, user.is_staff), ('민지', '토요일엔 산책', False))
        self.assertEqual(other.nickname, '다른 사람')
        for invalid in ('a' * 81, '두 줄\n소개'):
            response = client.post('/me/', {**payload, 'bio': invalid})
            self.assertIn('bio', response.context['my_profile']['errors'])
            user.refresh_from_db()
            self.assertEqual(user.bio, '토요일엔 산책')
