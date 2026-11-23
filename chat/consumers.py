from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .live import room_group
from .models import Friendship


class ChatConsumer(AsyncJsonWebsocketConsumer):
    group_name = None

    async def connect(self):
        user = self.scope['user']
        room_id = self.scope['url_route']['kwargs']['room_id']
        if not user.is_authenticated or not await self.user_owns_room(user.pk, room_id):
            await self.close()
            return
        group_name = room_group(room_id)
        await self.channel_layer.group_add(group_name, self.channel_name)
        self.group_name = group_name
        await self.accept()

    async def disconnect(self, close_code):
        if self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        # This socket only delivers server events. Chat writes use the authenticated REST API.
        return

    async def chat_events(self, message):
        for event in message['events']:
            await self.send_json(event)

    @database_sync_to_async
    def user_owns_room(self, user_id, room_id):
        return Friendship.objects.filter(pk=room_id, user_id=user_id).exists()
