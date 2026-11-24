from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings

from accounts.models import PersonalPost
from chat import personal_reactions, proactive
from chat.models import Friendship, Message, PersonalPostReaction, ProactiveClaim, ProactiveDelivery
from chat.replies import accept_message, dispatch_due, prepare_next_reply
from chat.serialization import message_json
from chat.test_proactive import CHANNELS, DAY, NOW, ProactiveFixtures
from llm import proactive as provider
from llm.dialogue import context_for
from memory.models import RoomMemoryState
from world.models import Persona, State


class Fixtures(ProactiveFixtures):
    def setUp(self):
        super().setUp()
        self.schedule.delete()
        RoomMemoryState.objects.filter(room=self.room).update(enabled=False)
        Persona.objects.create(character=self.room.character, personality='카페 알바, 커피 좋아함', speech_rules='단답')
        self.post = self.make_post()

    def make_post(self, caption='새로 마셔 본 커피', **kwargs):
        post = PersonalPost.objects.create(owner=self.owner, caption=caption, **kwargs)
        PersonalPost.objects.filter(pk=post.pk).update(created_at=NOW - timedelta(hours=1))
        return post


@override_settings(LLM_BACKEND='fake', CHANNEL_LAYERS=CHANNELS)
class PersonalReactionTests(Fixtures, TestCase):
    def test_delay_quiet_hours_free_slot_and_proactive_consent(self):
        def tick():
            self.assertIsNone(proactive.prepare_next_proactive())
            self.assertFalse(PersonalPostReaction.objects.exists())

        PersonalPost.objects.filter(pk=self.post.pk).update(created_at=NOW)
        tick()
        PersonalPost.objects.filter(pk=self.post.pk).update(created_at=NOW - timedelta(days=1))
        self.clock.return_value = NOW.replace(hour=0, minute=59)  # 09:59 KST
        tick()
        self.clock.return_value = NOW.replace(hour=12)  # 21:00 KST
        tick()
        self.clock.return_value = NOW
        self.slot.state = State.CLASS
        self.slot.save()
        tick()
        self.slot.state = State.FREE
        self.slot.save()
        Friendship.objects.filter(pk=self.room.pk).update(proactive_enabled=False)
        tick()
        Friendship.objects.filter(pk=self.room.pk).update(proactive_enabled=True)
        self.assertEqual(proactive.prepare_next_proactive().room_id, self.room.pk)

    def test_interest_and_private_sources_from_only_this_post(self):
        cat_room, _ = self.friend('서윤')
        Persona.objects.create(character=cat_room.character, personality='고양이를 좋아한다')
        self.room.messages.create(sender='user', kind='photo', text='다른 방 비밀', user_photo='chat/secret.jpg')
        peer = get_user_model().objects.create_user(username='peer')
        self.friend('타인', peer)
        opted_out, _ = self.friend('거절')
        Friendship.objects.filter(pk=opted_out.pk).update(proactive_enabled=False)
        PersonalPost.objects.create(owner=peer, caption='다른 계정 비밀', photo='personal-posts/peer.jpg')
        PersonalPost.objects.filter(pk=self.post.pk).update(caption='', photo='personal-posts/cat.jpg')
        with patch.object(personal_reactions.user_photos, 'describe_photo', return_value='회색 고양이와 초록 공') as see, \
                patch.object(provider, 'generate_post_reaction', wraps=provider.generate_post_reaction) as generate:
            message = proactive.prepare_next_proactive()
        self.assertEqual(message.room_id, cat_room.pk)
        self.assertEqual(see.call_count, 1)
        self.assertEqual(see.call_args.args[0].name, 'personal-posts/cat.jpg')
        post, friends = generate.call_args.args
        self.assertEqual(post, {'caption': '', 'photo_description': '회색 고양이와 초록 공'})
        self.assertEqual({item['room_id'] for item in friends}, {self.room.pk, cat_room.pk})
        self.assertNotIn('비밀', repr(friends))
        self.assertEqual(message_json(message)['source_post']['photo_url'], f'/api/me/posts/{self.post.pk}/photo/')
        self.assertEqual(self.post.reaction.photo_description, post['photo_description'])

    def test_text_only_quote_and_deleted_quote_history(self):
        message = proactive.prepare_next_proactive()
        self.assertEqual(message_json(message)['source_post'], {
            'id': self.post.pk, 'version': 1, 'kind': 'personal', 'status': 'available', 'caption': self.post.caption,
            'photo_url': None, 'photo_alt': '',
        })
        self.post.delete()
        self.client.force_login(self.owner)
        response = self.client.get(f'/api/rooms/{self.room.pk}/messages/').json()
        delivered = next(row for row in response['messages'] if row['id'] == message.pk)
        self.assertEqual(delivered['source_post'], {'kind': 'personal', 'status': 'unavailable'})
        self.assertEqual(delivered['text'], message.text)
        self.assertFalse(PersonalPostReaction.objects.exists())

    def test_decline_and_lost_grounding_do_not_invent_a_reaction(self):
        PersonalPost.objects.filter(pk=self.post.pk).update(caption='2층')
        self.assertIsNone(proactive.prepare_next_proactive())
        self.assertEqual(self.post.reaction.status, ProactiveClaim.Status.DECLINED)
        other = self.make_post(caption='', photo='personal-posts/no-pixels.jpg')
        with patch.object(provider, 'generate_post_reaction') as generate:
            self.assertIsNone(proactive.prepare_next_proactive())
            generate.assert_not_called()
        self.assertEqual(other.reaction.status, ProactiveClaim.Status.PENDING)
        self.assertFalse(ProactiveDelivery.objects.exists())

    def test_consent_and_slot_rechecked_at_delivery(self):
        for change in ('revision', 'slot'):
            with self.subTest(change=change):
                claimed = personal_reactions._claim(self.post.pk, self.owner.pk, DAY)
                if change == 'revision':
                    Friendship.objects.filter(pk=self.room.pk).update(proactive_revision=2)
                else:
                    self.slot.state = State.CLASS
                    self.slot.save()
                self.assertIsNone(personal_reactions._finish(claimed, (self.room.pk, '커피 어땠어?')))
        self.assertFalse(ProactiveDelivery.objects.exists())

    def test_ordinary_inbound_during_generation_cancels_delivery(self):
        claimed = personal_reactions._claim(self.post.pk, self.owner.pk, DAY)
        with patch('chat.replies.world_now', return_value=NOW):
            accept_message(self.room.pk, '지금 얘기할 수 있어?')
        self.assertIsNone(personal_reactions._finish(claimed, (self.room.pk, '커피 어땠어?')))
        self.assertEqual(self.post.reaction.status, ProactiveClaim.Status.DECLINED)

    def test_stale_lease_and_recreated_room_cannot_send_again(self):
        old = personal_reactions._claim(self.post.pk, self.owner.pk, DAY)
        self.clock.return_value += proactive.LEASE
        current = personal_reactions._claim(self.post.pk, self.owner.pk, DAY)
        decision = self.room.pk, '커피 어땠어?'
        self.assertIsNone(personal_reactions._finish(old, decision))
        self.assertEqual(personal_reactions._finish(current, decision).text, decision[1])
        self.assertIsNone(personal_reactions._finish(current, decision))
        character = self.room.character
        self.room.delete()
        Friendship.objects.create(user=self.owner, character=character, proactive_enabled=True)
        self.clock.return_value += timedelta(days=7)
        self.assertIsNone(proactive.prepare_next_proactive())
        self.assertEqual(self.post.reaction.status, ProactiveClaim.Status.SENT)

    def test_posts_and_schedules_share_the_daily_cap_even_across_rooms(self):
        other, _ = self.friend('서윤')
        self.make_post()
        self.assertIsNotNone(proactive.prepare_next_proactive())
        self.remember(other)
        self.assertIsNone(proactive.prepare_next_proactive())
        self.assertEqual(ProactiveDelivery.objects.count(), 1)
        self.assertFalse(ProactiveClaim.objects.exists())
        self.assertEqual(Message.objects.filter(source_personal_post__isnull=False).count(), 1)

    def test_friendship_removal_during_generation_cannot_send_to_a_replacement(self):
        claimed = personal_reactions._claim(self.post.pk, self.owner.pk, DAY)
        room_id, character = self.room.pk, self.room.character
        self.room.delete()
        replacement = Friendship.objects.create(user=self.owner, character=character, proactive_enabled=True)
        self.assertIsNone(personal_reactions._finish(claimed, (room_id, '커피 어땠어?')))
        self.assertFalse(replacement.messages.exists())
        self.assertFalse(ProactiveDelivery.objects.exists())

    def test_new_question_about_friends_own_photo_does_not_open_personal_post(self):
        proactive.prepare_next_proactive()
        with patch('chat.replies.world_now', return_value=NOW + timedelta(seconds=1)):
            plan = accept_message(self.room.pk, '아까 네가 올린 사진은 뭐야?').reply_plans.get()
        self.assertNotIn('personal_post', context_for(plan, NOW))

    def test_delivered_followup_uses_original_caption_and_keeps_its_text_after_deletion(self):
        reaction = proactive.prepare_next_proactive()
        with patch('chat.replies.world_now', return_value=NOW + timedelta(seconds=1)):
            plan = accept_message(self.room.pk, '내가 올린 글에서 뭐가 궁금했어?').reply_plans.get()
            self.assertTrue(prepare_next_reply())
            dispatch_due(plan.reply_at)
        reply = self.room.messages.filter(sender='character', pk__gt=reaction.pk).get()
        self.assertIn(self.post.caption, reply.text)
        self.post.delete()
        self.assertEqual(Message.objects.get(pk=reply.pk).text, reply.text)
        with patch('chat.replies.world_now', return_value=plan.reply_at + timedelta(seconds=1)):
            next_plan = accept_message(self.room.pk, '왜?').reply_plans.get()
        context = context_for(next_plan, next_plan.reply_at)
        self.assertNotIn('personal_post', context)
        self.assertNotIn(reply.text, [turn['text'] for turn in context['recent_turns']])

    def test_followup_uses_quoted_post_only_in_its_own_room_and_fences_deletion(self):
        PersonalPost.objects.filter(pk=self.post.pk).update(photo='personal-posts/cup.jpg')
        with patch.object(personal_reactions.user_photos, 'describe_photo', return_value='파란색 잔과 흰 받침'):
            reaction = proactive.prepare_next_proactive()
        other, _ = self.friend('서윤')
        self.friend('다른 계정', get_user_model().objects.create_user(username='peer'))
        with patch('chat.replies.world_now', return_value=NOW + timedelta(seconds=1)):
            question = accept_message(self.room.pk, '잔이 무슨 색으로 보였어?')
            plan = question.reply_plans.get()
            context = context_for(plan, NOW)
            self.assertEqual(context['personal_post']['caption'], self.post.caption)
            self.assertEqual(context['personal_post']['photo_description'], '파란색 잔과 흰 받침')
            self.assertFalse(context['photo_candidates'])
            self.assertTrue(prepare_next_reply())
            plan.refresh_from_db()
            self.assertIn('파란색', ' '.join(plan.result))
            self.assertEqual(plan.context_versions, {'personal_post': self.post.pk})
            outsider = accept_message(other.pk, '내 게시물 사진 기억해?').reply_plans.get()
            self.assertNotIn('personal_post', context_for(outsider, NOW))
            self.post.delete()
            dispatch_due(plan.reply_at)
            plan.refresh_from_db()
            self.assertNotIn('파란색', ' '.join(plan.result))
            context = context_for(plan, NOW)
            self.assertNotIn('personal_post', context)
            self.assertFalse(any(turn['text'] == reaction.text for turn in context['recent_turns']))


