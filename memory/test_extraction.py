import json
from datetime import date, datetime, timedelta, timezone as utc_timezone
from io import BytesIO, StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from chat.models import Friendship, Message
from memory import extraction, services
from memory.models import Memory, MemoryJob, RoomMemoryState
from world.models import Character


# Just past midnight in Seoul, still Monday in UTC.
SENT_AT = datetime(2027, 4, 19, 15, 2, tzinfo=utc_timezone.utc)
EXAM = '아 맞다 나 내일 통계 시험이야 ㅠㅠ'
EXAM_FACT = {'kind': 'schedule', 'text': '통계 시험', 'event_date': date(2027, 4, 21)}


def schedule_row(**changes):
    row = dict(kind='schedule', category='study', evidence=EXAM, topic='통계 시험',
               date_expression='내일', subject='self', certainty='stated', attitude=None)
    return row | changes


def envelope(contents):
    return {'status': 'completed', 'output': [
        {'type': 'reasoning', 'summary': []},
        {'type': 'message', 'status': 'completed', 'role': 'assistant', 'content': contents},
    ]}


def wire(response):
    return BytesIO(json.dumps(response, ensure_ascii=False).encode())


def model_response(*rows):
    return wire(envelope([{'type': 'output_text', 'text': json.dumps({'candidates': rows}, ensure_ascii=False)}]))


@override_settings(LLM_BACKEND='fake')
class ExtractionTests(SimpleTestCase):
    def test_dates_use_source_kst_day_and_week(self):
        self.assertEqual(extraction.extract(EXAM, SENT_AT), [EXAM_FACT])
        examples = {
            '오늘': date(2027, 4, 20), '모레': date(2027, 4, 22), '이번 목요일에': date(2027, 4, 22),
            '다음 주 월요일에': date(2027, 4, 26), '2027-05-01': date(2027, 5, 1),
            '5월 1일': date(2027, 5, 1),
        }
        for expression, expected in examples.items():
            with self.subTest(expression=expression):
                [fact] = extraction.extract(f'나 {expression} 통계 시험 봐. 기억해 줘.', SENT_AT)
                self.assertEqual(fact, {**EXAM_FACT, 'event_date': expected})
        nye = datetime(2026, 12, 31, 9, tzinfo=utc_timezone.utc)
        [fact] = extraction.extract('내일 통계 시험 봐.', nye)
        self.assertEqual(fact['event_date'], date(2027, 1, 1))

    def test_informal_explicit_self_disclosures(self):
        for source, text in [
            ('난 요즘 피스타치오 젤라토가 제일 좋더라 ㅎㅎ', '피스타치오 젤라토 선호'),
            ('이번 목요일에 도예 수업 가.', '도예 수업'), ('아 맞다 나 이번 목요일에 도예 수업 가 ㅎㅎ', '도예 수업'),
            ('나는 민트초코를 싫어해', '민트초코 비선호'), ('난 라면 좋아해', '라면 선호'),
            ('내일 문법 검사 수업 가', '문법 검사 수업'),
            ('도현아 오늘도 카페야? 나 내일 통계 시험인데 하나도 모르겠어.', '통계 시험'),
        ]:
            with self.subTest(source=source):
                [fact] = extraction.extract(source, SENT_AT)
                self.assertEqual(fact['text'], text)

    def test_multiple_assertions_and_duplicates(self):
        facts = extraction.extract('나 내일 통계 시험이야. 난 라면 좋아해. 난 라면 좋아해.', SENT_AT)
        self.assertEqual(facts, [EXAM_FACT, {'kind': 'preference', 'text': '라면 선호', 'event_date': None}])

    def test_skip_unsafe_transitory_other_and_ambiguous_sources(self):
        for source in [
            '내가 오늘 무슨 시험 본댔지?', '나 내일 통계 시험 보는 거 맞지', '나는 커피를 좋아해?',
            '내일 통계 시험이 있을지도 몰라', '어제 통계 시험 봤어', '나 이번 주 월요일에 통계 시험 봐',
            '나는 오늘만 커피 좋아해', '나 오늘은 커피가 땡겨', '난 커피를 좋아하지 않아',
            '나 내일 시험 없어', '내일 통계 시험 공부 도와줘', '나 다음 주쯤 통계 시험 봐', '나 2027년 2월 30일 시험 봐',
            '친구가 내일 통계 시험 봐', '민지는 내일 통계 시험 봐', '지수 내일 통계 시험 봐',
            '내일 민지가 통계 시험 봐', '민준이 커피 좋아해', '민지가 그랬어. “나는 커피 좋아해”',
            '나는 커피 좋아해. 전화번호는 010-1234-5678', '난 커피 좋아해. 집 주소는 서울시 강남구야',
            '나 내일 병원에서 통계 시험 봐', '난 커피 좋아해. 지금 우울증 치료 중이야.',
            '나는 OO 정당을 좋아해', '나 내일 가족 혈액 검사 결과 듣고 수업 가',
            '나 내일 통계 시험 봐. 기억하지 마.', '나 내일 통계 시험이야. 농담이야.',
        ]:
            with self.subTest(source=source):
                self.assertEqual(extraction.extract(source, SENT_AT), [])


