from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings

from config.asgi import get_application

from world.models import Character

from .live import publish_room_events
from .models import Friendship


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
