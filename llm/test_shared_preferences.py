from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from chat.models import Friendship, Message
from chat.replies import accept_message
from llm.dialogue import context_for
from memory.models import Memory, RoomMemoryState
from world.models import Character


class PreferenceContextTests(TestCase):
    def test_profile_follows_the_user_and_conversation_memory_follows_the_room(self):
        items = [{'tag': '커피', 'text': ''}, {'tag': '', 'text': '물소리 들리는 곳이 좋아'}]
        user = get_user_model().objects.create_user('owner', shared_preferences=items, preference_revision=4)
        other = get_user_model().objects.create_user('other')
        doy = Character.objects.create(name='도현', age=23)
        seo = Character.objects.create(name='서윤', age=22)
        room = Friendship.objects.create(user=user, character=doy)
        second = Friendship.objects.create(user=user, character=seo)
        foreign = Friendship.objects.create(user=other, character=doy)
        now = timezone.now()
        source = Message.objects.create(room=room, sender='user', text='수국이 좋아', created_at=now-timedelta(days=2))
        RoomMemoryState.objects.create(room=room, enabled=True)
        private = Memory.objects.create(source=source, kind='preference', text='수국 선호')
        for target, shared, revision, private_ids in [
            (room, items, 4, [private.pk]), (second, items, 4, []), (foreign, [], 0, []),
        ]:
            with self.subTest(room=target.pk):
                trigger = accept_message(target.pk, '내 취향 기억해?')
                context = context_for(trigger.reply_plans.get(), now)
                self.assertEqual(context['shared_preferences'], shared)
                self.assertEqual(context['preference_revision'], revision)
                self.assertEqual([m['id'] for m in context['authorized_memories']], private_ids)
