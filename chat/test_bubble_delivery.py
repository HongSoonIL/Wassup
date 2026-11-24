from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from threading import Barrier
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings

from chat import replies
from chat.models import Friendship, Message, ReplyBubble, ReplyPlan
from chat.replies import accept_message, dispatch_due, prepare_next_reply
from world.models import Character, RoutineTemplate, State


NOW = datetime(2026, 11, 24, 15, tzinfo=ZoneInfo('Asia/Seoul'))
TEST_SETTINGS = {
    'DEBUG': True,
    'WASSUP_REPLY_DEMO_SECONDS': 8,
    'LLM_BACKEND': 'openai',
    'LLM_MODEL': 'test-model',
    'CHANNEL_LAYERS': {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
}


def make_room():
    owner = get_user_model().objects.create_user(username='owner')
    return Friendship.objects.create(user=owner, character=Character.objects.create(name='서윤', age=22))


def start_plan(room, texts):
    result = {'bubbles': texts, 'used_memory_ids': []}
    with patch('chat.replies.world_now', return_value=NOW), patch('chat.replies.openai_reply', return_value=result) as model:
        accept_message(room.pk, '뭐 골랐어?')
        prepare_next_reply()
        prepare_next_reply()
        model.assert_called_once()
    return room.reply_plans.get()


@override_settings(**TEST_SETTINGS)
class BubbleDeliveryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.room = make_room()

    def delivered(self):
        return list(self.room.messages.filter(sender=Message.Sender.CHARACTER).values_list('text', flat=True))

    def interrupt_in_leisure(self, after):
        plan = start_plan(self.room, ['첫째', '둘째'])
        dispatch_due(plan.reply_at)
        RoutineTemplate.objects.create(character=self.room.character, weekday=NOW.weekday(), start_time=time(14),
                                       end_time=time(17), state=State.LEISURE)
        instant = plan.reply_at + after
        with patch('chat.replies.world_now', return_value=instant), override_settings(DEBUG=False):
            question = accept_message(self.room.pk, '잠깐, 어디야?')
        return self.room.reply_plans.get(trigger_message=question).read_at - instant

    def test_fresh_interruption_continues_leisure_conversation_with_phone_open(self):
        self.assertLessEqual(self.interrupt_in_leisure(timedelta(seconds=2)), timedelta(seconds=3))

    def test_interruption_after_worker_outage_keeps_normal_leisure_phone_check(self):
        self.assertGreaterEqual(self.interrupt_in_leisure(timedelta(minutes=20)), timedelta(minutes=10))

    def test_leisure_followup_after_final_bubble_uses_actual_delivery_time(self):
        plan = start_plan(self.room, ['방금 집 왔어'])
        actual = plan.reply_at + timedelta(minutes=15)
        dispatch_due(actual)
        RoutineTemplate.objects.create(character=self.room.character, weekday=NOW.weekday(), start_time=time(14),
                                       end_time=time(17), state=State.LEISURE)
        for age, low, high in [(5, 2, 3), (31, 10, 60), (301, 600, 3600)]:
            with self.subTest(age=age):
                instant = actual + timedelta(seconds=age)
                with patch('chat.replies.world_now', return_value=instant), override_settings(DEBUG=False):
                    question = accept_message(self.room.pk, '뭐 하고 쉴 거야?')
                followup = self.room.reply_plans.get(trigger_message=question)
                delay = (followup.read_at - instant).total_seconds()
                self.assertLessEqual(low, delay)
                self.assertLessEqual(delay, high)
                # Each subcase asks independently after the same delivery.
                followup.delete()

    def test_late_workers_restart_from_the_actual_delivery_and_typing_times(self):
        plan = start_plan(self.room, ['첫째', '둘째', '셋째'])
        late = plan.reply_at + timedelta(hours=1)
        dispatch_due(late)
        self.assertEqual(self.delivered(), ['첫째'])
        first, second, third = plan.bubbles.all()
        self.assertEqual(first.deliver_at, plan.reply_at)
        self.assertEqual(first.message.created_at, late)
        self.assertGreater(second.typing_at, late)
        self.assertIsNone(third.typing_at)

        # No process saw the second typing start. Reopening the room in this pause shows no dots.
        restarted = second.deliver_at + timedelta(minutes=15)
        self.client.force_login(self.room.user)
        url = f'/api/rooms/{self.room.pk}/messages/'
        with patch('chat.views.world_now', return_value=restarted):
            self.assertIsNone(self.client.get(url).json()['typing_until'])
        dispatch_due(restarted)
        dispatch_due(restarted)
        second.refresh_from_db()
        self.assertEqual(second.typing_at, restarted)
        self.assertEqual(second.deliver_at, restarted + timedelta(seconds=2))
        with patch('chat.views.world_now', return_value=restarted):
            page = self.client.get(url).json()
            self.assertEqual(datetime.fromisoformat(page['typing_until']), second.deliver_at)
            self.assertEqual([m['text'] for m in page['messages'] if m['sender'] == 'character'], ['첫째'])

        # Another worker opens a new transaction long after that typing interval ended.
        later = second.deliver_at + timedelta(days=1)
        dispatch_due(later)
        dispatch_due(later)
        self.assertEqual(self.delivered(), ['첫째', '둘째'])
        third.refresh_from_db()
        self.assertGreater(third.typing_at, later)
        dispatch_due(third.typing_at)
        dispatch_due(third.deliver_at)
        self.assertEqual(self.delivered(), ['첫째', '둘째', '셋째'])
        plan.refresh_from_db()
        self.assertEqual(plan.status, ReplyPlan.Status.SENT)

    def test_slow_model_cannot_replace_the_sequence_started_at_its_deadline(self):
        with patch('chat.replies.world_now', return_value=NOW):
            accept_message(self.room.pk, '어떤 게 좋아?')
            plan = self.room.reply_plans.get()

            def slow_model(context):
                dispatch_due(plan.reply_at)
                return {'bubbles': ['늦게 온 응답'], 'used_memory_ids': []}

            with patch('chat.replies.openai_reply', side_effect=slow_model) as model:
                prepare_next_reply()
                prepare_next_reply()
                model.assert_called_once()
        plan.refresh_from_db()
        self.assertEqual(plan.model_name, 'fallback')
        self.assertEqual(self.delivered(), plan.result)
        self.assertNotIn('늦게 온 응답', plan.result)
        self.assertEqual(plan.bubbles.get().message.created_at, plan.reply_at)

    def test_new_question_during_typing_uses_only_history_already_delivered(self):
        plan = start_plan(self.room, ['주황으로 했어', '말하지 않은 이유', '말하지 않은 다음 주 계획'])
        dispatch_due(plan.reply_at)
        second = plan.bubbles.get(position=1)
        dispatch_due(second.typing_at)
        with patch('chat.replies.world_now', return_value=second.typing_at):
            with patch('chat.replies.publish_room_events') as publish, self.captureOnCommitCallbacks(execute=True):
                question = accept_message(self.room.pk, '지금 어디야?')
            self.assertEqual(publish.call_args.args[1], [{'type': 'typing.stop'}])
            with patch('chat.replies.openai_reply', return_value={'bubbles': ['작업실이야'], 'used_memory_ids': []}) as model:
                prepare_next_reply()
        turns = model.call_args.args[0]['recent_turns']
        self.assertEqual([m['text'] for m in turns], ['뭐 골랐어?', '주황으로 했어', question.text])
        plan.refresh_from_db()
        self.assertEqual(plan.status, ReplyPlan.Status.DISCARDED)
        self.assertEqual(list(plan.bubbles.values_list('status', flat=True)),
                         [ReplyBubble.Status.SENT, ReplyBubble.Status.CANCELLED, ReplyBubble.Status.CANCELLED])
        replacement = self.room.reply_plans.get(trigger_message=question)
        self.assertGreater(replacement.reply_at, plan.reply_at)
        dispatch_due(replacement.reply_at)
        dispatch_due(replacement.reply_at + timedelta(minutes=1))
        self.assertEqual(self.delivered(), ['주황으로 했어', '작업실이야'])


@override_settings(**TEST_SETTINGS)
class ConcurrentBubbleDeliveryTests(TransactionTestCase):
    def test_two_workers_claiming_each_due_phase_emit_one_ordered_sequence(self):
        room = make_room()
        plan = start_plan(room, ['첫째', '둘째'])
        dispatch = replies._dispatch_plan
        gate = Barrier(2, timeout=5)

        def dispatch_together(*args):
            # Both workers selected the same due row before either takes the room lock.
            gate.wait()
            dispatch(*args)

        def tick(now):
            try:
                dispatch_due(now)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as workers, patch('chat.replies._dispatch_plan', dispatch_together), \
                patch('chat.replies.publish_room_events') as publish:
            list(workers.map(tick, [plan.reply_at] * 2))
            second = plan.bubbles.get(position=1)
            list(workers.map(tick, [second.typing_at] * 2))
            list(workers.map(tick, [second.deliver_at] * 2))
        events = [event for call in publish.call_args_list for event in call.args[1]]
        self.assertEqual([event['type'] for event in events], [
            'message.read', 'typing.stop', 'message.new', 'typing.start', 'typing.stop', 'message.new',
        ])
        delivered_ids = list(room.messages.filter(sender=Message.Sender.CHARACTER).values_list('pk', flat=True))
        self.assertEqual(list(plan.bubbles.values_list('message_id', flat=True)), delivered_ids)
        self.assertEqual([event['message']['id'] for event in events if event['type'] == 'message.new'], delivered_ids)
        self.assertEqual(len(delivered_ids), 2)
