from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.core import signing
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections, transaction
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image

from chat.models import Friendship, PersonalPostReaction
from world.models import Character, FeedPost, PostPhoto, PublicationWeek
from .models import PersonalPost, User
from .private_photos import MAX_PHOTO_BYTES, normalize_photo, private_storage


def sample_photo(kind='JPEG'):
    stream = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = 'private-author'
    Image.new('RGB', (12, 8), 'tomato').save(stream, kind, exif=exif)
    return SimpleUploadedFile('untrusted.html', stream.getvalue(), content_type='text/html')


class PhotoNormalizationTests(SimpleTestCase):
    def test_decodes_three_formats_and_removes_metadata_filename_and_orientation(self):
        for kind, extension in [('JPEG', 'jpg'), ('PNG', 'png'), ('WEBP', 'webp')]:
            with self.subTest(kind=kind):
                clean = normalize_photo(sample_photo(kind))
                self.assertRegex(clean.name, rf'^[0-9a-f]{{32}}\.{extension}$')
                data = clean.read()
                self.assertNotIn(b'private-author', data)
                with Image.open(BytesIO(data)) as image:
                    self.assertEqual((image.format, image.size), (kind, (8, 12)))
                    self.assertFalse(image.getexif())

    def test_rejects_oversized_unsupported_malformed_and_truncated_uploads(self):
        gif = BytesIO()
        Image.new('RGB', (4, 4)).save(gif, 'GIF')
        jpeg = sample_photo().read()
        for content in (b'X' * (MAX_PHOTO_BYTES + 1), gif.getvalue(), b'<svg/>', jpeg[:-12]):
            with self.subTest(size=len(content)), self.assertRaises(ValidationError):
                normalize_photo(SimpleUploadedFile('photo.jpg', content, content_type='image/jpeg'))
        boundary = jpeg.ljust(MAX_PHOTO_BYTES, b' ')
        self.assertLess(normalize_photo(SimpleUploadedFile('large.jpg', boundary)).size, MAX_PHOTO_BYTES)

    def test_public_media_cannot_be_used_as_private_storage(self):
        with override_settings(PRIVATE_PHOTO_ROOT='/tmp/media/private', MEDIA_ROOT='/tmp/media'):
            with self.assertRaises(ImproperlyConfigured):
                private_storage.path('test.png')
        with self.assertRaises(ValueError):
            private_storage.url('test.png')


