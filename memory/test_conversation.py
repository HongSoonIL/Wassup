"""The local, keyless path from a user message to a friend's next-day reply."""

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings

from chat.models import Friendship
from chat.replies import dispatch_due, prepare_next_reply
from world.models import Character


@override_settings(LLM_BACKEND='fake', DEBUG=True, WASSUP_REPLY_DEMO_SECONDS=16,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class MemoryConversationTests(TransactionTestCase):
    def test_next_day_recall_depends_on_the_friendship_and_survives_reopening(self):
        wednesday = datetime(2026, 11, 25, 18, tzinfo=ZoneInfo('Asia/Seoul'))
        a, b = (get_user_model().objects.create_user(name) for name in ('a', 'b'))
        artist, student = (Character.objects.create(name=name, age=22) for name in ('서윤', '도현'))
        room, peer, other = [Friendship.objects.create(user=u, character=c)
                             for u, c in [(a, artist), (b, artist), (a, student)]]
        self.client.force_login(a)
        url = f'/api/rooms/{room.pk}/memories/'
        response = self.client.patch(url, {'enabled': True, 'expected_revision': 0},
                                     content_type='application/json')
        self.assertTrue(response.json()['enabled'])

        def talk(friendship, text, when):
            self.client.force_login(friendship.user)
            path = f'/api/rooms/{friendship.pk}/messages/'
            with patch('chat.replies.world_now', return_value=when):
                receipt = self.client.post(path, {'text': text}, content_type='application/json')
                self.assertEqual(receipt.status_code, 201)
                prepare_next_reply()
            plan = friendship.reply_plans.get(trigger_message_id=receipt.json()['id'])
            dispatch_due(plan.reply_at)
            return self.client.get(path).json()['messages'][-1]['text']

        talk(room, '나 내일 통계 시험이야', wednesday)
        call_command('run_memory_worker', once=True)
        item, = self.client.get(url).json()['memories']
        self.assertEqual(item['event_date'], '2026-11-26')
        question = '오늘 나 무슨 일정 있는지 기억나?'
        for friendship in (room, peer, other):
            with self.subTest(room=friendship.pk):
                answer = talk(friendship, question, wednesday + timedelta(days=1))
                self.assertEqual('통계' in answer, friendship.pk == room.pk)