@override_settings(LLM_BACKEND='fake', CHANNEL_LAYERS=CHANNELS)
class PersonalReactionConcurrencyTests(Fixtures, TransactionTestCase):
    def run_generation(self):
        try:
            return proactive.prepare_next_proactive()
        finally:
            connections.close_all()

    def test_simultaneous_lease_and_delivery_attempts_only_win_once(self):
        def race(call):
            barrier = Barrier(2)
            def compete():
                try:
                    barrier.wait(timeout=5)
                    return call()
                finally:
                    connections.close_all()
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(compete) for _ in range(2)]
                return [result for future in futures if (result := future.result(timeout=5)) is not None]

        claims = race(lambda: personal_reactions._claim(self.post.pk, self.owner.pk, DAY))
        self.assertEqual(len(claims), 1)
        messages = race(lambda: personal_reactions._finish(claims[0], (self.room.pk, '커피 어땠어?')))
        self.assertEqual(len(messages), 1)
        self.assertEqual(self.post.reaction_message.pk, messages[0].pk)
        self.assertEqual(ProactiveDelivery.objects.count(), 1)

    def test_parallel_ticks_and_schedule_claims_share_generation_and_daily_capacity(self):
        # Add the schedule only once the post generation has started.
        entered, release = Event(), Event()
        def generate(post, friends):
            self.assertTrue(connections['default'].get_autocommit())
            entered.set()
            self.assertTrue(release.wait(timeout=5))
            return self.room.pk, '커피는 어땠어?'

        with patch.object(provider, 'generate_post_reaction', side_effect=generate) as generate_mock, \
                ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.run_generation)
            try:
                self.assertTrue(entered.wait(timeout=5))
                other, _ = self.friend('서윤')
                self.remember(other)
                self.assertIsNone(proactive.prepare_next_proactive())
                self.assertFalse(ProactiveClaim.objects.exists())
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5).room_id, self.room.pk)
            generate_mock.assert_called_once()
        self.assertIsNone(proactive.prepare_next_proactive())
        self.assertEqual(ProactiveDelivery.objects.count(), 1)

    def test_post_deletion_during_generation_cancels_delivery(self):
        entered, release = Event(), Event()
        def generate(post, friends):
            entered.set()
            self.assertTrue(release.wait(timeout=5))
            return self.room.pk, '커피는 어땠어?'

        with patch.object(provider, 'generate_post_reaction', side_effect=generate), \
                ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.run_generation)
            try:
                self.assertTrue(entered.wait(timeout=5))
                self.client.force_login(self.owner)
                self.assertIn(self.client.delete(f'/api/me/posts/{self.post.pk}/').status_code, (200, 204))
            finally:
                release.set()
            self.assertIsNone(future.result(timeout=5))
        self.assertFalse(ProactiveDelivery.objects.exists())
        self.assertFalse(Message.objects.filter(sender='character').exists())