class PersonalPostApiTests(TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.private = root / 'private'
        self.public = root / 'public'
        self.enterContext(override_settings(PRIVATE_PHOTO_ROOT=self.private, MEDIA_ROOT=self.public))
        self.owner = User.objects.create_user('owner')
        self.other = User.objects.create_user('other')
        self.client.force_login(self.owner)
        self.url = '/api/me/posts/'

    def create(self, **data):
        response = self.client.post(self.url, data, **({} if 'photo' in data else {'content_type': 'application/json'}))
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()['post']

    def test_text_photo_and_combined_posts_are_owner_only(self):
        text = self.create(caption='  산책했어요. ')
        self.assertEqual(set(text), {'kind', 'id', 'caption', 'published_at', 'photo'})
        self.assertEqual((text['kind'], text['caption'], text['photo']), ('personal', '산책했어요.', None))
        photo = self.create(photo=sample_photo())
        both = self.create(caption='오늘 찍은 하늘', photo=sample_photo('PNG'))
        self.assertEqual(photo['caption'], '')
        self.assertEqual(photo['photo'], {
            'url': f"/api/me/posts/{photo['id']}/photo/", 'alt': '내가 올린 사진', 'ai_generated': False,
        })
        for endpoint in (self.url, '/api/feed/'):
            response = self.client.get(endpoint)
            self.assertIn('no-store', response['Cache-Control'])
            self.assertEqual(response.json(), {'posts': [both, photo, text], 'next_cursor': None})
        response = self.client.get(photo['photo']['url'])
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        with Image.open(BytesIO(b''.join(response.streaming_content))) as image:
            self.assertEqual(image.size, (8, 12))
        stored = PersonalPost.objects.get(pk=photo['id']).photo
        self.assertTrue(Path(stored.path).is_relative_to(self.private))
        self.assertFalse(self.public.exists())
        self.assertEqual(self.client.get('/media/' + stored.name).status_code, 404)
        self.assertEqual(self.client.get(f"{self.url}{text['id']}/photo/").status_code, 404)
        self.client.force_login(self.other)
        for endpoint in (self.url, '/api/feed/'):
            self.assertEqual(self.client.get(endpoint).json()['posts'], [])
        self.assertEqual(self.client.get(photo['photo']['url']).status_code, 404)
        self.assertEqual(self.client.delete(f"{self.url}{photo['id']}/").status_code, 404)
        self.assertTrue(Path(stored.path).exists())
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(self.client.post(self.url, {'caption': 'hello'}).status_code, 401)
        self.assertEqual(self.client.get(photo['photo']['url']).status_code, 401)
        self.assertEqual(self.client.delete(f"{self.url}{photo['id']}/").status_code, 401)

    def test_invalid_creation_and_csrf(self):
        for data in ({}, [], {'caption': 20}, {'caption': ' \n '}, {'caption': '\x00'},
                     {'caption': '\udfff'}, {'caption': 'a' * 4001}, {'caption': 'a', 'owner': self.other.pk},
                     {'photo': 'https://example.org/image.jpg'}):
            with self.subTest(data_type=type(data)):
                self.assertEqual(self.client.post(self.url, data, content_type='application/json').status_code, 400)
        for data in ({'photo': SimpleUploadedFile('bad.jpg', b'bad')}, {'photo': [sample_photo(), sample_photo()]}):
            self.assertEqual(self.client.post(self.url, data).status_code, 400)
        self.assertFalse(PersonalPost.objects.exists())
        self.assertFalse(self.private.exists())
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.owner)
        csrf.get(self.url)
        self.assertEqual(csrf.post(self.url, {'caption': 'a'}).status_code, 403)
        response = csrf.post(self.url, {'caption': 'a'}, HTTP_X_CSRFTOKEN=csrf.cookies['csrftoken'].value)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(csrf.delete(f"{self.url}{response.json()['post']['id']}/").status_code, 403)

    def test_delete_cascade_and_rollback_cleanup_files_and_unsent_reactions(self):
        dto = self.create(photo=sample_photo())
        post = PersonalPost.objects.get(pk=dto['id'])
        path = Path(post.photo.path)
        PersonalPostReaction.objects.get_or_create(post=post)
        with self.captureOnCommitCallbacks(execute=True), transaction.atomic():
            PersonalPost.objects.only('pk').get(pk=post.pk).delete()
            self.assertTrue(path.exists())
            transaction.set_rollback(True)
        self.assertTrue(PersonalPost.objects.filter(pk=dto['id']).exists())
        self.assertTrue(path.exists())
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.delete(f"{self.url}{dto['id']}/").status_code, 204)
            self.assertTrue(path.exists())
            self.assertFalse(PersonalPostReaction.objects.filter(post_id=dto['id']).exists())
        self.assertFalse(path.exists())
        self.assertEqual(self.client.get(dto['photo']['url']).status_code, 404)
        self.assertEqual(self.client.get('/api/feed/').json()['posts'], [])
        other_dto = self.create(photo=sample_photo())
        other_path = Path(PersonalPost.objects.get(pk=other_dto['id']).photo.path)
        with self.captureOnCommitCallbacks(execute=True):
            self.owner.delete()
        self.assertFalse(other_path.exists())

    def test_failed_insert_discards_normalized_file(self):
        with patch.object(PersonalPost, '_do_insert', side_effect=RuntimeError), self.assertRaises(RuntimeError):
            self.client.post(self.url, {'photo': sample_photo()})
        self.assertFalse(PersonalPost.objects.exists())
        self.assertFalse(any(path.is_file() for path in self.private.rglob('*')))

    def test_mixed_pagination_preserves_ties_authorization_and_legacy_cursor(self):
        at = timezone.now() - timedelta(hours=1)
        friend = Character.objects.create(name='도현', age=23)
        Friendship.objects.create(user=self.owner, character=friend)
        week = PublicationWeek.objects.create(start=at.date())
        photo = PostPhoto.objects.create(key='cover', character=friend, image='cover.jpg')
        # Explicit overlapping ids exercise the tie-break across the two independent tables.
        for pk in range(1, 36):
            FeedPost.objects.create(pk=pk, character=friend, week=week, publish_at=at, published_at=at,
                                    state='published', caption='공개 글', photo=photo)
            PersonalPost.objects.create(pk=pk, owner=self.owner, caption='내 글')
        PersonalPost.objects.update(created_at=at)
        PersonalPost.objects.filter(pk=1).update(created_at=at + timedelta(minutes=1))
        PersonalPost.objects.create(owner=self.other, id=1001, caption='다른 사람 글')
        expected = [('personal', 1)]
        for pk in range(35, 1, -1):
            expected.extend([('personal', pk), ('character', pk)])
        expected.append(('character', 1))

        def collect(endpoint):
            cursor = None
            rows = []
            while True:
                page = self.client.get(endpoint, {'cursor': cursor} if cursor else {}).json()
                rows.extend((item['kind'], item['id']) for item in page['posts'])
                cursor = page['next_cursor']
                if cursor is None:
                    return rows
                self.assertEqual(len(page['posts']), 30)
                self.assertLessEqual(len(rows), len(expected))

        self.assertEqual(collect('/api/feed/'), expected)
        self.assertEqual(collect(self.url), [key for key in expected if key[0] == 'personal'])
        profile = f'/api/characters/{friend.pk}/profile/'
        self.assertEqual(collect(profile), [key for key in expected if key[0] == 'character'])
        legacy = signing.dumps([at.isoformat(), 10], salt='feed-page')
        tail = expected[expected.index(('character', 10)) + 1:]
        data = self.client.get('/api/feed/', {'cursor': legacy}).json()
        self.assertEqual([(item['kind'], item['id']) for item in data['posts']], tail)
        for endpoint in ('/api/feed/', self.url, profile):
            self.assertEqual(self.client.get(endpoint, {'cursor': 'broken'}).status_code, 400)