@override_settings(LLM_BACKEND='openai', LLM_MODEL='test-model', LLM_API_KEY='unit-test-key')
class ResponsesTests(SimpleTestCase):
    def test_real_adapter_accepts_novel_topic_using_verbatim_evidence(self):
        source = '난 요즘 유자 크림 소금빵을 제일 좋아해 ㅎㅎ'
        row = dict(kind='preference', category='food', subject='self', certainty='stated',
                   evidence='유자 크림 소금빵을 제일 좋아해', topic='유자 크림 소금빵', date_expression=None, attitude='like')
        with patch('memory.extraction.urlopen', return_value=model_response(row)) as open_mock:
            facts = extraction.extract(source, SENT_AT)
        self.assertEqual(facts, [{'kind': 'preference', 'text': '유자 크림 소금빵 선호', 'event_date': None}])
        request = open_mock.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.openai.com/v1/responses')
        self.assertEqual(open_mock.call_args.kwargs, {'timeout': extraction.REQUEST_TIMEOUT})
        body = json.loads(request.data)
        self.assertIs(body['store'], False)
        self.assertIs(body['text']['format']['strict'], True)
        self.assertEqual(json.loads(body['input']), {'kst_date': '2027-04-20', 'statements': [source]})

    def test_pottery_date_uses_full_source_span_across_model_variations(self):
        source = '아 맞다 나 다음 주 목요일에 도예 수업 가 ㅎㅎ'
        for expression in ('다음 주 목요일에', '목요일', '다음주 목요일', '2027-04-29'):
            row = schedule_row(evidence='다음 주 목요일에 도예 수업 가', topic='도예', date_expression=expression)
            with self.subTest(expression=expression), patch('memory.extraction.urlopen', return_value=model_response(row)):
                [fact] = extraction.extract(source, SENT_AT)
                self.assertEqual(fact, {'kind': 'schedule', 'text': '도예 수업', 'event_date': date(2027, 4, 29)})

    def test_model_schedule_topic_can_be_new_to_the_fake_vocabulary(self):
        source = '아 맞다 나 모레 현악 사중주 리허설 가 ㅎㅎ'
        row = schedule_row(category='leisure', evidence='나 모레 현악 사중주 리허설 가', topic='현악 사중주 리허설',
                           date_expression='모레')
        with patch('memory.extraction.urlopen', return_value=model_response(row)):
            [fact] = extraction.extract(source, SENT_AT)
        self.assertEqual(fact, {'kind': 'schedule', 'text': '현악 사중주 리허설', 'event_date': date(2027, 4, 22)})

    def test_unsupported_hallucinated_and_third_party_candidates(self):
        for row, source in [
            (schedule_row(topic='경제 시험'), EXAM), (schedule_row(evidence='내일 화학 시험이야'), EXAM),
            (schedule_row(category='food'), EXAM), (schedule_row(subject='other'), EXAM),
            (schedule_row(subject='unknown'), EXAM), (schedule_row(certainty='uncertain'), EXAM),
            (schedule_row(date_expression='모레'), EXAM), (schedule_row(attitude='like'), EXAM),
            (schedule_row(evidence='내일 통계 시험이야'), '민지는 내일 통계 시험이야'),
        ]:
            with self.subTest(row=row), patch('memory.extraction.urlopen', return_value=model_response(row)):
                self.assertEqual(extraction.extract(source, SENT_AT), [])
        with patch('memory.extraction.urlopen') as open_mock:
            self.assertEqual(extraction.extract(EXAM + '. 내 연락처는 private@example.com이야', SENT_AT), [])
            open_mock.assert_not_called()

    def test_malformed_refused_or_incomplete_output_fails_closed(self):
        private = 'private-synthetic-response-body'
        unexpected = schedule_row(extra_field=private)
        responses = [
            BytesIO(private.encode()), wire({'status': 'incomplete', 'output': []}),
            wire(envelope([{'type': 'refusal', 'refusal': private}])),
            wire(envelope([{'type': 'output_text', 'text': '{}'}, {'type': 'refusal', 'refusal': private}])),
            wire(envelope([{'type': 'output_text', 'text': private}])), wire(envelope([])),
            model_response(unexpected), model_response(schedule_row(kind=private)),
            model_response(schedule_row(topic=True)), model_response(*([schedule_row()] * 4)),
            model_response(schedule_row(evidence=private * 100)),
        ]
        for response in responses:
            with self.subTest(response=response), patch('memory.extraction.urlopen', return_value=response):
                with self.assertRaises(extraction.ExtractionError) as caught:
                    extraction.extract(EXAM, SENT_AT)
                self.assertNotIn(private, str(caught.exception))


