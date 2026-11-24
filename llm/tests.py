from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from chat.models import Friendship
from chat.replies import accept_message
from llm.dialogue import context_for
from config.llm import model_options
from world.models import Character, FeedPost, PublicationWeek


@override_settings(LLM_MODEL='shared', LLM_CHAT_MODEL='', LLM_CHAT_EFFORT='', LLM_MEMORY_MODEL='',
                   LLM_MEMORY_EFFORT='', LLM_WEEKLY_MODEL='', LLM_WEEKLY_EFFORT='')
class ModelConfigurationTests(SimpleTestCase):
    def test_legacy_settings_leave_reasoning_unspecified(self):
        for purpose in ('chat', 'memory', 'weekly'):
            self.assertEqual(model_options(purpose), {'model': 'shared'})
        with self.settings(LLM_MODEL=''):
            self.assertEqual(model_options('weekly'), {'model': 'gpt-5.4-mini'})

    @override_settings(LLM_CHAT_MODEL='sol', LLM_CHAT_EFFORT='low', LLM_MEMORY_MODEL='luna',
                       LLM_WEEKLY_EFFORT='medium')
    def test_each_worker_uses_its_own_model_and_effort(self):
        self.assertEqual(model_options('chat'), {'model': 'sol', 'reasoning': {'effort': 'low'}})
        self.assertEqual(model_options('memory'), {'model': 'luna'})
        self.assertEqual(model_options('weekly'), {'model': 'shared', 'reasoning': {'effort': 'medium'}})


class PublishedEventTimeTests(TestCase):
    def test_chat_knows_when_an_event_happened_only_when_recorded(self):
        user = get_user_model().objects.create_user('friend')
        character = Character.objects.create(name='서윤', age=22)
        room = Friendship.objects.create(user=user, character=character)
        now = timezone.now()
        week = PublicationWeek.objects.create(start=timezone.localdate())
        published = now - timedelta(hours=1)
        event = published - timedelta(days=1)
        post = FeedPost.objects.create(
            week=week, character=character, state='published', event_at=event,
            publish_at=published, published_at=published, caption='어제 뽑은 포스터',
        )
        question = accept_message(room.pk, '오늘 뽑았어?', post.pk)
        plan = question.reply_plans.get()

        shared = context_for(plan, now)['shared_events'][0]
        self.assertEqual(shared['event_at'], timezone.localtime(event).isoformat())
        self.assertEqual(shared['published_at'], timezone.localtime(published).isoformat())

        post.event_at = None
        post.save(update_fields=['event_at'])
        self.assertIsNone(context_for(plan, now)['shared_events'][0]['event_at'])
