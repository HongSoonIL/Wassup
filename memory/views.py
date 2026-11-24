import json
from datetime import date

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from world.clock import world_now
from chat.models import Friendship
from chat.views import error, json_response, login_required_json
from . import services
from .models import Memory


def _edit_fields(body, kind):
    if kind == Memory.Kind.RESULT:
        if not body or not body.keys() <= {'score', 'pinned'}:
            raise ValueError('점수 또는 꼭 기억 여부를 보내 주세요.')
        if 'pinned' in body and type(body['pinned']) is not bool:
            raise ValueError('꼭 기억 여부를 확인해 주세요.')
        if 'score' in body:
            if type(body['score']) is not int or not 0 <= body['score'] <= 100:
                raise ValueError('점수는 0부터 100까지의 정수로 보내 주세요.')
            body['confirmed_at'] = world_now()
        return body
    if not body or not body.keys() <= {'text', 'event_date'}:
        raise ValueError('수정할 기억 내용이나 날짜를 보내 주세요.')
    if 'text' in body:
        body['text'] = services.clean_text(body['text'])
    raw_date = body.get('event_date')
    if raw_date is not None:
        if kind == Memory.Kind.PREFERENCE:
            raise ValueError('취향 기억에는 날짜를 지정할 수 없어요.')
        try:
            parsed = date.fromisoformat(raw_date)
            if parsed.isoformat() != raw_date:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError('날짜는 YYYY-MM-DD 형식이나 null로 보내 주세요.') from None
        body['event_date'] = parsed
    return body


@never_cache
@ensure_csrf_cookie
@login_required_json
@transaction.atomic
def _manage(request, room_id, memory_id=None):
    room = get_object_or_404(Friendship.objects.select_for_update().only('user_id'), pk=room_id)
    if room.user_id != request.user.pk:
        return error(403, '내 채팅방이 아니에요.')
    if request.method == 'GET':
        return json_response(services.room_snapshot(room_id))

    try:
        body = json.loads(request.body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return error(400, 'JSON 형식이 아니에요.')
    if not isinstance(body, dict):
        return error(400, 'JSON 객체를 보내 주세요.')
    revision = body.pop('expected_revision', None)
    if type(revision) is not int or revision < 0:
        return error(400, 'expected_revision은 0 이상의 정수여야 해요.')
    state = services.state_for_locked_room(room_id)
    if revision != state.revision:
        snapshot = services.room_snapshot(room_id)
        snapshot['detail'] = '기억이 변경됐어요. 새로고침한 뒤 다시 시도해 주세요.'
        return json_response(snapshot, status=409)
    if memory_id is None:
        if set(body) == {'results_enabled'} and type(body['results_enabled']) is bool:
            try:
                services.set_results_enabled(state, body['results_enabled'])
            except ValueError as exc:
                return error(400, str(exc))
        elif set(body) != {'enabled'} or type(body['enabled']) is not bool:
            return error(400, 'enabled 값은 true나 false로 보내 주세요.')
        else:
            services.set_enabled(state, body['enabled'])
    else:
        memory = services.room_memories(room_id).filter(pk=memory_id).first() if state.enabled else None
        if memory is None:
            return error(404, '이 방에서 기억을 찾을 수 없어요.')
        if request.method == 'DELETE':
            if body:
                return error(400, '삭제할 때는 expected_revision만 보내 주세요.')
            changes = None
        else:
            try:
                changes = _edit_fields(body, memory.kind)
            except ValueError as exc:
                return error(400, str(exc))
        services.edit_memory(state, memory, changes)
    return json_response(services.room_snapshot(room_id))


room_memories = require_http_methods(['GET', 'PATCH'])(_manage)
room_memory = require_http_methods(['PATCH', 'DELETE'])(_manage)
