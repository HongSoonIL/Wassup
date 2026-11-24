from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from chat.models import Friendship, Message, ReplyBubble, ReplyPlan
from chat.replies import dispatch_due, prepare_next_reply
from llm.dialogue import context_for, sample_reply
from memory.models import RoomMemoryState
from world.models import Character, FeedPost, PostPhoto, PublicationWeek


NOW = datetime(2026, 11, 24, 10, tzinfo=timezone.utc)


@override_settings(LLM_BACKEND='openai')
class PostContinuityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.character = Character.objects.create(name='서윤', age=22)
        cls.room = Friendship.objects.create(
            user=get_user_model().objects.create_user('friend'), character=cls.character,
        )
        cls.peer = Friendship.objects.create(
            user=get_user_model().objects.create_user('peer'), character=cls.character,
        )
        cls.week = PublicationWeek.objects.create(start=NOW.date())
        cls.grandparent = cls.post('스케치', '스케치 연필', days=6)
        cls.earlier = cls.post('포스터 시안', '군청과 주황으로 포스터를 출력했다', days=5,
                               previous=cls.grandparent)
        cls.current = cls.post('포스터 완성', '최종 포스터에 주황을 사용했다', days=4, previous=cls.earlier)
        for offset in range(4):
            cls.post(f'비눗방울 {offset}', '산책 중 비눗방울을 찍었다', days=1, hours=offset)

    @classmethod
    def post(cls, title, facts, *, days, hours=0, previous=None):
        when = NOW - timedelta(days=days, hours=hours)
        photo = PostPhoto.objects.create(
            key=f'photo-{days}-{hours}', character=cls.character, alt=title, image=f'photos/{days}.webp',
        )
        return FeedPost.objects.create(
            week=cls.week, character=cls.character, state='published', caption=title, public_facts=[facts],
            photo=photo, photo_alt=photo.alt, event_at=when - timedelta(days=1),
            publish_at=when, published_at=when, operator_note='운영 전용 비공개 자료',
            previous_post=previous, previous_version=previous.version if previous else None,
        )

    def message(self, text, *, at=NOW, source=None, room=None, **values):
        return Message.objects.create(
            room=room or self.room, sender=values.pop('sender', 'user'), text=text, created_at=at,
            source_post=source, source_version=source.version if source else None, **values,
        )

    def plan(self, question, **kwargs):
        trigger = self.message(question, **kwargs)
        return ReplyPlan.objects.create(
            room=trigger.room, trigger_message=trigger, read_at=NOW,
            typing_at=NOW, reply_at=NOW + timedelta(seconds=30),
        )

    def assert_continuity(self, context):
        link = context['post_continuity']
        self.assertEqual(link['post_id'], self.current.pk)
        self.assertEqual(link['previous']['id'], self.earlier.pk)
        self.assertEqual(link['previous']['facts'], self.earlier.public_facts)
        self.assertNotEqual(link['previous']['event_at'], link['previous']['published_at'])
        self.assertNotIn(self.grandparent.pk, [p['id'] for p in context['shared_events']])
        self.assertNotIn('운영 전용 비공개 자료', str(context))

    def test_quote_brings_one_pinned_hop_without_adding_photo_candidates(self):
        context = context_for(self.plan('처음에는 언제 찍었어?', source=self.current), NOW)
        self.assert_continuity(context)
        self.assertEqual(context['quoted_post_id'], self.current.pk)
        self.assertEqual(len(context['photo_candidates']), 4)
        self.assertNotIn(self.earlier.pk, [p['id'] for p in context['photo_candidates']])
        self.assertEqual(sample_reply(context), ['11월 18일이었어'])
        published_context = context_for(self.plan('이전 게시물은 언제 올렸어?', source=self.current), NOW)
        self.assertEqual(sample_reply(published_context), ['11월 19일에 올렸어'])

    def test_real_reference_to_a_received_photo_brings_its_earlier_post(self):
        self.message('사진이야', at=NOW - timedelta(minutes=1), sender='character',
                     kind='photo', attachment_post=self.current, attachment_version=self.current.version)
        context = context_for(self.plan('그 사진 언제 찍었어?'), NOW)
        self.assert_continuity(context)
        self.assertEqual(context['photo_reference_id'], self.current.pk)

    def test_a_specific_recent_feed_reference_needs_no_quote(self):
        FeedPost.objects.filter(pk=self.current.pk).update(published_at=NOW - timedelta(minutes=1))
        context = context_for(self.plan('피드에 올린 포스터 완성 얘기 궁금해'), NOW)
        self.assert_continuity(context)
        self.assertIsNone(context['quoted_post_id'])
        self.assertIsNone(context['photo_reference_id'])

    def test_days_later_followup_reopens_only_the_relevant_same_room_subject(self):
        self.message('비공개 대화 단서', at=NOW - timedelta(days=3), source=self.current)
        context = context_for(self.plan('그래서 포스터 결국 어떻게 됐어?'), NOW)
        self.assert_continuity(context)
        self.assertEqual(context['quoted_post_id'], self.current.pk)
        self.assertEqual(len(context['recent_turns']), 1)
        self.assertNotIn('비공개 대화 단서', str(context))
        context = context_for(self.plan('그래서 결국 어떻게 됐어?', room=self.peer), NOW)
        self.assertNotIn('post_continuity', context)

    def test_next_day_outcome_uses_the_quoted_edition_and_its_published_update(self):
        blanket = self.post('담요 빨래', '두부가 잠들어서 담요 빨래를 내일로 미뤘다.', days=3)
        drying = self.post('건조대가 꽉 참', '손빨래한 담요를 널었다.', days=2, previous=blanket)
        self.message('얘가 두부야?', at=NOW - timedelta(days=3), source=blanket)

        def ask():
            return context_for(self.plan('그래서 담요는 빨았어?'), NOW)

        FeedPost.objects.filter(pk=drying.pk).update(state='scheduled')
        before = ask()
        self.assertEqual(before['quoted_post_id'], blanket.pk)
        self.assertNotIn('post_continuity', before)
        self.assertNotIn('손빨래한 담요', str(before))
        self.assertNotIn('얘가 두부야?', str(before))
        FeedPost.objects.filter(pk=drying.pk).update(state='published')
        after = ask()
        self.assertEqual(after['quoted_post_id'], blanket.pk)
        self.assertEqual(after['post_continuity']['post_id'], drying.pk)
        self.assertEqual(after['post_continuity']['previous']['id'], blanket.pk)
        self.assertIn(drying.pk, [p['id'] for p in after['shared_events']])
        self.assertNotIn('post_continuity', context_for(self.plan('담요 사진 언제 찍었어?'), NOW))
        self.assertNotIn('post_continuity', context_for(self.plan('지금은 뭐 해?'), NOW))
        self.assertNotIn('post_continuity', context_for(self.plan('그래서 담요는 빨았어?', room=self.peer), NOW))

        FeedPost.objects.filter(pk=drying.pk).update(previous_version=blanket.version + 1)
        self.assertNotIn('post_continuity', ask())
        self.post('다른 담요 이야기', '담요를 하나 더 꺼냈다.', days=2, hours=1, previous=blanket)
        FeedPost.objects.filter(pk=drying.pk).update(previous_version=blanket.version)
        self.assertNotIn('post_continuity', ask())

    def test_elliptical_followup_requires_an_unbroken_recent_topic(self):
        self.message('무슨 색 골랐어?', at=NOW - timedelta(days=3), source=self.current)
        self.assert_continuity(context_for(self.plan('그래서 결국 어떻게 됐어?'), NOW))
        context = context_for(self.plan('그때 시험은 어떻게 됐어?'), NOW)
        self.assertNotIn('post_continuity', context)
        self.assertIsNone(context['quoted_post_id'])
        self.assertNotIn('post_continuity', context_for(self.plan('그래서 결국 어떻게 됐어?'), NOW))
        self.assert_continuity(context_for(self.plan('포스터가 결국 어떻게 됐어?'), NOW))

    def test_a_same_day_topic_change_and_an_expired_quote_cannot_open_the_link(self):
        recent = self.message('포스터 궁금해', at=NOW - timedelta(hours=1), source=self.current)
        context = context_for(self.plan('지금 어디야?'), NOW)
        self.assertNotIn('post_continuity', context)
        self.assertIsNone(context['quoted_post_id'])
        recent.delete()
        self.message('포스터 궁금해', at=NOW - timedelta(days=8), source=self.current)
        self.assertNotIn('post_continuity', context_for(self.plan('포스터 결국 어떻게 됐어?'), NOW))

    def test_floor_stale_quote_and_stale_previous_each_hide_continuity(self):
        quote = self.message('포스터 궁금해', at=NOW - timedelta(days=3), source=self.current)
        followup = self.plan('포스터가 결국 어떻게 됐어?')
        state = RoomMemoryState.objects.create(room=self.room, history_floor=quote.pk)
        self.assertNotIn('post_continuity', context_for(followup, NOW))
        state.delete()
        FeedPost.objects.filter(pk=self.current.pk).update(version=self.current.version + 1)
        self.assertNotIn('post_continuity', context_for(followup, NOW))
        FeedPost.objects.filter(pk=self.current.pk).update(version=self.current.version)
        FeedPost.objects.filter(pk=self.earlier.pk).update(version=self.earlier.version + 1,
                                                           caption='인용하지 않은 새 내용')
        context = context_for(followup, NOW)
        self.assertEqual(context['quoted_post_id'], self.current.pk)
        self.assertNotIn('post_continuity', context)
        self.assertNotIn('인용하지 않은 새 내용', str(context))

    def prepare(self, plan):
        with (patch('chat.replies.world_now', return_value=NOW),
              patch('chat.replies.openai_reply', return_value={
                  'bubbles': ['예전 시안 내용', '이전 글에서 가져온 뒷이야기'], 'used_memory_ids': [],
              })):
            self.assertTrue(prepare_next_reply())
        plan.refresh_from_db()
        self.assertEqual(plan.context_versions[str(self.current.pk)], self.current.version)
        self.assertEqual(plan.context_versions[str(self.earlier.pk)], self.earlier.version)

    def test_stale_editions_or_removed_link_use_fallback_before_first_bubble(self):
        for post, changes, reset in (
            (self.earlier, {'version': self.earlier.version + 1}, {'version': self.earlier.version}),
            (self.current, {'version': self.current.version + 1}, {'version': self.current.version}),
            (self.current, {'previous_post': None}, {'previous_post': self.earlier}),
        ):
            with self.subTest(changes=changes):
                plan = self.plan('처음에는 어떻게 했어?', source=self.current)
                self.prepare(plan)
                FeedPost.objects.filter(pk=post.pk).update(**changes)
                dispatch_due(plan.reply_at)
                plan.refresh_from_db()
                self.assertEqual(plan.model_name, 'fallback')
                self.assertEqual(plan.context_versions, {})
                self.assertNotIn('예전 시안 내용', plan.result)
                FeedPost.objects.filter(pk=post.pk).update(**reset)

    def test_retraction_after_first_delivery_drops_tail_and_its_context_on_next_reply(self):
        plan = self.plan('어떻게 이어진 거야?', source=self.current)
        self.prepare(plan)
        dispatch_due(plan.reply_at)
        tail = plan.bubbles.get(position=1)
        dispatch_due(tail.typing_at)
        FeedPost.objects.filter(pk=self.earlier.pk).update(state='skipped')
        dispatch_due(tail.deliver_at)
        tail.refresh_from_db()
        self.assertEqual(tail.status, ReplyBubble.Status.CANCELLED)
        self.assertTrue(self.room.messages.filter(sender='character', text='예전 시안 내용').exists())
        followup = self.plan('그 처음 얘기 다시 해줘', at=tail.deliver_at + timedelta(seconds=1))
        context = context_for(followup, followup.trigger_message.created_at)
        self.assertNotIn('post_continuity', context)
        self.assertNotIn('예전 시안 내용', str(context))
