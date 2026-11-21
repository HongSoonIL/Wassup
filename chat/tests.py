import json
from datetime import timedelta

from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from world.models import Character

from .models import Friendship, Message, ReplyPlan


class ChatAPITestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.jisu = User.objects.create_user('jisu', password='pw-12345!')
        cls.other = User.objects.create_user('other', password='pw-12345!')
        cls.dohyun = Character.objects.create(name='도현', age=23, major='경영학과 3학년', status_message='알바 중')
        cls.seoyun = Character.objects.create(name='서윤', age=22, major='시각디자인과 3학년')
        cls.room = Friendship.objects.create(user=cls.jisu, character=cls.dohyun)
        cls.other_room = Friendship.objects.create(user=cls.other, character=cls.dohyun)

    def messages_url(self, room_id):
        return reverse('chat:room-messages', args=[room_id])

    def post_text(self, room_id, payload):
        return self.client.post(self.messages_url(room_id), data=json.dumps(payload), content_type='application/json')


class SendMessageTests(ChatAPITestBase):
    def setUp(self):
        self.client.force_login(self.jisu)

    def test_send_returns_201_without_reply(self):
        res = self.post_text(self.room.id, {'text': '뭐해?'})

        self.assertEqual(res.status_code, 201)
        body = res.json()
        self.assertEqual(set(body), {'id', 'status', 'created_at'})  # 답장은 응답에 담지 않는다
        self.assertEqual(body['status'], 'sent')
        message = Message.objects.get(pk=body['id'])
        self.assertEqual(message.room, self.room)
        self.assertEqual(message.sender, Message.Sender.USER)
        self.assertEqual(message.text, '뭐해?')
        self.assertIsNone(message.read_at)

    def test_created_at_is_korean_time(self):
        res = self.post_text(self.room.id, {'text': '뭐해?'})
        self.assertTrue(res.json()['created_at'].endswith('+09:00'))

    def test_other_users_room_returns_403(self):
        res = self.post_text(self.other_room.id, {'text': '몰래 보내기'})

        self.assertEqual(res.status_code, 403)
        self.assertFalse(Message.objects.exists())

    def test_missing_room_returns_404(self):
        res = self.post_text(999999, {'text': '뭐해?'})
        self.assertEqual(res.status_code, 404)

    def test_invalid_body_returns_400(self):
        cases = [
            {},
            {'text': ''},
            {'text': '   '},
            {'text': 123},
            ['뭐해?'],
            {'text': 'ㅋ' * 1001},
        ]
        for payload in cases:
            with self.subTest(payload=str(payload)[:30]):
                self.assertEqual(self.post_text(self.room.id, payload).status_code, 400)

        res = self.client.post(self.messages_url(self.room.id), data='not json', content_type='application/json')
        self.assertEqual(res.status_code, 400)
        self.assertFalse(Message.objects.exists())

    def test_anonymous_returns_401(self):
        self.client.logout()
        res = self.post_text(self.room.id, {'text': '뭐해?'})
        self.assertEqual(res.status_code, 401)


