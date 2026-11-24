from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import Client, TestCase, TransactionTestCase, override_settings

from memory import extraction
from memory.models import Memory, MemoryJob, RoomMemoryState
from world.models import Character

from .models import Friendship, Message, ProactiveClaim, ReplyBubble, ReplyPlan
from .replies import accept_message, dispatch_due, prepare_next_reply


JSON = 'application/json'
NOW = datetime(2026, 11, 24, 15, tzinfo=ZoneInfo('Asia/Seoul'))


class FriendManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user('owner', allow_proactive=True)
        cls.other = get_user_model().objects.create_user('other')
        cls.character = Character.objects.create(name='서윤', age=22)
        cls.room = Friendship.objects.create(user=cls.owner, character=cls.character)
        cls.other_room = Friendship.objects.create(user=cls.other, character=cls.character)
        cls.url = f'/api/friends/{cls.room.pk}/'

    def test_room_setting_defaults_off_and_changes_revision_only_when_toggled(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url).json(), {'proactive_enabled': False})
        for enabled, revision in [(False, 0), (True, 1), (True, 1), (False, 2), (False, 2)]:
            with self.subTest(enabled=enabled, revision=revision):
                data = {'proactive_enabled': enabled}
                response = self.client.patch(self.url, data, content_type=JSON)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), data)
                self.assertEqual(self.client.get(self.url).json(), data)
                self.room.refresh_from_db()
                self.assertEqual(self.room.proactive_revision, revision)
        self.other_room.refresh_from_db()
        self.assertFalse(self.other_room.proactive_enabled)
        self.assertEqual(self.other_room.proactive_revision, 0)
        self.assertFalse(RoomMemoryState.objects.exists())

    def test_settings_accept_only_a_json_boolean_field(self):
        self.client.force_login(self.owner)
        for data in ['{', '[]', 'true', '{}', '{"proactive_enabled":1}', '{"proactive_enabled":null}',
                     '{"proactive_enabled":"true"}', '{"proactive_enabled":true,"room_id":2}']:
            with self.subTest(data=data):
                self.assertEqual(self.client.patch(self.url, data, content_type=JSON).status_code, 400)
        self.room.refresh_from_db()
        self.assertFalse(self.room.proactive_enabled)
        self.assertEqual(self.room.proactive_revision, 0)

    def test_guest_and_other_account_cannot_read_change_or_delete(self):
        for user, expected in [(None, 401), (self.other, 403)]:
            if user:
                self.client.force_login(user)
            for response in [
                self.client.get(self.url),
                self.client.patch(self.url, {'proactive_enabled': True}, content_type=JSON),
                self.client.delete(self.url),
            ]:
                self.assertEqual(response.status_code, expected)
        self.room.refresh_from_db()
        self.assertFalse(self.room.proactive_enabled)
        self.assertEqual(self.room.proactive_revision, 0)

    def test_csrf_is_required_for_both_mutations(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.get(self.url).status_code, 200)
        fields = {'proactive_enabled': True}
        self.assertEqual(client.patch(self.url, fields, content_type=JSON).status_code, 403)
        self.assertEqual(client.delete(self.url).status_code, 403)
        csrf = client.cookies['csrftoken'].value
        self.assertEqual(client.patch(self.url, fields, content_type=JSON, HTTP_X_CSRFTOKEN=csrf).status_code, 200)
        self.assertEqual(client.delete(self.url, HTTP_X_CSRFTOKEN=csrf).status_code, 204)

    def test_deletion_cascades_private_data_and_readding_starts_fresh(self):
        self.client.force_login(self.owner)
        self.client.patch(self.url, {'proactive_enabled': True}, content_type=JSON)
        RoomMemoryState.objects.create(room=self.room, enabled=True)
        source = accept_message(self.room.pk, '오늘 통계 시험이야')
        job = MemoryJob.objects.get(message=source)
        memory = Memory.objects.create(source=source, kind=Memory.Kind.SCHEDULE, text='통계 시험', event_date=NOW.date())
        claim = ProactiveClaim.objects.create(schedule=memory, day=NOW.date(), inbound_through=source.pk)
        plan = source.reply_plans.get()
        bubble = ReplyBubble.objects.create(plan=plan, position=0, text='시험 잘 보고 와')
        other_message = Message.objects.create(room=self.other_room, sender=Message.Sender.USER, text='다른 계정의 대화')

        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b'')
        self.assertFalse(RoomMemoryState.objects.filter(room_id=self.room.pk).exists())
        for row in (self.room, source, job, memory, claim, plan, bubble):
            self.assertFalse(type(row).objects.filter(pk=row.pk).exists(), type(row).__name__)
        other_message.refresh_from_db()
        self.assertEqual(self.client.get('/api/friends/').json(), {'friends': []})
        self.assertIsNone(self.client.get('/api/characters/').json()['characters'][0]['room_id'])
        self.assertEqual(self.client.get(f'/api/characters/{self.character.pk}/profile/').status_code, 404)

        response = self.client.post('/api/friends/', {'character_id': self.character.pk}, content_type=JSON)
        self.assertEqual(response.status_code, 201)
        fresh = Friendship.objects.get(pk=response.json()['room_id'])
        self.assertNotEqual(fresh.pk, self.room.pk)
        self.assertEqual(fresh.proactive_revision, 0)
        self.assertFalse(fresh.proactive_enabled)
        self.assertFalse(fresh.messages.exists())
        self.assertFalse(RoomMemoryState.objects.filter(room=fresh).exists())
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.patch(self.url, {'proactive_enabled': True}, content_type=JSON).status_code, 404)
        self.assertEqual(self.client.delete(self.url).status_code, 404)
        messages = f'/api/rooms/{self.room.pk}/messages/'
        self.assertEqual(self.client.get(messages).status_code, 404)
        self.assertEqual(self.client.post(messages, {'text': '이전 탭'}, content_type=JSON).status_code, 404)
        self.assertFalse(fresh.messages.exists())


