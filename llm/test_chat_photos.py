import io
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from chat.models import Friendship, Message, ReplyBubble, ReplyPlan
from llm.dialogue import context_for, fake_reply, openai_reply
from memory.models import RoomMemoryState
from world.models import Character, FeedPost, PostPhoto, PublicationWeek


# 00:20 in Seoul on November 24, still November 23 in UTC.
NOW = datetime(2026, 11, 23, 15, 20, tzinfo=dt_timezone.utc)


class ChatPhotoContextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.character = Character.objects.create(name='서윤', age=22)
        cls.other = Character.objects.create(name='도현', age=23)
        cls.room = Friendship.objects.create(user=get_user_model().objects.create_user('friend'),
                                             character=cls.character)
        cls.peer = Friendship.objects.create(user=get_user_model().objects.create_user('peer'),
                                             character=cls.character)
        cls.week = PublicationWeek.objects.create(start=NOW.date())

    def post(self, key, alt, *, event_at=None, published_at=None, character=None, **fields):
        character = character or self.character
        published_at = published_at or NOW - timedelta(minutes=1)
        photo = PostPhoto.objects.create(key=key, character=character, alt=alt, image=f'photos/{key}.webp')
        return FeedPost.objects.create(
            week=self.week, character=character, photo=photo, photo_alt=alt, caption=key,
            public_facts=['두부를 고양이 병원에 데리고 갔다.'],
            event_at=event_at, publish_at=published_at, published_at=published_at,
            state=fields.pop('state', 'published'), **fields,
        )

    def received(self, post, *, at=NOW - timedelta(hours=1), room=None, text='이거 봐'):
        return Message.objects.create(
            room=room or self.room, sender='character', kind='photo', text=text,
            attachment_post=post, attachment_version=post.version, created_at=at,
        )

    def plan(self, text, *, photo_quote=None, at=NOW):
        trigger = Message.objects.create(
            room=self.room, sender='user', text=text, created_at=at,
            source_post=photo_quote, source_version=photo_quote.version if photo_quote else None,
        )
        return ReplyPlan.objects.create(room=self.room, trigger_message=trigger,
                                        read_at=at, typing_at=at, reply_at=at)

    def context(self, question, **kwargs):
        return context_for(self.plan(question, **kwargs), NOW)

    def candidate_ids(self, context):
        return [p['id'] for p in context['photo_candidates']]

    def test_requests_match_visible_subject_and_actual_korean_local_date(self):
        self.post('receipt', '고양이 병원 진료비 영수증', event_at=NOW - timedelta(minutes=5))
        self.post('drawing', '노트에 그린 고양이 그림', event_at=NOW - timedelta(minutes=6))
        self.post('undated', '겨자색 담요 위 흰 고양이 두부')
        self.post('prior-day', '창가에 앉은 흰 고양이 두부', event_at=NOW - timedelta(minutes=30))
        question = '오늘 찍은 고양이 사진 보여줘'
        empty = self.context(question)
        self.assertEqual(self.candidate_ids(empty), [])
        self.assertIsNone(fake_reply(empty)['attachment'])

        cat = self.post('today', '침대에 앉은 흰 고양이 두부', event_at=NOW - timedelta(minutes=10))
        context = self.context(question)
        self.assertEqual(self.candidate_ids(context), [cat.pk])
        self.assertEqual(fake_reply(context)['attachment'], {'post_id': cat.pk, 'bubble_index': 0})

    def test_candidates_are_bounded_to_latest_four_visible_own_photos(self):
        old = self.post('older', '흰 고양이 두부', published_at=NOW - timedelta(days=1))
        current = [self.post(f'poster-{i}', '작업실 벽에 붙인 포스터') for i in range(4)]
        self.post('draft', '고양이 두부', state='draft')
        self.post('future', '고양이 두부', published_at=NOW + timedelta(seconds=1))
        self.post('foreign', '고양이 두부', character=self.other)
        self.assertEqual(self.candidate_ids(self.context('요즘 뭐 해?')), [p.pk for p in reversed(current)])
        self.assertIsNone(fake_reply(self.context('고양이 사진 보여줘'))['attachment'])
        self.assertNotIn(old.pk, self.candidate_ids(self.context('사진 보여줘')))

    def test_duplicate_detection_is_room_version_and_privacy_floor_scoped(self):
        cat = self.post('cat', '담요 위 흰 고양이 두부')
        self.received(cat, room=self.peer)
        question = '두부 사진 좀 보여줘'
        self.assertEqual(self.candidate_ids(self.context(question)), [cat.pk])
        sent = self.received(cat)
        self.assertEqual(self.candidate_ids(self.context(question)), [])

        state, _ = RoomMemoryState.objects.update_or_create(room=self.room, defaults={'history_floor': sent.pk})
        self.assertEqual(self.candidate_ids(self.context(question)), [cat.pk])
        state.history_floor = 0
        state.save(update_fields=['history_floor'])
        cat.version += 1
        cat.save(update_fields=['version'])
        self.assertEqual(self.candidate_ids(self.context(question)), [cat.pk])

    def test_reference_crosses_midnight_and_repeat_uses_actual_last_reference(self):
        last = self.post('cat', '담요 위 흰 고양이 두부', event_at=NOW - timedelta(days=3),
                         published_at=NOW - timedelta(days=2))
        self.received(last)
        newer = [self.post(f'new-{i}', '창가에 앉은 고양이 두부') for i in range(4)]

        followup = self.context('그 사진 뭐 찍은 거야?')
        self.assertEqual(followup['photo_reference_id'], last.pk)
        self.assertIn('담요 위 흰 고양이 두부', fake_reply(followup)['bubbles'][0])
        self.assertIsNone(fake_reply(followup)['attachment'])
        self.assertIsNone(self.context('지금 어디야?')['photo_reference_id'])

        for question in (
            '어제 보내준 그 사진 다시 보내줘', '어제 보내준 그 두부 사진 다시 보내주라',
            '그 사진 다시 보내 줄래?', '그 사진 다시 보내 줘봐', '다시 볼 수 있을까?',
        ):
            with self.subTest(question=question):
                repeat = self.context(question)
                self.assertEqual(self.candidate_ids(repeat), [last.pk])
                self.assertEqual(fake_reply(repeat)['attachment']['post_id'], last.pk)
        compliment = self.context('어제 보내준 사진 예쁘다')
        self.assertFalse(compliment['photo_repeat_requested'])
        self.assertIsNone(fake_reply(compliment)['attachment'])
        self.assertEqual(self.candidate_ids(self.context('어제 찍은 그 사진 다시 보내줘')), [])
        self.assertEqual(self.candidate_ids(self.context('그 강아지 사진 다시 보내줘')), [])

        explicit = self.context('이 사진은 어디서 찍었어?', photo_quote=newer[0])
        self.assertEqual(explicit['quoted_post_id'], newer[0].pk)
        self.assertIsNone(explicit['photo_reference_id'])
        direct_share = self.context('이 사진 보내줘', photo_quote=newer[0])
        self.assertEqual(self.candidate_ids(direct_share), [newer[0].pk])
        self.assertEqual(fake_reply(direct_share)['attachment']['post_id'], newer[0].pk)
        RoomMemoryState.objects.update_or_create(room=self.room, defaults={
            'history_floor': self.room.messages.order_by('-pk').first().pk,
        })
        self.assertIsNone(self.context('그 사진 뭐 찍은 거야?')['photo_reference_id'])
        self.assertEqual(self.candidate_ids(self.context('아까 그 사진 다시 보내줘')), [])

    def test_withdrawn_photo_and_related_caption_do_not_reenter_context(self):
        old = self.post('old', '빨간 방석에 앉은 고양이 두부')
        self.received(old)
        last = self.post('last', '흰 커튼 사이의 고양이 두부')
        at = NOW - timedelta(minutes=10)
        plan = self.plan('두부 사진 보여줘', at=at)
        sent = self.received(last, at=at, text='회수될 사진 설명')
        tail = Message.objects.create(room=self.room, sender='character', text='회수될 후속 설명', created_at=at)
        ReplyBubble.objects.create(plan=plan, position=0, message=sent, text=sent.text)
        ReplyBubble.objects.create(plan=plan, position=1, message=tail, text=tail.text)

        last.state = 'skipped'
        last.save(update_fields=['state'])
        for delete in (False, True):
            if delete:
                last.delete()
            with self.subTest(deleted=delete):
                context = self.context('그 사진 다시 보내줘')
                self.assertEqual(self.candidate_ids(context), [])
                self.assertIsNone(context['photo_reference_id'])
                self.assertNotIn('회수될', json.dumps(context, ensure_ascii=False))
                self.assertIsNone(fake_reply(context)['attachment'])


