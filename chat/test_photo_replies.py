from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.test import TestCase, override_settings
from django.utils import timezone

from chat.models import Friendship, Message, ReplyBubble, ReplyPlan
from chat.replies import accept_message, dispatch_due, prepare_next_reply
from world.models import Character, FeedPost, PostPhoto, PublicationWeek


@override_settings(DEBUG=True, WASSUP_REPLY_DEMO_SECONDS=20, LLM_BACKEND='openai', LLM_MODEL='test-model')
class PhotoReplyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user('owner')
        cls.stranger = get_user_model().objects.create_user('stranger')
        cls.character = Character.objects.create(name='서윤', age=22)
        cls.other_character = Character.objects.create(name='도현', age=23)
        cls.room = Friendship.objects.create(user=cls.owner, character=cls.character)
        cls.now = timezone.now()
        cls.week = PublicationWeek.objects.create(start=timezone.localdate(cls.now))

    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.storage = FileSystemStorage(location=folder)
        self.enterContext(patch.object(PostPhoto._meta.get_field('image'), 'storage', self.storage))
        self.clock = self.enterContext(patch('chat.replies.world_now', return_value=self.now))
        self.post = self.make_post(self.character, 'cat-photo')

    def make_post(self, character, key):
        image = self.storage.save(f'photos/{key}.png', ContentFile(key.encode()))
        asset = PostPhoto.objects.create(key=key, image=image, alt='담요에 누운 고양이', character=character)
        return FeedPost.objects.create(
            week=self.week,
            character=character,
            state=FeedPost.State.PUBLISHED,
            publish_at=self.now,
            published_at=self.now,
            event_at=self.now,
            photo=asset,
            photo_alt=asset.alt,
            caption='낮잠 중인 두부',
            version=4,
        )

    def ask(self, *, quote=False):
        message = accept_message(self.room.pk, '고양이 사진 보여줘', self.post.pk if quote else None)
        return message.reply_plans.get()

    def prepare(self, *, bubbles=('이거 봐',), at=0, selection=None, invalid=False):
        if not invalid:
            selection = {'post_id': self.post.pk, 'bubble_index': at}
        with patch('chat.replies.openai_reply', return_value={
            'bubbles': list(bubbles), 'used_memory_ids': [], 'attachment': selection,
        }) as provider:
            self.assertTrue(prepare_next_reply())
        return provider

    def deliver_next(self, plan, position):
        bubble = plan.bubbles.get(position=position)
        dispatch_due(bubble.typing_at)
        dispatch_due(bubble.deliver_at)
        dispatch_due(bubble.deliver_at)
        bubble.refresh_from_db()
        return bubble

    def history(self):
        return self.client.get(f'/api/rooms/{self.room.pk}/messages/')

    @override_settings(LLM_BACKEND='fake')
    def test_whole_conversation_only_resends_a_delivered_photo_on_request(self):
        self.client.force_login(self.owner)
        url = f'/api/rooms/{self.room.pk}/messages/'
        for question, photo_count in [
            ('오늘 찍은 고양이 사진 보여줘', 1),
            ('이거 언제 찍었어?', 1),
            ('아까 그 고양이 사진 다시 보내주라', 2),
        ]:
            with self.subTest(question=question):
                response = self.client.post(url, {'text': question}, content_type='application/json')
                self.assertEqual(response.status_code, 201)
                self.assertTrue(prepare_next_reply())
                plan = self.room.reply_plans.get(trigger_message_id=response.json()['id'])
                dispatch_due(plan.reply_at)
                for position in range(1, plan.bubbles.count()):
                    self.deliver_next(plan, position)
                records = self.history().json()['messages']
                photos = [message['attachment']['id'] for message in records if message['kind'] == 'photo']
                self.assertEqual(photos, [self.post.pk] * photo_count)
                self.clock.return_value = self.room.messages.latest('pk').created_at + timedelta(seconds=1)

    def test_photo_is_durable_in_live_event_history_and_friend_preview(self):
        plan = self.ask(quote=True)
        self.prepare(bubbles=('응 두부 사진 있어', '이거 봐'), at=1)
        plan.refresh_from_db()
        self.assertEqual(plan.attachment, {'post_id': self.post.pk, 'version': 4, 'bubble_index': 1})
        dispatch_due(plan.reply_at)
        self.assertIsNone(plan.bubbles.get(position=0).attachment_post_id)
        queued = ReplyBubble.objects.get(plan=plan, position=1)
        self.assertEqual((queued.attachment_post_id, queued.attachment_version), (self.post.pk, 4))

        with patch('chat.replies.publish_room_events') as live, self.captureOnCommitCallbacks(execute=True):
            delivered = self.deliver_next(plan, 1)
        message = delivered.message
        self.assertEqual((message.kind, message.text, message.attachment_post_id, message.attachment_version),
                         ('photo', '이거 봐', self.post.pk, 4))
        self.assertEqual(self.room.messages.filter(kind=Message.Kind.PHOTO).count(), 1)
        self.client.force_login(self.owner)
        quoted, lead, photo = self.history().json()['messages']
        self.assertEqual(quoted['source_post']['photo_url'], f'/api/posts/{self.post.pk}/photo/')
        self.assertNotIn('attachment', quoted)
        self.assertNotIn('attachment', lead)
        self.assertIsNone(photo['source_post'])
        self.assertEqual(photo['attachment'], {
            **quoted['source_post'], 'photo_url': f'/api/posts/{self.post.pk}/photo/?version=4',
        })
        self.assertEqual(live.call_args.args, (self.room.pk, [
            {'type': 'typing.stop'}, {'type': 'message.new', 'message': photo},
        ]))
        self.assertEqual(self.client.get('/api/friends/').json()['friends'][0]['last_message'], photo)

    def test_media_requires_friendship_and_rejects_stale_version(self):
        plan = self.ask()
        self.prepare()
        dispatch_due(plan.reply_at)
        self.client.force_login(self.owner)
        url = self.history().json()['messages'][-1]['attachment']['photo_url']
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(b''.join(response.streaming_content), b'cat-photo')
        self.assertEqual(self.client.get(url + '&version=4').status_code, 404)
        self.assertEqual(self.client.get(url.rsplit('=', 1)[0] + '=999999999999999').status_code, 404)

        replacement = self.make_post(self.character, 'replacement')
        FeedPost.objects.filter(pk=self.post.pk).update(photo=replacement.photo, version=5)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.history().json()['messages'][-1]['attachment'], {'status': 'unavailable'})
        current = self.client.get(url.rsplit('=', 1)[0] + '=5')
        self.assertEqual(current.status_code, 200)
        # 끝까지 읽어야 파일이 닫힌다. Windows는 열린 파일을 지우지 못해 임시 폴더 정리가 실패한다.
        self.assertEqual(b''.join(current.streaming_content), b'replacement')

        self.client.force_login(self.stranger)
        self.assertEqual(self.history().status_code, 403)
        self.assertEqual(self.client.get(url.rsplit('=', 1)[0] + '=5').status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 401)

    def test_unknown_wrong_owner_and_invalid_selections_replace_the_full_sequence(self):
        foreign = self.make_post(self.other_character, 'foreign')
        plan = self.ask()
        for selection in [
            {'post_id': foreign.pk, 'bubble_index': 0},
            {'post_id': 2**64, 'bubble_index': 0},
            {'post_id': True, 'bubble_index': 0},
            {'post_id': self.post.pk, 'bubble_index': True},
            {'post_id': self.post.pk, 'bubble_index': -1},
            {'post_id': self.post.pk, 'bubble_index': 2},
            [{'post_id': self.post.pk, 'bubble_index': 0}],
        ]:
            with self.subTest(selection=selection):
                ReplyPlan.objects.filter(pk=plan.pk).update(result=[])
                self.prepare(bubbles=('이거 봐', '옆에는 동생이야'), invalid=True, selection=selection)
                plan.refresh_from_db()
                self.assertIsNone(plan.attachment)
                self.assertNotIn('이거 봐', plan.result)
                self.assertNotIn('옆에는 동생이야', plan.result)
                self.assertEqual(plan.model_name, 'fallback')
        dispatch_due(plan.reply_at)
        self.assertFalse(self.room.messages.filter(kind=Message.Kind.PHOTO).exists())

    def test_invalid_at_first_deadline_sends_only_the_plain_fallback(self):
        foreign = self.make_post(self.other_character, 'foreign')
        for change in (
            {'state': FeedPost.State.SKIPPED}, {'photo_id': None}, {'version': 5},
            {'photo_id': foreign.photo_id}, {'published_at': self.now + timedelta(days=1)},
            {'character_id': self.other_character.pk},
        ):
            with self.subTest(change=change):
                plan = self.ask()
                self.prepare(bubbles=('이거 봐', '아까 찍은 거야'))
                FeedPost.objects.filter(pk=self.post.pk).update(**change)
                dispatch_due(plan.reply_at)
                plan.refresh_from_db()
                self.assertIsNone(plan.attachment)
                message = plan.bubbles.get().message
                self.assertEqual(message.kind, Message.Kind.TEXT)
                self.assertNotIn(message.text, ('이거 봐', '아까 찍은 거야'))
                plan.delete()
                self.room.messages.all().delete()
                self.post.save()

    def test_missing_file_during_typing_cancels_the_unsent_photo_and_tail(self):
        plan = self.ask()
        self.prepare(bubbles=('응 두부 사진 있어', '이거 봐', '담요 좋아한대'), at=1)
        dispatch_due(plan.reply_at)
        photo = plan.bubbles.get(position=1)
        dispatch_due(photo.typing_at)
        self.storage.delete(self.post.photo.image.name)
        dispatch_due(photo.deliver_at)
        self.assertEqual(list(plan.bubbles.values_list('status', flat=True)), ['sent', 'cancelled', 'cancelled'])
        self.assertEqual(list(self.room.messages.filter(sender='character').values_list('text', flat=True)),
                         ['응 두부 사진 있어'])

    def test_retracted_after_delivery_cancels_tail_and_keeps_unavailable_history(self):
        plan = self.ask()
        self.prepare(bubbles=('이거 봐', '담요 좋아한대'))
        dispatch_due(plan.reply_at)
        self.post.delete()
        tail = self.deliver_next(plan, 1)
        self.assertEqual(tail.status, ReplyBubble.Status.CANCELLED)
        photo = plan.bubbles.get(position=0).message
        self.assertEqual(photo.attachment_version, 4)
        self.assertIsNone(photo.attachment_post_id)
        self.client.force_login(self.owner)
        self.assertEqual(self.history().json()['messages'][-1]['attachment'], {'status': 'unavailable'})

    def test_new_inbound_invalidates_stale_claim_and_cancels_timed_photo(self):
        plan = self.ask()

        def slow_reply(context):
            accept_message(self.room.pk, '아니 내가 다시 물어볼게')
            return {
                'bubbles': ['이거 봐'], 'used_memory_ids': [],
                'attachment': {'post_id': self.post.pk, 'bubble_index': 0},
            }

        with patch('chat.replies.openai_reply', side_effect=slow_reply):
            self.assertTrue(prepare_next_reply())
        plan.refresh_from_db()
        self.assertEqual(plan.result, [])
        self.assertIsNone(plan.attachment)
        self.prepare(bubbles=('알았어', '이거 봐'), at=1)
        dispatch_due(plan.reply_at)
        photo = plan.bubbles.get(position=1)
        dispatch_due(photo.typing_at)
        self.clock.return_value = photo.typing_at
        accept_message(self.room.pk, '사진은 됐어')
        dispatch_due(photo.deliver_at)
        photo.refresh_from_db()
        self.assertEqual(photo.status, ReplyBubble.Status.CANCELLED)
        self.assertFalse(self.room.messages.filter(kind=Message.Kind.PHOTO).exists())

    def test_memory_fence_discards_a_queued_photo(self):
        self.client.force_login(self.owner)
        url = f'/api/rooms/{self.room.pk}/memories/'
        self.assertEqual(self.client.patch(url, {'enabled': True, 'expected_revision': 0},
                                           content_type='application/json').status_code, 200)
        plan = self.ask()
        self.prepare(bubbles=('응 두부 사진 있어', '이거 봐'), at=1)
        dispatch_due(plan.reply_at)
        self.assertEqual(self.client.patch(url, {'enabled': False, 'expected_revision': 1},
                                           content_type='application/json').status_code, 200)
        self.assertEqual(self.deliver_next(plan, 1).status, ReplyBubble.Status.CANCELLED)
        self.assertFalse(self.room.messages.filter(kind=Message.Kind.PHOTO).exists())
