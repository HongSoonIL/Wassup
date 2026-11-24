from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.test import TestCase
from django.utils import timezone

from chat.models import Friendship
from world.models import Character, FeedPost, PostPhoto, PublicationWeek


class PreviousPostApiTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user = get_user_model().objects.create_user('reader')
        self.person = Character.objects.create(name='서윤', age=22)
        self.room = Friendship.objects.create(user=self.user, character=self.person)
        self.client.force_login(self.user)
        self.week = PublicationWeek.objects.create(start=timezone.localdate())
        storage = FileSystemStorage(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.object(PostPhoto._meta.get_field('image'), 'storage', storage))
        self.photo = PostPhoto.objects.create(character=self.person, key='proof', alt='포스터 시안',
                                              image=storage.save('proof.webp', ContentFile(b'photo')))
        self.old = self.post('군청이랑 주황 둘 다 뽑아 봄', 8)
        self.new = self.post('드디어 붙였다', 1, previous_post=self.old, previous_version=1)
        self.url = f'/api/posts/{self.new.pk}/previous/?version=1'

    def post(self, caption, days_old, **fields):
        at = self.now - timedelta(days=days_old)
        return FeedPost.objects.create(character=self.person, week=self.week, publish_at=at, published_at=at,
                                       state='published', photo=self.photo, photo_alt=self.photo.alt,
                                       caption=caption, **fields)

    def test_link_resolves_off_page_in_feed_and_profile_without_exposing_private_fields(self):
        for i in range(30):
            self.post(str(i), 3)
        summary = self.client.get(self.url).json()['previous_post']
        self.assertEqual(set(summary), {'id', 'version', 'caption', 'published_at', 'photo'})
        self.assertEqual(summary['photo'], {'url': f'/api/posts/{self.old.pk}/photo/?version=1', 'alt': self.photo.alt})
        self.assertEqual(summary['caption'], self.old.caption)
        for url in ['/api/feed/', f'/api/characters/{self.person.pk}/profile/']:
            posts = self.client.get(url).json()['posts']
            self.assertEqual(posts[0]['previous_post'], summary)
            self.assertIsNone(posts[1]['previous_post'])
            self.assertNotIn(self.old.pk, [p['id'] for p in posts])

    def test_edits_withdrawal_order_and_wrong_owner_hide_reference(self):
        other = Character.objects.create(name='도현', age=23)
        for changes in [{'state': 'skipped'}, {'version': 2}, {'published_at': self.now},
                        {'character_id': other.pk}, {'photo_id': None}]:
            with self.subTest(changes=changes):
                FeedPost.objects.filter(pk=self.old.pk).update(**changes)
                response = self.client.get(self.url)
                self.assertEqual(response.json(), {'previous_post': None})
                self.assertIn('no-store', response['Cache-Control'])
                self.old.save()
        self.photo.image.storage.delete(self.photo.image.name)
        self.assertIsNone(self.client.get(self.url).json()['previous_post'])

    def test_both_ends_and_reader_are_checked_again_on_expand(self):
        FeedPost.objects.filter(pk=self.new.pk).update(version=2)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.new.save()
        self.room.delete()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 401)
