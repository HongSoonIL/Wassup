import json
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from PIL import Image, PngImagePlugin

from accounts.private_photos import normalize_photo
from chat.models import Friendship, Message, ReplyPlan
from chat.replies import accept_message, dispatch_due, prepare_next_reply
from llm.dialogue import context_for
from llm.user_photos import describe_photo
from memory.models import MemoryJob, RoomMemoryState
from world.models import Character, FeedPost, PublicationWeek


def upload(color='red'):
    image = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('Comment', 'private-metadata')
    Image.new('RGB', (32, 24), color).save(image, 'PNG', pnginfo=metadata)
    return SimpleUploadedFile('private-filename.png', image.getvalue(), 'image/png')


def response_for(output):
    return BytesIO(json.dumps({'status': 'completed', 'output': [{
        'type': 'message', 'status': 'completed', 'role': 'assistant',
        'content': [{'type': 'output_text', 'text': output}],
    }]}).encode())


@override_settings(DEBUG=True, WASSUP_REPLY_DEMO_SECONDS=30, LLM_BACKEND='openai', LLM_API_KEY='test-only')
class UserPhotoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user('photo-owner')
        cls.other = get_user_model().objects.create_user('photo-peer')
        person = Character.objects.create(name='도현', age=23)
        cls.room = Friendship.objects.create(user=cls.owner, character=person)
        cls.peer = Friendship.objects.create(user=cls.other, character=person)

    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(override_settings(PRIVATE_PHOTO_ROOT=self.root))
        self.now = timezone.now()
        self.clock = self.enterContext(patch('chat.replies.world_now', return_value=self.now))
        self.url = f'/api/rooms/{self.room.pk}/messages/'
        self.client.force_login(self.owner)

    def test_upload_retry_keeps_single_private_file_message_and_plan(self):
        RoomMemoryState.objects.create(room=self.room, enabled=True)
        key = str(uuid4())
        first = self.client.post(self.url, {'photo': upload()}, headers={'Idempotency-Key': key})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(set(first.json()), {'id', 'status', 'created_at'})
        again = self.client.post(self.url, {'photo': upload(), 'client_message_id': key})
        self.assertEqual(again.json(), first.json())
        self.assertEqual(len(list(self.root.rglob('*.png'))), 1)
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(ReplyPlan.objects.count(), 1)
        self.assertFalse(MemoryJob.objects.exists())
        message, = self.client.get(self.url).json()['messages']
        self.assertEqual((message['kind'], message['text']), ('photo', ''))
        attachment = message['attachment']
        self.assertEqual(attachment['status'], 'available')
        self.assertTrue(attachment['photo_alt'])
        self.assertEqual(self.client.get('/api/friends/').json()['friends'][0]['last_message'], message)

        response = self.client.get(attachment['photo_url'])
        self.assertIn('no-store', response['Cache-Control'])
        pixels = b''.join(response.streaming_content)
        self.assertNotIn(b'private-metadata', pixels)
        self.assertEqual(Image.open(BytesIO(pixels)).getpixel((0, 0)), (255, 0, 0))
        self.assertNotIn('private-filename', str(attachment))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(attachment['photo_url']).status_code, 404)
        self.assertEqual(self.client.get(f'/api/rooms/{self.peer.pk}/messages/{message["id"]}/photo/').status_code, 404)
        self.assertEqual(self.client.post(self.url, {'photo': upload()}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(attachment['photo_url']).status_code, 401)
        self.assertEqual(self.client.post(self.url, {'photo': upload()}).status_code, 401)

    def test_caption_quote_combination_and_unavailable_file(self):
        week = PublicationWeek.objects.create(start=timezone.localdate())
        post = FeedPost.objects.create(week=week, character=self.room.character, state='published',
                                       publish_at=self.now, published_at=self.now, caption='새 시안')
        sent = self.client.post(self.url, {'text': '내 시안이랑 비교하면?', 'source_post_id': str(post.pk),
                                           'photo': upload()})
        self.assertEqual(sent.status_code, 201)
        message, = self.client.get(self.url).json()['messages']
        self.assertEqual(message['source_post']['id'], post.pk)
        self.assertEqual(message['text'], '내 시안이랑 비교하면?')
        path = Path(Message.objects.get().user_photo.path)
        path.unlink()
        self.assertEqual(self.client.get(message['attachment']['photo_url']).status_code, 404)
        self.assertEqual(self.client.get(self.url).json()['messages'][0]['attachment'], {'status': 'unavailable'})

    def test_rejected_uploads_leave_no_files_or_jobs(self):
        for body, status in [
            ({'photo': [upload(), upload()]}, 400),
            ({'photo': SimpleUploadedFile('invalid.png', b'not an image')}, 400),
            ({'photo': upload(), 'source_post_id': '9999'}, 404),
            ({'photo': upload(), 'source_post_id': '1e2'}, 400),
            ({'photo': upload(), 'client_message_id': 'bad-key'}, 400),
            ({'photo': upload(), 'text': 'x' * 1001}, 400),
        ]:
            with self.subTest(status=status):
                self.assertEqual(self.client.post(self.url, body).status_code, status)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.post(self.url, {'photo': upload()}).status_code, 403)
        with patch('chat.replies.enqueue', side_effect=RuntimeError('rollback')), self.assertRaises(RuntimeError):
            accept_message(self.room.pk, '', photo=normalize_photo(upload()))
        self.assertFalse(Message.objects.exists())
        self.assertFalse(ReplyPlan.objects.exists())
        self.assertFalse(any(p.is_file() for p in self.root.rglob('*')))

    def test_room_deletion_releases_file_after_commit(self):
        self.client.post(self.url, {'photo': upload()})
        field = Message.objects.get().user_photo
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.delete(f'/api/friends/{self.room.pk}/').status_code, 204)
            self.assertTrue(field.storage.exists(field.name))
        self.assertFalse(field.storage.exists(field.name))

    def test_grounded_followup_uses_private_pixels_and_normal_reply_sequence(self):
        RoomMemoryState.objects.create(room=self.room, enabled=True)
        self.client.post(self.url, {'photo': upload(), 'text': '내가 보낸 거 보여?'})
        self.assertFalse(MemoryJob.objects.exists())
        accept_message(self.room.pk, '어느 쪽이 더 좋아?')
        plan = self.room.reply_plans.get()
        expected = {'bubbles': ['빨간 쪽 ㅋㅋ', '색이 딱 눈에 들어옴'], 'used_memory_ids': [], 'attachment': None}
        with patch('llm.dialogue.urlopen', return_value=response_for(json.dumps(expected))) as provider:
            self.assertTrue(prepare_next_reply())
        payload = json.loads(provider.call_args.args[0].data)
        parts = payload['input'][0]['content']
        context = json.loads(parts[0]['text'])
        self.assertEqual([t.get('user_photo') for t in context['recent_turns']], [1, None])
        self.assertEqual([p['type'] for p in parts], ['input_text', 'input_text', 'input_image'])
        self.assertTrue(parts[-1]['image_url'].startswith('data:image/jpeg;base64,'))
        self.assertFalse(payload['store'])
        self.assertNotIn('private-filename', str(parts))
        plan.refresh_from_db()
        self.assertEqual(plan.result, expected['bubbles'])
        self.assertEqual(plan.used_memory_ids, [])
        self.assertNotIn('base64', str(plan.context_versions))
        self.assertFalse(prepare_next_reply())
        dispatch_due(plan.reply_at)
        second = plan.bubbles.get(position=1)
        dispatch_due(second.typing_at)
        dispatch_due(second.deliver_at)
        dispatch_due(second.deliver_at)
        self.assertEqual(list(self.room.messages.filter(sender='character').values_list('text', flat=True)),
                         expected['bubbles'])

    def test_context_is_bounded_by_room_day_and_memory_floor(self):
        first = accept_message(self.room.pk, '', photo=normalize_photo(upload()))
        second = accept_message(self.room.pk, '', photo=normalize_photo(upload('blue')))
        accept_message(self.peer.pk, 'peer-only', photo=normalize_photo(upload('green')))
        third = accept_message(self.room.pk, '', photo=normalize_photo(upload('white')))
        plan = third.reply_plans.get()
        fields = []
        snapshot = context_for(plan, self.now, photo_fields=fields)
        self.assertEqual([f.name for f in fields], [second.user_photo.name, third.user_photo.name])
        self.assertEqual([t.get('user_photo') for t in snapshot['recent_turns']], [None, 1, 2])
        self.assertNotIn('peer-only', str(snapshot))
        Message.objects.filter(pk=third.pk).update(created_at=self.now-timedelta(days=1))
        RoomMemoryState.objects.create(room=self.room, enabled=False, history_floor=second.pk)
        followup = accept_message(self.room.pk, '무슨 색이었지?')
        fields = []
        snapshot = context_for(followup.reply_plans.get(), self.now, photo_fields=fields)
        self.assertEqual(fields, [])
        self.assertEqual([t['text'] for t in snapshot['recent_turns']], ['무슨 색이었지?'])
        self.assertNotIn(first.user_photo.name, str(snapshot))

    def test_second_photo_supersedes_in_flight_result_and_preserves_photo_order(self):
        first = accept_message(self.room.pk, '', photo=normalize_photo(upload()))
        plan = first.reply_plans.get()

        def interrupted(context, *, photo_fields):
            self.assertEqual([f.name for f in photo_fields], [first.user_photo.name])
            accept_message(self.room.pk, '둘 중 골라 줘', photo=normalize_photo(upload('blue')))
            return {'bubbles': ['첫 사진만 보고 쓴 말'], 'used_memory_ids': []}

        with patch('chat.replies.openai_reply', side_effect=interrupted):
            self.assertTrue(prepare_next_reply())
        plan.refresh_from_db()
        self.assertEqual(plan.result, [])
        with patch('llm.dialogue.urlopen', return_value=response_for(json.dumps({
            'bubbles': ['두 번째 파란 쪽'], 'used_memory_ids': [], 'attachment': None,
        }))) as provider:
            self.assertTrue(prepare_next_reply())
        parts = json.loads(provider.call_args.args[0].data)['input'][0]['content']
        self.assertEqual([t['user_photo'] for t in json.loads(parts[0]['text'])['recent_turns']], [1, 2])
        self.assertEqual([p['type'] for p in parts[1:]], ['input_text', 'input_image'] * 2)
        self.assertNotEqual(parts[2]['image_url'], parts[4]['image_url'])
        dispatch_due(plan.reply_at)
        dispatch_due(plan.reply_at)
        self.assertEqual(list(self.room.messages.filter(sender='character').values_list('text', flat=True)),
                         ['두 번째 파란 쪽'])

    def test_description_uses_only_the_image_and_degrades_to_empty(self):
        field = accept_message(self.room.pk, '', photo=normalize_photo(upload())).user_photo
        with override_settings(LLM_MEMORY_MODEL='low-cost'), patch('llm.user_photos.urlopen',
                  return_value=response_for('붉은 사각형')) as provider:
            self.assertEqual(describe_photo(field), '붉은 사각형')
            payload = json.loads(provider.call_args.args[0].data)
            self.assertEqual(payload['model'], 'low-cost')
            self.assertFalse(payload['store'])
            self.assertEqual([p['type'] for p in payload['input'][0]['content']], ['input_image'])
            self.assertNotIn(field.name, str(payload))
        with patch('llm.user_photos.urlopen', side_effect=TimeoutError):
            self.assertEqual(describe_photo(field), '')
        with override_settings(LLM_BACKEND='fake'), patch('llm.user_photos.urlopen') as provider:
            self.assertEqual(describe_photo(field), '')
            provider.assert_not_called()
