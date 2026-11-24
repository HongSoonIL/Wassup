from datetime import datetime, timedelta
from shutil import copyfile
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.storage import FileSystemStorage
from django.test import Client, TestCase

from world.models import Character, FeedPost, PostPhoto, PublicationWeek
from world.publication import dispatch_publications


NOW = datetime(2027, 4, 19, 9, tzinfo=ZoneInfo('Asia/Seoul'))


class StudioPreviousTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.editor = get_user_model().objects.create_user('editor')
        cls.editor.user_permissions.add(Permission.objects.get(codename='publish_week'))
        cls.character = Character.objects.create(name='서윤', age=22, major='디자인')
        cls.photo = PostPhoto.objects.create(
            key='poster', character=cls.character, image='poster.webp', alt='주황색 포스터 두 장',
        )
        cls.week = PublicationWeek.objects.create(start=NOW.date(), generation='ready', revision=1)
        cls.draft = cls.week.posts.create(
            character=cls.character, photo=cls.photo, photo_alt='벽에 붙인 주황색 포스터',
            caption='드디어 걸었다', public_facts=['최종본을 벽에 걸었다.'],
            event_at=NOW + timedelta(hours=2), publish_at=NOW + timedelta(hours=3),
        )
        cls.previous = cls.make_history(NOW.replace(hour=0) - timedelta(weeks=4))

    @classmethod
    def make_history(cls, published_at, **overrides):
        week, _ = PublicationWeek.objects.get_or_create(start=published_at.date())
        return week.posts.create(**{
            'character': cls.character, 'photo': cls.photo, 'photo_alt': cls.photo.alt,
            'caption': '주황색으로 골랐다', 'public_facts': ['포스터 색으로 주황을 골랐다.'],
            'publish_at': published_at, 'published_at': published_at, 'state': 'published', 'version': 3,
            **overrides,
        })

    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        storage = FileSystemStorage(location=folder)
        copyfile(settings.BASE_DIR / 'frontend/src/design/assets/studio-print.webp', storage.path('poster.webp'))
        self.enterContext(patch.object(PostPhoto._meta.get_field('image'), 'storage', storage))
        for module in ('world.publication', 'dashboard.views'):
            self.enterContext(patch(f'{module}.world_now', return_value=NOW))
        self.client.force_login(self.editor)
        self.url = f'/api/ops/weeks/{self.week.pk}/'

    def patch_post(self, revision, version, **changes):
        return self.client.patch(
            f'{self.url}posts/{self.draft.pk}/',
            {'expected_revision': revision, 'version': version, **changes}, content_type='application/json',
        )

    def select(self):
        return self.patch_post(1, 1, previous_post={'id': self.previous.pk, 'version': self.previous.version})

    def test_studio_lists_only_eligible_posts_and_pins_edits(self):
        other = Character.objects.create(name='도현', age=22, major='경영')
        other_photo = PostPhoto.objects.create(key='cafe', character=other, image='cafe.webp', alt='카페 창가')
        preceding = NOW - timedelta(days=2)
        invalid = [
            self.make_history(preceding, character=other, photo=other_photo),
            self.make_history(preceding, state='scheduled'),
            self.make_history(preceding, photo=None),
            self.make_history(self.previous.published_at - timedelta(microseconds=1)),
            self.make_history(NOW.replace(hour=0)),
        ]
        initial = next(p for p in self.client.get(self.url).json()['posts'] if p['post_id'] == self.draft.pk)
        self.assertIsNone(initial['previous_post'])
        summary, = initial['previous_candidates']
        self.assertEqual(summary, {
            'id': self.previous.pk, 'version': 3, 'caption': self.previous.caption,
            'published_at': self.previous.published_at.isoformat(timespec='seconds'),
            'photo': {'url': '/api/ops/photos/poster/', 'alt': self.previous.photo_alt},
        })
        self.assertEqual(self.client.get(summary['photo']['url']).status_code, 200)
        self.assertEqual(Client().get(summary['photo']['url']).status_code, 401)
        self.assertEqual(self.client.get(f'/api/posts/{self.previous.pk}/photo/').status_code, 404)

        for post in [*invalid, self.draft]:
            response = self.patch_post(1, 1, previous_post={'id': post.pk, 'version': post.version})
            self.assertEqual((response.status_code, response.json()['code']), (422, 'invalid_previous_post'))
        self.assertEqual(self.patch_post(1, 1, previous_post={'id': self.previous.pk, 'version': 2}).status_code, 422)
        self.assertEqual(self.patch_post(1, 1, previous_post={'id': self.previous.pk, 'version': True}).status_code, 400)
        self.assertEqual(self.select().status_code, 200)
        self.assertEqual(self.patch_post(2, 2, caption='이제 벽에 걸었다').status_code, 200)
        self.draft.refresh_from_db()
        self.assertEqual((self.draft.previous_post_id, self.draft.previous_version), (self.previous.pk, 3))
        self.assertEqual(self.draft.versions.get(version=3).snapshot['previous_version'], 3)
        chosen = next(p for p in self.client.get(self.url).json()['posts'] if p['post_id'] == self.draft.pk)
        self.assertEqual(chosen['previous_post'], summary)
        self.assertEqual(self.patch_post(3, 2, previous_post=None).status_code, 409)
        self.assertEqual(self.patch_post(3, 3, previous_post=None).status_code, 200)
        self.draft.refresh_from_db()
        self.assertEqual((self.draft.previous_post_id, self.draft.previous_version), (None, None))

    def approve_week(self):
        return self.client.post(f'{self.url}approve/', {'expected_revision': 2}, content_type='application/json')

    def test_stale_selection_is_removed_at_approval(self):
        self.assertEqual(self.select().status_code, 200)
        FeedPost.objects.filter(pk=self.previous.pk).update(version=4)
        approved = self.approve_week()
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()['scheduled'], [{'post_id': self.draft.pk, 'version': 3}])
        self.draft.refresh_from_db()
        self.assertEqual((self.draft.previous_post_id, self.draft.previous_version), (None, None))
        self.assertIsNone(self.draft.versions.get(version=3).snapshot['previous_post_id'])

    def test_stale_selection_is_removed_at_dispatch_without_blocking_publication(self):
        self.assertEqual(self.select().status_code, 200)
        self.assertEqual(self.approve_week().status_code, 200)
        FeedPost.objects.filter(pk=self.previous.pk).update(state='skipped')
        dispatch_publications(self.draft.publish_at)
        self.draft.refresh_from_db()
        self.assertEqual((self.draft.state, self.draft.previous_post_id, self.draft.previous_version),
                         ('published', None, None))
        self.assertEqual(self.draft.versions.get(version=2).snapshot['previous_post_id'], self.previous.pk)
