import base64
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.db import connection, connections
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image

from llm import photo
from world import photo_jobs as jobs, publication
from world.models import Character, FeedPost, PostPhoto, PublicationWeek
from world.publication_models import PhotoJob


def png():
    data = BytesIO()
    Image.new('RGB', (16, 16), '#d93').save(data, 'PNG')
    return data.getvalue()


@override_settings(LLM_BACKEND='openai', LLM_API_KEY='test-only')
class ImageAdapterTests(SimpleTestCase):
    def test_one_image_with_matching_local_references(self):
        model = 'gpt-image-2.6-flare'
        for character, alt, assets in [
            ('서윤', '서윤이 두부를 안고 찍은 셀카', [photo.PORTRAITS['서윤'], photo.PET_REFERENCE]),
            ('도현', '카페 유리창에 비친 옆모습', [photo.PORTRAITS['도현']]),
            ('서윤', '담요에 누운 고양이', [photo.PET_REFERENCE]),
            ('도현', '창가의 아이스커피 한 잔', []),
            (
                '도현',
                '학생식당 회색 테이블 위에 반쯤 접힌 연습장과 검은 샤프, 오른쪽 가장자리에 놓인 스테인리스 식판',
                [],
            ),
            ('서윤', '학생회관 탁자 위 서윤의 학생증 케이스와 두부 포장', []),
            ('도현', '카페 앞에 서 있는 남학생', [photo.PORTRAITS['도현']]),
        ]:
            with self.subTest(alt=alt):
                usage = {'input_tokens': 8, 'output_tokens': 17, 'input_tokens_details': {'image_tokens': 3}}
                body = {
                    'data': [{'b64_json': base64.b64encode(png()).decode()}],
                    'usage': usage,
                    'cost_usd': 0.01,
                }
                with patch('llm.photo.urlopen', return_value=BytesIO(json.dumps(body).encode())) as urlopen:
                    result = photo.generate(
                        {
                            'character': character,
                            'photo_alt': alt,
                            'operator_note': 'never-send-private-data',
                            'memories': ['never-send-private-data'],
                        },
                        model,
                    )
                urlopen.assert_called_once()
                request = urlopen.call_args.args[0]
                endpoint = 'edits' if assets else 'generations'
                self.assertEqual(request.full_url, f'https://api.openai.com/v1/images/{endpoint}')
                payload = json.loads(request.data)
                self.assertEqual(
                    (payload['n'], payload['model'], payload['output_format']), (1, model, 'webp')
                )
                self.assertEqual(urlopen.call_args.kwargs['timeout'], photo.REQUEST_TIMEOUT)
                self.assertIn(alt, payload['prompt'])
                self.assertNotIn('never-send-private-data', payload['prompt'])
                self.assertEqual(
                    [base64.b64decode(item['image_url'].split(',')[1]) for item in payload.get('images', [])],
                    [(settings.BASE_DIR / path).read_bytes() for path in assets],
                )
                with Image.open(BytesIO(result.image)) as image:
                    self.assertEqual(image.format, 'WEBP')
                self.assertEqual(result.usage, {**usage, 'cost_usd': 0.01})

    def test_corrupt_or_oversized_images_fail_even_on_http_success(self):
        scene = {'character': '도현', 'photo_alt': '커피'}
        for data in [b'secret-private-content', png()]:
            body = {'usage': {'total_tokens': 12}, 'data': [{'b64_json': base64.b64encode(data).decode()}]}
            with (
                patch('llm.photo.MAX_PIXELS', 128),
                patch('llm.photo.urlopen', return_value=BytesIO(json.dumps(body).encode())),
                self.assertRaises(photo.PhotoError) as error,
            ):
                photo.generate(scene, 'gpt-image-2.6-flare')
            self.assertEqual(error.exception.usage['total_tokens'], 12)
            self.assertNotIn('secret', str(error.exception))
        with patch('llm.photo.MAX_IMAGE_BYTES', 3), self.assertRaises(photo.PhotoError):
            photo.validated_image(png())


def in_thread(fn, *args):
    try:
        return fn(*args)
    finally:
        connections.close_all()


