from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, current_thread, main_thread
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.db import close_old_connections
from django.test import Client, TransactionTestCase, override_settings

from chat.models import Friendship
from world.models import FeedPost, PostPhoto, PublicationWeek, SchoolEvent
from world.publication import ReviewError, approve, dispatch_publications

KST = ZoneInfo('Asia/Seoul')
START = datetime(2027, 4, 19, 9, tzinfo=KST)


@override_settings(LLM_BACKEND='fake', LLM_API_KEY='')
class PublicationTests(TransactionTestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        storage = patch.object(
            PostPhoto._meta.get_field('image'), 'storage', FileSystemStorage(location=folder.name)
        )
        storage.start()
        self.addCleanup(storage.stop)
        for command in ('seed_world', 'seed_publication'):
            call_command(command, stdout=StringIO())
        self.ops = Client()
        self.staff = get_user_model().objects.create_user('editor')
        self.staff.user_permissions.add(Permission.objects.get(codename='publish_week'))
        self.ops.force_login(self.staff)
        self.a, self.b, self.outsider = Client(), Client(), Client()
        for name, client in [('a', self.a), ('b', self.b), ('other', self.outsider)]:
            user = get_user_model().objects.create_user(name)
            client.force_login(user)
            if client is not self.outsider:
                Friendship.objects.create(
                    user=user, character=PostPhoto.objects.get(pk='studio-print').character
                )
        self.now = START
        for module in ('world.publication', 'dashboard.views'):
            clock = patch(module + '.world_now', side_effect=lambda: self.now)
            clock.start()
            self.addCleanup(clock.stop)
        generated = self.ops.post(
            '/api/ops/weeks/generate/', {'week_start': '2027-04-19'}, content_type='application/json'
        )
        self.assertEqual(generated.status_code, 202)
        self.url = f'/api/ops/weeks/{generated.json()["id"]}/'
        call_command('run_publication_worker', once=True)
        self.post = FeedPost.objects.get(photo_id='studio-print')
        self.sport = FeedPost.objects.get(events__kind='sports')
        self.missing = FeedPost.objects.get(photo=None, character=self.post.character)
        self.other_missing = FeedPost.objects.get(photo=None, character=self.sport.character)
        self.photo = f'/api/posts/{self.post.pk}/photo/'

    def edit(self, pk, rev, **data):
        return self.ops.patch(
            self.url + f'posts/{pk}/', {'expected_revision': rev, **data}, content_type='application/json'
        )

    def approve(self, rev):
        return self.ops.post(
            self.url + 'approve/', {'expected_revision': rev}, content_type='application/json'
        )

    def exclude_missing(self):
        self.assertEqual(self.edit(self.missing.pk, 1, state='skipped').status_code, 200)
        self.assertEqual(self.edit(self.other_missing.pk, 2, state='skipped').status_code, 200)

    def schedule(self):
        self.exclude_missing()
        self.assertEqual(self.approve(3).status_code, 200)

    def assert_visible(self, count):
        for client in (self.a, self.b):
            posts = client.get('/api/feed/').json()['posts']
            profile = client.get(f'/api/characters/{self.post.character_id}/profile/').json()
            self.assertEqual(profile['posts'], posts)
            self.assertEqual([p['id'] for p in posts], [self.post.pk] * count)
            photo = client.get(self.photo)
            self.assertEqual(photo.status_code, 200 if count else 404)
            if count:
                self.assertEqual(photo['Content-Type'], 'image/webp')
                self.assertIn('no-store', photo['Cache-Control'])
                self.assertTrue(b''.join(photo.streaming_content).startswith(b'RIFF'))
        self.assertEqual(self.outsider.get('/api/feed/').json()['posts'], [])
        self.assertEqual(self.outsider.get(self.photo).status_code, 404)

    def test_single_release_recover_and_replay(self):
        self.schedule()
        file = Path(self.post.photo.image.path)
        original = file.read_bytes()
        self.now = self.post.publish_at - timedelta(seconds=1)
        dispatch_publications()
        self.assert_visible(0)
        file.write_bytes(b'corrupt image')
        self.now = self.post.publish_at
        dispatch_publications()
        self.post.refresh_from_db()
        self.assertEqual(self.post.state, 'failed')
        self.assert_visible(0)
        self.now += timedelta(minutes=2)
        file.write_bytes(original)
        url = self.url + f'posts/{self.post.pk}/retry/'
        payload = {'expected_revision': 4, 'version': 1}
        for _ in range(2):
            self.assertEqual(self.ops.post(url, payload, content_type='application/json').status_code, 202)

        def worker(_):
            try:
                dispatch_publications(self.now)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(worker, range(2)))
        self.assertEqual(self.ops.post(url, payload, content_type='application/json').status_code, 200)
        self.assert_visible(1)
        self.post.refresh_from_db()
        self.assertEqual(self.post.published_at, self.now)

    def test_week_review_and_calendar_invalidation(self):
        self.assertEqual(self.approve(1).status_code, 422)
        self.assertFalse(FeedPost.objects.filter(state='scheduled').exists())
        self.schedule()
        self.assertEqual(self.edit(self.post.id, 2, caption='오래된 수정').status_code, 409)
        event = SchoolEvent.objects.get(kind='sports')
        event.start_date, event.end_date_exclusive = date(2027, 5, 1), date(2027, 5, 2)
        event.save()
        self.assertEqual(PublicationWeek.objects.get().revision, 5)
        self.sport.refresh_from_db()
        self.assertEqual(
            (self.sport.version, self.sport.state, self.sport.approved_version), (2, 'draft', None)
        )
        self.assertEqual(self.sport.versions.count(), 2)
        self.assertEqual(self.approve(5).status_code, 422)
        self.now = self.sport.publish_at
        dispatch_publications()
        self.assert_visible(1)
        self.sport.refresh_from_db()
        self.assertIsNone(self.sport.published_at)

    def test_permissions_and_week_reuse(self):
        self.assertEqual(Client().get('/api/feed/').status_code, 401)
        self.assertEqual(self.a.get(self.url).status_code, 403)
        self.assertEqual(self.a.get('/api/ops/photos/studio-print/').status_code, 403)
        self.assertEqual(
            self.a.post(
                self.url + 'approve/', {'expected_revision': 1}, content_type='application/json'
            ).status_code,
            403,
        )
        self.assertEqual(
            self.ops.post(
                '/api/ops/weeks/generate/', {'week_start': '9999-12-27'}, content_type='application/json'
            ).status_code,
            400,
        )
        result = self.ops.post(
            '/api/ops/weeks/generate/', {'week_start': '2027-04-19'}, content_type='application/json'
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(PublicationWeek.objects.count(), 1)
        self.assertEqual(FeedPost.objects.count(), 4)

    def test_concurrent_editors_approve_once(self):
        self.exclude_missing()

        def approve_current(_):
            try:
                approve(self.post.week_id, 3, self.staff)
                return 200
            except ReviewError as exc:
                return exc.status
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(approve_current, range(2))), [200, 409])
        self.assertEqual(FeedPost.objects.filter(state='scheduled').count(), 2)

    def test_photo_replacement_and_reapproval(self):
        self.schedule()
        self.assertEqual(self.edit(self.post.pk, 4, photo_id=self.sport.photo_id).status_code, 400)
        self.assertEqual(self.edit(self.post.pk, 4, caption='주황으로 정했다!').status_code, 200)
        self.now = self.post.publish_at
        dispatch_publications()
        self.assert_visible(0)
        later = self.now + timedelta(minutes=5)
        self.assertEqual(self.edit(self.post.pk, 5, publish_at=later.isoformat()).status_code, 200)
        self.assertEqual(self.approve(6).status_code, 200)
        self.now = later
        dispatch_publications()
        self.assert_visible(1)
        public = self.a.get('/api/feed/').json()['posts'][0]
        self.assertEqual((public['version'], public['caption']), (3, '주황으로 정했다!'))

    def test_late_failure_cannot_overwrite_a_new_retry(self):
        self.schedule()
        self.now = self.post.publish_at
        reading = Event()
        release = Event()

        def unavailable(*args):
            if current_thread() != main_thread():
                reading.set()
                if not release.wait(timeout=5):
                    raise AssertionError('worker did not finish retry setup')
            raise OSError('unavailable')

        def slow_worker():
            try:
                dispatch_publications(self.now)
            finally:
                close_old_connections()

        with patch('world.publication.Image.open', side_effect=unavailable), ThreadPoolExecutor() as pool:
            future = pool.submit(slow_worker)
            try:
                self.assertTrue(reading.wait(timeout=5))
                dispatch_publications(self.now)
                retry_url = self.url + f'posts/{self.post.pk}/retry/'
                response = self.ops.post(
                    retry_url, {'expected_revision': 4, 'version': 1}, content_type='application/json'
                )
                self.assertEqual(response.status_code, 202)
            finally:
                release.set()
            future.result()
        self.post.refresh_from_db()
        self.assertEqual((self.post.state, self.post.attempt), ('scheduled', 2))
        dispatch_publications(self.now)
        self.assert_visible(1)
