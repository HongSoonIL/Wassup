from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings

from chat.models import Friendship
from chat.replies import accept_message
from chat.test_memory_replies import NOW, SETTINGS
from accounts.models import User
from llm import dialogue
from world.models import Character
from . import exam_results, services
from .models import Memory


@override_settings(**SETTINGS)
class ExamRecallTests(TestCase):
    def setUp(self):
        user = User.objects.create_user('tester')
        self.room, self.peer = [Friendship.objects.create(user=user, character=Character.objects.create(name=n, age=22))
                                for n in ('서윤', '도현')]
        self.client.force_login(user)
        self.url = f'/api/rooms/{self.room.pk}/memories/'
        self.change(enabled=True)

    def change(self, id=None, **fields):
        url = self.url + (f'{id}/' if id else '')
        response = self.client.patch(url, {**fields, 'expected_revision': services.memory_revision(self.room.pk)},
                                     content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def say(self, text, days=0, room=None):
        when = NOW + timedelta(days=days)
        with patch('chat.replies.world_now', return_value=when):
            message = accept_message((room or self.room).pk, text)
            return dialogue.context_for(message.reply_plans.get(), when)

    def test_no_backfill_and_only_confirmed_personal_results(self):
        self.say('통계 70점 받았어')
        self.change(results_enabled=True)
        for text in ['엄마가 통계 86점 받았어', '민수는 시험 86점 받았어', '통계 86점 예상', '통계 86점 목표야',
                     '통계 86점 받았어?', '통계 101점 받았어', '통계 88.5점이야', '맞아 70점이었어']:
            self.say(text)
        self.assertFalse(Memory.objects.exists())
        self.say('나 통계 중간고사 86점 받았어')
        self.say('통계 기말고사 93점 나왔어')
        self.assertEqual(set(Memory.objects.values_list('text', 'score')), {('통계 중간고사', 86), ('통계 기말고사', 93)})
        self.assertEqual(self.say('내 통계 시험 몇 점이었지?', 1)['authorized_memories'], [])
        self.assertEqual(self.say('내 통계 기말 몇 점이었지?', 1)['authorized_memories'][0]['detail'], '93점')
        self.assertFalse(self.say('내 통계 기말 몇 점이었지?', 1, self.peer)['authorized_memories'])

    def test_boundaries_and_repeated_question_do_not_restore_digits(self):
        self.change(results_enabled=True)
        self.say('통계 86점 받았어')
        for day, detail in [(1, '86점'), (13, '86점'), (14, '80점대'), (30, '80점대'), (89, '80점대'), (90, ''), (365, '')]:
            context = self.say('내 통계 시험 몇 점이었지?', day)
            entry, = context['authorized_memories']
            self.assertEqual(entry['detail'], detail)
            reply = dialogue.fake_reply(context)
            self.assertEqual(reply['used_memory_ids'], [entry['id']])
            if day >= 14:
                self.assertNotIn('86점', str(context))
                self.assertNotIn('86', str(reply['bubbles']))
        self.assertFalse(self.say('오늘 뭐 해?', 365)['authorized_memories'])
        self.say('아 맞다, 통계 시험', 365)
        self.assertEqual(self.say('정확히 몇 점?', 365)['authorized_memories'][0]['detail'], '')
        self.say('맞아, 86점이었어', 365)
        self.assertEqual(self.say('내 통계 점수 기억나?', 366)['authorized_memories'][0]['detail'], '86점')

    def test_correction_pin_owner_controls_and_purge(self):
        self.change(results_enabled=True)
        self.say('통계 86점 받았어')
        self.say('86이 아니라 89였어')
        self.say('이 점수는 꼭 기억해줘')
        entry, = self.say('내 통계 몇 점이었지?', 365)['authorized_memories']
        self.assertEqual(entry['detail'], '89점')
        snapshot = self.change(entry['id'], pinned=False)
        self.assertEqual(snapshot['memories'][0]['score'], 89)
        self.assertEqual(self.say('내 통계 몇 점이었지?', 365)['authorized_memories'][0]['detail'], '')
        with patch('memory.views.world_now', return_value=NOW + timedelta(days=365)):
            self.change(entry['id'], score=100)
        self.assertEqual(self.say('내 통계 몇 점이었지?', 366)['authorized_memories'][0]['detail'], '100점')
        self.assertEqual(self.say('내 통계 몇 점이었지?', 379)['authorized_memories'][0]['detail'], '')
        self.change(results_enabled=False)
        self.change(results_enabled=True)
        self.assertFalse(self.say('내 통계 몇 점이었지?', 380)['authorized_memories'])
        self.assertTrue(self.client.get(self.url).json()['enabled'])

    def test_old_numbers_in_derived_history_and_unfounded_precision(self):
        item = dict(kind='result', id=5, text='통계 시험', confirmed_at=NOW.isoformat(), pinned=False, score=86)
        turns = [dict(sender='user', text='혹시 통계 86점이었나?'), dict(sender='character', text='응 86')]
        exam_results.limit_history(turns, [item], NOW.date() + timedelta(days=30))
        self.assertNotIn('86', str(turns))
        faded = exam_results.project(item, NOW.date() + timedelta(days=30))
        self.assertTrue(exam_results.overprecise(['아마 85점일걸?'], faded))
        self.assertFalse(exam_results.overprecise(['80점대였지'], faded))

    @patch('chat.replies.openai_reply', side_effect=ValueError('provider unavailable'))
    def test_timeout_and_deadline_deliver_only_allowed_detail(self, _provider):
        from chat.replies import dispatch_due, prepare_next_reply
        self.change(results_enabled=True)
        self.say('통계 86점 받았어')
        dispatch_due(self.room.reply_plans.get().reply_at)
        for day, expected in [(30, '80점대'), (100, '통계 시험')]:
            self.say('내 통계 시험 몇 점이었지?', day)
            plan = self.room.reply_plans.latest('pk')
            with patch('chat.replies.world_now', return_value=NOW + timedelta(days=day)), override_settings(LLM_BACKEND='openai'):
                if day == 30:
                    prepare_next_reply()
                dispatch_due(plan.reply_at)
            plan.refresh_from_db()
            self.assertIn(expected, plan.result[0])
            self.assertNotIn('86', plan.result[0])
            self.assertEqual(plan.used_memory_ids, [Memory.objects.get(kind='result').pk])

    def test_deletion_preserves_pending_preference_and_fences_chat(self):
        from .models import MemoryJob
        self.change(results_enabled=True)
        self.say('통계 86점 받았어. 이 점수 꼭 기억해줘')
        item = Memory.objects.get(kind='result')
        self.assertTrue(item.pinned)
        self.say('난 피스타치오 젤라토가 좋아')
        pending = MemoryJob.objects.get(message__text__contains='젤라토')
        response = self.client.delete(f'{self.url}{item.pk}/', {'expected_revision': services.memory_revision(self.room.pk)},
                                      content_type='application/json')
        self.assertEqual(response.json()['memories'], [])
        self.assertTrue(MemoryJob.objects.filter(pk=pending.pk).exists())
        context = self.say('내 통계 시험 몇 점이었지?')
        self.assertFalse(context['authorized_memories'])
        self.assertNotIn('86점', str(context['recent_turns']))

    def test_delayed_delivery_refreshes_prepared_exact_answer(self):
        from chat.replies import dispatch_due, prepare_next_reply
        self.change(results_enabled=True)
        self.say('통계 86점 받았어')
        dispatch_due(self.room.reply_plans.get().reply_at)
        self.say('통계 몇 점이었지?', 13)
        with patch('chat.replies.world_now', return_value=NOW + timedelta(days=13)):
            prepare_next_reply()
        plan = self.room.reply_plans.latest('pk')
        self.assertIn('86점', plan.result[0])
        dispatch_due(NOW + timedelta(days=14))
        plan.refresh_from_db()
        self.assertIn('80점대', plan.result[0])

    def test_result_requires_a_request_and_older_scores_leave_the_prompt(self):
        self.change(results_enabled=True)
        self.say('통계 86점 받았어')
        # Keep unrelated numbers that belong to the same conversation.
        self.room.messages.create(sender='character', text='통계 86점이었지. 버스는 52번 타면 돼', created_at=NOW)
        for question in ('통계 점수 때문에 엄마랑 싸웠어', '지난번 통계보다 이번 게 더 어려웠어',
                         '통계 성적 떨어졌어', '아 됐고, 너 오늘 뭐 해?', '너 통계 몇 점이었더라?'):
            with self.subTest(question=question):
                ctx = self.say(question)
                self.assertNotIn('result', [m['kind'] for m in ctx['authorized_memories']])
                self.assertNotIn('86', str(ctx['recent_turns']))
                self.assertIn('52번', str(ctx['recent_turns']))
                self.assertEqual(ctx['recent_turns'][-1]['text'], question)
        for q in ('내 통계 몇 점 받았지?', '통계 지난번 점수랑 비교해줘', '통계 92점이면 지난번보다 오른 거야?'):
            mem, = self.say(q)['authorized_memories']
            self.assertEqual(mem['detail'], '86점')

    def test_followups_need_one_exam_in_the_current_exchange(self):
        self.change(results_enabled=True)
        self.say('통계 86점 받았어')
        self.assertFalse(self.say('정확히 몇 점?', 2)['authorized_memories'])
        self.say('통계 몇 점이었지?', 2)
        for _ in range(2):
            self.assertEqual(self.say('정확히 몇 점?', 2)['authorized_memories'][0]['detail'], '86점')
        self.say('수학은 어땠더라?', 2)
        self.assertFalse(self.say('정확히 몇 점?', 2)['authorized_memories'])
        self.say('맞아, 90점이었어', 2)
        self.assertEqual(Memory.objects.get(kind='result').score, 86)
        self.say('통계 중간고사 72점 받았어', 2)
        self.say('통계 기말고사 94점 받았어', 2)
        self.say('통계 중간이랑 기말 얘기야', 2)
        self.assertFalse(self.say('정확히 몇 점?', 2)['authorized_memories'])

    def test_new_disclosure_and_repeat_fallback_have_distinct_replies(self):
        self.change(results_enabled=True)
        self.say('통계 86점 받았어')
        self.say('통계 몇 점이었지?', 365)
        self.room.messages.create(sender='character', text='통계 시험 본 건 기억나는데 점수는 기억 안 나 ㅠㅠ',
                                  created_at=NOW + timedelta(days=365))
        again = self.say('정확히 몇 점?', 365)
        self.assertEqual(dialogue.fake_reply(again)['bubbles'], ['점수까진 모르겠어'])
        fresh = self.say('맞아, 86점이었어', 365)
        self.assertFalse(fresh['authorized_memories'])
        self.assertEqual(fresh['recent_turns'][-1]['text'], '맞아, 86점이었어')
        self.assertEqual(dialogue.fake_reply(fresh)['used_memory_ids'], [])
        self.assertEqual(self.say('통계 몇 점이었지?', 366)['authorized_memories'][0]['detail'], '86점')
