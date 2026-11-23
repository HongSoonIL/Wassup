"""Room events sent after the corresponding database transaction commits."""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from redis.exceptions import RedisError


logger = logging.getLogger(__name__)


def room_group(room_id):
    return f'chat.room.{room_id}'


def publish_room_events(room_id, events):
    if not events:
        return
    try:
        async_to_sync(get_channel_layer().group_send)(
            room_group(room_id), {'type': 'chat.events', 'events': events},
        )
    except (RedisError, OSError):
        logger.exception('Could not publish updates for room %s', room_id)
