import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from io import BytesIO, StringIO
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.db import IntegrityError, connection, connections, transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from llm.weekly import WeeklyError, propose, weekly_schema
from llm.weekly_fixture import sample_week
from world.models import Character, FeedPost, PostPhoto, PostRevision, PublicationWeek, SchoolEvent
from world.publication import LEASE, available_weeks, claim_week, generate_week
from world.weekly import calendar_for, candidates_for, contexts_for

KST = ZoneInfo('Asia/Seoul')
MONDAY = date(2027, 4, 19)
NOW = datetime.combine(MONDAY, time(9), KST)


def run_in_worker(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    finally:
        connections.close_all()


@override_settings(LLM_BACKEND='fake', LLM_MODEL='gpt-5.4-mini', LLM_API_KEY='')
class WeeklyGenerationTests(TransactionTestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.enterContext(
            patch.object(
                PostPhoto._meta.get_field('image'), 'storage', FileSystemStorage(location=folder.name)
            )
        )
        for cmd in ('seed_world', 'seed_publication'):
            call_command(cmd, stdout=StringIO())
        self.week = PublicationWeek.objects.create(start=MONDAY)
        self.first = Character.objects.get(name='도현')
        self.artist = Character.objects.get(name='서윤')
        self.enterContext(patch('world.publication.world_now', return_value=NOW))

    def history(self, who, when, caption, state='published'):
        start = when.date() - timedelta(days=when.weekday())
        week, _ = PublicationWeek.objects.get_or_create(start=start, defaults={'generation': 'ready'})
        return FeedPost.objects.create(
            week=week,
            character=who,
            caption=caption,
            public_facts=['fact-' + caption],
            operator_note='private-editor-note',
            state=state,
            publish_at=when,
            published_at=when,
        )

    @override_settings(LLM_BACKEND='openai', LLM_API_KEY='unused-test-value')
    def test_strict_requests_use_only_authorized_preceding_four_weeks(self):
        beginning = NOW.replace(hour=0) - timedelta(weeks=4)
        self.history(self.first, beginning, 'included-boundary')
        self.history(self.first, beginning - timedelta(microseconds=1), 'outside-window')
        self.history(self.artist, NOW - timedelta(days=1), 'artist-history')
        for state in ('draft', 'failed', 'scheduled', 'skipped'):
            self.history(self.first, beginning, 'not-public-' + state, state)
        # Simulate inconsistent legacy week assignment: actual publication time controls visibility.
        past_post = self.history(self.first, beginning + timedelta(days=1), 'target-week-time')
        FeedPost.objects.filter(pk=past_post.pk).update(published_at=NOW.replace(hour=0))
        future = self.history(self.first, beginning + timedelta(days=2), 'future-time')
        FeedPost.objects.filter(pk=future.pk).update(published_at=NOW + timedelta(days=1))
        requests = []

        def respond(request, timeout):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(request.full_url, 'https://api.openai.com/v1/responses')
            payload = json.loads(request.data)
            self.assertEqual(payload['model'], 'gpt-5.4-mini')
            self.assertIs(payload['store'], False)
            form = payload['text']['format']
            self.assertIs(form['strict'], True)
            self.assertEqual(form['type'], 'json_schema')
            self.assertFalse(form['schema']['additionalProperties'])
            context = json.loads(payload['input'])
            self.assertEqual(context['season'], '봄')
            self.assertNotIn('private-editor-note', payload['input'])
            self.assertNotIn('outside-window', payload['input'])
            self.assertNotIn('target-week-time', payload['input'])
            self.assertNotIn('future-time', payload['input'])
            self.assertNotIn('not-public-', payload['input'])
            self.assertTrue(context['character']['voice'])
            self.assertTrue(context['character']['personality'])
            self.assertEqual(len({r['date'] for r in context['routines']}), 7)
            self.assertIn('2027-04-25', context['publish_windows'])
            self.assertEqual(len(context['school_calendar']), 2)
            artist = context['character']['id'] == self.artist.pk
            self.assertEqual(
                [p['caption'] for p in context['recent_posts']],
                ['artist-history'] if artist else ['included-boundary'],
            )
            self.assertEqual(
                [a['key'] for a in context['assets']],
                ['dubu-home', 'studio-print'] if artist else ['cafe-close', 'cafe-interior'],
            )
            fields = form['schema']['properties']['candidates']['items']['properties']
            self.assertEqual(fields['asset_key']['enum'], [a['key'] for a in context['assets']] + [None])
            requests.append(context)
            text = json.dumps(sample_week(context), ensure_ascii=False)
            return BytesIO(
                json.dumps(
                    {
                        'status': 'completed',
                        'output': [
                            {'type': 'message', 'content': [{'type': 'output_text', 'text': text}]},
                        ],
                    }
                ).encode()
            )

        with patch('llm.weekly.urlopen', side_effect=respond):
            generate_week(self.week.pk)
            generate_week(self.week.pk)
        self.assertEqual(len(requests), 2)
        self.week.refresh_from_db()
        self.assertEqual(
            (self.week.revision, self.week.generation, self.week.generation_token), (1, 'ready', None)
        )
        self.assertEqual(self.week.posts.filter(state='draft', approved_at=None).count(), 4)
        self.assertEqual(PostRevision.objects.count(), 4)
        for who in (self.first, self.artist):
            drafts = self.week.posts.filter(character=who)
            self.assertEqual(drafts.count(), 2)
            self.assertEqual(drafts.filter(photo=None, photo_alt='').count(), 1)
            for post in drafts:
                self.assertLess(post.event_at, post.publish_at)
                self.assertIsNotNone(post.versions.get().snapshot['event_at'])

    def test_unused_photos_take_priority_after_a_published_week(self):
        def assets():
            contexts = contexts_for(MONDAY, calendar_for(MONDAY), NOW)
            return {c['character']['name']: [a['key'] for a in c['assets']] for c in contexts}

        previous = self.history(self.artist, NOW - timedelta(days=2), '포스터')
        previous.photo_id = 'studio-print'
        previous.save(update_fields=['photo'])
        self.assertEqual(assets(), {'서윤': ['dubu-home'], '도현': ['cafe-close', 'cafe-interior']})
        other = self.history(self.artist, NOW - timedelta(days=1), '두부')
        other.photo_id = 'dubu-home'
        other.save(update_fields=['photo'])
        self.assertEqual(assets()['서윤'], ['dubu-home', 'studio-print'])

    def test_validation_rejects_wrong_owner_bounds_schedule_links_and_duplicates(self):
        context = contexts_for(MONDAY, calendar_for(MONDAY), NOW)[0]
        valid = sample_week(context)
        row = valid['candidates'][0]
        for changes in [
            {'character_id': self.artist.pk},
            {'asset_key': 'studio-print'},
            {'calendar_ids': [999999]},
            {'event_at': '2027-04-23T17:00:00'},
            {'event_at': row['publish_at']},
            {'photo_alt': ''},
            {'publish_at': '2027-04-26T12:00:00+09:00'},
            {'public_facts': []},
            {'caption': ' '},
            {'event_at': '2027-04-23T05:00:00+09:00'},
            {'event_at': '2027-04-21T17:00:00+09:00'},
            {'event_at': '2027-04-23T10:00:00+09:00', 'publish_at': '2027-04-23T11:00:00+09:00'},
            {'caption': valid['candidates'][1]['caption']},
            {'public_facts': valid['candidates'][1]['public_facts']},
        ]:
            with self.subTest(changes=changes), self.assertRaises(WeeklyError):
                bad = deepcopy(valid)
                bad['candidates'][0].update(changes)
                candidates_for(context, bad, NOW)
        with self.assertRaises(WeeklyError):
            candidates_for(context, {'candidates': [row]}, NOW)
        with self.assertRaisesRegex(WeeklyError, '중복'):
            candidates_for({**context, 'recent_posts': [row]}, valid, NOW)

    def test_wrong_photo_suggestion_leaves_the_asset_for_a_matching_scene(self):
        context = next(c for c in contexts_for(MONDAY, calendar_for(MONDAY), NOW)
                       if c['character']['name'] == '서윤')
        proposal = sample_week(context)
        for row in proposal['candidates']:
            row.update(asset_key='dubu-home', photo_alt='모델이 제안한 설명')
        proposal['candidates'][0].update(caption='손이 차다', public_facts=['작업실에서 따뜻한 음료를 마셨다.'])
        proposal['candidates'][1].update(caption='두부랑 저녁', public_facts=['두부가 담요에 앉았다.'])
        first, second = candidates_for(context, proposal, NOW)
        self.assertEqual((first['photo_id'], first['photo_alt']), (None, ''))
        self.assertEqual(second['photo_id'], 'dubu-home')
        self.assertEqual(second['photo_alt'], PostPhoto.objects.get(pk='dubu-home').alt)

    def test_second_response_failure_no_partial_week_and_explicit_retry(self):
        def propose(context):
            if context['character']['name'] == '서윤':
                return {'candidates': []}
            return sample_week(context)

        with patch('llm.weekly.propose', side_effect=propose) as provider:
            generate_week(self.week.pk)
            generate_week(self.week.pk)
            self.assertEqual(provider.call_count, 2)
        self.week.refresh_from_db()
        self.assertEqual(self.week.generation, 'failed')
        self.assertIn('서윤', self.week.generation_error)
        self.assertFalse(self.week.posts.exists())
        self.assertFalse(PostRevision.objects.exists())
        self.client.force_login(get_user_model().objects.create_user('operator', is_superuser=True))
        for retry, status in ((False, 200), (True, 202), (True, 202)):
            resp = self.client.post(
                '/api/ops/weeks/generate/',
                {'week_start': MONDAY, 'retry_failed': retry},
                content_type='application/json',
            )
            self.assertEqual(resp.status_code, status)
        generate_week(self.week.pk)
        self.week.refresh_from_db()
        self.assertEqual((self.week.generation, self.week.generation_attempts), ('ready', 1))

    def test_storage_failure_rolls_back_both_characters_and_versions(self):
        from world.publication import snapshot

        def fail_after_first_character(post):
            if post.character_id == self.artist.pk:
                raise IntegrityError('simulated constraint failure')
            snapshot(post)

        with (
            patch('world.publication.snapshot', side_effect=fail_after_first_character),
            self.assertLogs('world.publication', level='ERROR'),
        ):
            generate_week(self.week.pk)
        self.week.refresh_from_db()
        self.assertEqual(self.week.generation, 'failed')
        self.assertFalse(self.week.posts.exists())
        self.assertFalse(PostRevision.objects.exists())

    def test_revised_calendar_repeatedly_during_inference_stops_for_manual_retry(self):
        def revise_calendar(context):
            event = SchoolEvent.objects.get(kind='sports')
            event.label += ' 변경'
            event.save(update_fields=['label'])
            return sample_week(context)

        with patch('llm.weekly.propose', side_effect=revise_calendar) as provider:
            generate_week(self.week.pk)
            generate_week(self.week.pk)
            generate_week(self.week.pk)
            self.assertEqual(provider.call_count, 2)
        self.week.refresh_from_db()
        self.assertEqual(self.week.generation, 'failed')
        self.assertIn('학사 일정', self.week.generation_error)
        self.assertFalse(self.week.posts.exists())

    def test_future_timestamp_is_excluded_even_inside_preceding_window(self):
        self.history(self.artist, NOW - timedelta(hours=1), 'already-published')
        self.history(self.artist, NOW + timedelta(hours=1), 'future-published-at')
        target = MONDAY + timedelta(days=7)
        contexts = contexts_for(target, calendar_for(target), NOW)
        artist = next(c for c in contexts if c['character']['id'] == self.artist.pk)
        self.assertEqual([p['caption'] for p in artist['recent_posts']], ['already-published'])

    def test_legacy_reviewed_week_preserved(self):
        self.week.generation, self.week.revision = 'ready', 12
        self.week.save()
        post = self.week.posts.create(
            character=self.artist, caption='운영자 수정본', state='skipped', publish_at=NOW, version=3
        )
        with patch('llm.weekly.propose') as provider:
            generate_week(self.week.pk)
            provider.assert_not_called()
        post.refresh_from_db()
        self.week.refresh_from_db()
        self.assertEqual(
            (self.week.revision, post.version, post.caption, post.state), (12, 3, '운영자 수정본', 'skipped')
        )
        self.assertIsNone(post.event_at)

    def test_calendar_insert_during_inference_and_duplicate_worker(self):
        started, release = Event(), Event()

        def propose(context):
            if context['character']['name'] == '서윤':
                started.set()
                if not release.wait(8):
                    raise AssertionError('test synchronization timeout')
            return sample_week(context)

        with patch('llm.weekly.propose', side_effect=propose) as provider, ThreadPoolExecutor() as pool:
            first = pool.submit(run_in_worker, generate_week, self.week.pk)
            try:
                self.assertTrue(started.wait(5))
                self.assertFalse(self.week.posts.exists())
                with transaction.atomic():
                    PublicationWeek.objects.select_for_update(nowait=True).get(pk=self.week.pk)
                    list(SchoolEvent.objects.select_for_update(nowait=True))
                generate_week(self.week.pk)
                SchoolEvent.objects.create(
                    kind='break',
                    label='새 일정',
                    start_date=MONDAY,
                    end_date_exclusive=MONDAY + timedelta(days=1),
                )
            finally:
                release.set()
            first.result(5)
            self.assertEqual(provider.call_count, 2)
            self.week.refresh_from_db()
            self.assertEqual(self.week.generation, 'pending')
            self.assertFalse(self.week.posts.exists())
            generate_week(self.week.pk)
            self.assertEqual(provider.call_count, 4)
            self.assertEqual(len(provider.call_args.args[0]['school_calendar']), 3)
        self.assertEqual(self.week.posts.count(), 4)

    def test_expired_worker_cannot_overwrite_reclaimed_week(self):
        started, release = Event(), Event()

        def propose(context):
            if not started.is_set():
                started.set()
                if not release.wait(8):
                    raise AssertionError('test synchronization timeout')
                raise WeeklyError('예전 워커의 오류')
            return sample_week(context)

        with patch('llm.weekly.propose', side_effect=propose) as provider, ThreadPoolExecutor() as pool:
            old = pool.submit(run_in_worker, generate_week, self.week.pk)
            try:
                self.assertTrue(started.wait(5))
                PublicationWeek.objects.filter(pk=self.week.pk).update(lease_until=timezone.now() - LEASE)
                self.assertTrue(available_weeks().filter(pk=self.week.pk).exists())
                generate_week(self.week.pk)
            finally:
                release.set()
            old.result(5)
            self.assertEqual(provider.call_count, 3)
        self.week.refresh_from_db()
        self.assertEqual(
            (self.week.generation, self.week.generation_attempts, self.week.generation_error),
            ('ready', 2, ''),
        )
        self.assertEqual(self.week.posts.count(), 4)
        self.assertFalse(available_weeks().exists())

    def test_abandoned_recoveries_have_a_cost_bound(self):
        for _ in range(2):
            self.assertIsNotNone(claim_week(self.week.pk))
            token = PublicationWeek.objects.get(pk=self.week.pk).generation_token
            self.assertIsNotNone(token)
            PublicationWeek.objects.filter(pk=self.week.pk).update(lease_until=timezone.now() - LEASE)
        with patch('llm.weekly.propose') as provider:
            generate_week(self.week.pk)
            provider.assert_not_called()
        self.week.refresh_from_db()
        self.assertEqual(self.week.generation, 'failed')

    def test_worker_dispatches_approved_post_while_model_waits(self):
        started, release, dispatched = Event(), Event(), Event()
        previous = PublicationWeek.objects.create(start=MONDAY - timedelta(days=7), generation='ready')
        item = previous.posts.create(
            character=self.artist,
            state='scheduled',
            approved_version=1,
            caption='승인본',
            photo_id='studio-print',
            publish_at=NOW + timedelta(minutes=5),
        )
        from world.publication import dispatch_publications

        def observe():
            dispatch_publications()
            if FeedPost.objects.filter(pk=item.pk, state='published').exists():
                dispatched.set()

        def propose(context):
            started.set()
            if not release.wait(8):
                raise AssertionError('test synchronization timeout')
            return sample_week(context)

        with (
            patch('llm.weekly.propose', side_effect=propose),
            patch(
                'world.management.commands.run_publication_worker.dispatch_publications', side_effect=observe
            ),
            ThreadPoolExecutor() as pool,
        ):
            worker = pool.submit(
                run_in_worker, call_command, 'run_publication_worker', once=True, interval=0.02
            )
            try:
                self.assertTrue(started.wait(5))
                previous.posts.update(publish_at=NOW)
                self.assertTrue(dispatched.wait(5))
                self.assertFalse(self.week.posts.exists())
            finally:
                release.set()
            worker.result(5)
        self.assertEqual(self.week.posts.count(), 4)


@override_settings(LLM_BACKEND='openai', LLM_API_KEY='unused-test-value', LLM_MODEL='')
class ProviderErrorsTests(SimpleTestCase):
    def test_calendar_references_are_constrained_per_request(self):
        def calendar_field(calendar):
            return weekly_schema(calendar, [])['properties']['candidates']['items']['properties']['calendar_ids']

        self.assertEqual(calendar_field([])['maxItems'], 0)
        self.assertEqual(calendar_field([{'id': 34}])['items']['enum'], [34])
        self.assertNotIn('enum', calendar_field([])['items'])

    def test_refusal_incomplete_and_malformed_response_are_actionable(self):
        for response in (
            {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'refusal'}]}]},
            {'status': 'incomplete', 'output': []},
            {
                'status': 'completed',
                'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'non-json'}]}],
            },
        ):
            with (
                self.subTest(response=response),
                patch('llm.weekly.urlopen', return_value=BytesIO(json.dumps(response).encode())),
            ):
                with self.assertRaisesRegex(WeeklyError, '다시 시도'):
                    propose({})

    def test_provider_rate_limit_is_safe_to_display(self):
        err = HTTPError('mock-url', 429, 'private-provider-error', {}, None)
        with (
            patch('llm.weekly.urlopen', side_effect=err),
            self.assertRaisesRegex(WeeklyError, '한도') as raised,
        ):
            propose({})
        self.assertNotIn('private-provider-error', str(raised.exception))