class ChatPhotoAdapterTests(SimpleTestCase):
    def test_one_request_yields_one_valid_attachment_or_text(self):
        context = {
            'character': {'bubble_range': [1, 2]}, 'authorized_memories': [],
            'photo_candidates': [{'id': 4, 'version': 2}],
        }
        photo = {'post_id': 4, 'bubble_index': 0}
        for selection in (None, photo, dict(photo, post_id=99), dict(photo, post_id=True),
                          dict(photo, bubble_index=1), dict(photo, bubble_index=False), dict(photo, url='/fake')):
            result = {'bubbles': ['여기'], 'used_memory_ids': [], 'attachment': selection}
            response = {'status': 'completed', 'output': [{
                'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(result)}],
            }]}
            with self.subTest(selection=selection), patch(
                'llm.dialogue.urlopen', return_value=io.BytesIO(json.dumps(response).encode())
            ) as transport:
                if selection is None or selection is photo:
                    self.assertEqual(openai_reply(context), result)
                else:
                    with self.assertRaises(ValueError):
                        openai_reply(context)
                transport.assert_called_once()
                schema = json.loads(transport.call_args.args[0].data)['text']['format']['schema']
                attach = schema['properties']['attachment']['anyOf'][1]
                self.assertEqual(attach['properties']['post_id']['enum'], [4])
                self.assertFalse(attach['additionalProperties'])
