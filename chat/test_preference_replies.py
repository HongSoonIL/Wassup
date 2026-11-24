from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from chat.models import Friendship, Message, ReplyBubble, ReplyPlan
from chat.replies import PREFERENCE_RETRY_GRACE, accept_message, dispatch_due, prepare_next_reply
from memory.models import RoomMemoryState
from world.models import Character


NOW = datetime(2026, 11, 24, 7, tzinfo=timezone.utc)
OLD = '재즈를 좋아해'
NEW = '인디 음악을 좋아해'


@override_settings(DEBUG=True, WASSUP_REPLY_DEMO_SECONDS=20, LLM_BACKEND='openai', LLM_MODEL='test-model')
class PreferenceReplyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user('owner')
        cls.other_user = get_user_model().objects.create_user('other')
        character = Character.objects.create(name='서윤', age=22)
        friend = Character.objects.create(name='도현', age=23)
        cls.room = Friendship.objects.create(user=cls.owner, character=character)
        cls.second_room = Friendship.objects.create(user=cls.owner, character=friend)
        cls.other_room = Friendship.objects.create(user=cls.other_user, character=character)

    def setUp(self):
        self.clock = self.enterContext(patch('chat.replies.world_now', return_value=NOW))
        self.change(self.owner, OLD)

    def change(self, user, text):
        user.refresh_from_db()
        self.client.force_login(user)
        response = self.client.put(reverse('my-preferences'), {
            'items': [{'tag': '음악', 'text': text}] if text else [],
            'expected_revision': user.preference_revision,
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200)

    def ask(self, room=None):
        return accept_message((room or self.room).pk, '음악 추천해 줘').reply_plans.get()

    def prepare(self, model):
        with patch('chat.replies.openai_reply', side_effect=model):
            self.assertTrue(prepare_next_reply())

    @staticmethod
    def echo(context):
        items = context['shared_preferences']
        return {'bubbles': [items[0]['text'] if items else '요즘 무슨 음악 들어?'], 'used_memory_ids': []}

    def test_edit_during_generation_drops_old_output_and_retries_without_memory_or_early_read(self):
        RoomMemoryState.objects.create(room=self.room, enabled=False)
        plan = self.ask()

        def slow_model(context):
            self.assertEqual(context['authorized_memories'], [])
            self.assertEqual(context['preference_revision'], 1)
            self.assertEqual(context['shared_preferences'][0]['text'], OLD)
            self.change(self.owner, NEW)
            return self.echo(context)

        self.prepare(slow_model)
        plan.refresh_from_db()
        self.assertEqual(plan.result, [])
        self.assertIsNone(plan.generation_token)
        self.assertIsNone(plan.generation_until)
        self.prepare(self.echo)
        plan.refresh_from_db()
        self.assertEqual(plan.attempts, 2)
        self.assertEqual(plan.preference_revision, 2)
        self.assertEqual(plan.result, [NEW])
        self.assertIsNone(plan.trigger_message.read_at)
        dispatch_due(plan.reply_at)
        dispatch_due(plan.reply_at)
        self.assertEqual(list(plan.bubbles.values_list('message__text', flat=True)), [NEW])

    def test_imminent_edit_stops_typing_and_retries_after_original_delivery_time(self):
        plan = self.ask()
        self.prepare(self.echo)
        dispatch_due(plan.typing_at)
        self.change(self.owner, NEW)
        self.clock.return_value = plan.reply_at

        with patch('chat.replies.publish_room_events') as publish, self.captureOnCommitCallbacks(execute=True):
            dispatch_due(plan.reply_at)
            dispatch_due(plan.reply_at)
        publish.assert_called_once_with(self.room.pk, [{'type': 'typing.stop'}])
        self.assertFalse(plan.bubbles.exists())

        self.prepare(self.echo)
        dispatch_due(plan.reply_at + timedelta(seconds=1))
        plan.refresh_from_db()
        self.assertEqual(plan.status, ReplyPlan.Status.SENT)
        self.assertIsNone(plan.preference_retry_until)
        self.assertEqual(plan.bubbles.get().message.text, NEW)

    def test_legacy_preparation_can_retry_at_deadline_without_delaying_another_room(self):
        plan = self.ask()
        self.clock.return_value = NOW + timedelta(seconds=1)
        other = self.ask(self.other_room)
        # The migration gives pre-existing prepared rows a revision of zero.
        ReplyPlan.objects.filter(pk=plan.pk).update(preference_revision=0, result=['이전 답장'])
        dispatch_due(plan.reply_at)
        self.assertFalse(plan.bubbles.exists())
        dispatch_due(other.reply_at, limit=1)
        other.refresh_from_db()
        self.assertEqual(other.status, ReplyPlan.Status.SENT)

        self.clock.return_value = plan.reply_at
        self.prepare(self.echo)
        dispatch_due(plan.reply_at)
        self.assertEqual(plan.bubbles.get().message.text, OLD)

    def test_pre_deadline_retry_lease_survives_the_original_delivery_time(self):
        plan = self.ask()
        self.prepare(self.echo)
        self.change(self.owner, NEW)
        self.clock.return_value = plan.reply_at - timedelta(milliseconds=1)

        def retry(context):
            self.assertIsNone(plan.trigger_message.read_at)
            self.clock.return_value = plan.reply_at
            dispatch_due(plan.reply_at)
            self.assertFalse(plan.bubbles.exists())
            self.assertFalse(prepare_next_reply())
            return self.echo(context)

        self.prepare(retry)
        dispatch_due(plan.reply_at)
        self.assertEqual(plan.bubbles.get().message.text, NEW)

    def test_first_typing_waits_for_the_current_snapshot_and_stops_if_it_becomes_stale(self):
        plan = self.ask()
        self.prepare(self.echo)
        dispatch_due(plan.read_at)
        self.change(self.owner, NEW)
        with patch('chat.replies.publish_room_events') as publish, self.captureOnCommitCallbacks(execute=True):
            dispatch_due(plan.typing_at)
        publish.assert_not_called()

        self.prepare(self.echo)
        with patch('chat.replies.publish_room_events') as publish, self.captureOnCommitCallbacks(execute=True):
            dispatch_due(plan.typing_at)
        self.assertEqual([event['type'] for event in publish.call_args.args[1]], ['typing.start'])

        self.change(self.owner, None)
        with patch('chat.replies.publish_room_events') as publish, self.captureOnCommitCallbacks(execute=True):
            dispatch_due(plan.typing_at + timedelta(seconds=1))
            dispatch_due(plan.typing_at + timedelta(seconds=1))
        publish.assert_called_once_with(self.room.pk, [{'type': 'typing.stop'}])
        self.assertFalse(plan.bubbles.exists())

    def test_removal_cancels_an_already_typing_tail_without_affecting_other_accounts(self):
        plan = self.ask()
        other = self.ask(self.other_room)
        self.prepare(lambda context: {'bubbles': ['첫 말풍선', OLD], 'used_memory_ids': []})
        self.prepare(lambda context: {'bubbles': ['다른 첫 말풍선', '다른 후속 말풍선'], 'used_memory_ids': []})
        dispatch_due(plan.reply_at)
        tail = plan.bubbles.get(position=1)
        other_tail = other.bubbles.get(position=1)
        dispatch_due(tail.typing_at)
        self.change(self.owner, None)
        with patch('chat.replies.publish_room_events') as publish, self.captureOnCommitCallbacks(execute=True):
            dispatch_due(tail.typing_at)
        publish.assert_called_once_with(self.room.pk, [{'type': 'typing.stop'}])
        dispatch_due(tail.deliver_at)
        dispatch_due(other_tail.typing_at)
        other_tail.refresh_from_db()
        dispatch_due(other_tail.deliver_at)

        tail.refresh_from_db()
        plan.refresh_from_db()
        self.assertEqual(tail.status, ReplyBubble.Status.CANCELLED)
        self.assertIsNone(tail.message_id)
        self.assertEqual(plan.status, ReplyPlan.Status.DISCARDED)
        self.assertEqual(list(self.room.messages.filter(sender=Message.Sender.CHARACTER)
                              .values_list('text', flat=True)), ['첫 말풍선'])
        self.assertEqual(other.bubbles.get(position=1).message.text, '다른 후속 말풍선')

    def test_edit_invalidates_prepared_replies_in_every_owned_room(self):
        plans = [self.ask(self.room), self.ask(self.second_room), self.ask(self.other_room)]
        for _ in plans:
            self.prepare(self.echo)
        self.change(self.owner, NEW)
        self.prepare(self.echo)
        self.prepare(self.echo)
        self.assertFalse(prepare_next_reply())
        dispatch_due(plans[0].reply_at)
        for plan, text, attempts in zip(plans, (NEW, NEW, '요즘 무슨 음악 들어?'), (2, 2, 1)):
            with self.subTest(room=plan.room_id):
                plan.refresh_from_db()
                self.assertEqual(plan.attempts, attempts)
                self.assertEqual(plan.bubbles.get().message.text, text)

    def test_repeated_edits_never_extend_the_fallback_deadline(self):
        plan = self.ask()
        self.prepare(self.echo)
        self.change(self.owner, NEW)
        self.clock.return_value = plan.reply_at
        dispatch_due(plan.reply_at)
        deadline = plan.reply_at + PREFERENCE_RETRY_GRACE

        def invalidated(context):
            self.change(self.owner, None)
            return self.echo(context)

        self.prepare(invalidated)
        plan.refresh_from_db()
        self.assertEqual(plan.preference_retry_until, deadline)
        self.assertEqual(plan.result, [])
        self.assertIsNone(plan.generation_token)
        self.clock.return_value = deadline
        self.assertFalse(prepare_next_reply())
        dispatch_due(deadline)
        dispatch_due(deadline)
        plan.refresh_from_db()
        self.assertEqual(plan.model_name, 'fallback')
        self.assertEqual(plan.preference_revision, 3)
        self.assertEqual(plan.used_memory_ids, [])
        self.assertEqual(plan.bubbles.count(), 1)
        self.assertNotIn(NEW, plan.bubbles.get().message.text)

    def test_superseded_lease_cannot_clear_or_overwrite_the_new_lease_result(self):
        plan = self.ask()

        def superseded(context):
            self.change(self.owner, NEW)
            self.prepare(self.echo)
            return self.echo(context)

        self.prepare(superseded)
        plan.refresh_from_db()
        self.assertEqual(plan.attempts, 2)
        self.assertEqual(plan.result, [NEW])
        self.assertIsNone(plan.generation_token)
        dispatch_due(plan.reply_at)
        self.assertEqual(plan.bubbles.get().message.text, NEW)
