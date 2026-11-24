from datetime import timedelta

from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from config.asgi import get_application

from world.models import Character

from .live import publish_room_events
from .models import Friendship, Message, ReplyPlan
from .replies import dispatch_due


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
)
class RoomSocketTests(TransactionTestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(username='owner')
        self.visitor = user_model.objects.create_user(username='visitor')
        character = Character.objects.create(name='도현', age=23, major='경영')
        self.room = Friendship.objects.create(user=self.owner, character=character)

    def socket(self, user=None, origin=b'http://testserver'):
        headers = [(b'origin', origin)]
        if user is not None:
            self.client.force_login(user)
            cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].output(header='').strip()
            headers.append((b'cookie', cookie.encode()))
        return WebsocketCommunicator(get_application(), f'/ws/chat/{self.room.pk}/', headers=headers)

    async def test_owner_receives_events_in_order(self):
        owner = await sync_to_async(self.socket)(self.owner)
        self.assertTrue((await owner.connect())[0])
        try:
            events = [
                {'type': 'message.read', 'message_id': 1, 'read_at': '2026-11-23T19:00:00+09:00'},
                {'type': 'typing.start'},
                {'type': 'typing.stop'},
                {'type': 'message.new', 'message': {'id': 2, 'text': '쉬는 중 ㅋㅋ'}},
            ]
            await sync_to_async(publish_room_events)(self.room.pk + 1, events)
            self.assertTrue(await owner.receive_nothing())
            await sync_to_async(publish_room_events)(self.room.pk, events)
            self.assertEqual([await owner.receive_json_from() for _ in events], events)
        finally:
            await owner.disconnect()

    async def test_rejects_unauthenticated_nonowners_and_foreign_origins(self):
        for user, origin in [
            (None, b'http://testserver'),
            (self.visitor, b'http://testserver'),
            (self.owner, b'http://foreign.test'),
        ]:
            with self.subTest(user=user, origin=origin):
                socket = await sync_to_async(self.socket)(user, origin)
                self.assertFalse((await socket.connect())[0])
                await socket.disconnect()

    async def test_tabs_receive_the_same_bubble_and_typing_continues_after_a_tab_closes(self):
        now = timezone.now()

        @sync_to_async
        def plan_sequence():
            message = Message.objects.create(room=self.room, sender='user', text='뭐해?', created_at=now)
            return ReplyPlan.objects.create(room=self.room, trigger_message=message, read_at=now,
                                            typing_at=now, reply_at=now, result=['알바 끝남', '이제 집 감'])

        plan = await plan_sequence()
        first_tab, second_tab = [await sync_to_async(self.socket)(self.owner) for _ in range(2)]
        self.assertTrue((await first_tab.connect())[0])
        self.assertTrue((await second_tab.connect())[0])
        try:
            await sync_to_async(dispatch_due)(now)
            first_events = [await first_tab.receive_json_from() for _ in range(3)]
            self.assertEqual([await second_tab.receive_json_from() for _ in range(3)], first_events)
            self.assertEqual(first_events[-1]['message']['text'], '알바 끝남')
        finally:
            await first_tab.disconnect()
        try:
            second = await sync_to_async(plan.bubbles.get)(position=1)
            await sync_to_async(dispatch_due)(second.typing_at)
            self.assertEqual((await second_tab.receive_json_from())['type'], 'typing.start')
            await sync_to_async(dispatch_due)(second.deliver_at)
            self.assertEqual((await second_tab.receive_json_from())['type'], 'typing.stop')
            self.assertEqual((await second_tab.receive_json_from())['message']['text'], '이제 집 감')
            await sync_to_async(dispatch_due)(second.deliver_at + timedelta(minutes=1))
            self.assertTrue(await second_tab.receive_nothing())
        finally:
            await second_tab.disconnect()
