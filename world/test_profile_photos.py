from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from chat.models import Friendship
from world.models import Character, ProfilePhoto


NOW = datetime(2026, 11, 19, 9, 0, tzinfo=timezone.utc)


class ProfilePhotoHistoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user('owner')
        cls.other = get_user_model().objects.create_user('other')
        cls.stranger = get_user_model().objects.create_user('stranger')
        cls.character = Character.objects.create(name='도현', age=23, major='경영학과 3학년')
        Friendship.objects.create(user=cls.owner, character=cls.character)
        Friendship.objects.create(user=cls.other, character=cls.character)
        cls.url = f'/api/characters/{cls.character.pk}/profile/'

    def change_profile(self, name, at):
        with patch('world.models.world_now', return_value=at):
            self.character.profile_image = f'characters/profile/{name}.webp'
            self.character.save()

    def profile(self, user):
        self.client.force_login(user)
        with patch('dashboard.views.world_now', return_value=NOW):
            return self.client.get(self.url)

    def test_changing_profile_keeps_every_photo(self):
        self.change_profile('first', NOW - timedelta(days=10))
        self.character.status_message = '알바 중'
        self.character.save()  # 프사가 그대로면 이력이 늘지 않는다
        self.change_profile('second', NOW - timedelta(days=2))

        names = list(self.character.profile_photos.values_list('image', flat=True))
        self.assertEqual(names, ['characters/profile/second.webp', 'characters/profile/first.webp'])

    def test_profile_lists_history_newest_first_and_marks_current(self):
        self.change_profile('first', NOW - timedelta(days=10))
        self.change_profile('second', NOW - timedelta(days=2))

        photos = self.profile(self.owner).json()['profile_photos']
        self.assertEqual([p['url'] for p in photos],
                         ['/media/characters/profile/second.webp', '/media/characters/profile/first.webp'])
        self.assertEqual([p['current'] for p in photos], [True, False])
        self.assertTrue(all(p['ai_generated'] for p in photos))

    def test_every_friend_sees_the_same_history(self):
        self.change_profile('first', NOW - timedelta(days=10))
        self.change_profile('second', NOW - timedelta(days=2))
        self.assertEqual(self.profile(self.owner).json()['profile_photos'],
                         self.profile(self.other).json()['profile_photos'])

    def test_photo_scheduled_for_later_stays_hidden(self):
        self.change_profile('first', NOW - timedelta(days=1))
        ProfilePhoto.objects.create(character=self.character, image='characters/profile/later.webp',
                                    set_at=NOW + timedelta(hours=3))
        photos = self.profile(self.owner).json()['profile_photos']
        self.assertEqual([p['url'] for p in photos], ['/media/characters/profile/first.webp'])

    def test_admin_shows_history_on_character_page(self):
        self.change_profile('first', NOW - timedelta(days=1))
        admin = get_user_model().objects.create_superuser('admin')
        self.client.force_login(admin)
        response = self.client.get(f'/admin/world/character/{self.character.pk}/change/')
        self.assertContains(response, '프사 이력')
        self.assertContains(response, 'characters/profile/first.webp')

    def test_only_friends_can_see_history(self):
        self.change_profile('first', NOW - timedelta(days=1))
        self.assertEqual(self.profile(self.stranger).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 401)