class ListMessagesTests(ChatAPITestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.msgs = [
            Message.objects.create(
                room=cls.room,
                sender=Message.Sender.USER if i % 2 == 0 else Message.Sender.CHARACTER,
                text=f'메시지 {i}',
            )
            for i in range(5)
        ]
        Message.objects.create(room=cls.other_room, sender=Message.Sender.USER, text='남의 대화')

    def setUp(self):
        self.client.force_login(self.jisu)

    def test_latest_page_in_chronological_order(self):
        res = self.client.get(self.messages_url(self.room.id), {'limit': 3})

        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual([m['text'] for m in body['messages']], ['메시지 2', '메시지 3', '메시지 4'])
        self.assertTrue(body['has_more'])

    def test_before_loads_older_messages(self):
        res = self.client.get(self.messages_url(self.room.id), {'before': self.msgs[2].id, 'limit': 3})

        body = res.json()
        self.assertEqual([m['text'] for m in body['messages']], ['메시지 0', '메시지 1'])
        self.assertFalse(body['has_more'])

    def test_only_this_rooms_messages(self):
        texts = [m['text'] for m in self.client.get(self.messages_url(self.room.id)).json()['messages']]
        self.assertNotIn('남의 대화', texts)
        self.assertEqual(len(texts), 5)

    def test_other_users_room_returns_403(self):
        res = self.client.get(self.messages_url(self.other_room.id))
        self.assertEqual(res.status_code, 403)
        self.assertNotIn('messages', res.json())

    def test_missing_room_returns_404(self):
        self.assertEqual(self.client.get(self.messages_url(999999)).status_code, 404)

    def test_invalid_query_returns_400(self):
        for params in [{'before': 'abc'}, {'limit': 'x'}, {'limit': 0}]:
            with self.subTest(params=params):
                self.assertEqual(self.client.get(self.messages_url(self.room.id), params).status_code, 400)

    def test_anonymous_returns_401(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.messages_url(self.room.id)).status_code, 401)

    def test_other_methods_return_405(self):
        self.assertEqual(self.client.delete(self.messages_url(self.room.id)).status_code, 405)


class FriendListTests(ChatAPITestBase):
    url = reverse('chat:friend-list')

    def setUp(self):
        self.client.force_login(self.jisu)

    def test_lists_only_my_friends_with_last_message_and_unread_count(self):
        seoyun_room = Friendship.objects.create(user=self.jisu, character=self.seoyun)
        Message.objects.create(room=self.room, sender=Message.Sender.USER, text='뭐해?')
        Message.objects.create(room=self.room, sender=Message.Sender.CHARACTER, text='알바라 못 봤네 쏘리 ㅋㅋ')
        Message.objects.create(room=self.room, sender=Message.Sender.CHARACTER, text='이제 끝남')
        Message.objects.create(
            room=self.room, sender=Message.Sender.CHARACTER, text='읽은 메시지', read_at=timezone.now(),
        )
        Message.objects.create(room=self.other_room, sender=Message.Sender.CHARACTER, text='남의 방')

        res = self.client.get(self.url)

        self.assertEqual(res.status_code, 200)
        friends = res.json()['friends']
        self.assertEqual([f['room_id'] for f in friends], [self.room.id, seoyun_room.id])  # 최근 대화 순
        dohyun = friends[0]
        self.assertEqual(dohyun['character']['name'], '도현')
        self.assertEqual(dohyun['character']['status_message'], '알바 중')
        self.assertEqual(dohyun['last_message']['text'], '읽은 메시지')
        self.assertEqual(dohyun['unread_count'], 2)  # 내가 보낸 것과 이미 읽은 것은 세지 않는다
        self.assertIsNone(friends[1]['last_message'])
        self.assertEqual(friends[1]['unread_count'], 0)

    def test_anonymous_returns_401(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 401)


class ModelRuleTests(ChatAPITestBase):
    def test_one_room_per_user_and_character(self):
        with self.assertRaises(IntegrityError):
            Friendship.objects.create(user=self.jisu, character=self.dohyun)

    def test_reply_plan_times_must_be_in_order(self):
        message = Message.objects.create(room=self.room, sender=Message.Sender.USER, text='뭐해?')
        now = timezone.now()
        with self.assertRaises(IntegrityError):
            ReplyPlan.objects.create(
                room=self.room, trigger_message=message,
                read_at=now, typing_at=now - timedelta(minutes=1), reply_at=now + timedelta(minutes=2),
            )

    def test_deleting_friendship_deletes_conversation(self):
        message = Message.objects.create(room=self.room, sender=Message.Sender.USER, text='뭐해?')
        now = timezone.now()
        ReplyPlan.objects.create(room=self.room, trigger_message=message, read_at=now, typing_at=now, reply_at=now)

        self.room.delete()

        self.assertFalse(Message.objects.filter(pk=message.pk).exists())
        self.assertFalse(ReplyPlan.objects.exists())
        self.assertTrue(Character.objects.filter(pk=self.dohyun.pk).exists())  # 공개 층은 남는다
