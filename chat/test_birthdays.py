from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier
from unittest.mock import patch

from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings

from accounts.birthdays import observed_days
from chat import birthdays, proactive
from chat.models import BirthdayGreeting, Friendship, Message, ProactiveDelivery
from chat.replies import accept_message, dispatch_due, prepare_next_reply
from chat.test_proactive import CHANNELS, DAY, NOW, ProactiveFixtures
from llm.dialogue import context_for
from llm.proactive import FollowupError


@override_settings(LLM_BACKEND='fake', CHANNEL_LAYERS=CHANNELS, DEBUG=True, WASSUP_REPLY_DEMO_SECONDS=16)
class BirthdayTests(ProactiveFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def profile(self, month=DAY.month, day=DAY.day, shared=True):
        fields = dict(nickname='민지', bio='', birthday_month=month, birthday_day=day)
        if shared:
            fields['birthday_shared'] = 'on'
        response = self.client.post('/me/?section=edit', fields)
        self.owner.refresh_from_db()
        return response

    def chat(self, text, finish=True):
        with patch('chat.replies.world_now', return_value=self.clock.return_value):
            msg = accept_message(self.room.pk, text)
            prepare_next_reply()
        plan = msg.reply_plans.get()
        if finish:
            dispatch_due(plan.reply_at)
        return plan

    def test_calendar_and_profile_validation(self):
        self.assertEqual(self.profile(4, 31).status_code, 200)
        self.assertIsNone(self.owner.birthday_month)
        self.assertFalse(self.owner.birthday_shared)
        self.assertEqual(self.profile(2, 29).status_code, 302)
        self.assertIn((2, 29), observed_days(date(2027, 2, 28)))
        self.assertNotIn((2, 29), observed_days(date(2028, 2, 28)))
        self.assertEqual(observed_days(date(2028, 2, 29)), [(2, 29)])
        self.client.post('/me/', {'nickname': '민지', 'bio': '방학'})
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.birthday_shared)
        self.assertEqual(self.owner.birthday_day, 29)
        self.profile('', '')
        self.assertFalse(self.owner.birthday_shared)
        self.assertIsNone(self.owner.birthday_month)

    def test_profile_recall_without_proactive_and_withdrawal_before_delivery(self):
        self.profile()
        self.room.proactive_enabled = False
        self.room.save()
        plan = self.chat('내 생일 언제야?')
        first = plan.bubbles.get().message
        self.assertIn('11월 24일', first.text)
        self.assertTrue(first.birthday_profile_answer)
        self.clock.return_value += timedelta(minutes=10)
        prepared = self.chat('내 생일 언제야?', finish=False)
        self.profile(shared=False)
        self.clock.return_value = prepared.reply_at
        dispatch_due(prepared.reply_at)
        self.clock.return_value += timedelta(seconds=1)
        with patch('chat.replies.world_now', return_value=self.clock.return_value):
            prepare_next_reply()
        dispatch_due(self.clock.return_value + timedelta(seconds=40))
        self.assertNotIn('11월 24일', prepared.bubbles.get().message.text)
        later = self.chat('아까 뭐라고 했지?', finish=False)
        ctx = context_for(later, later.reply_at)
        self.assertNotIn('profile_birthday', ctx)
        self.assertNotIn('11월 24일', str(ctx['recent_turns']))

    def test_rotation_cooldown_and_annual_ledger_survive_readdition(self):
        self.profile()
        second, _ = self.friend('서윤')
        ProactiveDelivery.objects.create(user=self.owner, room=self.room, day=DAY - timedelta(days=1),
                                        sent_at=NOW - timedelta(days=1))
        first = proactive.prepare_next_proactive()
        self.assertEqual(first.room_id, second.pk)
        self.assertIn('축하', first.text)
        # The remaining friend is busy; ordinary outreach is still paused.
        self.slot.state = 'class'
        self.slot.save()
        self.clock.return_value += timedelta(minutes=59)
        self.assertIsNone(proactive.prepare_next_proactive())
        self.assertTrue(birthdays.defer_ordinary(self.owner.pk, DAY, self.clock.return_value))
        self.slot.state = 'free'
        self.slot.save()
        self.clock.return_value += timedelta(minutes=1)
        self.assertEqual(proactive.prepare_next_proactive().room_id, self.room.pk)
        character = second.character
        second.delete()
        Friendship.objects.create(user=self.owner, character=character, proactive_enabled=True)
        self.clock.return_value += timedelta(hours=1)
        self.assertIsNone(birthdays.prepare(DAY, self.clock.return_value))
        self.assertEqual(BirthdayGreeting.objects.filter(sent_at__isnull=False).count(), 2)
        self.assertFalse(ProactiveDelivery.objects.filter(day=DAY).exists())

    def test_only_a_delivered_birthday_reply_uses_the_annual_slot(self):
        self.profile()
        plan = self.chat('오늘 내 생일이야', finish=False)
        self.assertFalse(BirthdayGreeting.objects.exists())
        self.assertIsNone(birthdays.prepare(DAY, NOW))
        dispatch_due(plan.reply_at)
        self.assertIn('축하', plan.bubbles.get().message.text)
        self.assertEqual(BirthdayGreeting.objects.get().sent_at, plan.reply_at)
        self.clock.return_value += timedelta(hours=2)
        self.assertIsNone(birthdays.prepare(DAY, self.clock.return_value))

    def test_claim_rechecks_profile_date_consent_window_and_user_activity(self):
        self.profile()
        def claim():
            return birthdays._claim(self.room.pk, self.owner.pk, DAY)
        def finish(snapshot):
            return birthdays._finish(self.room.pk, DAY, snapshot, '생일 축하해!')
        for change in (lambda: self.profile(day=25), lambda: self.profile(shared=False),
                       lambda: Friendship.objects.filter(pk=self.room.pk).update(proactive_enabled=False)):
            with self.subTest(change=change):
                snapshot = claim()
                self.assertIsNotNone(snapshot)
                change()
                self.assertIsNone(finish(snapshot))
                self.profile()
                Friendship.objects.filter(pk=self.room.pk).update(proactive_enabled=True)
        snapshot = claim()
        self.room.messages.create(sender=Message.Sender.USER, text='오늘 좀 힘들어', created_at=NOW)
        self.assertIsNone(finish(snapshot))
        self.clock.return_value = NOW + timedelta(hours=9)
        self.assertIsNone(claim())
        self.assertFalse(BirthdayGreeting.objects.filter(sent_at__isnull=False).exists())

    def test_window_midnight_reclaim_and_new_year(self):
        self.profile()
        self.clock.return_value = NOW - timedelta(hours=2, seconds=1)
        self.assertIsNone(birthdays._claim(self.room.pk, self.owner.pk, DAY))
        self.clock.return_value = NOW
        stale = birthdays._claim(self.room.pk, self.owner.pk, DAY)
        self.clock.return_value += proactive.LEASE
        live = birthdays._claim(self.room.pk, self.owner.pk, DAY)
        self.assertIsNone(birthdays._finish(self.room.pk, DAY, stale, '생일 축하해!'))
        self.clock.return_value += timedelta(days=1)
        self.assertIsNone(birthdays._finish(self.room.pk, DAY, live, '생일 축하해!'))
        self.clock.return_value = NOW
        self.assertIsNotNone(birthdays.prepare(DAY, NOW))
        next_year = NOW.replace(year=NOW.year + 1)
        self.clock.return_value = next_year
        self.slot.weekday = next_year.date().weekday()
        self.slot.save()
        self.assertIsNotNone(birthdays.prepare(next_year.date(), next_year))

    def test_third_party_wish_does_not_use_users_birthday_slot(self):
        self.profile()
        plan = self.chat('친구 생일 축하 문구 써줘', finish=False)
        birthdays.delivered_in_chat(plan, '생일 축하해!', NOW)
        self.assertFalse(BirthdayGreeting.objects.exists())

    @override_settings(LLM_BACKEND='openai')
    @patch('llm.proactive._request', return_value={'message': '도현아 생일 축하해!'})
    def test_does_not_address_user_by_senders_name(self, provider):
        self.profile()
        text = proactive.prepare_next_proactive().text
        self.assertNotIn('도현', text)
        self.assertIn('축하', text)

    @override_settings(LLM_BACKEND='openai')
    @patch('llm.proactive._request', side_effect=FollowupError)
    def test_provider_failure_still_delivers_once(self, provider):
        self.profile()
        self.assertIn('생일 축하', proactive.prepare_next_proactive().text)
        self.assertIsNone(proactive.prepare_next_proactive())
        provider.assert_called_once()


@override_settings(LLM_BACKEND='fake', CHANNEL_LAYERS=CHANNELS)
class BirthdayConcurrencyTests(ProactiveFixtures, TransactionTestCase):
    def test_two_workers_one_user_one_greeting(self):
        self.owner.birthday_month, self.owner.birthday_day, self.owner.birthday_shared = DAY.month, DAY.day, True
        self.owner.save()
        self.friend('서윤')
        barrier = Barrier(2)
        def run():
            try:
                barrier.wait(timeout=5)
                return birthdays.prepare(DAY, NOW)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertEqual(sum(message is not None for message in results), 1)
        self.assertEqual(Message.objects.filter(sender='character').count(), 1)
