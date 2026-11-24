from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from chat.models import Friendship
from chat.replies import accept_message
from llm.dialogue import context_for
from world.models import Character, FeedPost, PublicationWeek


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
