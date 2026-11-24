import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import OperationalError, connections, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings

from chat.models import Friendship, ReplyBubble, ReplyPlan
from chat.replies import accept_message, dispatch_due, prepare_next_reply
from llm import dialogue
from memory import services
from memory.models import Memory, MemoryJob, RoomMemoryState
from world.models import Character, FeedPost, PublicationWeek


NOW = datetime(2026, 11, 24, 15, tzinfo=ZoneInfo('Asia/Seoul'))
SETTINGS = {
    'DEBUG': True, 'WASSUP_REPLY_DEMO_SECONDS': 20, 'LLM_BACKEND': 'fake',
    'CHANNEL_LAYERS': {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
}


def model_response(result):
    output = [{'type': 'message', 'role': 'assistant', 'status': 'completed',
               'content': [{'type': 'output_text', 'text': json.dumps(result)}]}]
    return io.BytesIO(json.dumps({'status': 'completed', 'output': output}).encode())


@override_settings(**SETTINGS)
class RoomMemoryReplyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user('owner')
        peer = get_user_model().objects.create_user('peer')
        artist = Character.objects.create(name='서윤', age=22)
        friend = Character.objects.create(name='도현', age=23)
        cls.room = Friendship.objects.create(user=user, character=artist)
        cls.other_friend = Friendship.objects.create(user=user, character=friend)
        cls.other_user = Friendship.objects.create(user=peer, character=artist)

    def setUp(self):
        self.enterContext(patch('chat.replies.world_now', return_value=NOW))
        self.client.force_login(self.room.user)
        self.url = f'/api/rooms/{self.room.pk}/memories/'
        self.change('patch', self.url, enabled=True)

    def change(self, method, url, **fields):
        data = dict(expected_revision=services.memory_revision(self.room.pk), **fields)
        response = getattr(self.client, method)(url, data, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        return response.json()

    def yesterday(self):
        with patch('chat.replies.world_now', return_value=NOW - timedelta(days=1)):
            source = accept_message(self.room.pk, '내일 통계 시험이야')
            dispatch_due(source.reply_plans.get().reply_at)
        return source

    def extract_schedule(self, source):
        job = MemoryJob.objects.get(message=source)
        token = uuid4()
        MemoryJob.objects.filter(pk=job.pk).update(token=token)
        self.assertTrue(services.finish_job(job.pk, token, [
            {'kind': 'schedule', 'text': '통계 시험', 'event_date': NOW.date()},
        ]))
        return Memory.objects.get(source=source)

    def ask(self, room=None, text='나 오늘 뭐 있는지 기억나?'):
        return accept_message((room or self.room).pk, text).reply_plans.get()

    def test_acceptance_and_extraction_job_roll_back_together(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            msg = accept_message(self.room.pk, '내일 통계 시험이야')
            self.assertTrue(MemoryJob.objects.filter(message=msg).exists())
            raise RuntimeError
        self.assertFalse(self.room.messages.exists())
        self.assertFalse(MemoryJob.objects.exists())

    def test_cross_day_recall_and_used_ids_are_isolated_by_account_and_friend(self):
        memory = self.extract_schedule(self.yesterday())
        for room in (self.room, self.other_user, self.other_friend):
            with self.subTest(room=room.pk):
                plan = self.ask(room)
                context = dialogue.context_for(plan, NOW)
                ids = [memory.pk] if room == self.room else []
                self.assertEqual([m['id'] for m in context['authorized_memories']], ids)
                self.assertNotIn('통계', str(context['recent_turns']))
                prepare_next_reply()
                dispatch_due(plan.reply_at)
                plan.refresh_from_db()
                self.assertEqual(plan.used_memory_ids, ids)
                self.assertEqual('통계 시험' in plan.bubbles.get().message.text, bool(ids))
                if ids:
                    self.assertIn('오늘', plan.bubbles.get().message.text)

    def test_late_extraction_regenerates_a_prepared_answer_before_its_deadline(self):
        source = self.yesterday()
        plan = self.ask()
        prepare_next_reply()
        plan.refresh_from_db()
        self.assertTrue(plan.result)
        self.assertEqual(plan.used_memory_ids, [])
        memory = self.extract_schedule(source)

        def regenerate(context):
            plan.refresh_from_db()
            self.assertEqual(plan.result, [])
            self.assertEqual(plan.used_memory_ids, [])
            return dialogue.fake_reply(context)

        with patch('chat.replies.fake_reply', side_effect=regenerate):
            prepare_next_reply()
        plan.refresh_from_db()
        self.assertIn('통계 시험', plan.result[0])
        self.assertEqual(plan.used_memory_ids, [memory.pk])
        self.assertEqual(plan.attempts, 2)

    def test_deletion_in_flight_never_stores_old_output_and_releases_lease(self):
        memory = self.extract_schedule(self.yesterday())
        plan = self.ask(text='통계 얘기했었지?')

        def slow_model(context):
            self.assertEqual(context['authorized_memories'][0]['id'], memory.pk)
            self.change('delete', f'{self.url}{memory.pk}/')
            return {'bubbles': ['통계 시험의 비공개 내용'], 'used_memory_ids': [memory.pk]}

        with override_settings(LLM_BACKEND='openai'), patch('chat.replies.openai_reply', side_effect=slow_model) as model:
            prepare_next_reply()
            plan.refresh_from_db()
            self.assertEqual(plan.result, [])
            self.assertEqual(plan.used_memory_ids, [])
            self.assertIsNone(plan.generation_token)
            self.assertIsNone(plan.generation_until)
            prepare_next_reply()
            model.assert_called_once()  # The deleted history includes the trigger itself.
        dispatch_due(plan.reply_at)
        plan.refresh_from_db()
        self.assertEqual(plan.bubbles.get().message.text, '어떤 게 궁금했어?')
        self.assertEqual(plan.memory_revision, services.memory_revision(self.room.pk))

    def test_deletion_after_first_bubble_cancels_tail_and_fences_derived_character_history(self):
        source = accept_message(self.room.pk, '내일 통계 시험이야')
        memory = self.extract_schedule(source)
        plan = self.ask()
        with override_settings(LLM_BACKEND='openai'), patch('chat.replies.openai_reply', return_value={
            'bubbles': ['오늘 통계 시험이네', '시험 잘 보고 와'], 'used_memory_ids': [memory.pk],
        }):
            prepare_next_reply()
        dispatch_due(plan.reply_at)
        tail = plan.bubbles.get(position=1)
        dispatch_due(tail.typing_at)
        self.change('delete', f'{self.url}{memory.pk}/')
        dispatch_due(tail.deliver_at)
        tail.refresh_from_db()
        plan.refresh_from_db()
        self.assertIsNone(tail.message_id)
        self.assertEqual(tail.status, ReplyBubble.Status.CANCELLED)
        self.assertEqual(plan.status, ReplyPlan.Status.DISCARDED)
        self.assertEqual(list(self.room.messages.filter(sender='character').values_list('text', flat=True)),
                         ['오늘 통계 시험이네'])
        fresh = self.ask(text='좀 전에 뭐라고 했지?')
        context = dialogue.context_for(fresh, NOW)
        self.assertEqual([t['text'] for t in context['recent_turns']], ['좀 전에 뭐라고 했지?'])
        self.assertNotIn('통계', json.dumps(context, ensure_ascii=False))

    def test_edit_and_disable_fence_existing_triggers_and_new_inputs(self):
        old = self.extract_schedule(self.yesterday())
        plan = self.ask(text='통계 시험 며칠이었지?')
        prepare_next_reply()
        self.change('patch', f'{self.url}{old.pk}/', text='영어 시험')
        self.assertEqual(dialogue.context_for(plan, NOW)['recent_turns'], [])
        fresh = self.ask()
        prepare_next_reply()
        fresh.refresh_from_db()
        self.assertIn('영어 시험', fresh.result[0])
        self.assertNotIn('통계', str(dialogue.context_for(fresh, NOW)))
        self.change('patch', self.url, enabled=False)
        dispatch_due(fresh.reply_at)
        fresh.refresh_from_db()
        self.assertEqual(fresh.used_memory_ids, [])
        self.assertNotIn('시험', fresh.bubbles.get().message.text)
        again = self.ask(text='오늘 무슨 일이야?')
        context = dialogue.context_for(again, NOW)
        self.assertEqual(context['authorized_memories'], [])
        self.assertNotIn('시험', str(context['recent_turns']))

    def test_additive_extraction_between_bubbles_preserves_the_safe_tail(self):
        source = self.yesterday()
        plan = self.ask(text='뭐 하고 있어?')
        with override_settings(LLM_BACKEND='openai'), patch('chat.replies.openai_reply', return_value={
            'bubbles': ['작업 중이야', '거의 다 끝났어'], 'used_memory_ids': [],
        }):
            prepare_next_reply()
        dispatch_due(plan.reply_at)
        tail = plan.bubbles.get(position=1)
        dispatch_due(tail.typing_at)
        self.extract_schedule(source)
        dispatch_due(tail.deliver_at)
        tail.refresh_from_db()
        self.assertEqual(tail.message.text, '거의 다 끝났어')

    def test_unquoted_personal_recall_cannot_inherit_a_characters_public_experience(self):
        week = PublicationWeek.objects.create(start=NOW.date())
        post = FeedPost.objects.create(
            character=self.room.character, week=week, state='published', publish_at=NOW, published_at=NOW,
            caption='주황 포스터를 봤어', public_facts=['서윤이 주황 포스터를 봤다.'],
        )
        # A recent quote can help with a follow-up about the post. This question
        # explicitly asks what the user saw yesterday.
        accept_message(self.room.pk, '사진 잘 나왔다', post.pk)
        plan = self.ask(text='어제 나 뭐 봤는지 다시 기억해?')
        context = dialogue.context_for(plan, NOW)
        self.assertIsNone(context['quoted_post_id'])
        self.assertEqual(context['shared_events'], [])
        prepare_next_reply()
        dispatch_due(plan.reply_at)
        answer = plan.bubbles.get().message.text
        self.assertIn('기억이 안 나', answer)
        self.assertNotIn('포스터', answer)

    def test_failure_and_missed_generation_deadline_use_memory_free_answers(self):
        self.extract_schedule(self.yesterday())
        for prepare in (True, False):
            plan = self.ask()
            if prepare:
                with override_settings(LLM_BACKEND='openai'), patch('llm.dialogue.urlopen', return_value=model_response({
                    'bubbles': ['통계 시험'], 'used_memory_ids': [False],
                })):
                    prepare_next_reply()
            dispatch_due(plan.reply_at)
            plan.refresh_from_db()
            self.assertEqual(plan.used_memory_ids, [])
            self.assertNotIn('통계', plan.bubbles.get().message.text)


class DialogueContractTests(SimpleTestCase):
    def test_adapter_rejects_malformed_claims_and_unoffered_ids(self):
        context = {'character': {'bubble_range': [1, 2]}, 'authorized_memories': [{'id': 27}]}
        good = {'bubbles': ['기억해'], 'used_memory_ids': [27]}
        for bad in ({'bubbles': ['기억해']}, dict(good, other=True), dict(good, bubbles=[' ']),
                    *(dict(good, used_memory_ids=ids) for ids in ([28], [27, 27], [True], ['27'], 27))):
            with self.subTest(output=bad), patch('llm.dialogue.urlopen', return_value=model_response(bad)):
                with self.assertRaises(ValueError):
                    dialogue.openai_reply(context)
        with patch('llm.dialogue.urlopen', return_value=model_response(good)) as transport:
            self.assertEqual(dialogue.openai_reply(context), good)
        schema = json.loads(transport.call_args.args[0].data)['text']['format']['schema']
        self.assertIn('used_memory_ids', schema['required'])
        self.assertEqual(schema['properties']['used_memory_ids']['items']['enum'], [27])

    def test_bounded_inputs_preserve_relevant_past_events_and_recent_preferences(self):
        today = NOW.date()
        facts = [dict(id=i, kind='preference', text=('음악' * 495) + str(i), event_date=None) for i in range(2, 100)]
        past = dict(id=1, kind='schedule', text='통계 시험', event_date=today - timedelta(days=5))
        facts += [past, dict(id=101, kind='schedule', text='영어 시험', event_date=today + timedelta(days=3))]
        question = '지난 통계 시험 기억나?'
        selected = dialogue.relevant_memories(facts, question, today)
        self.assertEqual(selected[0]['id'], past['id'])
        self.assertLessEqual(len(selected), dialogue.MEMORY_LIMIT)
        self.assertLessEqual(len(json.dumps(selected, ensure_ascii=False)), dialogue.MEMORY_CHAR_BUDGET)
        self.assertEqual(selected, dialogue.relevant_memories(list(reversed(facts)), question, today))
        context = {'now': NOW.isoformat(), 'recent_turns': [{'text': question}], 'authorized_memories': selected,
                   'character': {'name': '서윤', 'bubble_range': [1, 2]}, 'shared_events': [],
                   'quoted_post_id': None, 'current_activity': None}
        reply = dialogue.fake_reply(context)
        self.assertEqual(reply['used_memory_ids'], [1])
        self.assertIn('예정이었지', reply['bubbles'][0])
        self.assertIn('11월 19일', reply['bubbles'][0])
        self.assertEqual(dialogue.relevant_memories(facts, '내 취향 기억나?', today)[0]['id'], 99)

    def test_question_before_midnight_keeps_its_date_when_answered_the_next_day(self):
        tomorrow = NOW.date() + timedelta(days=1)
        question = '내일 나 무슨 시험이지?'
        memory = dict(id=9, kind='schedule', text='영어 시험', event_date=tomorrow)
        selected = dialogue.relevant_memories([memory], question, tomorrow, asked_on=NOW.date())
        context = {
            'now': '2026-11-25T00:05:00+09:00',
            'recent_turns': [{'text': question, 'at': '2026-11-24T23:59:00+09:00'}],
            'authorized_memories': selected,
            'character': {'name': '서윤', 'bubble_range': [1, 2]},
            'shared_events': [], 'quoted_post_id': None, 'current_activity': None,
        }
        reply = dialogue.fake_reply(context)
        self.assertEqual(reply['used_memory_ids'], [9])
        self.assertIn('오늘 영어 시험', reply['bubbles'][0])


@override_settings(**SETTINGS)
class MemorySnapshotLockTests(TransactionTestCase):
    def test_revision_and_history_floor_are_read_under_one_room_lock(self):
        user = get_user_model().objects.create_user('owner')
        room = Friendship.objects.create(user=user, character=Character.objects.create(name='서윤', age=22))
        state = RoomMemoryState.objects.create(room=room, enabled=True, revision=1)
        trigger = accept_message(room.pk, '예전 얘기')
        memory = Memory.objects.create(source=trigger, kind='preference', text='재즈 선호')
        plan = trigger.reply_plans.get()
        snapshot_read, resume = Event(), Event()

        def pause_after_read(room_id):
            snapshot = services.memory_snapshot(room_id)
            snapshot_read.set()
            if not resume.wait(timeout=5):
                raise TimeoutError
            return snapshot

        def build():
            try:
                with patch('llm.dialogue.memory_snapshot', side_effect=pause_after_read):
                    return dialogue.context_for(plan, NOW)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(build)
            try:
                self.assertTrue(snapshot_read.wait(timeout=5))
                with self.assertRaises(OperationalError), transaction.atomic():
                    Friendship.objects.select_for_update(nowait=True).get(pk=room.pk)
            finally:
                resume.set()
            before = future.result(timeout=5)
        with transaction.atomic():
            Friendship.objects.select_for_update().get(pk=room.pk)
            services.edit_memory(state, memory, None)
        after = dialogue.context_for(plan, NOW)
        self.assertEqual((before['memory_revision'], before['recent_turns'][0]['text']), (1, '예전 얘기'))
        self.assertEqual((after['memory_revision'], after['recent_turns'], after['authorized_memories']), (2, [], []))