@override_settings(LLM_BACKEND='fake')
class MemoryWorkerTests(TransactionTestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user(username='memory-worker')
        self.room = Friendship.objects.create(user=owner, character=Character.objects.create(name='서윤', age=22))
        self.state = RoomMemoryState.objects.create(room=self.room, enabled=True)

    def job(self):
        message = Message.objects.create(room=self.room, sender=Message.Sender.USER, text=EXAM, created_at=SENT_AT)
        services.enqueue(message)
        return MemoryJob.objects.get(message=message)

    def test_command_once_commits_exactly_once_and_released_lock_during_inference(self):
        self.job()
        real_extract = extraction.extract

        def checked_extract(text, sent_at):
            self.assertFalse(connection.in_atomic_block)
            return real_extract(text, sent_at)

        out = StringIO()
        with patch('memory.extraction.extract', side_effect=checked_extract) as adapter:
            call_command('run_memory_worker', once=True, interval=0.01, stdout=out, stderr=out)
            call_command('run_memory_worker', once=True, interval=0.01, stdout=out, stderr=out)
        self.assertEqual(adapter.call_count, 1)
        self.assertFalse(MemoryJob.objects.exists())
        self.assertEqual(list(Memory.objects.values('kind', 'text', 'event_date')), [EXAM_FACT])
        self.assertNotIn(EXAM, out.getvalue())
        with self.assertRaises(CommandError):
            call_command('run_memory_worker', once=True, interval=0)

    def test_consent_revoked_between_claim_and_request_skips_adapter(self):
        self.job()
        real_claim = extraction.claim_next

        def claim_and_disable():
            claim = real_claim()
            # Keep the job to specifically test the pre-inference consent read.
            RoomMemoryState.objects.filter(pk=self.room.pk).update(enabled=False)
            return claim

        with patch('memory.extraction.claim_next', side_effect=claim_and_disable), patch('memory.extraction.extract') as adapter:
            self.assertTrue(extraction.process_next())
            adapter.assert_not_called()
        self.assertFalse(Memory.objects.exists())
        self.assertFalse(MemoryJob.objects.exists())

    def test_consent_revoked_during_inference_ignores_result(self):
        self.job()

        def revoke(*_):
            with transaction.atomic():
                Friendship.objects.select_for_update().get(pk=self.room.pk)
                services.set_enabled(self.state, False)
                services.set_enabled(self.state, True)
            return [EXAM_FACT]

        with patch('memory.extraction.extract', side_effect=revoke):
            extraction.process_next()
        self.assertFalse(Memory.objects.exists())
        self.assertFalse(MemoryJob.objects.exists())

    def test_expired_claim_cannot_finish_the_new_attempt(self):
        job = self.job()
        first, _ = extraction.claim_next()
        self.assertIsNone(extraction.claim_next())
        MemoryJob.objects.filter(pk=job.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        second, exhausted = extraction.claim_next()
        self.assertFalse(exhausted)
        self.assertNotEqual(first.token, second.token)
        self.assertFalse(services.finish_job(job.pk, first.token, [EXAM_FACT]))
        self.assertTrue(services.finish_job(job.pk, second.token, [EXAM_FACT]))
        self.assertEqual(Memory.objects.count(), 1)

    def test_failure_backs_off_and_bounds_attempts_even_after_worker_crash(self):
        job = self.job()
        with patch('memory.extraction.extract', side_effect=extraction.ExtractionError('synthetic')) as adapter:
            for attempt in range(1, extraction.MAX_ATTEMPTS + 1):
                self.assertTrue(extraction.process_next())
                if attempt < extraction.MAX_ATTEMPTS:
                    job.refresh_from_db()
                    self.assertIsNone(job.token)
                    self.assertEqual(job.attempts, attempt)
                    self.assertIsNone(extraction.claim_next())
                    MemoryJob.objects.filter(pk=job.pk).update(lease_until=timezone.now())
            self.assertEqual(adapter.call_count, extraction.MAX_ATTEMPTS)
            self.assertFalse(MemoryJob.objects.exists())

            job = self.job()
            MemoryJob.objects.filter(pk=job.pk).update(attempts=extraction.MAX_ATTEMPTS, lease_until=timezone.now())
            self.assertTrue(extraction.process_next())
            self.assertEqual(adapter.call_count, extraction.MAX_ATTEMPTS)
        self.assertFalse(Memory.objects.exists())
        self.assertFalse(MemoryJob.objects.exists())
