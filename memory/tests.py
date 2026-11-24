from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.db import connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from chat.models import Friendship, Message
from chat.replies import accept_message
from world.models import Character
from . import services
from .models import Memory, MemoryJob, RoomMemoryState


EXAM = {'kind': 'schedule', 'text': '통계 시험', 'event_date': date(2027, 4, 22)}
TEA = {'kind': 'preference', 'text': '홍차를 좋아함', 'event_date': None}


def url(room, memory_id=None):
    if memory_id is None:
        return reverse('memory:room-memories', args=[room.pk])
    return reverse('memory:room-memory', args=[room.pk, memory_id])


def claim_message(room, text):
    message = Message.objects.create(room=room, sender=Message.Sender.USER, text=text)
    services.enqueue(message)
    job = MemoryJob.objects.get(message=message)
    job.token, job.lease_until = uuid4(), timezone.now() + timedelta(minutes=1)
    job.save(update_fields=['token', 'lease_until'])
    return job


class MemoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('mina')
        other_user = User.objects.create_user('minsu')
        first = Character.objects.create(name='서윤', major='디자인', age=22)
        second = Character.objects.create(name='도현', major='경영', age=23)
        cls.room = Friendship.objects.create(user=cls.user, character=first)
        cls.other_friend = Friendship.objects.create(user=cls.user, character=second)
        cls.other_account = Friendship.objects.create(user=other_user, character=first)

    def setUp(self):
        self.client.force_login(self.user)

    def patch(self, room, payload, memory_id=None):
        return self.client.patch(url(room, memory_id), payload, content_type='application/json')

    def enable(self, room):
        response = self.patch(room, {'expected_revision': 0, 'enabled': True})
        self.assertEqual(response.json(), {'enabled': True, 'results_enabled': False, 'revision': 1, 'memories': []})

    def remember_exam(self):
        self.enable(self.room)
        job = claim_message(self.room, '목요일에 통계 시험이 있어.')
        self.assertTrue(services.finish_job(job.pk, job.token, [EXAM]))
        return job.message, Memory.objects.get(source_id=job.message_id)

    def test_default_and_enrollment_do_not_backfill_old_messages(self):
        old = Message.objects.create(room=self.room, sender=Message.Sender.USER, text='예전 대화')
        services.enqueue(old)
        response = self.client.get(url(self.room))
        self.assertEqual(response.json(), {'enabled': False, 'results_enabled': False, 'revision': 0, 'memories': []})
        self.assertIn('csrftoken', response.cookies)
        self.assertEqual(services.history_floor(self.room.pk), 0)
        self.assertFalse(RoomMemoryState.objects.exists())
        self.enable(self.room)
        self.assertFalse(MemoryJob.objects.exists())
        with patch('chat.replies.plan_reply', side_effect=RuntimeError), self.assertRaises(RuntimeError):
            accept_message(self.room.pk, '롤백할 메시지')
        self.assertFalse(MemoryJob.objects.exists())
        character = Message.objects.create(room=self.room, sender=Message.Sender.CHARACTER, text='시험 잘 봐!')
        services.enqueue(character)
        self.assertFalse(MemoryJob.objects.exists())
        accepted = accept_message(self.room.pk, '목요일에 통계 시험이 있어.')
        self.assertEqual(MemoryJob.objects.get().message_id, accepted.pk)

    def test_dedup_retry_and_source_visibility(self):
        self.enable(self.room)
        first = claim_message(self.room, '4월 22일에 통계 시험이야.')
        self.assertFalse(services.finish_job(first.pk, uuid4(), [EXAM]))
        self.assertFalse(services.finish_job(first.pk, None, [EXAM]))
        self.assertTrue(services.finish_job(first.pk, first.token, [EXAM, EXAM]))
        self.assertFalse(services.finish_job(first.pk, first.token, [TEA]))
        duplicate = claim_message(self.room, '시험은 4월 22일이야.')
        self.assertFalse(services.finish_job(duplicate.pk, duplicate.token, [EXAM]))
        item = Memory.objects.get()
        public = {'id': item.pk, 'kind': 'schedule', 'text': EXAM['text'], 'event_date': '2027-04-22'}
        self.assertEqual(services.memory_snapshot(self.room.pk), (2, [public]))
        self.assertEqual(self.client.get(url(self.room)).json(), {
            'enabled': True, 'results_enabled': False, 'revision': 2, 'memories': [
                {**public, 'source': {'id': first.message_id, 'text': first.message.text}}
            ],
        })
        self.assertFalse(MemoryJob.objects.exists())

    def test_failed_or_expired_claim_cannot_partially_apply(self):
        self.enable(self.room)
        job = claim_message(self.room, '홍차도 좋아하고 통계 시험도 있어.')
        with self.assertRaises(ValueError):
            services.finish_job(job.pk, job.token, [TEA, {**EXAM, 'event_date': '내일'}])
        self.assertFalse(Memory.objects.exists())
        MemoryJob.objects.filter(pk=job.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        self.assertFalse(services.finish_job(job.pk, job.token, [TEA]))
        self.assertEqual(services.memory_snapshot(self.room.pk), (1, []))
        self.assertTrue(MemoryJob.objects.filter(pk=job.pk).exists())

    def test_room_and_account_scope_and_csrf(self):
        _, item = self.remember_exam()
        self.enable(self.other_friend)
        self.assertEqual(services.memory_snapshot(self.other_friend.pk), (1, []))
        self.assertEqual(services.memory_snapshot(self.other_account.pk), (0, []))
        for method in (self.client.patch, self.client.delete):
            response = method(url(self.other_friend, item.pk), {'expected_revision': 1}, content_type='application/json')
            self.assertEqual(response.status_code, 404)
            response = method(url(self.other_account, item.pk), {'expected_revision': 0}, content_type='application/json')
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(url(self.other_account)).status_code, 403)
        self.assertEqual(self.client.get(reverse('memory:room-memories', args=[999999])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(url(self.room)).status_code, 401)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        blocked = csrf.delete(url(self.room, item.pk), {'expected_revision': 2}, content_type='application/json')
        self.assertEqual(blocked.status_code, 403)
        self.assertTrue(Memory.objects.filter(pk=item.pk).exists())

    def test_owner_edit_fences_inflight_extraction_and_preserves_source(self):
        source, item = self.remember_exam()
        job = claim_message(self.room, '이전 날짜를 다시 말한 메시지')
        tail = Message.objects.create(room=self.room, sender=Message.Sender.CHARACTER, text='목요일에 잘 봐!')
        response = self.patch(self.room, {'text': '경제 시험', 'event_date': '2027-05-01', 'expected_revision': 2}, item.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['memories'], [{
            'id': item.pk, 'kind': 'schedule', 'text': '경제 시험', 'event_date': '2027-05-01',
            'source': {'id': source.pk, 'text': source.text},
        }])
        self.assertEqual(services.history_floor(self.room.pk), tail.pk)
        self.assertFalse(services.finish_job(job.pk, job.token, [EXAM]))
        self.assertFalse(MemoryJob.objects.exists())
        services.enqueue(job.message)
        self.assertFalse(MemoryJob.objects.exists())
        stale = self.patch(self.room, {'text': '예전 탭', 'expected_revision': 2}, item.pk)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()['revision'], 3)
        self.assertIn('새로고침', stale.json()['detail'])
        item.refresh_from_db()
        self.assertEqual(item.text, '경제 시험')

    def test_partial_date_edit_validates_and_clears_date(self):
        _, item = self.remember_exam()
        for value in ('2027-02-29', '20270422', '내일', True):
            response = self.patch(self.room, {'event_date': value, 'expected_revision': 2}, item.pk)
            self.assertEqual(response.status_code, 400)
        cleared = self.patch(self.room, {'event_date': None, 'expected_revision': 2}, item.pk)
        self.assertEqual(cleared.json()['revision'], 3)
        self.assertIsNone(cleared.json()['memories'][0]['event_date'])
        self.assertEqual(cleared.json()['memories'][0]['text'], EXAM['text'])

    def test_delete_fences_all_old_jobs_only_in_this_room_and_allows_new_disclosure(self):
        source, item = self.remember_exam()
        old = claim_message(self.room, '여전히 통계 시험이 목요일이야.')
        pending = claim_message(self.room, '다른 내용')
        MemoryJob.objects.filter(pk=pending.pk).update(token=None, lease_until=None)
        self.enable(self.other_friend)
        elsewhere = claim_message(self.other_friend, '홍차가 좋아.')
        deleted = self.client.delete(url(self.room, item.pk), {'expected_revision': 2}, content_type='application/json')
        self.assertEqual(deleted.json(), {'enabled': True, 'results_enabled': False, 'revision': 3, 'memories': []})
        self.assertFalse(Memory.objects.exists())
        self.assertTrue(Message.objects.filter(pk=source.pk, text=source.text).exists())
        self.assertEqual(services.history_floor(self.room.pk), pending.message_id)
        self.assertFalse(services.finish_job(old.pk, old.token, [EXAM]))
        self.assertEqual(list(MemoryJob.objects.values_list('pk', flat=True)), [elsewhere.pk])
        self.assertTrue(services.finish_job(elsewhere.pk, elsewhere.token, [TEA]))
        new = claim_message(self.room, '새로 말할게. 4월 22일에 통계 시험 봐.')
        self.assertTrue(services.finish_job(new.pk, new.token, [EXAM]))
        self.assertEqual(Memory.objects.get(kind='schedule').source_id, new.message_id)

    def test_opt_out_purges_and_blocks_claims_across_reenable(self):
        self.remember_exam()
        old = claim_message(self.room, '통계 시험 날짜가 맞아.')
        disabled = self.patch(self.room, {'enabled': False, 'expected_revision': 2})
        self.assertEqual(disabled.json(), {'enabled': False, 'results_enabled': False, 'revision': 3, 'memories': []})
        self.assertFalse(Memory.objects.exists())
        self.assertFalse(MemoryJob.objects.exists())
        self.assertEqual(services.history_floor(self.room.pk), old.message_id)
        off = Message.objects.create(room=self.room, sender=Message.Sender.USER, text='기억을 끄고 보낸 메시지')
        services.enqueue(off)
        self.assertFalse(MemoryJob.objects.exists())
        self.assertFalse(services.finish_job(old.pk, old.token, [EXAM]))
        self.assertEqual(self.patch(self.room, {'enabled': True, 'expected_revision': 3}).json()['memories'], [])
        self.assertFalse(services.finish_job(old.pk, old.token, [EXAM]))
        # Even an incorrectly restored old queue entry must fail the floor check at completion.
        replay = MemoryJob.objects.create(message=old.message, token=uuid4())
        self.assertFalse(services.finish_job(replay.pk, replay.token, [EXAM]))
        self.assertEqual(services.memory_snapshot(self.room.pk), (4, []))

    def test_body_validation_is_atomic(self):
        for payload in [{}, {'enabled': True}, {'enabled': True, 'expected_revision': False},
                        {'enabled': 1, 'expected_revision': 0}, {'enabled': True, 'expected_revision': -1}]:
            self.assertEqual(self.patch(self.room, payload).status_code, 400)
        self.enable(self.room)
        job = claim_message(self.room, '홍차를 좋아해.')
        services.finish_job(job.pk, job.token, [TEA])
        item = Memory.objects.get()
        for fields in [{}, {'text': ' '}, {'text': 12}, {'text': 'a' * 1001}, {'kind': 'schedule'},
                       {'event_date': '2027-04-01'}, {'text': 'a\x00b'}]:
            self.assertEqual(self.patch(self.room, {'expected_revision': 2, **fields}, item.pk).status_code, 400)
        self.assertEqual(services.history_floor(self.room.pk), 0)
        self.assertEqual(services.memory_revision(self.room.pk), 2)


class MemoryRevisionRaceTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user('mina')
        friend = Character.objects.create(name='서윤', major='디자인', age=22)
        self.room = Friendship.objects.create(user=self.user, character=friend)
        RoomMemoryState.objects.create(room=self.room, enabled=True, revision=1)

    def test_only_one_editor_can_commit_the_same_expected_revision(self):
        job = claim_message(self.room, '목요일에 시험이 있어.')
        services.finish_job(job.pk, job.token, [EXAM])
        endpoint = url(self.room, Memory.objects.get().pk)
        gate = Barrier(2, timeout=5)

        def change(text):
            try:
                client = Client()
                client.force_login(self.user)
                gate.wait()
                response = client.patch(endpoint, {'expected_revision': 2, 'text': text}, content_type='application/json')
                return response.status_code, response.json()['memories']
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(change, ['경제 시험', '물리 시험']))
        self.assertCountEqual([status for status, _ in results], [200, 409])
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(services.memory_revision(self.room.pk), 3)

    def test_concurrent_extractions_of_same_fact_change_revision_once(self):
        jobs = [claim_message(self.room, text) for text in ('홍차가 좋아.', '나는 홍차를 좋아해.')]
        gate = Barrier(2, timeout=5)

        def finish(job):
            try:
                gate.wait()
                return services.finish_job(job.pk, job.token, [TEA])
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(finish, jobs), [True, False])
        self.assertEqual(services.memory_revision(self.room.pk), 2)
        self.assertEqual(Memory.objects.count(), 1)
        self.assertFalse(MemoryJob.objects.exists())
