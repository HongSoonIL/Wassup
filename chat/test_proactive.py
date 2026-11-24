from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from threading import Event
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings

from chat import proactive
from chat.models import Friendship, Message, ProactiveClaim, ProactiveDelivery, ReplyPlan
from chat.replies import accept_message, dispatch_due, prepare_next_reply
from llm import proactive as provider
from memory.models import Memory, RoomMemoryState
from memory.services import set_enabled
from world.models import Character, Persona, State


NOW = datetime(2026, 11, 24, 3, tzinfo=timezone.utc)  # 12:00 KST
DAY = NOW.astimezone(proactive.KST).date()
TEXT = '통계 시험 어땠음?'
CHANNELS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}


class ProactiveFixtures:
    def setUp(self):
        self.clock = self.enterContext(patch('chat.proactive.world_now', return_value=NOW))
        self.owner = get_user_model().objects.create_user(username='owner')
        self.room, self.slot = self.friend('도현')
        self.schedule = self.remember(self.room)

    def friend(self, name, owner=None):
        character = Character.objects.create(name=name, age=23)
        slot = character.routines.create(
            weekday=DAY.weekday(), start_time=time(9), end_time=time(22), state=State.FREE, activity='쉬는 중',
        )
        room = Friendship.objects.create(user=owner or self.owner, character=character, proactive_enabled=True)
        RoomMemoryState.objects.create(room=room, enabled=True)
        return room, slot

    def remember(self, room, *, event_date=DAY - timedelta(days=1), sender=Message.Sender.USER):
        source = room.messages.create(sender=sender, text='내일 통계 시험 있어', created_at=NOW - timedelta(days=2))
        return Memory.objects.create(source=source, kind=Memory.Kind.SCHEDULE, text='통계 시험', event_date=event_date)


