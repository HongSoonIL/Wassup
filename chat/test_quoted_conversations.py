import io
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from chat.models import Friendship, Message, ReplyBubble, ReplyPlan
from chat.replies import accept_message, dispatch_due, prepare_next_reply
from llm.dialogue import context_for
from world.models import Character, FeedPost, Persona, PublicationWeek


@override_settings(LLM_BACKEND='openai', LLM_API_KEY='test-only', LLM_MODEL='test-model')
class QuotedConversationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(username='owner')
        peer = get_user_model().objects.create_user(username='peer')
        cls.artist = Character.objects.create(name='서윤', age=22)
        Persona.objects.create(character=cls.artist, bubble_min=2, bubble_max=4)
        other = Character.objects.create(name='도현', age=23)
        cls.room = Friendship.objects.create(user=cls.owner, character=cls.artist)
        cls.peer_room = Friendship.objects.create(user=peer, character=cls.artist)
        cls.other_room = Friendship.objects.create(user=cls.owner, character=other)
        now = timezone.now()
        week = PublicationWeek.objects.create(start=timezone.localdate())
        cls.post = FeedPost.objects.create(
            week=week,
            character=cls.artist,
            state='published',
            published_at=now,
            publish_at=now,
            caption='오늘은 주황 🍊',
            public_facts=['군청과 주황 포스터를 뽑았다.', '최종 색으로 주황을 골랐다.'],
            operator_note='운영 메모 939',
            photo_alt='책상 위 군청색과 주황색 시안',
        )
        cls.draft = FeedPost.objects.create(
            week=week,
            character=cls.artist,
            publish_at=now,
            caption='초안 989',
        )

    def send(self, room, data):
        return self.client.post(f'/api/rooms/{room.pk}/messages/', data, content_type='application/json')

    def quote(self):
        return accept_message(self.room.pk, '근데 왜 주황 골랐어?', self.post.pk)

    def test_same_feed_opens_each_existing_room_without_sending(self):
        for room in (self.room, self.peer_room):
            with self.subTest(room=room.pk):
                self.client.force_login(room.user)
                response = self.client.get(f'/chat/from-post/{self.post.pk}/', follow=True)
                self.assertEqual(
                    response.redirect_chain, [(f'/chat/{room.pk}/?source_post={self.post.pk}', 302)]
                )
                self.assertEqual(response.context['initial_quote']['caption'], self.post.caption)
        self.assertFalse(Message.objects.exists())

    def test_post_link_returns_through_login_and_checks_visibility(self):
        url = f'/chat/from-post/{self.post.pk}/'
        self.assertRedirects(self.client.get(url), '/login/?next=' + url)
        self.owner.set_password('owner-pw')
        self.owner.save(update_fields=['password'])
        response = self.client.post(
            '/login/', {'username': self.owner.username, 'password': 'owner-pw', 'next': url}, follow=True,
        )
        self.assertEqual(response.redirect_chain, [
            (url, 302), (f'/chat/{self.room.pk}/?source_post={self.post.pk}', 302),
        ])
        self.assertEqual(response.context['initial_quote']['id'], self.post.pk)
        self.assertEqual(self.client.get(f'/chat/from-post/{self.draft.pk}/').status_code, 404)
        self.client.force_login(get_user_model().objects.create_user('stranger'))
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertFalse(Message.objects.exists())

    def test_invalid_invisible_or_foreign_quote_never_creates_a_plan(self):
        self.client.force_login(self.owner)
        for room, source, status in [
            (self.room, True, 400),
            (self.room, '2', 400),
            (self.room, 2**64, 400),
            (self.peer_room, self.post.pk, 403),
            (self.other_room, self.post.pk, 404),
            (self.room, self.draft.pk, 404),
        ]:
            with self.subTest(room=room.pk, source=source):
                self.assertEqual(
                    self.send(room, {'text': '안녕', 'source_post_id': source}).status_code, status
                )
        self.post.published_at += timedelta(days=1)
        self.post.save(update_fields=['published_at'])
        self.assertEqual(
            self.send(self.room, {'text': '안녕', 'source_post_id': self.post.pk}).status_code, 404
        )
        self.assertFalse(Message.objects.exists())
        self.assertFalse(ReplyPlan.objects.exists())

    def test_history_old_client_and_revoked_quote(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.send(self.room, {'text': '안녕'}).status_code, 201)
        receipt = self.send(self.room, {'text': '그거 어때?', 'source_post_id': self.post.pk})
        self.assertEqual(set(receipt.json()), {'id', 'status', 'created_at'})
        url = f'/api/rooms/{self.room.pk}/messages/'
        plain, quoted = self.client.get(url).json()['messages']
        self.assertIsNone(plain['source_post'])
        self.assertEqual(quoted['source_post']['version'], self.post.version)
        self.post.state = 'skipped'
        self.post.save(update_fields=['state'])
        self.assertEqual(
            self.client.get(url).json()['messages'][1]['source_post'], {'status': 'unavailable'}
        )

    def test_responses_request_uses_only_current_room_and_published_facts(self):
        Message.objects.create(room=self.peer_room, sender='user', text='다른 사용자 618')
        Message.objects.create(room=self.other_room, sender='user', text='다른 친구 217')
        Message.objects.create(
            room=self.room,
            sender='user',
            text='어제의 개인 정보 484',
            created_at=timezone.now() - timedelta(days=1),
        )
        self.quote()
        result = {
            'status': 'completed',
            'output': [
                {
                    'type': 'message',
                    'content': [
                        {
                            'type': 'output_text',
                            'text': json.dumps({'bubbles': ['주황이 좋더라', '같이 뽑아 보길 잘했어'],
                                                'used_memory_ids': []}),
                        }
                    ],
                }
            ],
        }
        with patch(
            'llm.dialogue.urlopen', return_value=io.BytesIO(json.dumps(result).encode())
        ) as transport:
            prepare_next_reply()
        request = transport.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.openai.com/v1/responses')
        body = json.loads(request.data)
        self.assertFalse(body['store'])
        self.assertTrue(body['text']['format']['strict'])
        context = json.loads(body['input'])
        self.assertEqual(context['quoted_post_id'], self.post.pk)
        self.assertEqual(context['shared_events'][0]['facts'], self.post.public_facts)
        for excluded in [
            '다른 사용자 618',
            '다른 친구 217',
            '초안 989',
            '운영 메모 939',
            '어제의 개인 정보 484',
        ]:
            self.assertNotIn(excluded, body['input'])
        plan = self.room.reply_plans.get()
        dispatch_due(plan.reply_at)
        dispatch_due(plan.reply_at)
        self.assertEqual(list(self.room.messages.filter(sender='character').values_list('text', flat=True)),
                         ['주황이 좋더라'])
        second = plan.bubbles.get(position=1)
        dispatch_due(second.typing_at)
        dispatch_due(second.deliver_at)
        self.assertEqual(
            list(self.room.messages.filter(sender='character').values_list('text', flat=True)),
            ['주황이 좋더라', '같이 뽑아 보길 잘했어'],
        )
        self.assertEqual(plan.model_name, 'test-model')

    def test_a_short_question_can_have_one_model_bubble_for_seoyun(self):
        accept_message(self.room.pk, '무슨 색으로 골랐어?', self.post.pk)
        result = {
            'status': 'completed',
            'output': [{'type': 'message', 'content': [
                {'type': 'output_text', 'text': '{"bubbles":["주황"],"used_memory_ids":[]}'},
            ]}],
        }
        with patch('llm.dialogue.urlopen', return_value=io.BytesIO(json.dumps(result).encode())) as transport:
            prepare_next_reply()
            prepare_next_reply()
        transport.assert_called_once()
        schema = json.loads(transport.call_args.args[0].data)['text']['format']['schema']
        self.assertEqual(schema['properties']['bubbles']['minItems'], 1)
        plan = self.room.reply_plans.get()
        self.assertEqual(plan.model_name, 'test-model')
        dispatch_due(plan.reply_at)
        self.assertEqual(plan.bubbles.get().message.text, '주황')

    def test_revocation_between_bubbles_cancels_only_unsent_text(self):
        self.quote()
        with patch('chat.replies.openai_reply', return_value={
            'bubbles': ['처음 답장', '회수한 사진의 세부 내용'], 'used_memory_ids': [],
        }):
            prepare_next_reply()
        plan = self.room.reply_plans.get()
        dispatch_due(plan.reply_at)
        second = plan.bubbles.get(position=1)
        dispatch_due(second.typing_at)
        self.post.state = 'skipped'
        self.post.save(update_fields=['state'])
        dispatch_due(second.deliver_at)
        dispatch_due(second.deliver_at + timedelta(days=1))
        self.assertEqual(list(plan.bubbles.values_list('status', flat=True)),
                         [ReplyBubble.Status.SENT, ReplyBubble.Status.CANCELLED])
        self.assertEqual(list(self.room.messages.filter(sender='character').values_list('text', flat=True)),
                         ['처음 답장'])

    def test_sampled_sequence_rechecks_public_version_before_each_bubble(self):
        self.quote()
        plan = self.room.reply_plans.get()
        with override_settings(LLM_BACKEND='fake'):
            dispatch_due(plan.reply_at)
        plan.refresh_from_db()
        self.assertEqual(plan.context_versions, {str(self.post.pk): self.post.version})
        second = plan.bubbles.get(position=1)
        self.post.version += 1
        self.post.save(update_fields=['version'])
        dispatch_due(second.typing_at)
        dispatch_due(second.deliver_at)
        self.assertEqual(self.room.messages.filter(sender='character').count(), 1)
        plan.refresh_from_db()
        self.assertEqual(plan.status, ReplyPlan.Status.DISCARDED)

    def test_edited_quote_cannot_reenter_generation_as_a_recent_post(self):
        self.quote()
        self.post.version += 1
        self.post.caption = '사용자가 인용하지 않은 새 내용'
        self.post.save(update_fields=['version', 'caption'])
        plan = self.room.reply_plans.get()
        context = context_for(plan, timezone.now())
        self.assertIsNone(context['quoted_post_id'])
        self.assertEqual(context['shared_events'], [])
        with override_settings(LLM_BACKEND='fake'):
            dispatch_due(plan.reply_at)
        self.assertNotIn('주황', plan.bubbles.get().message.text)

    def test_long_wait_defers_generation_and_uses_delivery_activity(self):
        from datetime import time
        from world.models import RoutineTemplate, State
        self.quote()
        plan = self.room.reply_plans.get()
        now = timezone.localtime(plan.trigger_message.created_at).replace(hour=10, minute=0, second=0)
        plan.reply_at = now + timedelta(hours=2)
        plan.read_at = plan.reply_at - timedelta(seconds=10)
        plan.typing_at = plan.reply_at - timedelta(seconds=6)
        plan.save(update_fields=['read_at', 'typing_at', 'reply_at'])
        RoutineTemplate.objects.create(character=self.artist, weekday=now.weekday(),
                                       start_time=time(12), end_time=time(13), state=State.FREE, activity='점심시간')
        with patch('chat.replies.world_now', return_value=now), patch('chat.replies.openai_reply') as model:
            prepare_next_reply()
            model.assert_not_called()
        with patch('chat.replies.world_now', return_value=plan.reply_at - timedelta(seconds=30)), \
                patch('chat.replies.openai_reply', return_value={'bubbles': ['점심 먹으러 왔어'], 'used_memory_ids': []}) as model:
            prepare_next_reply()
            self.assertEqual(model.call_args.args[0]['current_activity']['activity'], '점심시간')
        plan.refresh_from_db()
        self.assertIsNotNone(plan.generation_ms)

    def test_additional_message_or_revocation_prevents_stale_delivery(self):
        self.quote()
        first = self.room.reply_plans.get()

        def new_turn(context):
            accept_message(self.room.pk, '근데 종이로도 봤어?')
            return {'bubbles': ['이전 질문에 만든 답장'], 'used_memory_ids': []}

        with patch('chat.replies.openai_reply', side_effect=new_turn):
            prepare_next_reply()
        plan = self.room.reply_plans.get()
        self.assertEqual(plan.reply_at, first.reply_at)
        self.assertEqual(plan.result, [])
        self.assertEqual(context_for(plan, timezone.now())['quoted_post_id'], self.post.pk)
        with patch('chat.replies.openai_reply', return_value={
            'bubbles': ['회수할 사진을 보고 만든 답장'], 'used_memory_ids': [],
        }):
            prepare_next_reply()
        self.post.state = 'skipped'
        self.post.save(update_fields=['state'])
        dispatch_due(plan.reply_at)
        plan.refresh_from_db()
        self.assertEqual(plan.model_name, 'fallback')
        self.assertNotIn('회수할 사진을 보고 만든 답장', plan.result)
        self.assertNotIn('주황', ' '.join(plan.result))

    def test_invalid_model_result_uses_reviewed_replies_by_deadline(self):
        self.quote()
        result = {
            'status': 'completed',
            'output': [
                {'type': 'message', 'content': [{'type': 'output_text', 'text': '{"bubbles":[""],"used_memory_ids":[]}'}]}
            ],
        }
        with patch('llm.dialogue.urlopen', return_value=io.BytesIO(json.dumps(result).encode())):
            prepare_next_reply()
        plan = self.room.reply_plans.get()
        dispatch_due(plan.reply_at)
        plan.refresh_from_db()
        self.assertEqual(plan.model_name, 'fallback')
        self.assertIn('주황', ' '.join(plan.result))