@override_settings(LLM_BACKEND='fake', LLM_API_KEY='', WASSUP_IMAGE_MODEL='gpt-image-2.6-flare')
class PhotoJobTests(TransactionTestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.storage = FileSystemStorage(location=tmp.name)
        self.enterContext(patch.object(PostPhoto._meta.get_field('image'), 'storage', self.storage))
        self.enterContext(
            patch('llm.photo.urlopen', side_effect=AssertionError('unexpected network request'))
        )
        self.week = PublicationWeek.objects.create(start=date(2030, 5, 6), generation='ready', revision=1)
        self.artist = Character.objects.create(name='서윤', age=22, major='시각디자인')
        self.instant = datetime(2030, 5, 6, 18, tzinfo=ZoneInfo('Asia/Seoul'))
        self.enterContext(patch('world.publication.world_now', return_value=self.instant - timedelta(days=1)))

    def post(self, **fields):
        return FeedPost.objects.create(
            **{
                'week': self.week,
                'character': self.artist,
                'caption': '점심시간에 찍었어',
                'public_facts': ['공책 옆에 연필을 두었다.'],
                'photo_alt': '펼친 공책 옆에 둔 연필',
                'operator_note': 'never-send-this',
                'event_at': self.instant,
                'publish_at': self.instant + timedelta(minutes=12),
                **fields,
            }
        )

    def test_six_initial_slots_across_concurrent_enqueues_and_restarts(self):
        posts = [self.post() for _ in range(9)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda p: in_thread(jobs.enqueue_initial, p), posts + posts))
        self.assertEqual(PhotoJob.objects.filter(initial=True).count(), 6)
        week2 = PublicationWeek.objects.create(start=date(2030, 5, 13), generation='ready', revision=1)
        jobs.enqueue_initial(self.post(week=week2))
        with patch('llm.photo.generate', wraps=photo.generate) as generate:
            call_command('run_photo_worker', once=True)
            self.assertEqual(generate.call_count, 6)
            call_command('run_photo_worker', once=True)
            call_command('run_photo_worker', once=True)
            self.assertEqual(generate.call_count, 7)
        self.assertEqual(PhotoJob.objects.filter(state='ready').count(), 7)
        self.assertEqual(PostPhoto.objects.count(), 7)
        self.week.refresh_from_db()
        self.assertEqual(self.week.revision, 7)

    def test_worker_race_single_request_snapshot_and_stale_approval(self):
        post = self.post()
        publication.snapshot(post)
        jobs.enqueue_initial(post)
        self.assertEqual(jobs.status_for(post)['state'], 'queued')
        entered, release = Event(), Event()

        def respond(scene, model):
            self.assertFalse(connection.in_atomic_block)
            self.assertNotIn('never-send-this', str(scene))
            entered.set()
            if not release.wait(5):
                raise AssertionError('concurrent worker timed out')
            return photo.fake_image(scene)

        with patch('llm.photo.generate', side_effect=respond) as generate, ThreadPoolExecutor() as pool:
            worker = pool.submit(in_thread, jobs.drain)
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(jobs.status_for(post)['state'], 'generating')
                jobs.drain()
                jobs.regenerate(self.week.pk, post.pk, 1, 1)
            finally:
                release.set()
            worker.result()
            generate.assert_called_once()
        post.refresh_from_db()
        self.week.refresh_from_db()
        self.assertEqual((post.version, post.state, self.week.revision), (2, 'draft', 2))
        self.assertEqual(
            (post.versions.get(version=2).snapshot['photo_id'], post.photo.alt),
            (post.photo_id, post.photo_alt),
        )
        self.assertTrue(post.photo.ai_generated)
        self.assertEqual(jobs.status_for(post)['state'], 'ready')
        self.assertEqual(self.client.get('/api/feed/').status_code, 401)
        self.client.force_login(get_user_model().objects.create(username='friend'))
        self.assertEqual(self.client.get(f'/api/ops/photos/{post.photo_id}/').status_code, 403)
        self.assertEqual(self.client.get(f'/api/posts/{post.pk}/photo/').status_code, 404)
        with self.assertRaises(publication.ReviewError) as error:
            publication.approve(self.week.pk, 1, None)
        self.assertEqual(error.exception.body['code'], 'week_changed')

    def test_scene_edit_skip_or_manual_replacement_during_request_discards_result(self):
        manual = PostPhoto.objects.create(
            key='manual', character=self.artist, alt='기존 사진', image='manual.webp'
        )
        for change in [{'caption': '새 글'}, {'state': 'skipped'}, {'photo_id': manual.pk}]:
            with self.subTest(change=change):
                post = self.post()
                jobs.enqueue_initial(post)

                def modify(scene, model):
                    self.week.refresh_from_db()
                    publication.edit(self.week.pk, post.pk, self.week.revision, change)
                    return photo.PhotoResult(png(), {'total_tokens': 12})

                with patch('llm.photo.generate', side_effect=modify):
                    jobs.drain()
                job = post.photo_jobs.get()
                self.assertEqual(
                    (job.state, job.result_id, job.usage), ('discarded', None, {'total_tokens': 12})
                )
                self.assertIsNotNone(job.elapsed_ms)
                self.assertEqual(list(self.root.rglob('*.webp')), [])
        self.assertEqual(PostPhoto.objects.count(), 1)

    def test_queued_snapshot_changed_before_claim_never_reaches_provider(self):
        post = self.post()
        jobs.enqueue_initial(post)
        FeedPost.objects.filter(pk=post.pk).update(photo_alt='책상에 세워 놓은 연필꽂이')
        post.refresh_from_db()
        self.assertEqual(jobs.status_for(post)['state'], 'idle')
        with patch('llm.photo.generate') as generate:
            jobs.drain()
            generate.assert_not_called()
        self.assertEqual(post.photo_jobs.get().state, 'discarded')

    def test_legacy_draft_without_direction_can_generate_from_public_facts(self):
        post = self.post(photo_alt='', event_at=None)
        jobs.regenerate(self.week.pk, post.pk, 1, 1)
        jobs.drain()
        post.refresh_from_db()
        self.assertEqual(post.photo_alt, post.public_facts[0])
        self.assertEqual(post.version, 2)

    def test_review_of_previous_photo_supersedes_inflight_replacement(self):
        post = self.post()
        jobs.enqueue_initial(post)
        jobs.drain()
        post.refresh_from_db()
        original = post.photo_id
        for revision, version, code in [(1, 2, 'week_changed'), (2, 1, 'stale_version')]:
            with self.assertRaises(publication.ReviewError) as error:
                jobs.regenerate(self.week.pk, post.pk, revision, version)
            self.assertEqual(error.exception.body['code'], code)
        jobs.regenerate(self.week.pk, post.pk, 2, 2)

        def approve_old(scene, model):
            with self.assertRaises(publication.ReviewError) as blocked:
                publication.approve(self.week.pk, 3, None)
            self.assertIn('photo_generating', blocked.exception.body['posts'][0]['issues'])
            publication.edit(self.week.pk, post.pk, 3, {'photo_id': original})
            publication.approve(self.week.pk, 4, None)
            return photo.fake_image(scene)

        with patch('llm.photo.generate', side_effect=approve_old):
            jobs.drain()
        post.refresh_from_db()
        self.assertEqual(
            (post.state, post.version, post.approved_version, post.photo_id), ('scheduled', 3, 3, original)
        )
        self.assertEqual(post.photo_jobs.latest('pk').state, 'discarded')
        self.assertTrue(self.storage.exists(post.photo.image.name))
        self.assertEqual(PostPhoto.objects.count(), 1)
        with self.assertRaises(publication.ReviewError):
            jobs.regenerate(self.week.pk, post.pk, 5, 3)

    def test_expired_orphan_requires_explicit_retry_and_late_completion_is_fenced(self):
        post = self.post()
        jobs.enqueue_initial(post)
        job = jobs.claim(post.photo_jobs.get().pk, self.week.pk)
        orphan = self.storage.save(jobs.filename(job), ContentFile(png()))
        PhotoJob.objects.filter(pk=job.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(jobs.status_for(post)['state'], 'failed')
        with patch('llm.photo.generate') as generate:
            jobs.drain()
            jobs.drain()
            generate.assert_not_called()
        self.assertFalse(self.storage.exists(orphan))
        self.assertFalse(PostPhoto.objects.exists())
        jobs.regenerate(self.week.pk, post.pk, 1, 1)
        jobs.finish(job.pk, self.week.pk, 100, {'total_tokens': 42}, image=png())
        self.assertFalse(PostPhoto.objects.exists())
        jobs.drain()
        post.refresh_from_db()
        self.assertEqual(jobs.status_for(post)['state'], 'ready')
        job.refresh_from_db()
        self.assertEqual((job.state, job.usage), ('failed', {'total_tokens': 42}))

    def test_storage_rollback_removes_file_and_retains_previous_photo(self):
        post = self.post()
        jobs.enqueue_initial(post)
        jobs.drain()
        post.refresh_from_db()
        original = post.photo
        jobs.regenerate(self.week.pk, post.pk, 2, 2)
        with (
            patch('world.publication.snapshot', side_effect=RuntimeError('write failed')),
            self.assertLogs('world.photo_jobs', level='ERROR'),
        ):
            jobs.drain()
        post.refresh_from_db()
        self.assertEqual((post.photo_id, post.version), (original.pk, 2))
        self.assertEqual(jobs.status_for(post)['state'], 'failed')
        self.assertEqual(set(self.root.rglob('*.webp')), {Path(original.image.path)})
        self.assertEqual(PostPhoto.objects.count(), 1)
        jobs.regenerate(self.week.pk, post.pk, 3, 2)
        jobs.drain()
        post.refresh_from_db()
        self.assertNotEqual(post.photo_id, original.pk)
        self.assertTrue(self.storage.exists(original.image.name))
        self.assertEqual(post.version, 3)
        self.assertIsNone(post.approved_version)

    @override_settings(LLM_BACKEND='openai', LLM_API_KEY='test-key')
    def test_real_provider_rejection_is_visible_with_no_decoy_or_automatic_retry(self):
        post = self.post()
        jobs.enqueue_initial(post)
        denied = HTTPError(
            'https://api.openai.com', 404, 'private', {}, BytesIO(b'{"error":{"code":"model_not_found"}}')
        )
        with patch('llm.photo.urlopen', side_effect=denied) as request:
            jobs.drain()
            jobs.drain()
            request.assert_called_once()
        status = jobs.status_for(post)
        self.assertEqual(status['state'], 'failed')
        self.assertIn('gpt-image-2.6-flare', status['detail'])
        self.assertIn('model_not_found', status['detail'])
        self.assertFalse(PostPhoto.objects.exists())
        self.assertFalse(list(self.root.rglob('*.webp')))