class PersonalPostRetryTests(TransactionTestCase):
    def test_concurrent_retries_return_one_receipt_and_file_per_owner(self):
        root = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.enterContext(override_settings(PRIVATE_PHOTO_ROOT=root))
        owner = User.objects.create_user('posting')
        headers = {'HTTP_IDEMPOTENCY_KEY': str(uuid4())}
        gate = Barrier(2, timeout=10)
        url = '/api/me/posts/'

        def submit_photo():
            try:
                client = Client()
                client.force_login(owner)
                upload = sample_photo()
                gate.wait()
                response = client.post(url, {'caption': '산책', 'photo': upload}, **headers)
                return response.status_code, response.json()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit_photo) for _ in range(2)]
            receipts = [future.result(timeout=15) for future in futures]
        status, receipt = receipts[0]
        self.assertEqual(status, 201)
        self.assertEqual(receipts[0], receipts[1])
        post = PersonalPost.objects.get()
        self.assertEqual(receipt['post']['id'], post.pk)
        self.assertEqual([path for path in root.rglob('*') if path.is_file()], [Path(post.photo.path)])
        self.client.force_login(owner)
        replay = self.client.post(url, {'caption': '산책', 'photo': sample_photo()}, **headers)
        self.assertEqual((replay.status_code, replay.json()), receipts[0])
        self.assertEqual(len(list(root.rglob('*.jpg'))), 1)
        self.client.force_login(User.objects.create_user('also-posting'))
        other = self.client.post(url, {'caption': '다른 계정'}, content_type='application/json', **headers)
        self.assertEqual(other.status_code, 201)
        self.assertNotEqual(other.json()['post']['id'], post.pk)
        bad = self.client.post(url, {'caption': '글'}, content_type='application/json', HTTP_IDEMPOTENCY_KEY='invalid')
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(PersonalPost.objects.count(), 2)
