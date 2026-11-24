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
from world.publication import LEASE, available_weeks, claim_week, ensure_due_week, generate_week
from world.publication_models import PhotoJob
from world.weekly import calendar_for, candidates_for, contexts_for, due_week_start, weekly_capacity

KST = ZoneInfo('Asia/Seoul')
MONDAY = date(2027, 4, 19)
NOW = datetime.combine(MONDAY, time(9), KST)


def run_in_worker(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    finally:
        connections.close_all()


class CadenceBoundaryTests(SimpleTestCase):
    def test_friday_switches_weeks_at_1800_kst_in_any_process_timezone(self):
        friday = datetime(2027, 4, 23, 9, tzinfo=ZoneInfo('UTC'))
        following = MONDAY + timedelta(weeks=1)
        with timezone.override('America/New_York'):
            for when, expected in [
                (friday - timedelta(microseconds=1), MONDAY),
                (friday, following),
                (friday + timedelta(days=1), following),
                (friday + timedelta(days=2), following),
                (friday + timedelta(days=3), following),
            ]:
                with self.subTest(when=when):
                    self.assertEqual(due_week_start(when), expected)


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

    @override_settings(LLM_BACKEND='openai', LLM_API_KEY='unused-test-value',
                       LLM_WEEKLY_MODEL='gpt-6.2-sol', LLM_WEEKLY_EFFORT='low')
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
            self.assertEqual(payload['model'], 'gpt-6.2-sol')
            self.assertEqual(payload['reasoning'], {'effort': 'low'})
            self.assertIs(payload['store'], False)
            form = payload['text']['format']
            self.assertIs(form['strict'], True)
            self.assertEqual(form['type'], 'json_schema')
            self.assertFalse(form['schema']['additionalProperties'])
            context = json.loads(payload['input'])
            self.assertEqual(context['season'], '봄')
            limits = form['schema']['properties']['candidates']
            self.assertEqual((limits['minItems'], limits['maxItems']), (0, context['candidate_limit']))
            self.assertIn('candidate_limit', payload['instructions'])
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
        self.assertEqual(self.week.posts.filter(state='draft', approved_at=None).count(), 2)
        self.assertEqual(PostRevision.objects.count(), 2)
        for who in (self.first, self.artist):
            drafts = self.week.posts.filter(character=who)
            self.assertEqual(drafts.count(), 1)
            for post in drafts:
                self.assertLess(post.event_at, post.publish_at)
                self.assertIsNotNone(post.versions.get().snapshot['event_at'])

    def test_photos_from_all_four_preceding_weeks_are_excluded_even_if_none_remain(self):
        def assets():
            contexts = contexts_for(MONDAY, calendar_for(MONDAY), NOW)
            return {c['character']['name']: [a['key'] for a in c['assets']] for c in contexts}

        previous = self.history(self.artist, NOW.replace(hour=0) - timedelta(weeks=4), '포스터')
        previous.photo_id = 'studio-print'
        previous.save(update_fields=['photo'])
        self.assertEqual(assets(), {'서윤': ['dubu-home'], '도현': ['cafe-close', 'cafe-interior']})
        other = self.history(self.artist, NOW - timedelta(days=1), '두부')
        other.photo_id = 'dubu-home'
        other.save(update_fields=['photo'])
        self.assertEqual(assets()['서윤'], [])
        previous.published_at -= timedelta(microseconds=1)
        previous.save(update_fields=['published_at'])
        self.assertEqual(assets()['서윤'], ['studio-print'])

    def test_generated_job_results_stay_out_of_the_auto_pair_library(self):
        draft = self.week.posts.create(character=self.artist, caption='비공개 초안', publish_at=NOW)
        asset = PostPhoto.objects.create(key='draft-only', character=self.artist, image='draft.webp',
                                         alt='초안에만 적힌 고유한 사진 설명')
        PhotoJob.objects.create(post=draft, version=1, result=asset, state='ready', scene={})
        context = next(c for c in contexts_for(MONDAY, calendar_for(MONDAY), NOW)
                       if c['character']['id'] == self.artist.pk)
        self.assertEqual([p['key'] for p in context['assets']], ['dubu-home', 'studio-print'])
        self.assertNotIn(asset.alt, json.dumps(context, ensure_ascii=False))
        self.assertTrue(PostPhoto.objects.filter(pk=asset.pk).exists())

    @override_settings(LLM_BACKEND='openai')
    def test_live_capacities_vary_by_person_week_and_load_with_no_minimum(self):
        ordinary, vacation = date(2027, 5, 3), date(2027, 7, 12)
        SchoolEvent.objects.create(kind='break', label='방학', start_date=vacation,
                                   end_date_exclusive=vacation + timedelta(weeks=1))

        def capacities(start, now):
            return {c['character']['name']: c['candidate_limit']
                    for c in contexts_for(start, calendar_for(start), now)}

        for start, expected in [(MONDAY, {'도현': 1, '서윤': 1}), (ordinary, {'도현': 3, '서윤': 1}),
                                (vacation, {'도현': 2, '서윤': 3})]:
            friday = datetime.combine(start - timedelta(days=3), time(18), KST)
            self.assertEqual(capacities(start, friday), expected)
            self.assertEqual(capacities(start, friday + timedelta(days=3)), expected)
        # A frequent poster in the preceding four weeks gets a smaller budget.
        for i in range(8):
            self.history(self.first, NOW + timedelta(minutes=i), f'지난달 공개 글 {i}')
        self.assertEqual(capacities(ordinary, NOW + timedelta(weeks=1)), {'도현': 2, '서윤': 1})
        context = contexts_for(MONDAY, calendar_for(MONDAY), NOW)[0]
        self.assertEqual(candidates_for(context, {'candidates': []}, NOW), [])
        old_fixture = {k: v for k, v in context.items() if k != 'candidate_limit'}
        two = sample_week(old_fixture)
        self.assertEqual(len(candidates_for(old_fixture, two, NOW)), 2)
        with self.assertRaisesRegex(WeeklyError, '0~1'):
            candidates_for(context, two, NOW)

    @override_settings(LLM_BACKEND='openai')
    def test_zero_capacity_quiet_week_skips_paid_requests(self):
        with patch('world.weekly.weekly_capacity', return_value=0), patch('llm.weekly.propose') as provider:
            generate_week(self.week.pk)
            generate_week(self.week.pk)
            provider.assert_not_called()
        self.week.refresh_from_db()
        self.assertEqual((self.week.generation, self.week.revision), ('ready', 1))
        self.assertFalse(self.week.posts.exists())

    def test_routine_load_reduces_capacity_only_for_actual_study_or_work(self):
        start = date(2027, 5, 3)
        classes = [{'date': (start + timedelta(days=i)).isoformat(), 'state': 'class',
                    'start': '09:00:00', 'end': '16:00:00'} for i in range(5)]
        vacation = [{'kind': 'break', 'start_date': start.isoformat(),
                     'end_date_exclusive': (start + timedelta(weeks=1)).isoformat()}]
        self.assertEqual(weekly_capacity(start, self.first, [], classes, 0),
                         weekly_capacity(start, self.first, [], [], 0) - 1)
        self.assertEqual(weekly_capacity(start, self.first, vacation, classes, 0),
                         weekly_capacity(start, self.first, vacation, [], 0))

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
            {'caption': row['public_facts'][0]},
            {'asset_key': None, 'photo_alt': row['caption']},
        ]:
            with self.subTest(changes=changes), self.assertRaises(WeeklyError):
                bad = deepcopy(valid)
                bad['candidates'][0].update(changes)
                candidates_for(context, bad, NOW)
        for length in (0, 1):
            self.assertEqual(len(candidates_for(context, {'candidates': [row][:length]}, NOW)), length)
        with self.assertRaises(WeeklyError):
            candidates_for(context, {'candidates': [row] * 4}, NOW)
        with self.assertRaisesRegex(WeeklyError, '중복'):
            candidates_for({**context, 'recent_posts': [row]}, valid, NOW)

    def test_repeated_multi_post_timetable_and_redated_story(self):
        context = contexts_for(MONDAY, calendar_for(MONDAY), NOW)[0]
        proposal = sample_week(context)
        previous = []
        for i, row in enumerate(proposal['candidates']):
            published = datetime.fromisoformat(row['publish_at']) - timedelta(weeks=1)
            previous.append({'published_at': published.isoformat(), 'caption': f'다른 글 {i}',
                             'public_facts': []})
        with self.assertRaisesRegex(WeeklyError, '요일과 시각'):
            candidates_for({**context, 'recent_posts': previous}, proposal, NOW)
        row = proposal['candidates'][0]
        previous[0]['caption'] = '4월 16일 ' + row['caption']
        row['caption'] = '4월 23일 ' + row['caption']
        with self.assertRaisesRegex(WeeklyError, '중복'):
            candidates_for({**context, 'recent_posts': previous}, proposal, NOW)

    def test_keyless_ordinary_exam_and_vacation_scenes(self):
        winter = date(2027, 1, 18)
        summer = date(2027, 7, 5)
        vacation = SchoolEvent.objects.create(kind='break', label='여름방학', start_date=summer,
                                               end_date_exclusive=summer + timedelta(days=7))
        for start, season, kind in [(winter, '겨울', None), (MONDAY, '봄', 'exams'), (summer, '여름', 'break')]:
            now = datetime.combine(start, time.min, KST) - timedelta(days=2)
            calendar = calendar_for(start)
            for context in contexts_for(start, calendar, now):
                rows = candidates_for(context, sample_week(context), now)
                self.assertEqual(context['season'], season)
                self.assertTrue(context['routines'])
                self.assertTrue(1 <= len(rows) <= 3)
                for row in rows:
                    self.assertTrue(row['photo_alt'])
                    self.assertNotIn(row['photo_alt'], row['public_facts'])
                    self.assertNotEqual(row['photo_alt'], row['caption'])
                if kind is None:
                    self.assertFalse(any(r['calendar_ids'] for r in rows))
                    if context['character']['name'] == '도현':
                        self.assertTrue(any('겨울 장갑' in r['public_facts'][0] for r in rows))
                elif kind == 'break':
                    self.assertTrue(any(r['calendar_ids'] == [vacation.pk] and '방학' in r['public_facts'][0]
                                        for r in rows))
                else:
                    exams = next(e['id'] for e in calendar if e['kind'] == kind)
                    self.assertTrue(any(exams in r['calendar_ids'] for r in rows))

    def test_catchup_keeps_only_future_events_and_trims_elapsed_windows(self):
        thursday = NOW + timedelta(days=3, hours=5)
        for full in contexts_for(MONDAY, calendar_for(MONDAY), NOW):
            self.assertEqual(len(sample_week(full)['candidates']), 2)
            rows = candidates_for(full, sample_week(full), thursday)
            self.assertEqual(len(rows), 1)
            self.assertGreater(rows[0]['event_at'], thursday)
        for late in contexts_for(MONDAY, calendar_for(MONDAY), thursday):
            windows = late['publish_windows']
            self.assertEqual(min(windows), '2027-04-22')
            self.assertEqual(windows['2027-04-22'][0][0], '14:00:00')
            remaining = candidates_for(late, sample_week(late), thursday)
            self.assertTrue(all(row['event_at'] > thursday for row in remaining))

    def test_catchup_after_long_downtime_creates_only_the_due_week_once(self):
        self.week.generation = 'ready'
        self.week.save(update_fields=['generation'])
        august = date(2027, 8, 9)
        wednesday = datetime.combine(august + timedelta(days=2), time(15, 27), KST)
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _: run_in_worker(ensure_due_week, wednesday).pk, range(2)))
        self.assertEqual(ids[0], ids[1])
        with patch('world.publication.world_now', return_value=wednesday), patch(
            'llm.weekly.propose', wraps=sample_week
        ) as provider:
            for _ in range(2):
                call_command('run_publication_worker', once=True, interval=0.02)
            self.assertEqual(provider.call_count, 2)
        self.assertEqual(set(PublicationWeek.objects.values_list('start', flat=True)), {MONDAY, august})
        week = PublicationWeek.objects.get(start=august)
        self.assertEqual((week.generation, week.generation_attempts), ('ready', 1))
        self.assertTrue(week.posts.exists())
        self.assertFalse(week.posts.filter(event_at__lte=wednesday).exists())

    def test_friday_quiet_week_and_failed_or_edited_week_are_terminal_across_restarts(self):
        self.week.generation = 'failed'
        self.week.generation_error = '운영자 확인 필요'
        self.week.save()
        friday = datetime.combine(MONDAY + timedelta(days=4), time(18), KST)
        following = MONDAY + timedelta(weeks=1)
        with patch('llm.weekly.propose', return_value={'candidates': []}) as provider:
            for now in (friday - timedelta(seconds=1), friday, friday + timedelta(days=1),
                        friday + timedelta(days=3)):
                with patch('world.publication.world_now', return_value=now):
                    call_command('run_publication_worker', once=True, interval=0.02)
            self.assertEqual(provider.call_count, 2)
            week = PublicationWeek.objects.get(start=following)
            self.assertEqual((week.generation, week.revision, week.generation_attempts), ('ready', 1, 1))
            self.assertFalse(week.posts.exists())
            self.assertFalse(PhotoJob.objects.exists())
            self.week.refresh_from_db()
            self.assertEqual((self.week.generation, self.week.generation_error), ('failed', '운영자 확인 필요'))
            # Older edited rows can have a pending generation flag and no remaining candidates.
            week.generation, week.revision = 'pending', 7
            week.save()
            generate_week(week.pk)
            self.assertEqual(provider.call_count, 2)
            week.refresh_from_db()
            self.assertEqual((week.generation, week.revision), ('ready', 7))

    def test_six_new_scenes_queue_only_six_initial_jobs(self):
        def three(context):
            rows = sample_week(context)['candidates']
            for row in rows:
                row['asset_key'] = None
            rows.append({**rows[-1], 'event_at': '2027-04-25T15:00:00+09:00',
                         'publish_at': '2027-04-25T15:13:00+09:00', 'calendar_ids': [],
                         'public_facts': ['책갈피를 접어 읽던 책에 끼웠다.'], 'caption': '여기까지 읽음',
                         'photo_alt': '펼친 책 모서리에 끼운 노란 책갈피'})
            return {'candidates': rows}

        with patch('llm.weekly.propose', side_effect=three):
            generate_week(self.week.pk)
            generate_week(self.week.pk)
        jobs = PhotoJob.objects.filter(initial=True, state='queued', post__week=self.week)
        self.assertEqual(jobs.count(), 6)
        self.assertEqual(set(jobs.values_list('post_id', flat=True)),
                         set(self.week.posts.values_list('pk', flat=True)))
        self.assertEqual(self.week.posts.filter(state='draft', approved_at=None, photo=None).count(), 6)

    def test_wrong_photo_suggestion_leaves_the_asset_for_a_matching_scene(self):
        context = next(c for c in contexts_for(MONDAY, calendar_for(MONDAY), NOW)
                       if c['character']['name'] == '서윤')
        proposal = sample_week(context)
        for row in proposal['candidates']:
            row.update(asset_key='dubu-home', photo_alt='작업대 위 김이 나는 흰 머그잔')
        proposal['candidates'][0].update(caption='손이 차다', public_facts=['작업실에서 따뜻한 음료를 마셨다.'])
        proposal['candidates'][1].update(caption='두부랑 저녁', public_facts=['두부가 담요에 앉았다.'])
        first, second = candidates_for(context, proposal, NOW)
        self.assertEqual((first['photo_id'], first['photo_alt']), (None, '작업대 위 김이 나는 흰 머그잔'))
        self.assertEqual(second['photo_id'], 'dubu-home')
        self.assertEqual(second['photo_alt'], PostPhoto.objects.get(pk='dubu-home').alt)

    def test_second_response_failure_no_partial_week_and_explicit_retry(self):
        def propose(context):
            if context['character']['name'] == '서윤':
                return {'candidates': 'not an array'}
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
        self.assertFalse(PhotoJob.objects.exists())
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
        self.assertFalse(PhotoJob.objects.exists())

    def test_photo_job_failure_rolls_back_week_posts_and_earlier_jobs(self):
        from world.photo_jobs import enqueue_initial

        def fail_after_one_job(post):
            self.assertTrue(connection.in_atomic_block)
            enqueue_initial(post)
            if post.character_id == self.artist.pk:
                raise IntegrityError('simulated job insert failure')

        with patch('world.photo_jobs.enqueue_initial', side_effect=fail_after_one_job), self.assertLogs(
            'world.publication', level='ERROR'
        ):
            generate_week(self.week.pk)
        self.week.refresh_from_db()
        self.assertEqual(self.week.generation, 'failed')
        self.assertFalse(self.week.posts.exists())
        self.assertFalse(PostRevision.objects.exists())
        self.assertFalse(PhotoJob.objects.exists())

    def test_newly_published_photo_during_inference_is_not_reused(self):
        friday = NOW - timedelta(days=3)

        def publish_during_proposal(context):
            if context['character']['name'] == '서윤':
                recent = self.history(self.first, friday, '카페 창가에 앉았다')
                recent.photo_id = 'cafe-interior'
                recent.save(update_fields=['photo'])
            return sample_week(context)

        with patch('world.publication.world_now', return_value=friday), patch(
            'llm.weekly.propose', side_effect=publish_during_proposal
        ):
            generate_week(self.week.pk)
        self.week.refresh_from_db()
        self.assertEqual(self.week.generation, 'failed')
        self.assertIn('사진', self.week.generation_error)
        self.assertFalse(self.week.posts.exists())

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