@override_settings(DEBUG=True, LLM_BACKEND='fake', WASSUP_REPLY_DEMO_SECONDS=20)
class FriendDeletionRaceTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('owner')
        self.character = Character.objects.create(name='서윤', age=22)
        self.room = Friendship.objects.create(user=self.user, character=self.character)
        self.url = f'/api/friends/{self.room.pk}/'
        self.client.force_login(self.user)

    def test_send_returns_missing_if_room_is_deleted_after_ownership_check(self):
        sender = Client()
        sender.force_login(self.user)
        passed_ownership = Event()
        resume = Event()

        def pause_before_acceptance(*args):
            passed_ownership.set()
            if not resume.wait(5):
                raise TimeoutError('Timed out waiting for deletion')
            return accept_message(*args)

        def send():
            try:
                return sender.post(f'/api/rooms/{self.room.pk}/messages/', {'text': '이전 탭'}, content_type=JSON)
            finally:
                connections.close_all()

        with patch('chat.views.accept_message', side_effect=pause_before_acceptance):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(send)
                try:
                    self.assertTrue(passed_ownership.wait(5))
                    self.assertEqual(self.client.delete(self.url).status_code, 204)
                finally:
                    resume.set()
                response = pending.result(timeout=5)

        self.assertEqual(response.status_code, 404)
        self.assertIn('detail', response.json())
        self.assertFalse(Message.objects.exists())
        self.assertFalse(ReplyPlan.objects.exists())
        self.assertFalse(MemoryJob.objects.exists())

    def test_deleting_during_reply_and_memory_generation_discards_both_results(self):
        RoomMemoryState.objects.create(room=self.room, enabled=True)
        with patch('chat.replies.world_now', return_value=NOW):
            source = accept_message(self.room.pk, '오늘 통계 시험이야')
            due = source.reply_plans.get().reply_at

            def delete_during_reply(_context):
                self.assertEqual(self.client.delete(self.url).status_code, 204)
                return {'bubbles': ['생성 중인 답장'], 'used_memory_ids': []}

            def run_reply_during_extraction(*_args):
                with patch('chat.replies.fake_reply', side_effect=delete_during_reply) as reply:
                    prepare_next_reply()
                    reply.assert_called_once()
                return [{'kind': 'schedule', 'text': '통계 시험', 'event_date': NOW.date()}]

            with patch('memory.extraction.extract', side_effect=run_reply_during_extraction) as extract:
                self.assertTrue(extraction.process_next())
                extract.assert_called_once()

        dispatch_due(due)
        for model in (Friendship, Message, ReplyPlan, ReplyBubble, RoomMemoryState, MemoryJob, Memory):
            self.assertFalse(model.objects.exists(), model.__name__)