@override_settings(LLM_BACKEND='fake', CHANNEL_LAYERS=CHANNELS)
class ProactiveTests(ProactiveFixtures, TestCase):
    def test_legacy_friendship_insert_uses_database_defaults(self):
        character = Character.objects.create(name='서윤', age=22)
        with connections['default'].cursor() as cursor:
            cursor.execute(
                'INSERT INTO chat_friendship (user_id, character_id, created_at) '
                'VALUES (%s, %s, %s) RETURNING proactive_enabled, proactive_revision',
                [self.owner.pk, character.pk, NOW],
            )
            self.assertEqual(cursor.fetchone(), (False, 0))

    def test_kst_window_free_slot_and_room_consents(self):
        self.clock.return_value = NOW.replace(hour=0, minute=59)
        with patch.object(provider, 'generate_followup') as generate:
            proactive.prepare_next_proactive()
            self.clock.return_value = NOW.replace(hour=12)
            proactive.prepare_next_proactive()
            self.clock.return_value = NOW.replace(hour=1)
            Friendship.objects.filter(pk=self.room.pk).update(proactive_enabled=False)
            proactive.prepare_next_proactive()
            Friendship.objects.filter(pk=self.room.pk).update(proactive_enabled=True)
            RoomMemoryState.objects.filter(room=self.room).update(enabled=False)
            proactive.prepare_next_proactive()
            RoomMemoryState.objects.filter(room=self.room).update(enabled=True)
            self.room.character.routines.filter(pk=self.slot.pk).update(state=State.LEISURE)
            proactive.prepare_next_proactive()
            generate.assert_not_called()
        self.room.character.routines.filter(pk=self.slot.pk).update(state=State.FREE)
        self.assertEqual(proactive.prepare_next_proactive().text, TEXT)

    def test_delivery_context_event_and_ledger_survive_friendship_removal(self):
        Persona.objects.create(character=self.room.character, personality='차분함', speech_rules='짧게')
        peer = get_user_model().objects.create_user(username='peer')
        peer_room, _ = self.friend('서윤', peer)
        self.remember(peer_room, event_date=DAY)
        self.remember(peer_room, sender=Message.Sender.CHARACTER)
        RoomMemoryState.objects.filter(room=self.room).update(history_floor=self.schedule.source_id - 1)
        self.room.messages.create(sender=Message.Sender.CHARACTER, text='잘 봐', created_at=NOW - timedelta(days=1))
        with patch.object(provider, 'generate_followup', return_value=TEXT) as generate, \
                patch.object(proactive, 'publish_room_events') as publish:
            with self.captureOnCommitCallbacks(execute=True):
                message = proactive.prepare_next_proactive()
                publish.assert_not_called()
            publish.assert_called_once_with(self.room.pk, [{'type': 'message.new', 'message': {
                'id': message.pk, 'sender': 'character', 'source_post': None, 'kind': 'text', 'text': TEXT,
                'status': 'sent', 'created_at': '2026-11-24T12:00:00+09:00', 'read_at': None,
            }}])
            context = generate.call_args.args[0]
            self.assertEqual(set(context), {'name', 'persona', 'activity', 'schedule', 'event_date', 'recent'})
            self.assertEqual(context['persona']['speech_rules'], '짧게')
            self.assertEqual(context['event_date'], '2026-11-23')
            self.assertEqual(context['recent'], [
                {'sender': 'user', 'text': '내일 통계 시험 있어'}, {'sender': 'character', 'text': '잘 봐'},
            ])
            proactive.prepare_next_proactive()
            character = self.room.character
            self.room.delete()
            self.assertFalse(ProactiveClaim.objects.exists())
            replacement = Friendship.objects.create(user=self.owner, character=character, proactive_enabled=True)
            RoomMemoryState.objects.create(room=replacement, enabled=True)
            self.remember(replacement)
            proactive.prepare_next_proactive()
            generate.assert_called_once()
        self.assertIsNone(ProactiveDelivery.objects.get(user=self.owner, day=DAY).room_id)
        self.assertFalse(replacement.messages.filter(sender=Message.Sender.CHARACTER).exists())

    def test_oldest_delivery_wins_with_deterministic_room_tie(self):
        other, _ = self.friend('서윤')
        tied, _ = self.friend('하린')
        self.remember(other)
        self.remember(tied)
        ProactiveDelivery.objects.create(user=self.owner, room=self.room,
                                         day=DAY - timedelta(days=1), sent_at=NOW - timedelta(days=1))
        self.assertEqual(proactive.prepare_next_proactive().room_id, other.pk)
        self.assertIsNone(proactive.prepare_next_proactive())

    def test_discussed_result_declines_and_is_never_offered_again(self):
        self.room.messages.create(sender=Message.Sender.USER, text='시험 말아먹음', created_at=NOW - timedelta(days=1))
        with patch.object(provider, 'generate_followup', wraps=provider.generate_followup) as generate:
            self.assertIsNone(proactive.prepare_next_proactive())
            self.assertIsNone(proactive.prepare_next_proactive())
            generate.assert_called_once()
        self.assertEqual(ProactiveClaim.objects.get().status, ProactiveClaim.Status.DECLINED)
        self.assertFalse(ProactiveDelivery.objects.exists())

    def test_discussed_result_cannot_disappear_when_history_exceeds_bound(self):
        self.room.messages.create(sender=Message.Sender.USER, text='시험 말아먹음', created_at=NOW - timedelta(days=1))
        Message.objects.bulk_create([
            Message(room=self.room, sender=Message.Sender.USER, text=f'다른 얘기 {i}', created_at=NOW)
            for i in range(proactive.HISTORY_LIMIT)
        ])
        with patch.object(provider, 'generate_followup') as generate:
            self.assertIsNone(proactive.prepare_next_proactive())
            generate.assert_not_called()
        self.assertEqual(ProactiveClaim.objects.get().status, ProactiveClaim.Status.DECLINED)

    def test_deletion_floor_disqualifies_an_old_schedule(self):
        RoomMemoryState.objects.filter(room=self.room).update(history_floor=self.schedule.source_id)
        with patch.object(provider, 'generate_followup') as generate:
            self.assertIsNone(proactive.prepare_next_proactive())
            generate.assert_not_called()

    def test_pending_ordinary_reply_prevents_claim(self):
        with patch('chat.replies.world_now', return_value=NOW):
            accept_message(self.room.pk, '뭐해?')
        with patch.object(provider, 'generate_followup') as generate:
            self.assertIsNone(proactive.prepare_next_proactive())
            generate.assert_not_called()
        self.assertFalse(ProactiveClaim.objects.exists())

    def test_memory_consent_withdrawal_and_reenable_during_generation(self):
        def toggle(_context):
            with transaction.atomic():
                Friendship.objects.select_for_update().get(pk=self.room.pk)
                state = RoomMemoryState.objects.get(room=self.room)
                set_enabled(state, False)
                set_enabled(state, True)
            return TEXT

        with patch.object(provider, 'generate_followup', side_effect=toggle):
            self.assertIsNone(proactive.prepare_next_proactive())
        self.assertFalse(ProactiveClaim.objects.exists())
        self.assertFalse(ProactiveDelivery.objects.exists())

    def test_room_removed_during_generation_cleans_up_claim(self):
        room_id = self.room.pk

        def remove_room(_context):
            with transaction.atomic():
                Friendship.objects.select_for_update().get(pk=room_id).delete()
            return TEXT

        with patch.object(provider, 'generate_followup', side_effect=remove_room):
            self.assertIsNone(proactive.prepare_next_proactive())
        self.assertFalse(Message.objects.filter(room_id=room_id).exists())
        self.assertFalse(ProactiveClaim.objects.exists())
        self.assertFalse(ProactiveDelivery.objects.exists())

    def test_proactive_revision_fences_output_after_optout_and_back_in(self):
        def toggle(_context):
            with transaction.atomic():
                room = Friendship.objects.select_for_update().get(pk=self.room.pk)
                for state in (False, True):
                    room.proactive_enabled = state
                    room.proactive_revision += 1
                    room.save(update_fields=['proactive_enabled', 'proactive_revision'])
            return TEXT

        with patch.object(provider, 'generate_followup', side_effect=toggle):
            self.assertIsNone(proactive.prepare_next_proactive())
        self.assertFalse(ProactiveDelivery.objects.exists())
        self.assertEqual(proactive.prepare_next_proactive().text, TEXT)

    def test_memory_revision_change_fences_in_flight_text(self):
        def extract_fact(_context):
            with transaction.atomic():
                Friendship.objects.select_for_update().get(pk=self.room.pk)
                Memory.objects.create(source=self.schedule.source, kind=Memory.Kind.PREFERENCE, text='커피 좋아함')
                RoomMemoryState.objects.filter(room=self.room).update(revision=1)
            return TEXT

        with patch.object(provider, 'generate_followup', side_effect=extract_fact):
            self.assertIsNone(proactive.prepare_next_proactive())
        self.assertFalse(ProactiveDelivery.objects.exists())

    def test_slot_and_window_rechecked_at_delivery(self):
        def end_slot(_context):
            self.room.character.routines.filter(pk=self.slot.pk).update(state=State.CLASS)
            return TEXT

        with patch.object(provider, 'generate_followup', side_effect=end_slot):
            self.assertIsNone(proactive.prepare_next_proactive())
        self.room.character.routines.filter(pk=self.slot.pk).update(state=State.FREE)
        self.clock.return_value = NOW.replace(hour=11, minute=59, second=50)

        def close_window(_context):
            self.clock.return_value += timedelta(seconds=10)
            return TEXT

        with patch.object(provider, 'generate_followup', side_effect=close_window):
            self.assertIsNone(proactive.prepare_next_proactive())
        self.assertFalse(ProactiveDelivery.objects.exists())

    def test_retry_after_provider_failure_with_backoff(self):
        with patch.object(provider, 'generate_followup', side_effect=[provider.FollowupError(), TEXT]) as generate:
            self.assertIsNone(proactive.prepare_next_proactive())
            self.clock.return_value = NOW + timedelta(seconds=59)
            self.assertIsNone(proactive.prepare_next_proactive())
            generate.assert_called_once()
            self.clock.return_value += timedelta(seconds=1)
            self.assertEqual(proactive.prepare_next_proactive().text, TEXT)
        self.assertEqual(ProactiveClaim.objects.get().attempts, 2)

    def test_expired_lease_reclaimed_and_old_token_cannot_send(self):
        old = proactive._claim(self.schedule.pk, self.room.pk, self.owner.pk, DAY)
        self.assertIsNone(proactive._claim(self.schedule.pk, self.room.pk, self.owner.pk, DAY))
        self.clock.return_value += proactive.LEASE
        new = proactive._claim(self.schedule.pk, self.room.pk, self.owner.pk, DAY)
        self.assertNotEqual(old.token, new.token)
        self.assertIsNone(proactive._finish(old, TEXT))
        self.assertEqual(proactive._finish(new, TEXT).text, TEXT)


