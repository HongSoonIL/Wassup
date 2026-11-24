from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from chat.models import Friendship, Message, ReplyPlan
from chat.replies import accept_message, dispatch_due
from world.models import Character


class ReplyDispatchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        owner = get_user_model().objects.create_user(username='owner')
        character = Character.objects.create(name='도현', age=23)
        cls.room = Friendship.objects.create(character=character, user=owner)

    @override_settings(DEBUG=True, WASSUP_REPLY_DEMO_SECONDS=4)
    def test_read_then_coalesce_then_dispatch_once(self):
        now = datetime(2026, 11, 19, 9, 40, tzinfo=timezone.utc)
        with patch('chat.replies.world_now', return_value=now):
            first = accept_message(self.room.pk, '뭐해?')

        dispatch_due(now)
        first.refresh_from_db()
        self.assertIsNone(first.read_at)
        planned = ReplyPlan.objects.get(room=self.room)
        self.assertEqual(planned.reply_at, now + timedelta(seconds=4))

        dispatch_due(now + timedelta(seconds=1))
        first.refresh_from_db()
        self.assertIsNone(first.read_at)
        with patch('chat.replies.publish_room_events'), self.captureOnCommitCallbacks(execute=True):
            dispatch_due(now + timedelta(seconds=2))
        first.refresh_from_db()
        self.assertIsNotNone(first.read_at)
        planned.refresh_from_db()
        self.assertEqual(planned.status, ReplyPlan.Status.READ)

        with patch('chat.replies.world_now', return_value=now + timedelta(seconds=2)):
            second = accept_message(self.room.pk, '아 맞다 오늘 알바지?')

        planned.refresh_from_db()
        self.assertEqual(planned.trigger_message_id, second.pk)
        self.assertEqual(planned.reply_at, now + timedelta(seconds=4))

        with patch('chat.replies.publish_room_events') as publish, self.captureOnCommitCallbacks(execute=True):
            dispatch_due(now + timedelta(seconds=3))
        self.assertEqual([e['type'] for e in publish.call_args.args[1]], ['message.read', 'typing.start'])
        planned.refresh_from_db()
        self.assertEqual(planned.status, ReplyPlan.Status.READY)
        self.client.force_login(self.room.user)
        with patch('chat.views.world_now', return_value=now + timedelta(seconds=3)):
            self.assertIsNotNone(self.client.get(f'/api/rooms/{self.room.pk}/messages/').json()['typing_until'])
        dispatch_due(now + timedelta(seconds=4))
        dispatch_due(now + timedelta(seconds=5))
        planned.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(planned.status, ReplyPlan.Status.SENT)
        self.assertIsNotNone(second.read_at)
        self.assertEqual(self.room.messages.filter(sender=Message.Sender.CHARACTER).count(), 1)

    @override_settings(DEBUG=False, WASSUP_REPLY_DEMO_SECONDS=4)
    def test_demo_speed_cannot_change_normal_timing(self):
        now = datetime(2026, 11, 19, 9, 40, tzinfo=timezone.utc)
        with patch('chat.replies.world_now', return_value=now):
            accept_message(self.room.pk, '뭐해?')
        plan = self.room.reply_plans.get()
        self.assertTrue(now + timedelta(seconds=4) < plan.read_at < plan.typing_at < plan.reply_at)
