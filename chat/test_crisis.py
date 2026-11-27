from datetime import datetime, time, timedelta, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from chat.crisis import CRISIS_BUBBLES, is_crisis
from chat.models import Friendship, Message, ReplyBubble, ReplyPlan
from chat.replies import accept_message, dispatch_due
from world.models import Character, RoutineTemplate, State


# 2026-11-19는 목요일이다. 14:00~22:00 알바(집중) 한가운데에 메시지를 보낸다.
NOW = datetime(2026, 11, 19, 9, 40, tzinfo=timezone.utc)  # 18:40 KST


class CrisisDetectionTests(TestCase):
    def test_detects_crisis_signals(self):
        for text in ('요즘 진짜 죽고 싶다', '자해했어', '살기 싫어', '그냥 사라지고 싶어', '자살 생각 나'):
            with self.subTest(text=text):
                self.assertTrue(is_crisis(text))

    def test_ignores_everyday_exaggeration(self):
        for text in ('배고파 죽겠다', '웃겨 죽겠네 ㅋㅋ', '어제 풋살에서 자살골 넣음', '뭐해?', ''):
            with self.subTest(text=text):
                self.assertFalse(is_crisis(text))


class CrisisReplyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        owner = get_user_model().objects.create_user(username='owner')
        other = get_user_model().objects.create_user(username='other')
        character = Character.objects.create(name='도현', age=23)
        RoutineTemplate.objects.create(character=character, weekday=NOW.weekday(), start_time=time(14),
                                       end_time=time(22), activity='카페 알바', state=State.FOCUS)
        cls.room = Friendship.objects.create(character=character, user=owner)
        cls.other_room = Friendship.objects.create(character=character, user=other)

    def accept(self, text, at=NOW):
        with patch('chat.replies.world_now', return_value=at), \
                patch('chat.replies.publish_room_events') as publish, \
                self.captureOnCommitCallbacks(execute=True):
            message = accept_message(self.room.pk, text)
        return message, publish

    def test_focus_state_normally_delays_reading(self):
        message, _ = self.accept('뭐해?')
        plan = ReplyPlan.objects.get(room=self.room)
        # 알바가 끝날 때까지 미루되, 깨어 있는 동안의 3시간 상한 직전까지만 미룬다.
        self.assertGreater(plan.read_at, NOW + timedelta(hours=2))
        self.assertIsNone(message.read_at)

    def test_crisis_is_read_and_answered_at_once_during_focus(self):
        message, publish = self.accept('나 그냥 죽고 싶어')

        message.refresh_from_db()
        self.assertEqual(message.read_at, NOW)
        replies = list(self.room.messages.filter(sender=Message.Sender.CHARACTER).values_list('text', 'created_at'))
        self.assertEqual(replies, [(text, NOW) for text in CRISIS_BUBBLES])
        self.assertTrue(any('109' in text for text, _ in replies))

        plan = ReplyPlan.objects.get(room=self.room)
        self.assertEqual((plan.status, plan.model_name), (ReplyPlan.Status.SENT, 'crisis'))
        self.assertEqual(plan.bubbles.filter(status=ReplyBubble.Status.SENT).count(), len(CRISIS_BUBBLES))

        room_id, events = publish.call_args.args
        self.assertEqual(room_id, self.room.pk)
        self.assertEqual([e['type'] for e in events],
                         ['typing.stop', 'message.read', *['message.new'] * len(CRISIS_BUBBLES)])
        # 다른 사용자의 채팅방에는 아무것도 가지 않는다.
        self.assertFalse(self.other_room.messages.exists())

    def test_crisis_replaces_a_delayed_reply(self):
        first, _ = self.accept('뭐해?')
        pending = ReplyPlan.objects.get(room=self.room)

        self.accept('사실 요즘 살기 싫어', NOW + timedelta(minutes=1))

        pending.refresh_from_db()
        first.refresh_from_db()
        self.assertEqual(pending.status, ReplyPlan.Status.DISCARDED)
        self.assertIsNotNone(first.read_at)
        self.assertFalse(ReplyPlan.objects.filter(room=self.room, status__in=['planned', 'read', 'ready']).exists())

        # 나중에 일과가 끝나도 버린 답장이 다시 나가지 않는다.
        with patch('chat.replies.publish_room_events'):
            dispatch_due(NOW + timedelta(hours=6))
        self.assertEqual(self.room.messages.filter(sender=Message.Sender.CHARACTER).count(), len(CRISIS_BUBBLES))

    def test_post_response_shows_message_as_read(self):
        self.client.force_login(self.room.user)
        with patch('chat.replies.world_now', return_value=NOW), patch('chat.replies.publish_room_events'):
            response = self.client.post(f'/api/rooms/{self.room.pk}/messages/', {'text': '자해하고 싶어'},
                                        content_type='application/json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['status'], 'read')