@override_settings(LLM_BACKEND='fake', DEBUG=True, WASSUP_REPLY_DEMO_SECONDS=4, CHANNEL_LAYERS=CHANNELS)
class ProactiveConcurrencyTests(ProactiveFixtures, TransactionTestCase):
    def test_ordinary_reply_can_finish_while_proactive_generates_and_cancels_it(self):
        entered, release = Event(), Event()
        other, _ = self.friend('서윤')
        self.remember(other)

        def generate(_context):
            self.assertTrue(connections['default'].get_autocommit())
            entered.set()
            self.assertTrue(release.wait(timeout=5))
            return TEXT

        def run_followup():
            try:
                return proactive.prepare_next_proactive()
            finally:
                connections.close_all()

        with patch.object(provider, 'generate_followup', side_effect=generate) as mocked, \
                patch('chat.replies.world_now', return_value=NOW), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_followup)
            try:
                self.assertTrue(entered.wait(timeout=5))
                self.assertIsNone(proactive.prepare_next_proactive())  # The account already has a live lease.
                message = accept_message(self.room.pk, '뭐해?')
                self.assertTrue(prepare_next_reply())
                plan = message.reply_plans.get()
                dispatch_due(plan.reply_at)
                for _ in range(6):
                    plan.refresh_from_db()
                    if plan.status == ReplyPlan.Status.SENT:
                        break
                    bubble = plan.bubbles.filter(message__isnull=True).first()
                    dispatch_due(bubble.typing_at)
                    bubble.refresh_from_db()
                    dispatch_due(bubble.deliver_at)
                self.assertEqual(self.room.reply_plans.get().status, ReplyPlan.Status.SENT)
            finally:
                release.set()
            self.assertIsNone(future.result(timeout=5))
            mocked.assert_called_once()
        other.delete()
        self.assertEqual(ProactiveClaim.objects.get().status, ProactiveClaim.Status.DECLINED)
        with patch.object(provider, 'generate_followup') as generate:
            self.assertIsNone(proactive.prepare_next_proactive())
            generate.assert_not_called()
        self.assertFalse(ProactiveDelivery.objects.exists())
