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
from llm import photo
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

    def regenerate(self, rev, version):
        return self.ops.post(
            self.url + f'posts/{self.post.pk}/regenerate-photo/',
            {'expected_revision': rev, 'version': version},
            content_type='application/json',
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

    def test_studio_uses_kst_cadence_and_bookmarks_without_opening_a_week(self):
        self.now = datetime(2027, 4, 23, 17, 59, tzinfo=KST)
        self.assertContains(self.ops.get('/studio/'), 'data-week-start="2027-04-19"')
        self.now += timedelta(minutes=1)
        self.assertContains(self.ops.get('/studio/'), 'data-week-start="2027-04-26"')
        self.assertContains(self.ops.get('/studio/?week_start=2027-05-03'), 'data-week-start="2027-05-03"')
        for value in ('2027-05-04', '20270503', 'bad-date'):
            self.assertContains(self.ops.get('/studio/', {'week_start': value}), 'data-week-start="2027-04-26"')
        self.assertEqual(PublicationWeek.objects.count(), 1)

    def test_empty_ready_week_remains_available_for_review(self):
        empty = PublicationWeek.objects.create(start=date(2027, 5, 3), revision=1, generation='ready')
        url = f'/api/ops/weeks/{empty.pk}/'
        result = self.ops.get(url)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['posts'], [])
        self.assertEqual(result.json()['generation'], 'ready')
        response = self.ops.post(url + 'approve/', {'expected_revision': 1}, content_type='application/json')
        self.assertEqual((response.status_code, response.json()['code']), (409, 'no_drafts'))

    def test_edit_validates_scene_and_times_and_fences_both_revisions(self):
        for fields in (
            {'public_facts': '문장'}, {'public_facts': []}, {'public_facts': [' ']},
            {'public_facts': ['a'] * 6}, {'public_facts': ['x' * 501]},
            {'public_facts': ['시험 완료', ' 시험   완료 ']}, {'photo_alt': 'x' * 201},
            {'caption': ' '}, {'event_at': '2027-04-21T16:20'}, {'event_at': None},
            {'version': True},
        ):
            with self.subTest(fields=fields):
                self.assertEqual(self.edit(self.post.pk, 1, **fields).status_code, 400)
        late = self.edit(self.post.pk, 1, version=1, event_at=self.post.publish_at.isoformat())
        self.assertEqual(late.status_code, 422)
        self.assertIn('event_order', late.json()['posts'][0]['issues'])
        self.assertEqual(self.edit(self.post.pk, 1, version=9, caption='옛 버전').status_code, 409)
        self.assertEqual(self.edit(self.post.pk, 9, version=1, caption='옛 편집본').status_code, 409)
        event_at = self.post.event_at - timedelta(minutes=1)
        result = self.edit(
            self.post.pk, 1, version=1, event_at=event_at.isoformat(),
            public_facts=['  인쇄 후 포스터에 날짜를 적었다.  '], operator_note='운영자에게만 보이는 메모',
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['version'], 2)
        alt = self.post.photo_alt
        self.post.refresh_from_db()
        self.assertEqual(self.post.event_at, event_at)
        self.assertEqual(self.post.public_facts, ['인쇄 후 포스터에 날짜를 적었다.'])
        self.assertEqual(self.post.photo_alt, alt)
        self.assert_visible(0)

    def test_regenerate_requires_permission_csrf_and_exact_versions(self):
        url = self.url + f'posts/{self.post.pk}/regenerate-photo/'
        payload = {'expected_revision': 1, 'version': 1}
        self.assertEqual(Client().post(url, payload, content_type='application/json').status_code, 401)
        self.assertEqual(self.a.post(url, payload, content_type='application/json').status_code, 403)
        self.assertEqual(self.ops.get(url).status_code, 405)
        for data in ({'expected_revision': 1}, {'version': 1}, {**payload, 'version': True}, {**payload, 'scene': []}):
            self.assertEqual(self.ops.post(url, data, content_type='application/json').status_code, 400)
        self.assertEqual(self.regenerate(2, 1).status_code, 409)
        self.assertEqual(self.regenerate(1, 2).status_code, 409)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.staff)
        csrf.get('/studio/')
        self.assertEqual(csrf.post(url, payload, content_type='application/json').status_code, 403)
        response = csrf.post(
            url, payload, content_type='application/json', HTTP_X_CSRFTOKEN=csrf.cookies['csrftoken'].value
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['photo_job']['state'], 'queued')
        detail = self.ops.get(self.url).json()
        candidate = next(p for p in detail['posts'] if p['post_id'] == self.post.pk)
        self.assertEqual(candidate['photo_job'], response.json()['photo_job'])
        self.assert_visible(0)

    def test_owned_replacement_supersedes_inflight_photo_and_unblocks_approval(self):
        self.exclude_missing()
        queued = self.regenerate(3, 1)
        self.assertEqual(queued.status_code, 202)
        rev = queued.json()['revision']
        invalid = self.approve(rev)
        self.assertEqual(invalid.status_code, 422)
        self.assertIn('photo_queued', invalid.json()['posts'][0]['issues'])
        self.assertEqual(self.edit(self.post.pk, rev, version=1, photo_id='cafe-close').status_code, 400)
        replacement = PostPhoto.objects.get(pk='dubu-home')
        seen = []

        def during_generation(scene, model):
            blocked = self.approve(rev)
            seen.append(blocked.json()['posts'][0]['issues'])
            self.assertEqual(blocked.status_code, 422)
            saved = self.edit(self.post.pk, rev, version=1, photo_id=replacement.pk)
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(self.approve(saved.json()['revision']).status_code, 200)
            return photo.fake_image(scene)

        with patch('llm.photo.generate', side_effect=during_generation):
            call_command('run_photo_worker', once=True)
        self.assertEqual(seen, [['photo_generating']])
        self.post.refresh_from_db()
        self.assertEqual((self.post.photo_id, self.post.photo_alt), (replacement.pk, replacement.alt))
        self.assertEqual((self.post.state, self.post.version, self.post.approved_version), ('scheduled', 2, 2))
        self.assert_visible(0)

    def test_failed_photo_action_can_retry_edited_scene_privately(self):
        self.exclude_missing()
        queued = self.regenerate(3, 1).json()
        with patch('llm.photo.generate', side_effect=photo.PhotoError('검수용 실패')):
            call_command('run_photo_worker', once=True)
        detail = self.ops.get(self.url).json()
        candidate = next(p for p in detail['posts'] if p['post_id'] == self.post.pk)
        self.assertEqual(candidate['photo_job'], {'state': 'failed', 'detail': '검수용 실패'})
        blocked = self.approve(queued['revision'])
        self.assertEqual(blocked.status_code, 422)
        self.assertIn('photo_failed', blocked.json()['posts'][0]['issues'])
        attempt = self.regenerate(detail['revision'], candidate['version'])
        self.assertEqual(attempt.status_code, 202)
        changed = self.edit(
            self.post.pk, attempt.json()['revision'], version=1,
            photo_alt='벽에 붙인 포스터 두 장', public_facts=['시험 후 포스터를 벽에 붙였다.'],
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.regenerate(changed.json()['revision'], 1).status_code, 409)
        self.assertEqual(self.regenerate(changed.json()['revision'], 2).status_code, 202)
        with patch('llm.photo.generate', wraps=photo.generate) as generate:
            call_command('run_photo_worker', once=True)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(generate.call_args.args[0]['public_facts'], ['시험 후 포스터를 벽에 붙였다.'])
        self.post.refresh_from_db()
        self.assertEqual((self.post.photo_alt, self.post.version, self.post.state), ('벽에 붙인 포스터 두 장', 3, 'draft'))
        url = f'/api/ops/photos/{self.post.photo_id}/'
        self.assertEqual(self.ops.get(url).status_code, 200)
        self.assertEqual(self.a.get(url).status_code, 403)
        self.assert_visible(0)

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
